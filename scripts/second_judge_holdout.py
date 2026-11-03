"""Settle the holdout drafts with a blind second judge from another model family.

Plan docs/plans/2026-10-26-001-feat-holdout-second-judge-plan.md; womm.eval.second_judge.

    uv run python scripts/second_judge_holdout.py acceptance --max-usd 2
    uv run python scripts/second_judge_holdout.py settle --dry-run
    uv run python scripts/second_judge_holdout.py settle --max-usd 3

``acceptance`` runs the judge twice on the public regression set
(evals/regression/key_fidelity_2026-10-17.yaml) and writes
evals/regression/second_judge_acceptance.yaml: aggregates only. Each pass must keep no point the
set marks unsupported, judged as whole points (gate A), and keep at least 12 of the 15 supported
clauses, judged clause by clause (gate B).

``settle`` refuses until that record has passed for the current prompt and model. It takes the
tie-broken holdout drafts in .cache/drafts/, has the judge judge every item without seeing any
Claude decision, and keeps an item only when the Claude chain kept it and the judge says keep;
the others are recorded as rejected (drop) or unclear (unsure) with the judge's reason. A case
with fewer than 3 kept impacts is not handed off, and fewer than 5 qualifying cases writes no
handoff at all. Handoffs go to .cache/holdout_import/ for scripts/import_holdout_case.py.

Every call is paid at list price: each command has its own cap (``--max-usd``), and all
second-judge spend together is capped at USD 5 (.cache/second_judge/spend.json). Verdicts are
kept per batch in .cache/second_judge/, so a rerun pays only for what is missing. Holdout calls
run with LangSmith tracing disabled.

Exit codes: 0 done, 1 refused or stopped (acceptance failed, too few cases, budget, an API
error), 2 bad input.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from womm.config import REPO_ROOT
from womm.data.fixtures import fixture_dir, load_fixture
from womm.eval import ia_sources
from womm.eval import second_judge as sj
from womm.eval.drafting import DEFAULT_CONFIG, DraftingError, GoldenDraft, load_drafting_config
from womm.eval.golden_review import (
    MIN_KEPT_IMPACTS,
    ReviewError,
    audit_result,
    build_case,
    check_draft,
    load_draft_for_review,
    swept_out,
)
from womm.eval.holdout import MIN_PROPOSALS, PRIVATE_DIR, SCENARIOS_PATH, HoldoutError
from womm.llm.base import LLMBackend, LLMError, get_backend


def _load_script(name: str):
    """A sibling script, loaded once (its error classes must be the same objects)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


verify = _load_script("verify_golden_case")
tiebreak_script = _load_script("tiebreak_golden_case")

DRAFTS_DIR = REPO_ROOT / ".cache" / "drafts"
RECORD = REPO_ROOT / "evals/regression/second_judge_acceptance.yaml"
PASSES = 2
CLAUDE_KEPT = ("auto_accepted", "llm_kept")
VERIFIED_BY = "two-family agreement: claude tie-break chain + {model}"


def backends_for(config) -> dict[str, LLMBackend]:
    role = sj.role_of(config)
    return {role.backend: get_backend(role.backend)}


# ----------------------------------------------------------------------------- acceptance


async def acceptance(args: argparse.Namespace) -> int:
    config = load_drafting_config(args.config)
    role = sj.role_of(config)
    sha = sj.prompt_sha256(config)
    cases = sj.load_regression(args.regression)
    w_items, c_items, notes = sj.acceptance_items(cases, args.ia_root, args.golden_dir)
    if args.dry_run:
        calls, usd = sj.estimate_items(w_items + c_items, config)
        print(f"acceptance: {PASSES} passes x {calls} calls, at most about USD {PASSES * usd:.2f}")
        return 0
    backends = backends_for(config)
    spend = sj.Spend(args.max_usd, args.ledger)
    passes, partial = [], []
    for n in range(1, PASSES + 1):
        # Each pass is its own run; its store only resumes the same pass after a crash.
        store = sj.BatchStore(args.cache_dir / f"acceptance_{sha[:12]}_pass{n}.json",
                              role.model, sha, role.reasoning_effort)  # fmt: skip
        label = f"acceptance pass {n}"
        w = await sj.judge(w_items, config, backends, spend, store, label)
        c = await sj.judge(c_items, config, backends, spend, store, label)
        passes.append(sj.score_pass(cases, w, c))
        partial.append(sj.score_pass(cases, w, c, exclude=sj.CODEX_LABELLED))
    accepted = all(p["gate_a"]["passed"] and p["gate_b"]["passed"] for p in passes)
    record = {
        "date": args.today.isoformat(),
        "model": role.model,
        "reasoning_effort": role.reasoning_effort,
        "prompt": role.prompt,
        "prompt_sha256": sha,
        "regression_set": str(args.regression.relative_to(REPO_ROOT))
        if args.regression.is_relative_to(REPO_ROOT)
        else str(args.regression),
        "accepted": accepted,
        "passes": passes,
        "without_codex_labelled": partial,
        "notes": notes,
        "spend_usd": round(spend.spent, 4),
    }
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(
        "# Second-judge acceptance (plan 2026-10-26-001 KTD4); aggregates of the public "
        "regression set only.\n" + yaml.safe_dump(record, sort_keys=False, width=100),
        encoding="utf-8",
    )
    for n, p in enumerate(passes, 1):
        print(
            f"pass {n}: gate A {'passed' if p['gate_a']['passed'] else 'FAILED'} "
            f"(unsupported kept: {p['gate_a']['kept_unsupported'] or 'none'}); gate B "
            f"{p['gate_b']['kept']}/{p['gate_b']['controls']} "
            f"{'passed' if p['gate_b']['passed'] else 'FAILED'}; window cases kept on the "
            f"anchor passage alone: {p['window_kept_in_w']}/{p['window_cases']}"
        )
    print(f"acceptance {'PASSED' if accepted else 'FAILED'}; spent USD {spend.spent:.2f}; "
          f"record {args.record.name}")  # fmt: skip
    return 0 if accepted else 1


