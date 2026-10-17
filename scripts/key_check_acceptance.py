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
import importlib.util
import json
import sys
from pathlib import Path

import yaml

from womm.config import REPO_ROOT
from womm.eval import key_check as kc
from womm.eval.drafting import load_drafting_config
from womm.eval.key_sources import load_sources

CASES = REPO_ROOT / "evals/regression/key_fidelity_2026-10-17.yaml"
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


def score(cases: list[dict], results: list[dict]) -> list[dict]:
    rows = []
    for c, r in zip(cases, results, strict=True):
        claim = r["claims"][0]
        got = MAPPED[claim["verdict"]]
        rows.append({"id": c["id"], "kind": c["kind"], "claim": c["claim"],
                     "expected": c["expected"], "got": got, "pass": got == c["expected"],
                     "reason": claim["reason"], "evidence": claim["evidence"],
                     "shown": claim["shown"]})  # fmt: skip
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="a new directory for this run")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--skip-self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.out.exists():
        print(f"error: {args.out} exists; every run gets a new directory", file=sys.stderr)
        return 2
    cases = yaml.safe_load(args.cases.read_text(encoding="utf-8"))["cases"]
    try:
        pts = points(cases)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    config = load_drafting_config()
    version = kc.check_version(config)
    backends = _backends(config, args.skip_self_check)
    results = asyncio.run(kc.check_points(pts, config, backends))
    rows = score(cases, results)
    passed = sum(r["pass"] for r in rows)
    args.out.mkdir(parents=True)
    (args.out / "run.json").write_text(json.dumps(
        {"check_version": version, "ran_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
         "cases_file": str(args.cases), "passed": passed, "total": len(rows), "rows": rows,
         "raw": results}, indent=1, ensure_ascii=False), encoding="utf-8")  # fmt: skip
    print(f"check {version}: {passed}/{len(rows)} cases right")
    for r in rows:
        if not r["pass"]:
            print(f"  MISS {r['id']} ({r['kind']}): expected {r['expected']}, got {r['got']}: "
                  f"{r['reason'][:160]}")  # fmt: skip
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
