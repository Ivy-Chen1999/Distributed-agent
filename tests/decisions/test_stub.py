from womm.config import DEFAULT_SYSTEM_VERSION, REPO_ROOT
from womm.decisions.stub import StubDecisionService
from womm.models.system_version import load_system_version

SV = load_system_version(DEFAULT_SYSTEM_VERSION, REPO_ROOT)


async def test_default_all_relevant_with_stub_decider():
    recs = await StubDecisionService().expert_relevance(SV.spec.experts, "ctx", SV)
    assert [r.subject for r in recs] == ["legal", "fiscal", "stakeholder"]
    assert {r.decision for r in recs} == {"relevant"}
    assert {r.decider for r in recs} == {"stub"}
    assert {r.mode for r in recs} == {"shadow"}
    assert {r.system_version for r in recs} == {SV.version_id}


async def test_override():
    svc = StubDecisionService({"fiscal": ("not_relevant", 0.2)})
    recs = {r.subject: r for r in await svc.expert_relevance(SV.spec.experts, "ctx", SV)}
    assert (recs["fiscal"].decision, recs["fiscal"].probability) == ("not_relevant", 0.2)
