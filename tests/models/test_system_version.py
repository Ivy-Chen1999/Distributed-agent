from pathlib import Path

import pytest
import yaml

from womm.models.system_version import load_system_version

ROLE = {"backend": "fake", "model": "fake-1", "prompt": "prompts/p.md"}


def _write(tmp: Path, spec: dict, name: str = "v.yaml") -> Path:
    (tmp / "prompts").mkdir(exist_ok=True)
    (tmp / "prompts" / "p.md").write_text("You are a planner.")
    path = tmp / name
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    return path


def _spec() -> dict:
    return {
        "name": "t",
        "planner": ROLE,
        "synthesis": ROLE,
        "judge": ROLE,
        "experts": [{"id": "legal", "domain": "legal", "role": ROLE}],
    }


def test_hash_stable(tmp_path):
    p = _write(tmp_path, _spec())
    first = load_system_version(p, tmp_path).version_id
    assert first == load_system_version(p, tmp_path).version_id


def test_prompt_change_changes_hash(tmp_path):
    p = _write(tmp_path, _spec())
    before = load_system_version(p, tmp_path).version_id
    (tmp_path / "prompts" / "p.md").write_text("You are a planner!")
    assert load_system_version(p, tmp_path).version_id != before


def test_key_order_does_not_change_hash(tmp_path):
    spec = _spec()
    a = load_system_version(_write(tmp_path, spec, "a.yaml"), tmp_path).version_id
    reordered = dict(reversed(list(spec.items())))
    b = load_system_version(_write(tmp_path, reordered, "b.yaml"), tmp_path).version_id
    assert a == b


def test_missing_prompt_file(tmp_path):
    spec = _spec()
    spec["planner"] = {**ROLE, "prompt": "prompts/missing.md"}
    with pytest.raises(FileNotFoundError, match="missing.md"):
        load_system_version(_write(tmp_path, spec), tmp_path)


def test_duplicate_expert_ids_rejected(tmp_path):
    spec = _spec()
    spec["experts"] = spec["experts"] * 2
    with pytest.raises(ValueError, match="duplicate expert ids"):
        load_system_version(_write(tmp_path, spec), tmp_path)


def test_prompt_snapshot_survives_file_edits(tmp_path):
    p = _write(tmp_path, _spec())
    sv = load_system_version(p, tmp_path)
    (tmp_path / "prompts" / "p.md").write_text("edited during a run")
    assert sv.prompt_text(sv.spec.planner) == "You are a planner."
