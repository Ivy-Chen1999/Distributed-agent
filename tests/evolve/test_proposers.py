"""The Improvement Planner's proposers (U5): request shape, structured answers, provenance and
billing, on scripted backends."""

import json

import pytest

from womm.evolve.proposers import (
    Budget,
    ExpertProposal,
    PromptEdit,
    Proposer,
    load_evolution_config,
    pattern_key,
)
from womm.llm.base import LLMBackend, LLMError
from womm.llm.fake import FakeBackend
from womm.models.run import CallUsage

from .test_gepa_adapter import config

PATTERN = {"kind": "missed_impact", "category": "fiscal", "owner": "none", "proposals": ["a", "b"]}
EDIT = {"role": "expert:fiscal", "new_text": "new prompt text", "rationale": "r"}
EXPERT = {
    "id": "workforce",
    "domain": "workforce",
    "prompt_text": "p",
    "router_gloss": "employment",
    "rationale": "r",
    "target_pattern": "missed_impact/fiscal/none",
}


class Billed(LLMBackend):
    """A backend whose every attempt costs ``cost`` and returns the next scripted step."""

    name = "billed"

    def __init__(self, steps, cost=0.5):
        self.steps, self.cost, self.calls = list(steps), cost, []

    async def _invoke(self, role_name, system_prompt, user_content, schema, role_cfg, agent):
        self.calls.append((role_name, agent, system_prompt, user_content))
        usage = CallUsage(role=role_name, agent=agent, backend=self.name, model=role_cfg.model,
                          cost_usd=self.cost)  # fmt: skip
        step = self.steps.pop(0)
        if isinstance(step, LLMError):
            step.usage = usage
            raise step
        return step, usage


def test_config_snapshots_and_hashes_the_proposer_prompts():
    cfg = load_evolution_config()
    for role in (cfg.roles.reflect, cfg.roles.propose_expert):
        assert cfg.prompts[role.prompt].strip() and len(cfg.prompt_hashes[role.prompt]) == 16
    assert cfg.budget.selection_k == 1.0
    assert Budget().selection_k == 1.0


async def test_propose_prompt_asks_the_reflect_role_for_one_typed_edit():
    backend = FakeBackend({"improvement_planner": [EDIT]})
    p = Proposer(backend, config())
    records = [{"Inputs": {"case_id": "c1"}, "Feedback": {"score": 0.2}}]
    edit = await p.propose_prompt("expert:fiscal", "current text", records)
    assert isinstance(edit, PromptEdit) and edit.new_text == "new prompt text"
    call = backend.calls[0]
    assert call.schema is PromptEdit and call.agent == "expert:fiscal"
    assert call.system_prompt == p.config.prompts[p.config.roles.reflect.prompt]
    assert "Component: expert:fiscal" in call.user_content
    assert "current text" in call.user_content
    assert json.dumps(records, indent=1) in call.user_content
    assert len(p.usage) == 1


async def test_propose_expert_asks_the_expert_role_with_the_pattern_key():
    backend = FakeBackend({"improvement_planner/topology": [EXPERT]})
    p = Proposer(backend, config())
    out = await p.propose_expert(PATTERN, [{"impact": "x"}], [{"id": "fiscal"}])
    assert isinstance(out, ExpertProposal) and out.id == "workforce"
    call = backend.calls[0]
    assert call.schema is ExpertProposal and call.agent == "topology"
    assert f"Target pattern: {pattern_key(PATTERN)}" in call.user_content
    assert call.system_prompt == p.config.prompts[p.config.roles.propose_expert.prompt]


def test_provenance_names_the_role_and_its_prompt_hash():
    p = Proposer(FakeBackend({}), config())
    prompt, expert = p.provenance("prompt"), p.provenance("expert")
    assert prompt["prompt"] == "prompts/evolution/reflect_prompt.md"
    assert expert["prompt"] == "prompts/evolution/propose_expert.md"
    assert prompt["prompt_hash"] == p.config.prompt_hashes[prompt["prompt"]]


async def test_successful_calls_are_billed():
    p = Proposer(Billed([EDIT], cost=0.25), config())
    await p.propose_prompt("expert:fiscal", "t", [])
    assert p.spent_usd == pytest.approx(0.25)


@pytest.mark.parametrize("method", ["prompt", "expert"])
async def test_failed_calls_are_billed_and_re_raised(method):
    p = Proposer(Billed([LLMError("timeout", "slow")], cost=0.4), config())
    with pytest.raises(LLMError):
        if method == "prompt":
            await p.propose_prompt("expert:fiscal", "t", [])
        else:
            await p.propose_expert(PATTERN, [], [])
    assert p.spent_usd == pytest.approx(0.4)


async def test_schema_retries_that_all_fail_are_billed():
    # Three invalid answers (max_retries 2 on the role): every attempt costs.
    p = Proposer(Billed([{"role": "x"}] * 3, cost=0.1), config())
    assert p.config.roles.reflect.max_retries == 2
    with pytest.raises(LLMError, match="validation"):
        await p.propose_prompt("expert:fiscal", "t", [])
    assert p.spent_usd == pytest.approx(0.3)


async def test_each_role_calls_its_own_backend():
    reflect = FakeBackend({"improvement_planner": [EDIT]})
    expert = FakeBackend({"improvement_planner/topology": [EXPERT]})
    p = Proposer(reflect, config(), expert_backend=expert)
    await p.propose_prompt("expert:fiscal", "t", [])
    await p.propose_expert(PATTERN, [], [])
    assert [c.agent for c in reflect.calls] == ["expert:fiscal"]
    assert [c.agent for c in expert.calls] == ["topology"]
    assert len(p.usage) == 2
