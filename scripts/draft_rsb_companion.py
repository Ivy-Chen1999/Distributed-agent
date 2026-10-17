"""Draft the RSB omissions of a golden case as a companion draft next to it.

    uv run python scripts/draft_rsb_companion.py evals/golden/drafts/<case>.yaml [--version rsb2]

Plan 2026-10-12-001 (U3, U5). The case draft is read, never changed. Its RSB opinion (cached by
scripts/fetch_rsb_opinions.py) goes to the same drafter, with the case's existing points listed
as already in the key; only omissions anchored in the opinion are kept, judged by the same three
judges and, for train/val, ruled by the same tie-break. The result is written as
``<case>.<version>.yaml`` beside the case draft and published with it
(scripts/publish_golden_cases.py). A case without a cached opinion is refused with the reason.
A discarded companion is replaced by drafting a new version (``--version rsb2``).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import re
import sys
from pathlib import Path

from langsmith import tracing_context

from womm.data.fixtures import fixture_dir, load_fixture
from womm.data.ia_index import IA_INDEX_PATH, load_ia_index
from womm.eval import ia_sources
from womm.eval.drafting import (
    DraftingError,
    DraftInputs,
    draft_path,
    draft_rsb_companion,
    load_draft,
    load_drafting_config,
    write_draft,
)
from womm.eval.tiebreak import tiebreak_draft

_spec = importlib.util.spec_from_file_location(
    "draft_golden_case", Path(__file__).with_name("draft_golden_case.py")
)
_draft_script = sys.modules.get("draft_golden_case")
if _draft_script is None:
    _draft_script = importlib.util.module_from_spec(_spec)
    sys.modules["draft_golden_case"] = _draft_script
    _spec.loader.exec_module(_draft_script)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("drafts", nargs="+", type=Path, help="train/val case drafts")
    parser.add_argument("--version", default="rsb", help="companion version: rsb, rsb2, ...")
    parser.add_argument("--config", type=Path, default=None, help="drafting config")
    parser.add_argument("--skip-self-check", action="store_true")
    parser.add_argument("--ia-index", type=Path, default=IA_INDEX_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--ia-root", type=Path, default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def companion_inputs(case, ia_root: Path | None, ia_index: Path) -> tuple[DraftInputs, str]:
    """The drafter's inputs for ``case``'s companion (RSB text: the opinion alone) and the RSB
    text the tie-break shows around anchors (the opinion and the annex)."""
    cached = ia_sources.load_cached_ia(case.fixture, ia_root)
    if cached is None:
        raise DraftingError(f"{case.case_id}: no cached IA for {case.fixture}")
    if not cached.rsb_opinion.strip():
        raise DraftingError(
            f"{case.case_id}: no RSB opinion for {case.fixture} ({cached.rsb_status}); run "
            "scripts/fetch_rsb_opinions.py"
        )
    extract = ia_sources.reparse_cached(case.fixture, ia_root)
    sections = [s for s in extract.sections if s.kind != "procedural"]
    fixture = load_fixture(fixture_dir(case.fixture))
    inputs = DraftInputs(
        case_id=case.case_id,
        split=case.split,
        fixture=fixture,
        scenario=fixture.scenario(case.scenario_id),
        ia_full_text=cached.full_text,
        ia_sections_text="\n\n".join(f"### {s.title}\n\n{s.text}" for s in sections),
        ia_section_titles=[s.title for s in sections],
        rsb_text=cached.rsb_opinion,
        rsb_status=cached.rsb_status,
        ia_reference=case.ia_reference,
        notes=f"RSB companion of {case.case_id} (plan 2026-10-12-001)",
        ia_record=load_ia_index(ia_index).get(case.fixture),
        drafted_by=case.provenance.drafted_by,
    )
    return inputs, cached.rsb_text


async def run(args: argparse.Namespace, backends=None) -> list[Path]:
    if not re.fullmatch(r"rsb\d*", args.version):
        raise DraftingError(f"--version {args.version!r}: use rsb, rsb2, rsb3, ...")
    config = load_drafting_config(args.config) if args.config else load_drafting_config()
    written = []
    for path in args.drafts:
        case = load_draft(path)
        if case.companion is not None:
            raise DraftingError(f"{path}: is a companion; give the case draft")
        if case.split == "holdout":
            raise DraftingError(f"{case.case_id}: holdout companions are not drafted here")
        target = draft_path(case.case_id, case.split, path.parent, args.version)
        if target.exists():
            raise DraftingError(f"{target} exists; discard it or draft a new --version")
        inputs, rsb_text = companion_inputs(case, args.ia_root, args.ia_index)
        if backends is None:
            backends = await _draft_script.prepare_backends(
                config, skip_self_check=args.skip_self_check
            )
        comp = await draft_rsb_companion(case, inputs, config, backends, args.version)
        if config.roles.tiebreak is not None and any(
            i.review.decision == "pending" for i in comp.items()
        ):
            comp = await tiebreak_draft(comp, inputs.fixture, inputs.ia_full_text, config,
                                        backends, rsb_text=rsb_text)  # fmt: skip
        out = write_draft(comp, path.parent)
        kept = sum(i.review.decision in ("auto_accepted", "llm_kept") for i in comp.items())
        print(f"{case.case_id}: {len(comp.items())} RSB omission(s) drafted, {kept} kept -> {out}")
        written.append(out)
    return written


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        with tracing_context(enabled=False):
            asyncio.run(run(args))
    except DraftingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
