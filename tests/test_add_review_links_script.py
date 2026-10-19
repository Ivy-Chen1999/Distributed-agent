"""scripts/add_review_links.py: public IA links on train/val drafts, never on holdout ones."""

import importlib.util
import sys

import yaml

from womm.config import REPO_ROOT
from womm.data.ia_index import IaRecord, update_ia_index
from womm.eval.drafting import DRAFT_HEADER, LEGACY_DRAFT_HEADER, load_draft
from womm.eval.golden_review import check_draft

from .eval.draft_factory import KEYS, draft_dict

spec = importlib.util.spec_from_file_location(
    "add_review_links", REPO_ROOT / "scripts/add_review_links.py"
)
links_script = importlib.util.module_from_spec(spec)
sys.modules["add_review_links"] = links_script
spec.loader.exec_module(links_script)


def _setup(tmp_path, split="train"):
    index = tmp_path / "ia_index.yaml"
    update_ia_index("data_act", IaRecord(celex="52099PC0001", ia_celex="52099SC0001"), index)
    path = tmp_path / "drafts" / "case_90_widget_switching.yaml"
    path.parent.mkdir()
    body = yaml.safe_dump(draft_dict(split), sort_keys=False, allow_unicode=True, width=100)
    path.write_text(LEGACY_DRAFT_HEADER + body)
    registry = tmp_path / "holdout_scenarios.yaml"
    return path, index, registry


def _argv(path, index, registry, tmp_path):
    return [str(path), "--ia-index", str(index), "--holdout-registry", str(registry),
            "--ia-root", str(tmp_path / "no_cache")]  # fmt: skip


def test_adds_links_in_place_and_keeps_the_draft_valid(tmp_path):
    path, index, registry = _setup(tmp_path)
    assert links_script.main(_argv(path, index, registry, tmp_path)) == 0
    text = path.read_text()
    assert text.startswith(DRAFT_HEADER)
    draft = load_draft(path)
    assert draft.review_links.ia.endswith("CELEX:52099SC0001")
    assert check_draft(draft, set(KEYS), allow_pending=True) == []


def test_refuses_a_fixture_registered_as_holdout(tmp_path):
    path, index, registry = _setup(tmp_path)
    registry.write_text(yaml.safe_dump({"data_act": {"scenario": "x"}}))
    before = path.read_text()
    assert links_script.main(_argv(path, index, registry, tmp_path)) == 2
    assert path.read_text() == before


def test_refuses_a_holdout_draft(tmp_path):
    path, index, registry = _setup(tmp_path, split="holdout")
    before = path.read_text()
    assert links_script.main(_argv(path, index, registry, tmp_path)) == 2
    assert path.read_text() == before
