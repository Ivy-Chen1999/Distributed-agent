"""scripts/fetch_rsb_opinions.py: every cached IA gets its RSB opinion or a stated reason."""

import importlib.util
import sys

import httpx

from womm.config import REPO_ROOT
from womm.data.ia_index import IaRecord, update_ia_index
from womm.eval.ia_sources import SPARQL_URL, cache_ia

from .eval.test_ia_sources import IA_CELEX, OPINION_PDF, WORK, multipart_routes, serve, sparql

_spec = importlib.util.spec_from_file_location(
    "fetch_rsb_opinions", REPO_ROOT / "scripts/fetch_rsb_opinions.py"
)
script = importlib.util.module_from_spec(_spec)
sys.modules["fetch_rsb_opinions"] = script
_spec.loader.exec_module(script)


def _setup(tmp_path):
    index = tmp_path / "ia_index.yaml"
    for fixture, ref in (("widgets", "SEC(2099) 7"), ("gadgets", "SEC(2099) 8")):
        update_ia_index(fixture, IaRecord(celex="52099PC0001", ia_celex=IA_CELEX, rsb_ref=ref),
                        index)  # fmt: skip
    ia = tmp_path / "ia"
    client, _ = serve(multipart_routes())  # both IAs cached, no opinion yet
    for fixture in ("widgets", "gadgets"):
        cache_ia(fixture, IA_CELEX, None, root=ia, client=client)
    return index, ia


def _client(found_for: str):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(SPARQL_URL):
            return sparql([WORK] if found_for in request.url.params["query"] else [])
        if url == WORK:
            return httpx.Response(200, content=OPINION_PDF)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_table_names_every_fixture_and_its_status(tmp_path, capsys):
    index, ia = _setup(tmp_path)
    argv = ["--index", str(index), "--ia-root", str(ia)]
    code = script.main(argv, client=_client("SEC(2099)7"))
    out = capsys.readouterr().out
    assert code == 1  # one fixture without an opinion
    assert "widgets" in out and "opinion found in Cellar (pi_com PDF)" in out
    assert "gadgets" in out and "opinion none found in Cellar" in out
    assert "1 of 2 with the RSB opinion; without: gadgets" in out
    assert (ia / "widgets" / "rsb_opinion.txt").read_text().startswith("REGULATORY SCRUTINY")


def test_unknown_or_uncached_fixtures_are_listed_not_skipped(tmp_path, capsys):
    index, ia = _setup(tmp_path)
    update_ia_index("cogs", IaRecord(celex="52099PC0002", ia_celex=IA_CELEX), index)
    argv = ["--index", str(index), "--ia-root", str(ia), "--fixture", "cogs", "--fixture", "nope"]
    assert script.main(argv, client=_client("none")) == 1
    out = capsys.readouterr().out
    assert "IA not cached; fetch it first" in out and "not in the IA index" in out
