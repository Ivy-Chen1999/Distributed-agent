"""scripts/import_feedback.py (U11, R31): LangSmith annotation-queue marks on train/val eval runs
into the audit table, Failure Memory and the golden-candidate queue. A fake LangSmith client;
Postgres through the throwaway-database fixture."""

import datetime as dt
import importlib.util
import json
import sys
from types import SimpleNamespace

import pytest

from womm.api.db import Database
from womm.config import REPO_ROOT
from womm.eval.drafting import load_draft
from womm.evolve.failure_memory import CaseRun
from womm.evolve.feedback import FEEDBACK_KEY

from .api.conftest import database_url  # noqa: F401
from .eval import draft_factory as df
from .evolve.test_failure_memory import K1, _finding, _run

spec = importlib.util.spec_from_file_location(
    "import_feedback", REPO_ROOT / "scripts/import_feedback.py"
)
import_feedback = importlib.util.module_from_spec(spec)
sys.modules["import_feedback"] = import_feedback
spec.loader.exec_module(import_feedback)

NOW = dt.datetime(2026, 10, 7, tzinfo=dt.UTC)
CASE = "case_90_widget_switching"


class FakeClient:
    def __init__(self, feedback=()):
        self.feedback = list(feedback)
        self.queues: dict[str, SimpleNamespace] = {}
        self.queued: dict[str, list[str]] = {}
        self.configs: list[str] = []
        self.listed: list[list[str]] = []

    def list_feedback(self, *, run_ids=None, feedback_key=None, **_):
        self.listed.append(sorted(map(str, run_ids)))
        assert feedback_key == [FEEDBACK_KEY]
        return [f for f in self.feedback if str(f.run_id) in set(map(str, run_ids))]

    def create_feedback_config(self, key, *, feedback_config, **_):
        self.configs.append(key)
        assert [c["label"] for c in feedback_config["categories"]][-1] == "weak_evidence"

    def list_annotation_queues(self, *, name=None, **_):
        return [q for q in self.queues.values() if q.name == name]

    def create_annotation_queue(self, *, name, rubric_items=None, **_):
        assert rubric_items[0]["feedback_key"] == FEEDBACK_KEY
        self.queues[name] = SimpleNamespace(id=f"q-{name}", name=name)
        return self.queues[name]

    def add_runs_to_annotation_queue(self, queue_id, *, run_ids=None, **_):
        self.queued.setdefault(queue_id, []).extend(map(str, run_ids))


MISSING = f"impact: Buyers pay twice\nprovisions: {K1}"


def _fb(fid, run="t1", value="missing_impact", comment=MISSING, modified_at=NOW):
    return SimpleNamespace(id=fid, run_id=run, key=FEEDBACK_KEY, value=value, score=None,
                           comment=comment, correction=None, created_at=NOW,
                           modified_at=modified_at,
                           feedback_source=SimpleNamespace(user_name="ana"))  # fmt: skip


def _case_run(run_id: str, rep: int, split="train") -> CaseRun:
    return CaseRun(case_id=CASE, fixture="data_act", split=split, run_id=run_id,
                   repetition=rep, system_version="sv_x")  # fmt: skip


