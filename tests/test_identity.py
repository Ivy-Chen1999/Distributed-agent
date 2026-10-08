import subprocess

from womm import identity


def test_git_failure_falls_back_to_dirty(monkeypatch):
    def boom(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", boom)
    ci = identity.code_identity("2.1.283")
    assert ci.git_sha is None and ci.dirty is True and ci.claude_cli_version == "2.1.283"


def test_clean_and_dirty(monkeypatch):
    outputs = {}

    def fake_run(cmd, **k):
        if "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=outputs.get("diff", ""), stderr="")
        out = outputs["sha"] if "rev-parse" in cmd else outputs["status"]
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    outputs.update(sha="abc123\n", status="")
    assert identity.code_identity() == identity.CodeIdentity(git_sha="abc123", dirty=False)
    outputs.update(status=" M src/womm/cli.py\n")
    assert identity.code_identity().dirty is True


def test_dirty_tree_records_a_hash_of_its_diff(monkeypatch):
    outputs = {"sha": "abc123\n", "status": " M src/womm/cli.py\n", "diff": "+a\n"}

    def fake_run(cmd, **k):
        key = "diff" if "diff" in cmd else "sha" if "rev-parse" in cmd else "status"
        return subprocess.CompletedProcess(cmd, 0, stdout=outputs[key], stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    first = identity.code_identity()
    outputs["diff"] = "+b\n"
    second = identity.code_identity()
    assert first.dirty and first.diff_sha and second.diff_sha
    assert first.diff_sha != second.diff_sha
    outputs["status"] = ""
    assert identity.code_identity().diff_sha is None
