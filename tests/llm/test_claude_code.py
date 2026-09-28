"""claude_code backend with a stubbed subprocess (no real CLI calls)."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from womm.llm import claude_code as cc
from womm.llm.base import LLMError
from womm.llm.claude_code import (
    ClaudeCodeBackend,
    IsolationCheckFailed,
    build_child_env,
    classify_failure,
)
from womm.models.findings import FindingBatch
from womm.models.system_version import RoleConfig

MODEL = "claude-haiku-4-5-20251001"


class Answer(BaseModel):
    name: str
    score: int


def cfg(**kw: Any) -> RoleConfig:
    base = {"backend": "claude_code", "model": MODEL, "prompt": "prompts/x.md", "timeout_s": 5}
    return RoleConfig(**(base | kw))


def result_payload(structured: Any = None, **kw: Any) -> str:
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": json.dumps(structured),
        "structured_output": structured,
        "total_cost_usd": 0.0115,
        "usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 5351,
            "cache_read_input_tokens": 4,
            "output_tokens": 153,
        },
        "api_error_status": None,
    }
    payload.update(kw)
    return json.dumps(payload)


NOT_LOGGED_IN = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "result": "Not logged in · Please run /login",
        "api_error_status": None,
        "total_cost_usd": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
)


class FakeProc:
    def __init__(self, responder, argv, kwargs, registry):
        self._responder = responder
        self.argv = list(argv)
        self.kwargs = kwargs
        self.pid = 999_999
        self.returncode: int | None = None
        self.stdin: str | None = None
        self.killed = False
        self._registry = registry

    async def communicate(self, input: bytes | None = None):
        self.stdin = input.decode() if input else None
        self._registry["active"] += 1
        self._registry["max_active"] = max(self._registry["max_active"], self._registry["active"])
        try:
            out, err, rc, delay = self._responder(self)
            if delay:
                await asyncio.sleep(delay)
        finally:
            self._registry["active"] -= 1
        self.returncode = rc
        return out.encode(), err.encode()

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


class Cli:
    """Installs a fake asyncio.create_subprocess_exec; `responses` is consumed in order."""

    def __init__(self, monkeypatch):
        self.procs: list[FakeProc] = []
        self.registry = {"active": 0, "max_active": 0}
        self.responses: list[Any] = []
        self.cwd_existed: list[bool] = []

        async def fake_exec(*argv, **kwargs):
            if "cwd" in kwargs:
                self.cwd_existed.append(Path(kwargs["cwd"]).is_dir())
            proc = FakeProc(self._respond, argv, kwargs, self.registry)
            self.procs.append(proc)
            return proc

        monkeypatch.setattr(cc.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(cc.os, "killpg", lambda pid, sig: None)

    def _respond(self, proc: FakeProc):
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if callable(item):
            item = item(proc)
        if isinstance(item, str):
            return item, "", 0, 0
        out, err, rc, *rest = item
        return out, err, rc, (rest[0] if rest else 0)


@pytest.fixture
def cli(monkeypatch):
    return Cli(monkeypatch)


async def test_happy_path_returns_validated_object_and_usage(cli):
    cli.responses = [result_payload({"name": "Bob", "score": 3})]
    obj, usage = await ClaudeCodeBackend().call("planner", "sys", "hello", Answer, cfg())
    assert obj == Answer(name="Bob", score=3)
    assert usage.backend == "claude_code"
    assert usage.model == MODEL
    assert usage.input_tokens == 10 + 5351 + 4
    assert usage.output_tokens == 153
    assert usage.cost_usd == pytest.approx(0.0115)
    assert usage.latency_s >= 0


async def test_structured_output_missing_falls_back_to_result_text(cli):
    payload = json.loads(result_payload({"name": "A", "score": 1}))
    del payload["structured_output"]
    cli.responses = [json.dumps(payload)]
    obj, _ = await ClaudeCodeBackend().call("planner", "sys", "u", Answer, cfg())
    assert obj.name == "A"


async def test_schema_invalid_retries_with_feedback_then_fails(cli):
    cli.responses = [result_payload({"name": "Bob"})]  # missing score, every time
    with pytest.raises(LLMError) as ei:
        await ClaudeCodeBackend().call("planner", "sys", "USER", Answer, cfg(max_retries=2))
    assert ei.value.error_kind == "schema_invalid"
    assert ei.value.attempts == 3
    assert len(cli.procs) == 3
    assert cli.procs[0].stdin == "USER"
    assert "score" in cli.procs[1].stdin and "did not match" in cli.procs[1].stdin
    assert ei.value.usage is not None and ei.value.usage.output_tokens == 3 * 153


async def test_schema_retry_succeeds_on_second_attempt(cli):
    cli.responses = [result_payload({"name": "Bob"}), result_payload({"name": "Bob", "score": 2})]
    obj, usage = await ClaudeCodeBackend().call("planner", "s", "u", Answer, cfg())
    assert obj.score == 2
    assert len(cli.procs) == 2
    assert usage.output_tokens == 2 * 153


async def test_cli_structured_output_error_subtype_is_schema_invalid(cli):
    bad = json.dumps({"type": "result", "subtype": "error_max_structured_output_retries",
                      "is_error": True, "result": ""})
    cli.responses = [(bad, "", 1)]
    with pytest.raises(LLMError) as ei:
        await ClaudeCodeBackend().call("planner", "s", "u", Answer, cfg(max_retries=1))
    assert ei.value.error_kind == "schema_invalid"
    assert len(cli.procs) == 2


async def test_not_logged_in_is_auth_and_not_retried(cli):
    cli.responses = [(NOT_LOGGED_IN, "", 1)]
    with pytest.raises(LLMError) as ei:
        await ClaudeCodeBackend().call("planner", "s", "u", Answer, cfg(max_retries=2))
    assert ei.value.error_kind == "auth"
    assert len(cli.procs) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"is_error": True, "result": "API Error: 429", "api_error_status": 429},
        {"is_error": True, "result": "Claude usage limit reached. Resets at 5pm"},
        {"is_error": True, "result": "You've hit your limit · resets 3am"},
    ],
)
async def test_rate_limit_classified(cli, payload):
    cli.responses = [(json.dumps({"type": "result", **payload}), "", 1)]
    with pytest.raises(LLMError) as ei:
        await ClaudeCodeBackend().call("planner", "s", "u", Answer, cfg())
    assert ei.value.error_kind == "rate_limit"
    assert len(cli.procs) == 1


async def test_garbage_output_is_process_error(cli):
    cli.responses = [("", "segfault", 139)]
    with pytest.raises(LLMError) as ei:
        await ClaudeCodeBackend().call("planner", "s", "u", Answer, cfg())
    assert ei.value.error_kind == "process_error"


async def test_missing_executable_is_process_error(tmp_path):
    backend = ClaudeCodeBackend(executable=str(tmp_path / "nope"))
    with pytest.raises(LLMError) as ei:
        await backend.call("planner", "s", "u", Answer, cfg())
    assert ei.value.error_kind == "process_error"


def test_classify_failure_table():
    assert classify_failure(1, {"api_error_status": 401}, "") == "auth"
    assert classify_failure(1, {"api_error_status": 404, "result": "bad model"}, "") == (
        "process_error"
    )
    assert classify_failure(1, None, "Invalid API key · Fix external API key") == "auth"
    assert classify_failure(1, {"api_error_status": 429}, "") == "rate_limit"


async def test_argv_isolation_flags_stdin_and_temp_cwd(cli):
    cli.responses = [result_payload({"name": "Bob", "score": 1})]
    await ClaudeCodeBackend().call(
        "expert", "SYSTEM PROMPT", "SECRET USER CONTENT", Answer, cfg(), agent="legal"
    )
    proc = cli.procs[0]
    argv = proc.argv
    assert argv[0] == "claude"
    assert "--print" in argv
    i = argv.index("--tools")
    assert argv[i + 1] == ""
    i = argv.index("--setting-sources")
    assert argv[i + 1] == ""
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--restricted",
                 "--disable-slash-commands"):
        assert flag in argv
    assert "--bare" not in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == MODEL
    assert argv[argv.index("--system-prompt") + 1] == "SYSTEM PROMPT"
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert set(schema["required"]) == {"name", "score"}
    # user content only via stdin
    assert all("SECRET USER CONTENT" not in a for a in argv)
    assert proc.stdin == "SECRET USER CONTENT"
    # fresh temp cwd, existed during the call, removed afterwards
    cwd = Path(proc.kwargs["cwd"])
    assert cli.cwd_existed == [True]
    assert cwd.parent.name.startswith("womm-cc-")
    assert str(cwd.resolve()).startswith(str(Path(tempfile.gettempdir()).resolve()))
    assert not cwd.parent.exists()
    assert proc.kwargs["start_new_session"] is True


async def test_pydantic_defs_schema_passed_as_is(cli):
    cli.responses = [result_payload({"findings": []})]
    obj, _ = await ClaudeCodeBackend().call("expert", "s", "u", FindingBatch, cfg())
    schema = json.loads(cli.procs[0].argv[cli.procs[0].argv.index("--json-schema") + 1])
    assert "$defs" in schema
    assert obj.findings == []


def test_bare_flag_rejected():
    with pytest.raises(ValueError, match="--bare"):
        ClaudeCodeBackend(isolation_flags=("--bare",))


LEAKY_ENV = {
    "PATH": "/usr/bin",
    "HOME": "/Users/x",
    "USER": "x",
    "LANG": "en_US.UTF-8",
    "LC_ALL": "en_US.UTF-8",
    "TMPDIR": "/tmp/x",
    "SHELL": "/bin/zsh",
    "ANTHROPIC_API_KEY": "sk-ant-leak",
    "ANTHROPIC_BASE_URL": "http://evil",
    "LANGSMITH_API_KEY": "ls-leak",
    "LANGSMITH_PROJECT": "womm",
    "LANGSMITH_TRACING": "true",
    "CC_LANGSMITH_API_KEY": "cc-leak",
    "CC_LANGSMITH_PROJECT": "cc",
    "TRACE_TO_LANGSMITH": "true",
    "OPENAI_API_KEY": "sk-leak",
    "CLAUDECODE": "1",
    "CLAUDE_CODE_SESSION_ID": "parent-session",
    "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/sock",
    "AWS_SECRET_ACCESS_KEY": "aws-leak",
}


def test_child_env_is_allowlisted():
    env = build_child_env(LEAKY_ENV)
    for k in ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "SHELL"):
        assert env[k] == LEAKY_ENV[k]
    assert env["TERM"] == "dumb"
    forbidden = [k for k in env if k.startswith(("ANTHROPIC", "LANGSMITH", "CC_LANGSMITH",
                                                 "OPENAI", "TRACE_TO", "CLAUDECODE", "AWS"))]
    assert forbidden == []
    assert "CLAUDE_CODE_SESSION_ID" not in env and "CLAUDE_CODE_MESSAGING_SOCKET" not in env


async def test_subprocess_env_strips_secrets_from_real_parent_env(cli, monkeypatch):
    for k, v in LEAKY_ENV.items():
        monkeypatch.setenv(k, v)
    cli.responses = [result_payload({"name": "Bob", "score": 1})]
    await ClaudeCodeBackend().call("planner", "s", "u", Answer, cfg())
    env = cli.procs[0].kwargs["env"]
    for k in ("ANTHROPIC_API_KEY", "LANGSMITH_API_KEY", "LANGSMITH_TRACING",
              "CC_LANGSMITH_API_KEY", "TRACE_TO_LANGSMITH", "OPENAI_API_KEY", "CLAUDECODE"):
        assert k not in env
    assert env["HOME"] == "/Users/x"


async def test_semaphore_limits_concurrency(cli):
    cli.responses = [(result_payload({"name": "Bob", "score": 1}), "", 0, 0.05)]
    backend = ClaudeCodeBackend(max_concurrency=2)
    results = await asyncio.gather(
        *(backend.call("expert", "s", f"u{i}", Answer, cfg()) for i in range(6))
    )
    assert len(results) == 6
    assert cli.registry["max_active"] == 2


# ---------------------------------------------------------------- real process: timeout/orphans


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def test_timeout_kills_process_group_without_orphans(tmp_path):
    pidfile = tmp_path / "pids"
    script = tmp_path / "fake-claude"
    script.write_text(
        "#!/bin/sh\n"
        f"echo $$ > '{pidfile}'\n"
        "sleep 60 &\n"
        f"echo $! >> '{pidfile}'\n"
        "wait\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    backend = ClaudeCodeBackend(executable=str(script))
    with pytest.raises(LLMError) as ei:
        await backend.call("planner", "s", "u", Answer, cfg(timeout_s=1.0))
    assert ei.value.error_kind == "timeout"
    assert ei.value.attempts == 1
    pids = [int(p) for p in pidfile.read_text().split()]
    assert len(pids) == 2
    for _ in range(50):
        if not any(_pid_alive(p) for p in pids):
            break
        await asyncio.sleep(0.05)
    assert not any(_pid_alive(p) for p in pids), "shell or its child survived the timeout"


# ---------------------------------------------------------------- self-check


def stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events)


CLEAN_INIT = {
    "type": "system",
    "subtype": "init",
    "tools": [],
    "mcp_servers": [],
    "plugins": [{"name": "agents-md", "source": "agents-md@builtin"}],
    "skills": [],
    "apiKeySource": "none",
    "model": MODEL,
}
OK_RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "NONE"}


def self_check_responder(init: dict, extra_events=(), echo_parent_claude_md=False):
    def respond(proc: FakeProc):
        if "stream-json" not in proc.argv:
            return result_payload({"ok": True})
        result = dict(OK_RESULT)
        if echo_parent_claude_md:
            md = Path(proc.kwargs["cwd"]).parent / "CLAUDE.md"
            result["result"] = md.read_text()
        return stream(*extra_events, init, result)

    return respond


async def test_self_check_passes_on_clean_stream(cli):
    cli.responses = [self_check_responder(CLEAN_INIT)]
    backend = ClaudeCodeBackend()
    report = await backend.self_check(MODEL)
    assert report.passed, report.problems
    assert report.flags == list(cc.ISOLATION_FLAGS)
    assert all(report.checks.values())
    stream_proc = next(p for p in cli.procs if "stream-json" in p.argv)
    assert "--verbose" in stream_proc.argv and "--include-hook-events" in stream_proc.argv


@pytest.mark.parametrize(
    ("init_update", "check"),
    [
        ({"memory_paths": {"auto": "/Users/x/.claude/projects/p/memory/"}}, "no_memory"),
        ({"tools": ["Bash", "Read"]}, "no_tools"),
        ({"mcp_servers": [{"name": "github", "status": "connected"}]}, "no_mcp_servers"),
        ({"plugins": [{"name": "ecc", "source": "everything-claude-code@ecc"}]},
         "no_user_plugins"),
        ({"apiKeySource": "ANTHROPIC_API_KEY"}, "subscription_auth"),
    ],
)
async def test_self_check_fails_closed_and_backend_refuses(cli, init_update, check):
    cli.responses = [self_check_responder(CLEAN_INIT | init_update)]
    backend = ClaudeCodeBackend()
    with pytest.raises(IsolationCheckFailed) as ei:
        await backend.self_check(MODEL)
    assert ei.value.report.checks[check] is False
    n = len(cli.procs)
    with pytest.raises(LLMError, match="self-check failed"):
        await backend.call("planner", "s", "u", Answer, cfg())
    assert len(cli.procs) == n, "no subprocess may run after a failed self-check"


async def test_self_check_detects_hook_events(cli):
    hook = {"type": "system", "subtype": "hook_started", "hook_name": "SessionStart:startup"}
    cli.responses = [self_check_responder(CLEAN_INIT, extra_events=[hook])]
    with pytest.raises(IsolationCheckFailed) as ei:
        await ClaudeCodeBackend().self_check(MODEL)
    assert ei.value.report.checks["no_hooks"] is False


async def test_self_check_detects_canary_leak(cli):
    cli.responses = [self_check_responder(CLEAN_INIT, echo_parent_claude_md=True)]
    with pytest.raises(IsolationCheckFailed) as ei:
        await ClaudeCodeBackend().self_check(MODEL)
    assert ei.value.report.checks["canary_absent"] is False


async def test_self_check_auth_preflight_failure_raises_auth(cli):
    cli.responses = [(NOT_LOGGED_IN, "", 1)]
    backend = ClaudeCodeBackend()
    with pytest.raises(LLMError) as ei:
        await backend.self_check(MODEL)
    assert ei.value.error_kind == "auth"
    assert backend.isolation_report is not None and not backend.isolation_report.passed
