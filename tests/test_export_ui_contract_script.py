"""scripts/export_ui_contract.py: the console's API schemas stay in step with the models
(plan 2026-10-12-001, U8: the redesign handoff works from these files)."""

import importlib.util
import json
import sys

from womm.config import REPO_ROOT

_spec = importlib.util.spec_from_file_location(
    "export_ui_contract", REPO_ROOT / "scripts/export_ui_contract.py"
)
export = importlib.util.module_from_spec(_spec)
sys.modules["export_ui_contract"] = export
_spec.loader.exec_module(export)


def test_the_committed_schemas_match_the_models(capsys):
    assert export.main(["--check"]) == 0, capsys.readouterr().out


def test_check_reports_missing_and_changed_schemas(tmp_path, capsys):
    assert export.main(["--check", "--out", str(tmp_path)]) == 1
    assert "cost_section.schema.json" in capsys.readouterr().out
    assert export.main(["--out", str(tmp_path)]) == 0
    assert export.main(["--check", "--out", str(tmp_path)]) == 0
    (tmp_path / "schema" / "run_result.schema.json").write_text("{}")
    assert export.main(["--check", "--out", str(tmp_path)]) == 1


def test_the_real_sample_run_is_never_overwritten(tmp_path):
    real = REPO_ROOT / "docs" / "ui" / "sample_run.json"
    before = real.read_bytes()
    assert export.main(["--out", str(tmp_path), "--sample-out", str(real)]) == 2
    assert real.read_bytes() == before
    out = tmp_path / "illustrative.json"
    assert export.main(["--out", str(tmp_path), "--sample-out", str(out)]) == 0
    assert json.loads(out.read_text())["run_id"].startswith("run_")
