"""scripts/tiebreak_golden_case.py on a holdout draft: local scenario, untraced, kept in .cache/."""

import importlib.util
import sys
from unittest.mock import MagicMock

import pytest
import yaml
from langsmith.run_helpers import get_tracing_context

from womm.config import REPO_ROOT
from womm.eval import drafting as drafting_module
from womm.eval.drafting import load_draft
from womm.llm.fake import FakeBackend

from .eval.draft_factory import KEYS, draft_dict, write
from .eval.test_tiebreak import IA, config, verdict

spec = importlib.util.spec_from_file_location(
    "tiebreak_golden_case", REPO_ROOT / "scripts/tiebreak_golden_case.py"
)
script = importlib.util.module_from_spec(spec)
sys.modules["tiebreak_golden_case"] = script
spec.loader.exec_module(script)

CASE = "case_90_widget_switching"
SCENARIO = "eval_spot_sealed"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A holdout draft under tmp_path/cache (the stand-in for .cache/), its local scenario in
    tmp_path/private, a fake tie-break backend and a recorder of the tracing state."""
    monkeypatch.setattr(drafting_module, "CACHE_DIR", tmp_path / "cache")
    private = tmp_path / "private"
    private.mkdir()
    articles = [k.rsplit("/", 1)[1] for k in KEYS]
    scenarios = {"data_act": [{"scenario_id": SCENARIO, "description": "Sealed switching",
                               "articles": articles}]}  # fmt: skip
    (private / "holdout_scenarios.yaml").write_text(yaml.safe_dump(scenarios))
    draft = tmp_path / "cache" / "drafts" / f"{CASE}.yaml"
    write(draft, draft_dict("holdout") | {"scenario_id": SCENARIO})
    fake = FakeBackend({"tiebreak": [{"verdicts": [verdict("c90_e05")]}]})
    monkeypatch.setattr(script, "load_drafting_config", lambda _p: config())

    async def backends(_config, _skip):
        return {"fake": fake}

    monkeypatch.setattr(script, "backends_for", backends)
    monkeypatch.setattr(script.ia_sources, "load_cached_ia",
                        lambda _f, _r: MagicMock(full_text=IA))  # fmt: skip
    tracing = []
    real = script.tiebreak_draft

    async def spy(*a, **k):
        tracing.append(get_tracing_context()["enabled"])
        return await real(*a, **k)

    monkeypatch.setattr(script, "tiebreak_draft", spy)
    argv = ["--scenarios", str(private / "holdout_scenarios.yaml"), "--private-dir", str(private)]
    return draft, argv, fake, tracing


def test_a_holdout_draft_is_tiebroken_in_place_untraced(setup, capsys):
    draft, argv, fake, tracing = setup
    assert script.main([str(draft), *argv]) == 0
    assert tracing == [False], "holdout material never reaches LangSmith"
    assert f"Scenario: {SCENARIO}: Sealed switching" in fake.calls[0].user_content
    ruled = load_draft(draft)
    assert ruled.split == "holdout" and ruled.provenance.tiebreak_audit is not None
    assert {i.item_id: i.review.decision for i in ruled.items()}["c90_e05"] == "llm_kept"
    assert sorted(p.name for p in draft.parent.iterdir()) == [draft.name]


def test_a_holdout_draft_outside_the_cache_is_refused(setup, tmp_path, capsys):
    draft, argv, fake, _ = setup
    outside = tmp_path / "elsewhere" / draft.name
    write(outside, yaml.safe_load(draft.read_text()))
    assert script.main([str(outside), *argv]) == 1
    assert "only under .cache/" in capsys.readouterr().err and fake.calls == []


def test_a_holdout_draft_without_its_local_scenario_is_refused(setup, capsys):
    draft, argv, fake, _ = setup
    write(draft, draft_dict("holdout") | {"scenario_id": "eval_unknown"})
    assert script.main([str(draft), *argv]) == 1
    assert "no local holdout scenario 'eval_unknown'" in capsys.readouterr().err
    assert fake.calls == []