def require_acceptance(record_path: Path, role, sha: str) -> dict[str, Any]:
    if not record_path.exists():
        raise sj.SecondJudgeError(
            f"no acceptance record ({record_path.name}); run the acceptance subcommand first"
        )
    record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    accepted_judge = (
        record.get("model"),
        record.get("reasoning_effort"),
        record.get("prompt_sha256"),
    )
    if accepted_judge != (role.model, role.reasoning_effort, sha):
        raise sj.SecondJudgeError(
            "the acceptance record is for another model, reasoning effort or prompt; run "
            "acceptance again"
        )
    if not record.get("accepted"):
        raise sj.SecondJudgeError("the acceptance did not pass; nothing holdout is sent")
    return record


# ----------------------------------------------------------------------------- settle


def settle_draft(
    draft: GoldenDraft,
    verdicts: dict[str, sj.SecondJudgeVerdict],
    judge: str,
    today: dt.date,
) -> tuple[Any, dict[str, Any]]:
    """The case (None under the floor) and its settlement counts. Only items the Claude chain
    kept can be kept; the second judge's drop or doubt excludes one (plan R3)."""
    decisions: dict[str, dict[str, Any]] = {}
    both = disagree = 0
    for item in draft.items():
        claude_kept = item.review.decision in CLAUDE_KEPT and not swept_out(item)
        v = verdicts[item.item_id]
        both += claude_kept == (v.verdict == "keep")
        disagree += claude_kept != (v.verdict == "keep")
        if claude_kept and v.verdict != "keep":
            decisions[item.item_id] = {
                "decision": "rejected" if v.verdict == "drop" else "unclear",
                "note": f"second judge ({judge.removeprefix('second-judge:')}): {v.reason}",
            }
    decided = verify._decide(draft, decisions, judge)
    kept_before = sum(i.review.decision in CLAUDE_KEPT and not swept_out(i) for i in draft.items())
    counts = {
        "items": len(draft.items()),
        "claude_kept": kept_before,
        "kept": kept_before - len(decisions),
        "excluded_drop": sum(d["decision"] == "rejected" for d in decisions.values()),
        "excluded_unsure": sum(d["decision"] == "unclear" for d in decisions.values()),
        "agreement": round(both / max(1, both + disagree), 3),
    }
    problems = check_draft(
        decided, verify.fixture_keys(decided), holdout_second_judge=True, reviewer_pattern=None
    )
    floor = [p for p in problems if "expected impacts would be kept" in p]
    if problems and not floor:
        raise sj.SecondJudgeError("settlement incomplete:\n  " + "\n  ".join(problems))
    if floor:
        return None, counts | {"under_floor": True}
    try:
        case = build_case(decided, published_on=today, audit=audit_result([decided]),
                          action="settled", second_judge=judge)  # fmt: skip
    except ReviewError:
        return None, counts | {"under_floor": True}
    return case, counts | {"under_floor": False, "kept_impacts": len(case.expected_impacts)}


