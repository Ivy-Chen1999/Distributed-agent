"""Config edit surface (U2, R26): typed edits, the allow-list and in-memory candidates."""

import pytest

from womm.config import REPO_ROOT
from womm.decisions.stub import StubDecisionService
from womm.evolve.edits import (
    ConfigDiff,
    EditPrompt,
    EditRejected,
    SetRetrieval,
    SetRouterGloss,
    build_candidate,
    materialize,
    render_diff,
    validate_diff,
)
from womm.graph.build import run_scenario
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity
from womm.models.system_version import load_system_version

from ..eval.test_run_eval_fake import _script
from ..graph.conftest import SCENARIO, fake_sv

BASE = load_system_version(REPO_ROOT / "system_versions" / "v1.0-unscoped.yaml", REPO_ROOT)
EXTRA = "\n\nAlso name the cost driver of every compliance cost you report."
WORKFORCE_PROMPT = (
    "You are the workforce specialist. Assess how the regulatory changes affect jobs, skills, "
    "wages and working conditions of the people who build, deploy or are subject to the "
    "regulated systems. Cite the provision text verbatim for every finding you report, and "
    "say who is affected and through which mechanism."
)


def _fiscal_text(sv=BASE) -> str:
    return sv.prompt_text(next(e for e in sv.spec.experts if e.id == "fiscal").role) + EXTRA


def _add_workforce(**kw) -> dict:
    return {"op": "add_expert", "id": "workforce", "domain": "workforce",
            "prompt_text": WORKFORCE_PROMPT,
            "router_gloss": "labour market effects: jobs, skills, wages and working conditions",
            **kw}  # fmt: skip


def test_edit_prompt_gives_a_new_id_that_survives_materialisation(tmp_path):
    diff = validate_diff(BASE, [{"op": "edit_prompt", "role": "expert:fiscal",
                                 "new_text": _fiscal_text()}])  # fmt: skip
    child = build_candidate(BASE, diff)
    assert child.version_id != BASE.version_id
    fiscal = next(e for e in child.spec.experts if e.id == "fiscal")
    assert fiscal.role.prompt.startswith(f"prompts/evolved/{BASE.version_id}/expert-fiscal-")
    assert child.prompt_text(fiscal.role).endswith(EXTRA)
    # Unchanged prompts keep their paths; nothing is written for an in-memory candidate.
    assert child.spec.synthesis.prompt == BASE.spec.synthesis.prompt
    assert not (REPO_ROOT / fiscal.role.prompt).exists()

    path = materialize(child, tmp_path)
    assert path == tmp_path / "system_versions" / "candidates" / f"{child.spec.name}.yaml"
    assert load_system_version(path, tmp_path).version_id == child.version_id
    # Materialising twice is harmless.
    assert materialize(child, tmp_path) == path


def test_the_same_diff_gives_the_same_candidate():
    ops = [{"op": "edit_prompt", "role": "expert:fiscal", "new_text": _fiscal_text()}]
    a = build_candidate(BASE, validate_diff(BASE, ops))
    b = build_candidate(BASE, validate_diff(BASE, ops))
    assert a.version_id == b.version_id


async def test_add_expert_inherits_the_role_and_runs_as_a_fourth_node(fixture):
    parent = fake_sv()
    diff = validate_diff(parent, [_add_workforce()])
    child = build_candidate(parent, diff)
    assert [e.id for e in child.spec.experts] == ["legal", "fiscal", "stakeholder", "workforce"]
    new = child.spec.experts[-1]
    first = parent.spec.experts[0].role
    assert (new.role.backend, new.role.model, new.role.timeout_s, new.role.max_retries) == (
        first.backend, first.model, first.timeout_s, first.max_retries,
    )  # fmt: skip
    assert new.scope is None and new.router_gloss.startswith("labour market")

    script = _script()
    script["expert/workforce"] = [{"findings": []}]
    backend = FakeBackend(script)
    result = await run_scenario(
        SCENARIO, sv=child, fixture=fixture, backends={"fake": backend},
        decisions=StubDecisionService(), code_identity=CodeIdentity(git_sha="t", dirty=False),
    )  # fmt: skip
    experts = {c.agent for c in backend.calls if c.role_name == "expert"}
    assert experts == {"legal", "fiscal", "stakeholder", "workforce"}
    assert result.status.value in ("succeeded", "degraded")
    call = next(c for c in backend.calls if c.agent == "workforce")
    assert call.system_prompt == WORKFORCE_PROMPT