@pytest.fixture
def setup(tmp_path, database_url, monkeypatch):  # noqa: F811
    """An eval report with two scored train runs (t1 -> r1, t2 -> r2) persisted in Failure
    Memory, their saved runs, and an open golden draft for the case."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    runs = [_case_run("r1", 1), _case_run("r2", 2)]
    report = {"system_version": "sv_x", "metadata": {"split": "train", "splits": ["train"]},
              "case_runs": [r.model_dump(mode="json") for r in runs],
              "scores": [{"run_id": "r1", "graph_run_id": "t1"},
                         {"run_id": "r2", "graph_run_id": "t2"}]}  # fmt: skip
    (runs_dir / "eval_20261007T000000Z_sv_x.json").write_text(json.dumps(report))
    for r in ("r1", "r2"):
        (runs_dir / f"{r}.json").write_text(_run(r, board=[_finding("legal", K1, "f1")])
                                             .model_dump_json())  # fmt: skip

    async def persist():
        db = Database(database_url)
        await db.open()
        await db.migrate()
        await db.record_failure_events([], runs)
        await db.close()

    import asyncio

    asyncio.run(persist())
    drafts = tmp_path / "drafts"
    df.write(drafts / f"{CASE}.yaml", df.fully_decided())
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("HOLDOUT_DATABASE_URL", raising=False)
    return SimpleNamespace(runs_dir=runs_dir, drafts=drafts, url=database_url, tmp=tmp_path)


def _main(setup, client, *argv):
    import_feedback._client = lambda: client
    return import_feedback.main([*argv, "--runs-dir", str(setup.runs_dir),
                                 "--drafts-dir", str(setup.drafts),
                                 "--holdout-registry", str(setup.tmp / "none.yaml")])  # fmt: skip


def _rows(url):
    import asyncio

    async def read():
        db = Database(url)
        await db.open()
        try:
            return await db.analyst_feedback("sv_x"), await db.failure_patterns("sv_x")
        finally:
            await db.close()

    return asyncio.run(read())


def test_queue_adds_the_traced_train_val_runs(setup):
    client = FakeClient()
    assert _main(setup, client, "queue", "--sv", "sv_x", "--queue", "womm-review") == 0
    assert client.queued == {"q-womm-review": ["t1", "t2"]} and client.configs == [FEEDBACK_KEY]
    assert _main(setup, client, "queue", "--sv", "sv_x", "--queue", "womm-review") == 0
    assert len(client.queues) == 1  # the existing queue is reused


def test_import_then_stage(setup, capsys):
    client = FakeClient([
        _fb("fb1"),
        _fb("fb2", run="t2", value="weak_evidence", comment="finding: f1\nquote is thin"),
        _fb("fb3", value="accept", comment="finding: f1"),
        _fb("fb4", value="reject", comment="finding: f404"),  # unknown finding: refused
    ])  # fmt: skip
    assert _main(setup, client, "import", "--sv", "sv_x") == 2
    assert "f404" in capsys.readouterr().err
    rows, pats = _rows(setup.url)
    assert sorted(r["feedback_id"] for r in rows) == ["fb1", "fb2", "fb3"]
    assert {(p["kind"], p["source"]) for p in pats} == {
        ("analyst_missing_impact", "human"), ("analyst_weak_evidence", "human")
    }  # fmt: skip
    # A second import is a no-op for the marks already stored.
    client.feedback = client.feedback[:3]
    assert _main(setup, client, "import", "--sv", "sv_x") == 0
    assert "0 new" in capsys.readouterr().out
    # Staging puts the missing impact into the open draft, pending a reviewer.
    assert _main(setup, client, "stage") == 0
    cand = load_draft(setup.drafts / f"{CASE}.yaml").possibly_missing[-1]
    assert (cand.impact, cand.review.decision, cand.provenance.origin) == (
        "Buyers pay twice",
        "pending",
        "human_added",
    )
    rows, _ = _rows(setup.url)
    staged = {r["feedback_id"]: r["golden_candidate"] for r in rows}
    assert staged == {"fb1": "staged", "fb2": None, "fb3": None}


def test_dry_run_writes_nothing(setup):
    assert _main(setup, FakeClient([_fb("fb1")]), "import", "--sv", "sv_x", "--dry-run") == 0
    assert _rows(setup.url)[0] == []


def test_a_holdout_report_is_refused_before_any_feedback_is_read(setup, capsys):
    path = next(setup.runs_dir.glob("eval_*.json"))
    data = json.loads(path.read_text())
    data["metadata"]["split"] = "holdout"
    path.write_text(json.dumps(data))
    client = FakeClient([_fb("fb1")])
    assert _main(setup, client, "import", "--sv", "sv_x") == 2
    assert client.listed == [] and "holdout" in capsys.readouterr().err
    assert _rows(setup.url)[0] == []


def test_a_run_not_in_failure_memory_is_refused(setup, capsys):
    path = next(setup.runs_dir.glob("eval_*.json"))
    data = json.loads(path.read_text())
    data["case_runs"].append(_case_run("r3", 3).model_dump(mode="json"))
    data["scores"].append({"run_id": "r3", "graph_run_id": "t3"})
    path.write_text(json.dumps(data))
    (setup.runs_dir / "r3.json").write_text(_run("r3").model_dump_json())
    assert _main(setup, FakeClient([_fb("fb1", run="t3")]), "import", "--sv", "sv_x") == 2
    assert "not in Failure Memory" in capsys.readouterr().err


def test_refused_with_the_holdout_url_in_the_environment(setup, monkeypatch, capsys):
    monkeypatch.setenv("HOLDOUT_DATABASE_URL", "postgresql://x/y")
    assert _main(setup, FakeClient(), "import", "--sv", "sv_x") == 2
    assert "HOLDOUT_DATABASE_URL" in capsys.readouterr().err


def test_an_edited_mark_is_reimported_and_a_deleted_one_reported(setup, capsys):
    """P2: a newer LangSmith modification replaces the stored mark and its event; a mark gone
    from LangSmith is only reported unless --apply-retractions is given."""
    client = FakeClient([_fb("fb1"), _fb("fb2", run="t2", value="weak_evidence",
                                          comment="finding: f1")])  # fmt: skip
    assert _main(setup, client, "import", "--sv", "sv_x") == 0
    capsys.readouterr()
    later = NOW + dt.timedelta(hours=1)
    client.feedback = [_fb("fb1", comment=f"impact: Buyers pay thrice\nprovisions: {K1}",
                           modified_at=later)]  # fmt: skip
    assert _main(setup, client, "import", "--sv", "sv_x") == 2
    io = capsys.readouterr()
    assert "1 updated" in io.out and "fb2" in io.err and "--apply-retractions" in io.err
    rows, pats = _rows(setup.url)
    by_id = {r["feedback_id"]: r for r in rows}
    assert by_id["fb1"]["payload"]["impact"] == "Buyers pay thrice"
    assert by_id["fb2"]["retracted_at"] is None  # report-only by default
    assert _main(setup, client, "import", "--sv", "sv_x", "--apply-retractions") == 0
    assert "retracted fb2" in capsys.readouterr().out
    rows, pats = _rows(setup.url)
    assert {r["feedback_id"]: r["retracted_at"] is not None for r in rows} == {
        "fb1": False, "fb2": True
    }  # fmt: skip
    assert {p["kind"] for p in pats} == {"analyst_missing_impact"}
    assert _main(setup, client, "import", "--sv", "sv_x") == 0  # nothing left to report


def test_an_edit_after_staging_is_reported_loudly(setup, capsys):
    client = FakeClient([_fb("fb1")])
    assert _main(setup, client, "import", "--sv", "sv_x") == 0
    assert _main(setup, client, "stage") == 0
    capsys.readouterr()
    client.feedback = [_fb("fb1", comment=f"impact: changed\nprovisions: {K1}",
                           modified_at=NOW + dt.timedelta(hours=1))]  # fmt: skip
    assert _main(setup, client, "import", "--sv", "sv_x") == 2
    err = capsys.readouterr().err
    assert "fb1" in err and "already staged" in err and "by hand" in err
