"""Behaviour acceptance of the full-text key check (plan 2026-10-16-001, F4b).

    uv run python scripts/key_check_acceptance.py --out .cache/key_check/acceptance/run1
    uv run python scripts/key_check_acceptance.py --out .cache/key_check/acceptance/run2

Runs the check on each case of evals/regression/key_fidelity_2026-10-17.yaml, on its frozen input
(the full point before any fix) and only on the clause the case judges, and compares the verdict
with ``expected`` (supported -> supported; not_found -> not_supported; cant_tell is a miss).
Refused when a source's sha256 differs from the frozen one. Each run writes a new directory and
reads no earlier run, so two runs are independent. The check passes acceptance when two runs
each get every case right with the same check version. Exit codes: 0 every case right, 1 a miss,
2 bad input.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

from womm.config import REPO_ROOT
from womm.eval import key_check as kc
from womm.eval.drafting import load_drafting_config
from womm.eval.key_sources import load_sources

CASES = REPO_ROOT / "evals/regression/key_fidelity_2026-10-17.yaml"
SPLIT_CASES = REPO_ROOT / "evals/regression/key_split_2026-10-17.yaml"
MAPPED = {"supported": "supported", "not_found": "not_supported", "cant_tell": "cant_tell"}


def _backends(config, skip_self_check: bool):
    spec = importlib.util.spec_from_file_location(
        "draft_golden_case", Path(__file__).with_name("draft_golden_case.py")
    )
    script = sys.modules.get("draft_golden_case")
    if script is None:
        script = importlib.util.module_from_spec(spec)
        sys.modules["draft_golden_case"] = script
        spec.loader.exec_module(script)
    return asyncio.run(script.prepare_backends(config, skip_self_check=skip_self_check))


def check_sources(cases: list[dict], ia_root: Path | None = None) -> None:
    """Refused when a source has changed since the cases were frozen."""
    for c in cases:
        inp = c["input"]
        if load_sources(inp["fixture"], ia_root).digests != inp["sources"]:
            raise ValueError(f"{c['id']}: the sources of {inp['fixture']} changed since the "
                             "cases were frozen; freeze them again before an acceptance run")  # fmt: skip


def points(cases: list[dict], ia_root: Path | None = None) -> list[kc.PointInput]:
    """One point per case, on its frozen input; refused when a source has changed."""
    out = []
    for c in cases:
        inp = c["input"]
        now = load_sources(inp["fixture"], ia_root).digests
        if now != inp["sources"]:
            raise ValueError(f"{c['id']}: the sources of {inp['fixture']} changed since the "
                             "cases were frozen; freeze them again before an acceptance run")  # fmt: skip
        out.append(kc.PointInput(key=c["id"], fixture=inp["fixture"], fields=inp["point"],
                                 anchor=inp["anchor"], anchored_on=inp["anchored_on"],
                                 claims=(c["claim"],)))  # fmt: skip
    return out


def stage(case: dict, got: str, claim: dict) -> str:
    """Where a miss went wrong: the split, retrieval (the cited evidence was never shown),
    quote verification (a verdict without a verifiable quote) or the judgement itself."""
    reason = claim.get("reason", "")
    if got == "cant_tell" and reason.startswith(("quote not found", "no quote from")):
        return "quote"
    where = re.match(r"[a-z_]+/([a-z_]+\.txt:\d+)", case.get("source", ""))
    if case["expected"] == "supported" and where and where.group(1) not in claim.get("shown", []):
        return "retrieval"
    return "judgement"


def score(cases: list[dict], results: list[dict]) -> list[dict]:
    rows = []
    for c, r in zip(cases, results, strict=True):
        if r.get("incomplete") or not r["claims"]:
            rows.append({"id": c["id"], "kind": c["kind"], "claim": c["claim"],
                         "expected": c["expected"], "got": "incomplete", "pass": False,
                         "stage": "split", "reason": r.get("incomplete", "no claims"),
                         "evidence": [], "shown": []})  # fmt: skip
            continue
        claim = r["claims"][0]
        got = MAPPED[claim["verdict"]]
        ok = got == c["expected"]
        rows.append({"id": c["id"], "kind": c["kind"], "claim": c["claim"],
                     "expected": c["expected"], "got": got, "pass": ok,
                     "stage": None if ok else stage(c, got, claim), "reason": claim["reason"],
                     "evidence": claim["evidence"], "shown": claim["shown"]})  # fmt: skip
    return rows


def _covers(element: str, claim: str) -> bool:
    words = [w for w in kc.tokens(element) if len(w) > 2]
    have = set(kc.tokens(claim))
    return bool(words) and all(w in have for w in words)


def score_split(cases: list[dict], results: list[dict]) -> list[dict]:
    """Each element must land whole in one claim of the check's own split, with its verdict."""
    rows = []
    for c, r in zip(cases, results, strict=True):
        for n, e in enumerate(c["must_cover"], 1):
            rid = f"{c['id']}.{n}"
            hit = next(
                (cl for cl in r.get("claims", []) if _covers(e["element"], cl["text"])), None
            )
            if r.get("incomplete") or hit is None:
                rows.append({"id": rid, "kind": "split", "claim": e["element"],
                             "expected": e["expected"], "got": "missing", "pass": False,
                             "stage": "split", "reason": r.get("incomplete") or
                             "no claim holds the element whole", "evidence": [],
                             "shown": [], "claims": [cl["text"] for cl in r.get("claims", [])]})  # fmt: skip
                continue
            got = MAPPED[hit["verdict"]]
            ok = got == e["expected"]
            rows.append({"id": rid, "kind": "split", "claim": hit["text"],
                         "expected": e["expected"], "got": got, "pass": ok,
                         "stage": None if ok else stage({"expected": e["expected"]}, got, hit),
                         "reason": hit["reason"], "evidence": hit["evidence"],
                         "shown": hit["shown"]})  # fmt: skip
    return rows


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="a new directory for this run")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--split-cases", type=Path, default=SPLIT_CASES)
    parser.add_argument("--skip-self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.out.exists():
        print(f"error: {args.out} exists; every run gets a new directory", file=sys.stderr)
        return 2
    cases = yaml.safe_load(args.cases.read_text(encoding="utf-8"))["cases"]
    split_cases = (yaml.safe_load(args.split_cases.read_text(encoding="utf-8")) or {}).get(
        "cases"
    ) or []
    if not cases or not split_cases:
        print("error: no cases: an empty case set never passes acceptance", file=sys.stderr)
        return 2
    try:
        pts = points(cases)
        split_pts = [kc.PointInput(key=c["id"], fixture=c["input"]["fixture"],
                                   fields=c["input"]["point"], anchor=c["input"]["anchor"],
                                   anchored_on=c["input"]["anchored_on"]) for c in split_cases]  # fmt: skip
        check_sources(split_cases)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    config = load_drafting_config()
    version = kc.check_version(config)
    backends = _backends(config, args.skip_self_check)
    results = asyncio.run(kc.check_points(pts + split_pts, config, backends))
    rows = score(cases, results[: len(pts)]) + score_split(split_cases, results[len(pts) :])
    passed = sum(r["pass"] for r in rows)
    args.out.mkdir(parents=True)
    record = {"check_version": version,
              "ran_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
              "cases": {str(args.cases): _sha(args.cases), str(args.split_cases): _sha(args.split_cases)},
              "sources": {c["input"]["fixture"]: c["input"]["sources"] for c in cases},
              "passed": passed, "total": len(rows),
              "misses_by_stage": dict(Counter(r["stage"] for r in rows if not r["pass"])),
              "rows": rows, "raw": results}  # fmt: skip
    (args.out / "run.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), "utf-8")
    print(f"check {version}: {passed}/{len(rows)} right "
          f"({len(cases)} given clauses, {len(rows) - len(cases)} split elements)")  # fmt: skip
    for r in rows:
        if not r["pass"]:
            print(f"  MISS {r['id']} [{r['stage']}] ({r['kind']}): expected {r['expected']}, "
                  f"got {r['got']}: {r['reason'][:150]}")  # fmt: skip
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
