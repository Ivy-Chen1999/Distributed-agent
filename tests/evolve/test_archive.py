"""Candidate archive and lineage (U3, R27): every candidate, its parent, diff and train/val
metrics; never holdout metrics."""

import psycopg
import pytest

from womm.config import REPO_ROOT
from womm.eval.evaluators import CaseScore
from womm.evolve.archive import (
    Archive,
    archive_child,
    archive_twin,
    metric_rows,
    seed_archive,
)
from womm.evolve.edits import validate_diff
from womm.models.system_version import load_system_version

from .test_edits import EXTRA, _add_workforce

BASE = load_system_version(REPO_ROOT / "system_versions" / "v1.0-unscoped.yaml", REPO_ROOT)


def _fiscal_edit(sv, extra=EXTRA) -> list[dict]:
    fiscal = next(e for e in sv.spec.experts if e.id == "fiscal").role
    return [
        {"op": "edit_prompt", "role": "expert:fiscal", "new_text": sv.prompt_text(fiscal) + extra}
    ]


async def test_seed_and_round_trip(db):
    archive = Archive(db)
    seeds = await seed_archive(archive, REPO_ROOT)
    assert [s.spec.name for s in seeds] == ["v1.0-unscoped", "v1.0-unscoped-api"]
    child = await archive_child(archive, BASE, validate_diff(BASE, _fiscal_edit(BASE)),
                                origin="gepa", cycle_id="c1",
                                proposer={"model": "m", "prompt_hash": "h"})  # fmt: skip
    loaded = await archive.load_candidate(child.version_id)
    assert loaded.version_id == child.version_id and loaded.prompts == child.prompts
    row = await archive.get(child.version_id)
    assert (row["parent_id"], row["origin"], row["cycle_id"]) == (BASE.version_id, "gepa", "c1")
    assert row["diff"]["ops"][0]["op"] == "edit_prompt"
    assert row["diff"]["rendered"]["summary"]["prompts_changed"] == ["expert:fiscal"]
    assert row["proposer"] == {"model": "m", "prompt_hash": "h"}
    # Archive rows carry no decision fields (U6 content boundary).
    assert not {"decision", "promoted", "holdout"} & set(row)


async def test_lineage_and_twins(db):
    archive = Archive(db)
    await seed_archive(archive, REPO_ROOT)
    child = await archive_child(archive, BASE, validate_diff(BASE, _fiscal_edit(BASE)),
                                origin="gepa")  # fmt: skip
    grandchild = await archive_child(
        archive, child, validate_diff(child, [_add_workforce()]), origin="topology"
    )
    lineage = await archive.lineage(grandchild.version_id)
    assert [r["version_id"] for r in lineage] == [
        BASE.version_id, child.version_id, grandchild.version_id,
    ]  # fmt: skip
    twin = await archive_twin(archive, grandchild)
    assert {r.backend for r in twin.spec.roles().values()} == {"api"}
    row = await archive.get(twin.version_id)
    assert (row["origin"], row["twin_of"]) == ("twin", grandchild.version_id)
    assert [r["version_id"] for r in await archive.lineage(twin.version_id)][-2:] == [
        grandchild.version_id, twin.version_id,
    ]  # fmt: skip
    assert (await archive.load_candidate(twin.version_id)).version_id == twin.version_id
    kids = await archive.children(child.version_id)
    assert [k["version_id"] for k in kids] == [grandchild.version_id]


async def test_archiving_twice_is_idempotent(db):
    archive = Archive(db)
    await seed_archive(archive, REPO_ROOT)
    first = (await archive.get(BASE.version_id))["created_at"]
    assert await archive.archive(BASE, origin="manual") is False
    row = await archive.get(BASE.version_id)
    assert row["created_at"] == first and row["origin"] == "seed"


async def test_unknown_parent_is_refused(db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await Archive(db).archive(BASE, origin="gepa", parent_id="sv_unknown")


async def test_holdout_metrics_fail_on_the_constraint(db):
    archive = Archive(db)
    await seed_archive(archive, REPO_ROOT)
    row = {"level": "split", "subject": "", "metric": "coverage", "n": 1, "mean": 0.5,
           "sd": None}  # fmt: skip
    await archive.record_metrics(BASE.version_id, "val", [row])
    with pytest.raises(psycopg.errors.CheckViolation):
        await archive.record_metrics(BASE.version_id, "holdout", [row])
    assert [m["split"] for m in await archive.metrics(BASE.version_id)] == ["val"]


async def test_rebuilt_with_no_prompt_files_on_disk(db, tmp_path, monkeypatch):
    archive = Archive(db)
    await seed_archive(archive, REPO_ROOT)
    child = await archive_child(archive, BASE, validate_diff(BASE, _fiscal_edit(BASE)),
                                origin="gepa")  # fmt: skip
    monkeypatch.chdir(tmp_path)  # no repo files reachable by relative path
    loaded = await archive.load_candidate(child.version_id)
    assert loaded.version_id == child.version_id


def test_metric_rows_per_case_proposal_and_split():
    def s(case, cov):
        return CaseScore(case_id=case, scenario_id="s", outcome="scored", coverage=cov,
                         grounding=1.0, omissions_addressed=None)  # fmt: skip

    scores = [s("a", 0.5), s("a", 0.7), s("b", 1.0), s("c", 0.2)]
    scores.append(CaseScore(case_id="c", scenario_id="s", outcome="errored"))
    rows = metric_rows(scores, {"a": "ai_act", "b": "ai_act", "c": "cra"})
    cov = {(r["level"], r["subject"]): r for r in rows if r["metric"] == "coverage"}
    assert cov[("case", "a")]["mean"] == pytest.approx(0.6) and cov[("case", "a")]["n"] == 2
    assert cov[("proposal", "ai_act")]["mean"] == pytest.approx(0.8)
    assert cov[("split", "")]["mean"] == pytest.approx((0.6 + 1.0 + 0.2) / 3)
    assert cov[("case", "c")]["n"] == 1 and cov[("case", "c")]["sd"] is None
    assert not [r for r in rows if r["metric"] == "omissions_addressed"]
