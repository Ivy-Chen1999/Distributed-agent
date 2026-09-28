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
        out = outputs["sha"] if "rev-parse" in cmd else outputs["status"]
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    outputs.update(sha="abc123\n", status="")
    assert identity.code_identity() == identity.CodeIdentity(git_sha="abc123", dirty=False)
    outputs.update(status=" M src/womm/cli.py\n")
    assert identity.code_identity().dirty is True
