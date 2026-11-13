"""LangSmith trace links: built from the fixed host and lazily resolved project ids; a missing
link always carries a reason and never fails the request."""

from types import SimpleNamespace

from womm.api.traces import NOT_STARTED, TRACING_OFF, UNRESOLVED, TraceLinks, trace_url

TRACE = "0193a5c4-0000-7000-8000-000000000001"


class Client:
    def __init__(self, fail: int = 0):
        self.calls = 0
        self.fail = fail

    def read_project(self, *, project_name):
        self.calls += 1
        if self.calls <= self.fail:
            raise RuntimeError("project not found")
        return SimpleNamespace(tenant_id="tenant-1", id="project-1", name=project_name)


def _links(client, now=None, enabled=True):
    clock = now or (lambda: 0.0)
    return TraceLinks("womm-prod", client_factory=lambda: client, enabled=lambda: enabled,
                      clock=clock)  # fmt: skip


async def test_a_traced_run_links_to_its_workspace_trace():
    client = Client()
    links = _links(client)
    got = await links.link(TRACE, "succeeded")
    assert got == {"url": trace_url("tenant-1", "project-1", TRACE)}
    assert got["url"].startswith(
        f"https://smith.langchain.com/o/tenant-1/projects/p/project-1/r/{TRACE}"
    )
    await links.link(TRACE, "running")
    assert client.calls == 1  # resolved once, then cached


async def test_untraced_and_queued_runs_say_why_without_calling_langsmith():
    client = Client()
    links = _links(client)
    assert await links.link(None, "succeeded") == {"reason": TRACING_OFF}
    assert await links.link(None, "queued") == {"reason": NOT_STARTED}
    assert client.calls == 0


async def test_tracing_off_now_gives_a_reason_not_an_error():
    assert await _links(Client(), enabled=False).link(TRACE, "failed") == {"reason": UNRESOLVED}


async def test_a_missing_project_is_retried_after_a_minute_not_remembered():
    t = {"now": 0.0}
    client = Client(fail=1)
    links = _links(client, now=lambda: t["now"])
    assert await links.link(TRACE, "succeeded") == {"reason": UNRESOLVED}
    t["now"] = 30.0
    assert await links.link(TRACE, "succeeded") == {"reason": UNRESOLVED}
    assert client.calls == 1  # not retried within the minute
    t["now"] = 61.0
    assert "url" in await links.link(TRACE, "succeeded")
    assert client.calls == 2