@pytest.mark.parametrize(
    ("op", "match"),
    [
        ({"op": "edit_prompt", "role": "judge", "new_text": "x" * 300}, "edit_prompt"),
        ({"op": "set_model", "role": "planner", "model": "other"}, "set_model"),
        ({"op": "set_backend", "role": "expert:legal", "backend": "api"}, "set_backend"),
        ({"op": "set_router_mode", "mode": "active"}, "set_router_mode"),
        ({"op": "set_scope", "expert_id": "legal", "scope": None}, "set_scope"),
        ({"op": "remove_expert", "id": "legal"}, "remove_expert"),
        ({"op": "edit_judge", "new_text": "x" * 300}, "edit_judge"),
    ],
)
def test_forbidden_edits_are_rejected_with_the_op_named(op, match):
    with pytest.raises(EditRejected, match=match) as err:
        validate_diff(BASE, [op])
    assert err.value.op == op["op"]


@pytest.mark.parametrize(
    ("ops", "match"),
    [
        ([_add_workforce(), _add_workforce(id="workforce2")], "one add_expert"),
        ([_add_workforce(id="legal")], "already exists"),
        ([_add_workforce(id="Workforce!")], "id"),
        ([_add_workforce(id="w")], "id"),
        ([], "no change"),
        ([{"op": "edit_prompt", "role": "expert:fiscal", "new_text": "too short"}], "200"),
        ([{"op": "edit_prompt", "role": "expert:nobody", "new_text": "x" * 300}], "unknown"),
        ([{"op": "set_retrieval", "max_provisions": 40, "max_prompt_chars": 60000}], "4"),
        ([{"op": "set_retrieval", "max_provisions": 8, "max_prompt_chars": 60000}], "no change"),
        ([{"op": "set_router_gloss", "expert_id": "nobody", "text": "a gloss"}], "unknown"),
        (
            [{"op": "edit_prompt", "role": "expert:fiscal", "new_text": "x" * 300, "x": 1}],
            "edit_prompt",
        ),
    ],
)
def test_invalid_diffs_are_rejected(ops, match):
    with pytest.raises(EditRejected, match=match):
        validate_diff(BASE, ops)


def test_overlong_prompt_and_unchanged_text_are_rejected():
    fiscal = next(e for e in BASE.spec.experts if e.id == "fiscal").role
    text = BASE.prompt_text(fiscal)
    with pytest.raises(EditRejected, match="3x"):
        validate_diff(BASE, [{"op": "edit_prompt", "role": "expert:fiscal",
                              "new_text": text * 4}])  # fmt: skip
    with pytest.raises(EditRejected, match="no change"):
        validate_diff(BASE, [{"op": "edit_prompt", "role": "expert:fiscal", "new_text": text}])


def test_router_gloss_and_retrieval_edits():
    diff = validate_diff(BASE, [
        {"op": "set_router_gloss", "expert_id": "legal", "text": "duties and enforcement"},
        {"op": "set_retrieval", "max_provisions": 12, "max_prompt_chars": 50000},
    ])  # fmt: skip
    child = build_candidate(BASE, diff)
    assert child.spec.experts[0].router_gloss == "duties and enforcement"
    assert (child.spec.retrieval.max_provisions, child.spec.retrieval.max_prompt_chars) == (
        12,
        50000,
    )
    # Explore and preset Planner prompts are both editable.
    text = BASE.prompt_file(BASE.spec.planner.explore_prompt) + EXTRA
    child = build_candidate(
        BASE,
        validate_diff(
            BASE, [{"op": "edit_prompt", "role": "planner:explore", "new_text": text}]
        ),  # fmt: skip
    )
    assert child.spec.planner.explore_prompt.startswith("prompts/evolved/")
    assert child.spec.planner.prompt == BASE.spec.planner.prompt


def test_render_diff_has_a_summary_and_unified_prompt_diffs():
    diff = validate_diff(BASE, [
        _add_workforce(),
        {"op": "edit_prompt", "role": "expert:fiscal", "new_text": _fiscal_text()},
    ])  # fmt: skip
    child = build_candidate(BASE, diff)
    rendered = render_diff(BASE, child)
    assert rendered["summary"]["experts_added"] == ["workforce"]
    assert rendered["summary"]["prompts_changed"] == ["expert:fiscal"]
    assert "+Also name the cost driver" in rendered["prompts"]["expert:fiscal"]
    assert rendered["prompts"]["expert:workforce"].startswith("--- /dev/null")


@pytest.mark.parametrize(
    ("op", "reason"),
    [
        (SetRetrieval(max_provisions=100, max_prompt_chars=20_000), "max_provisions"),
        (SetRetrieval(max_provisions=8, max_prompt_chars=10), "max_prompt_chars"),
        (EditPrompt(role="judge", new_text="x" * 400), "judge"),
        (SetRouterGloss(expert_id="legal", text="x"), "gloss"),
    ],
)
def test_build_candidate_refuses_a_hand_built_out_of_bounds_diff(op, reason):
    """A ConfigDiff built directly, skipping validate_diff, is validated again on build."""
    diff = ConfigDiff(parent_id=BASE.version_id, ops=(op,))
    with pytest.raises(EditRejected, match=reason):
        build_candidate(BASE, diff)
