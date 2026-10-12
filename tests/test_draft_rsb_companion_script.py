"""scripts/draft_rsb_companion.py: a case's RSB companion, drafted next to it from the cached
opinion, the case draft untouched."""

import asyncio
import importlib.util
import sys

import httpx
import pytest

from womm.config import REPO_ROOT
from womm.eval import ia_sources
from womm.eval.drafting import DraftingError, load_draft
from womm.llm.fake import FakeBackend

from .eval.draft_factory import draft_dict, write
from .eval.test_drafting import all_agree, config, impact, omission
from .eval.test_ia_sources import IA_CELEX, OPINION_PDF, WORK, multipart_routes, serve, sparql

_spec = importlib.util.spec_from_file_location(
    "draft_rsb_companion", REPO_ROOT / "scripts/draft_rsb_companion.py"
)
script = importlib.util.module_from_spec(_spec)
sys.modules["draft_rsb_companion"] = script
_spec.loader.exec_module(script)

CASE = "case_90_widget_switching"
QUOTE = "does not show the costs for small widget makers"


def _ia(tmp_path, with_opinion=True):
    """data_act's IA cached under tmp_path, with or without its RSB opinion."""
    root = tmp_path / "ia"
    client, _ = serve(multipart_routes())
    ia_sources.cache_ia("data_act", IA_CELEX, None, root=root, client=client)
    if with_opinion:

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith(ia_sources.SPARQL_URL):
                return sparql([WORK])
            return httpx.Response(200, content=OPINION_PDF) if str(request.url) == WORK else (
                httpx.Response(404))  # fmt: skip

        client = httpx.Client(transport=httpx.MockTransport(handler))
        ia_sources.refresh_rsb("data_act", "SEC(2099) 7", root=root, client=client)
    return root


def _args(tmp_path, root, *extra):
    case = tmp_path / "drafts" / f"{CASE}.yaml"
    write(case, draft_dict("train"))
    argv = [str(case), "--ia-root", str(root), "--ia-index", str(tmp_path / "none.yaml"), *extra]
    return case, script.parse_args(argv)


def _backends():
    return {"fake": FakeBackend({
        "drafter": [{"expected_impacts": [impact(QUOTE)],
                     "important_omissions": [omission(QUOTE, "rsb"), omission("x", "ia")]}],
        "judge_anchor": [all_agree], "judge_derivability": [all_agree],
        "judge_category": [all_agree],
    })}  # fmt: skip


def test_drafts_the_companion_next_to_the_untouched_case(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(script, "load_drafting_config", lambda *_a, **_k: config())
    root = _ia(tmp_path)
    case, args = _args(tmp_path, root)
    before = case.read_bytes()
    [out] = asyncio.run(script.run(args, backends=_backends()))
    assert out == tmp_path / "drafts" / f"{CASE}.rsb.yaml"
    assert case.read_bytes() == before
    comp = load_draft(out)
    assert comp.companion == "rsb" and len(comp.important_omissions) == 1
    assert comp.important_omissions[0].anchor.status == "verified"
    assert "RSB omission(s) drafted, 1 kept" in capsys.readouterr().out
    # A second run of the same version is refused; a new version is drafted beside it.
    with pytest.raises(DraftingError, match="exists"):
        asyncio.run(script.run(args, backends=_backends()))


def test_a_case_without_its_opinion_is_refused_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(script, "load_drafting_config", lambda *_a, **_k: config())
    root = _ia(tmp_path, with_opinion=False)
    _, args = _args(tmp_path, root)
    with pytest.raises(DraftingError, match="no RSB opinion for data_act"):
        asyncio.run(script.run(args, backends=_backends()))
