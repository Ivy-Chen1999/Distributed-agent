"""The router's relevance question reads a configured gloss first (U2: the routing table)."""

from womm.decisions.jev import relevance_question
from womm.models.system_version import ExpertConfig, RoleConfig

ROLE = RoleConfig(backend="fake", model="m", prompt="prompts/legal.md")


def test_question_without_gloss_is_unchanged():
    legal = ExpertConfig(id="legal", domain="legal", role=ROLE)
    assert relevance_question(legal) == (
        "Does assessing these regulatory changes need the legal specialist, who analyses "
        "duties, rights, powers, enforcement and institutional effects: who must do what, "
        "which authorities gain powers?"
    )
    other = ExpertConfig(id="workforce", domain="workforce", role=ROLE)
    assert relevance_question(other).endswith("who analyses the workforce domain?")


def test_question_uses_the_router_gloss():
    expert = ExpertConfig(
        id="workforce", domain="workforce", role=ROLE,
        router_gloss="labour market effects: jobs, skills, wages and working conditions",
    )  # fmt: skip
    q = relevance_question(expert)
    assert "workforce specialist" in q and "jobs, skills, wages" in q