async def settle(args: argparse.Namespace) -> int:
    config = load_drafting_config(args.config)
    role = sj.role_of(config)
    sha = sj.prompt_sha256(config)
    judge = sj.judge_id(role)
    record = require_acceptance(args.record, role, sha)
    paths = sorted(args.drafts or args.drafts_dir.glob("case_*.yaml"))
    if not paths:
        raise sj.SecondJudgeError(f"no holdout drafts in {args.drafts_dir}")
    # Preflight: every draft is a settled holdout draft with its scenario and IA, before a call.
    work = []
    for path in paths:
        draft = load_draft_for_review(path)
        if draft.split != "holdout":
            raise sj.SecondJudgeError(f"{path.name} is a {draft.split} draft, not holdout")
        verify.require_settled(draft)
        # Every other problem of the gate is found before any paid call, not after it.
        problems = [p for p in check_draft(draft, verify.fixture_keys(draft),
                                           holdout_second_judge=True, reviewer_pattern=None)
                    if "expected impacts would be kept" not in p]  # fmt: skip
        if problems:
            raise sj.SecondJudgeError(f"{path.name}:\n  " + "\n  ".join(problems))
        fixture = load_fixture(fixture_dir(draft.fixture))
        scenario = tiebreak_script.holdout_scenario(path, draft, fixture, args)
        cached = ia_sources.load_cached_ia(draft.fixture, args.ia_root)
        if cached is None:
            raise sj.SecondJudgeError(f"{path.name}: no IA cache for its proposal")
        index = sj.index_for(draft.fixture, args.ia_root)
        items = sj.draft_items(draft, fixture, scenario, cached.full_text, cached.rsb_text, index)
        work.append((path, draft, items))
    if args.dry_run:
        calls, usd = sj.estimate_items([i for *_, items in work for i in items], config)
        print(f"settle: {len(work)} drafts, {sum(len(w[2]) for w in work)} items, {calls} calls, "
              f"at most about USD {usd:.2f}; no call made")  # fmt: skip
        return 0
    backends = backends_for(config)
    spend = sj.Spend(args.max_usd, args.ledger)
    results = []
    for path, draft, items in work:
        store = sj.BatchStore(
            args.cache_dir / f"{draft.case_id}.json", role.model, sha, role.reasoning_effort
        )
        verdicts = await sj.judge(items, config, backends, spend, store, "settle")
        case, counts = settle_draft(draft, verdicts, judge, args.today)
        results.append((path, draft, case, counts))
    qualifying = [r for r in results if r[2] is not None]
    under = [r[1].case_id for r in results if r[2] is None]
    totals = {
        k: sum(r[3][k] for r in results)
        for k in ("items", "claude_kept", "kept", "excluded_drop", "excluded_unsure")
    }
    print(
        f"settled {len(results)} drafts: {totals}; under the floor of {MIN_KEPT_IMPACTS} "
        f"impacts: {len(under)}; spent USD {spend.spent:.2f}"
    )
    if len({r[1].fixture for r in qualifying}) < MIN_PROPOSALS:
        print(
            f"only {len(qualifying)} cases qualify (at least {MIN_PROPOSALS} needed): no handoff "
            f"written; under the floor: {', '.join(under) or 'none'}"
        )
        return 1
    for path, _draft, case, counts in qualifying:
        verify.write_handoff(
            case, path, VERIFIED_BY.format(model=role.model), args.today, args.out_dir,
            second_judge={
                "model": role.model,
                "reasoning_effort": role.reasoning_effort,
                "prompt_sha256": sha,
                "acceptance": {"date": record["date"], "record": args.record.name},
                **counts,
            },
        )  # fmt: skip
    print(f"{len(qualifying)} handoffs written to {args.out_dir}; under the floor: "
          f"{', '.join(under) or 'none'}")  # fmt: skip
    return 0


# ----------------------------------------------------------------------------- cli


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("acceptance", "settle"):
        p = sub.add_parser(name)
        p.add_argument("--dry-run", action="store_true", help="estimate only, no call")
        p.add_argument("--max-usd", type=float, default=2.0 if name == "acceptance" else 3.0)
        p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        p.add_argument("--record", type=Path, default=RECORD, help=argparse.SUPPRESS)
        p.add_argument("--ledger", type=Path, default=sj.LEDGER, help=argparse.SUPPRESS)
        p.add_argument("--cache-dir", type=Path, default=sj.CACHE_DIR, help=argparse.SUPPRESS)
        p.add_argument("--ia-root", type=Path, default=ia_sources.IA_CACHE_ROOT,
                       help=argparse.SUPPRESS)  # fmt: skip
        p.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today(),
                       help=argparse.SUPPRESS)  # fmt: skip
        if name == "acceptance":
            p.add_argument("--regression", type=Path, default=sj.REGRESSION,
                           help=argparse.SUPPRESS)  # fmt: skip
            p.add_argument("--golden-dir", type=Path, default=REPO_ROOT / "evals/golden",
                           help=argparse.SUPPRESS)  # fmt: skip
        else:
            p.add_argument("drafts", nargs="*", type=Path, help="default: .cache/drafts/case_*")
            p.add_argument("--drafts-dir", type=Path, default=DRAFTS_DIR, help=argparse.SUPPRESS)
            p.add_argument("--out-dir", type=Path, default=verify.HANDOFF_DIR,
                           help=argparse.SUPPRESS)  # fmt: skip
            p.add_argument("--scenarios", type=Path, default=SCENARIOS_PATH,
                           help=argparse.SUPPRESS)  # fmt: skip
            p.add_argument("--private-dir", type=Path, default=PRIVATE_DIR,
                           help=argparse.SUPPRESS)  # fmt: skip
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)  # OPENAI_API_KEY; tracing stays off
    args = parse_args(argv)
    try:
        return asyncio.run(acceptance(args) if args.command == "acceptance" else settle(args))
    except sj.BudgetExceeded as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"stopped by an API error ({exc.error_kind}): {exc.message}", file=sys.stderr)
        return 1
    except (sj.SecondJudgeError, verify.VerifyError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except (DraftingError, HoldoutError, ReviewError, OSError) as exc:
        print(f"bad input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
