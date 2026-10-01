import pytest

from womm.config import DEFAULT_SYSTEM_VERSION, REPO_ROOT
from womm.data.fixtures import load_fixture
from womm.decisions.stub import StubDecisionService
from womm.graph.build import run_scenario
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity
from womm.models.system_version import build_system_version, load_system_version

SCENARIO = "eval_sme_impacts"
Q55 = (
    "provide small-scale providers and start-ups with priority access to the AI regulatory "
    "sandboxes"
)
Q71 = "Member States shall lay down the rules on penalties, including administrative fines"
K55 = "ai_act/innovation/sme_measures"
K71 = "ai_act/penalties/penalties"


def fake_sv(experts: list[str] | None = None, router_mode: str = "shadow"):
    base = load_system_version(DEFAULT_SYSTEM_VERSION, REPO_ROOT).spec
    data = base.model_dump()
    for role in ("planner", "synthesis", "judge"):
        data[role]["backend"] = "fake"
    data["experts"] = [
        {**e, "role": {**e["role"], "backend": "fake"}}
        for e in data["experts"]
        if experts is None or e["id"] in experts
    ]
    data["router"]["mode"] = router_mode
    return build_system_version(type(base).model_validate(data), REPO_ROOT)


def finding(key: str, source: str, quote: str, actor: str = "SMEs", impact: str = "impact"):
    return {
        "provision_key": key,
        "affected_actor": actor,
        "impact": impact,
        "mechanism": "mechanism",
        "evidence": [{"source_id": source, "quote": quote}],
        "confidence": 0.8,
    }


PLAN = {
    "focus_areas": [
        {"provision_keys": [K55], "question": "How are SMEs affected?", "rationale": "r"}
    ]
}


def good_script(synthesis=None) -> dict:
    return {
        "planner": [PLAN],
        "expert/legal": [{"findings": [finding(K71, "com2021_206/art_71", Q71, "providers")]}],
        "expert/fiscal": [{"findings": [finding(K55, "com2021_206/art_55", Q55)]}],
        "expert/stakeholder": [
            {"findings": [finding(K55, "com2021_206/art_55", "invented text that is not there")]}
        ],
        "synthesis": [synthesis] if synthesis is not None else [],
    }


@pytest.fixture(scope="session")
def fixture():
    return load_fixture()


@pytest.fixture
def run(fixture):
    async def _run(script, *, sv=None, decisions=None, scenario=SCENARIO, fixture_obj=None):
        backend = FakeBackend(script)
        result = await run_scenario(
            scenario,
            sv=sv or fake_sv(),
            fixture=fixture_obj or fixture,
            backends={"fake": backend},
            decisions=decisions or StubDecisionService(),
            code_identity=CodeIdentity(git_sha="test", dirty=False),
            run_id="run_test",
        )
        return result, backend

    return _run
