"""IA fetcher and section extractor (U3). Samples are synthetic: no IA text is committed."""

import json

import httpx
import pytest

from womm.config import REPO_ROOT
from womm.data.fixtures import FIXTURES_ROOT, fixture_dir, load_fixture
from womm.eval import ia_sources
from womm.eval.ia_sources import (
    IaSourceError,
    assert_not_forbidden,
    cache_ia,
    extract_sections,
    ia_cache_dir,
    load_cached_ia,
    ngram_overlap,
    parse_ia,
    select_sections,
)

NS = "http://www.w3.org/1999/xhtml"


def doc(body: str) -> bytes:
    return f'<?xml version="1.0"?><html xmlns="{NS}"><body>{body}</body></html>'.encode()


def h(level: int, text: str, li: bool = True) -> str:
    cls = f"li Heading{level}" if li else f"Heading{level}"
    if not text[0].isdigit():
        return f'<p class="{cls}">{text}</p>'
    num, title = text.split(" ", 1)
    return f'<p class="{cls}"><span class="num">{num}</span>{title}</p>'


def p(text: str) -> str:
    return f'<p class="Normal">{text}</p>'


PART1 = doc(
    '<p class="TOCHeading">Table of Contents</p><p class="li TOC1">1. Introduction</p>'
    + h(1, "1. Introduction")
    + h(2, "1.1. Context")
    + p("Context paragraph about widgets.")
    + h(1, "1. What are the impacts of the policy options?")  # list numbering lost
    + h(2, "6.1. Impact on businesses")
    + p(
        'Widget makers face one-off costs<a class="footnoteRef" href="#f1">12</a>'
        " of EUR 5 per unit."
    )
    + h(3, "6.1.1. Impact on widget repairers", li=False)
    + p("Repairers gain access to widget diagnostics and lower repair prices for owners.")
    + h(2, "6.2. Impact on SMEs")
    + p("Small widget makers benefit from a lighter regime for micro enterprises.")
    + h(1, "7. How do the options compare?")
    + p("Comparison text that is not part of the cut.")
    + h(1, "8. Preferred option")
    + p("The preferred option is option 2 with a widget registry.")
    + '<p class="FootnoteText">12 Footnote text that must not appear.</p>'
)
PART2 = doc(
    h(1, "Annex 1: Procedural information")
    + h(2, "1.1. Regulatory Scrutiny Board")
    + p("The Board asked to clarify who bears the widget registry costs and why.")
    + h(1, "Annex 3: Who is affected and how?")
    + p("Widget makers and repairers are affected as follows.")
    + "<table><tr><td>Cost item</td><td>Widget makers</td></tr>"
    + '<tr><td><p class="Normal">Registry fee</p></td>'
    + '<td><p class="Normal">EUR 100 per year</p></td></tr></table>'
    + h(1, "Annex 4: Analytical methods")
    + p("Methods text.")
)
NO_IMPACTS = doc(h(1, "1. Introduction") + p("x") + h(1, "2. Problem definition") + p("y"))

IA_CELEX = "52099SC0001"
BASE = "https://publications.europa.eu/resource"
LISTING = (
    "<html><body><ul><li title='manifestation'>m<ul>"
    + "".join(
        f'<li title="item"><a href="http://publications.europa.eu/resource/cellar/x.0001.03/DOC_{n}">'
        f'd</a><ul><li title="stream_name">{name}</li><li title="stream_order">{n}</li></ul></li>'
        for n, name in [
            (1, "1_EN_impact_assessment_part1_v2.html"),
            (2, "1_EN_impact_assessment_part2_v2.html"),
            (3, "1_EN_executive_summary_impact_assessment_v1.html"),
        ]
    )
    + "</ul></li></ul></body></html>"
)


def serve(routes: dict[str, httpx.Response]) -> tuple[httpx.Client, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def multipart_routes() -> dict[str, httpx.Response]:
    return {
        f"{BASE}/celex/{IA_CELEX}": httpx.Response(300, text=LISTING),
        f"{BASE}/cellar/x.0001.03/DOC_1": httpx.Response(200, content=PART1),
        f"{BASE}/cellar/x.0001.03/DOC_2": httpx.Response(200, content=PART2),
    }


# ----------------------------------------------------------------------------- parsing


def test_extracts_impacts_preferred_annex_and_procedural():
    extract = extract_sections([parse_ia(PART1, 1), parse_ia(PART2, 2)])
    kinds = [(s.kind, s.title, s.part) for s in extract.sections]
    assert kinds == [
        ("impacts", "6. What are the impacts of the policy options?", 1),
        ("preferred_option", "8. Preferred option", 1),
        ("procedural", "Annex 1: Procedural information", 2),
        ("who_is_affected", "Annex 3: Who is affected and how?", 2),
    ]
    impacts = extract.sections[0].text
    assert "6.1.1. Impact on widget repairers" in impacts and "lighter regime" in impacts
    assert "Comparison text" not in impacts  # stops at the next chapter
    assert "one-off costs of EUR 5" in impacts  # footnote marker removed
    assert "Footnote text" not in extract.full_text()
    annex = extract.sections[3].text
    assert "Registry fee | EUR 100 per year" in annex
    assert "Methods text" not in annex
    assert extract.missing == []


def li(style: str, num: str, title: str) -> str:
    """A Word list paragraph with no heading style: the number sits in a nested span.num."""
    return (
        f'<p class="li {style}"><span><span class="num">{num}</span></span><span>{title}</span></p>'
    )


# Some IAs carry no HeadingN styles at all: chapters are numbered list paragraphs
# ("li ListParagraph" / "li Normal") and annexes are "Annex N:" list paragraphs.
LIST_STYLED = doc(
    '<p class="li TOC1"><span class="num">6.</span>What are the impacts?</p>'
    + li("AnnexHeading2", "1.", "Introduction")
    + p("Intro text.")
    + li("ListParagraph", "6.", "What are the impacts of the Policy Options?")
    + li("ListParagraph", "6.1.", "Economic impacts")
    + li("ListParagraph", "6.1.1.1", "Gadget authorities")
    + p("Gadget authorities pay for the gadget register.")
    + li("Normal", "\u00b7", "a bullet item that is not a heading")
    + li("Normal", "a)", "a lettered item that is not a heading")
    + li("ListParagraph", "7.", "How do the options compare?")
    + p("Comparison text.")
    + li("ListParagraph", "8.", "Preferred Option")
    + p("Option 2 is preferred.")
    + li("Normal", "Annex 1:", "Procedural information")
    + li("ListParagraph", "1.", "Consultation of the Board")
    + p("The Board asked for clearer gadget costs.")
    + li("AnnexTitle", "Annex 3:", "Who is affected and how?")
    + li("ListParagraph", "1.", "Practical implications of the initiative")
    + p("Gadget owners save time.")
    + li("AnnexTitle", "Annex 4:", "Analytical methods")
    + p("Methods text.")
)


def test_numbered_list_paragraphs_are_headings_when_no_heading_styles_exist():
    parsed = parse_ia(LIST_STYLED)
    titles = [(x.level, x.title) for x in parsed.headings]
    assert titles == [
        (1, "1. Introduction"),
        (1, "6. What are the impacts of the Policy Options?"),
        (2, "6.1. Economic impacts"),
        (4, "6.1.1.1 Gadget authorities"),
        (1, "7. How do the options compare?"),
        (1, "8. Preferred Option"),
        (1, "Annex 1: Procedural information"),
        (2, "1. Consultation of the Board"),  # numbering restarts inside an annex
        (1, "Annex 3: Who is affected and how?"),
        (2, "1. Practical implications of the initiative"),
        (1, "Annex 4: Analytical methods"),
    ]
    extract = extract_sections([parsed])
    kinds = [(x.kind, x.title) for x in extract.sections]
    assert kinds == [
        ("impacts", "6. What are the impacts of the Policy Options?"),
        ("preferred_option", "8. Preferred Option"),
        ("procedural", "Annex 1: Procedural information"),
        ("who_is_affected", "Annex 3: Who is affected and how?"),
    ]
    impacts, _, procedural, annex = (x.text for x in extract.sections)
    assert "register" in impacts and "bullet item" in impacts and "Comparison" not in impacts
    assert "clearer gadget costs" in procedural
    assert "save time" in annex and "Methods text" not in annex
    assert [x.title for x in select_sections(extract, ["6.1.1.1"])] == [
        "6.1.1.1 Gadget authorities"
    ]


def test_annex_titles_count_as_chapters_in_an_annexes_only_volume():
    # An annexes-only volume styles its annex titles one level below a volume title.
    annexes = doc(
        h(1, "Annexes: Table of Contents")
        + h(3, "Annex 3: Who is affected and how?", li=False)
        + h(4, "A3.1 Overview of benefits", li=False)
        + p("Gadget owners save time.")
        + h(3, "Annex 4: Impacts of the policy options", li=False)
        + p("Option 2 lowers gadget costs.")
        + h(3, "Annex 5: Analytical methods", li=False)
        + p("Methods text.")
    )
    extract = extract_sections([parse_ia(annexes)])
    kinds = [(x.kind, x.title) for x in extract.sections]
    assert kinds == [
        ("who_is_affected", "Annex 3: Who is affected and how?"),
        ("impacts", "Annex 4: Impacts of the policy options"),
    ]
    assert "save time" in extract.sections[0].text
    assert "Methods text" not in extract.sections[1].text


def test_list_paragraphs_are_not_headings_when_heading_styles_exist():
    styled = doc(h(1, "6. What are the impacts of the policy options?") + li("Normal", "1.", "x"))
    assert [x.title for x in parse_ia(styled).headings] == [
        "6. What are the impacts of the policy options?"
    ]


def test_toc_entries_are_not_headings():
    doc_ = parse_ia(PART1)
    assert [x.title for x in doc_.headings][0] == "1. Introduction"
    assert sum(x.title == "1. Introduction" for x in doc_.headings) == 1


def test_no_impacts_section_lists_headings_seen():
    with pytest.raises(IaSourceError) as err:
        extract_sections([parse_ia(NO_IMPACTS)])
    msg = str(err.value)
    assert "impacts of the policy options" in msg
    assert "# 1. Introduction" in msg and "# 2. Problem definition" in msg


def test_select_sections_by_number_and_title():
    extract = extract_sections([parse_ia(PART1, 1), parse_ia(PART2, 2)])
    picked = select_sections(extract, ["6.1.1.", "Annex 3"])
    assert [s.title for s in picked] == [
        "6.1.1. Impact on widget repairers",
        "Annex 3: Who is affected and how?",
    ]
    assert "diagnostics" in picked[0].text


def test_select_sections_outside_the_cut_fails_listing_available():
    extract = extract_sections([parse_ia(PART1, 1), parse_ia(PART2, 2)])
    with pytest.raises(IaSourceError, match=r"(?s)\['7\.'\].*6\.1\. Impact on businesses"):
        select_sections(extract, ["7."])


# ----------------------------------------------------------------------------- fetching


def test_multipart_ia_resolves_both_parts_and_caches_under_cache_ia(tmp_path):
    client, seen = serve(multipart_routes())
    cached = cache_ia("widgets", IA_CELEX, "SEC(2099) 7", root=tmp_path, client=client)
    directory = tmp_path / "widgets"
    assert cached.directory == directory.resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    assert [x["url"].rsplit("/", 1)[-1] for x in manifest["parts"]] == ["DOC_1", "DOC_2"]
    assert not any("DOC_3" in u for u in seen)  # executive summary skipped
    assert {s["kind"] for s in manifest["sections"]} >= {"impacts", "who_is_affected"}
    assert "Registry fee" in (directory / "ia_full.txt").read_text()
    assert "widget registry" in (directory / "cut.txt").read_text()
    # RSB opinion is not in Cellar (404): the procedural annex stands in, and says so.
    assert manifest["rsb_status"].startswith("opinion none found in Cellar")
    assert "who bears the widget registry costs" in (directory / "rsb.txt").read_text()
    assert any(u.endswith("/comnat/SEC_2099_0007_FIN") for u in seen)
    again = load_cached_ia("widgets", tmp_path)
    assert again.full_text == cached.full_text and again.rsb_text == cached.rsb_text


def test_single_document_ia(tmp_path):
    body = doc(h(1, "1. Preferred option") + p("Only a preferred option chapter here."))
    client, _ = serve({f"{BASE}/celex/{IA_CELEX}": httpx.Response(200, content=body)})
    cached = cache_ia("widgets", IA_CELEX, None, root=tmp_path, client=client)
    assert [s["kind"] for s in cached.sections] == ["preferred_option"]
    assert cached.rsb_status == "no RSB reference in the local IA index"


def test_listing_without_ia_part_fails_naming_celex(tmp_path):
    listing = LISTING.replace("impact_assessment_part", "annex_part")
    listing = listing.replace("executive_summary_impact_assessment", "summary")
    client, _ = serve({f"{BASE}/celex/{IA_CELEX}": httpx.Response(300, text=listing)})
    with pytest.raises(IaSourceError, match=IA_CELEX):
        cache_ia("widgets", IA_CELEX, None, root=tmp_path, client=client)


@pytest.mark.parametrize(
    "root", [FIXTURES_ROOT, REPO_ROOT / "data", REPO_ROOT / "evals", REPO_ROOT / "evals/golden"]
)
def test_cache_never_under_fixtures_or_evals(root):
    with pytest.raises(IaSourceError, match="refusing to write IA material"):
        ia_cache_dir("data_act", root)


def test_default_cache_is_dot_cache_ia():
    path = ia_cache_dir("data_act")
    assert path == (REPO_ROOT / ".cache" / "ia" / "data_act").resolve()
    assert_not_forbidden(path)  # no exception
    assert ".cache/" in (REPO_ROOT / ".gitignore").read_text()


def test_invalid_fixture_name():
    with pytest.raises(IaSourceError):
        ia_cache_dir("../data/fixtures/ai_act")


def test_rsb_url():
    assert ia_sources.rsb_url("SEC(2099) 81").endswith("/comnat/SEC_2099_0081_FIN")
    assert ia_sources.rsb_url("Ares(2022) 1") is None


# ----------------------------------------------------------------------------- overlap


def test_ngram_overlap():
    ia = "the preferred option would cost widget makers about five euros per unit in the first year"
    copied = "intro words here " + ia
    share, longest = ngram_overlap(copied, ia, n=6)
    assert share > 0.5 and longest == len(ia.split())
    assert ngram_overlap("an entirely different sentence about other matters " * 3, ia, 6) == (
        0.0,
        0,
    )


# Thresholds measured 2026-10-04: against the cut (impacts, preferred option, annexes) the AI Act
# and Data Act fixtures share at most 0.4% of 12-grams and one 21-word run (an obligation
# restated in both); against the whole IA, context sections share policy background (Council
# conclusions, other legislation) up to 11% of a memorandum section.
# Re-measured 2026-10-05 over all 23 fixtures: every source passes the cut check. Only the
# whole-IA check fails, in two shapes that are not IA findings: provisions (the IA quotes or
# describes the proposal's own articles, up to 61% for DSA art 72) and the memorandum's context
# and legal-basis sections (the IA restates the same procedural background and Treaty basis, up
# to 29%). Both stay subject to the cut check; the whole-IA check covers the other sections.
CUT_MAX_SHARE, CUT_MAX_RUN, FULL_MAX_SHARE = 0.02, 30, 0.15
SHARED_BACKGROUND = ("context", "legal_basis")
# Provisions whose text the IA impact cut paraphrases closely (the IA describing the article, not
# the article restating an IA finding), with the overlap measured 2026-10-05. Each passes only up
# to its recorded share and run; any growth, or any other provision over the limits, still fails.
PROVISION_ALLOWANCES = {
    "com2026_504/art_38": (0.049, 22),  # chips_act_2
    "com2026_504/art_41": (0.028, 16),  # chips_act_2
    "com2021_346/art_3": (0.042, 24),  # gpsr, definitions
}


def _fixtures_with_cached_ia():
    names = sorted(p.parent.name for p in FIXTURES_ROOT.glob("*/sources.json"))
    return [n for n in names if load_cached_ia(n) is not None]


@pytest.mark.parametrize("name", _fixtures_with_cached_ia() or ["(none cached)"])
def test_fixture_sources_do_not_restate_their_ia(name):
    if name == "(none cached)":
        pytest.skip("no IA cached under .cache/ia/ (run scripts/draft_golden_case.py first)")
    ia = load_cached_ia(name)
    fixture = load_fixture(fixture_dir(name))
    for source in fixture.sources.values():
        share, run = ngram_overlap(source.text, ia.cut_text)
        max_share, max_run = CUT_MAX_SHARE, CUT_MAX_RUN
        if source.kind == "provision" and source.source_id in PROVISION_ALLOWANCES:
            max_share, allowed_run = PROVISION_ALLOWANCES[source.source_id]
            max_run = allowed_run + 1
        assert share <= max_share and run < max_run, (source.source_id, share, run)
        if source.kind == "provision" or source.source_id.rsplit("/", 1)[-1] in SHARED_BACKGROUND:
            continue
        full_share, _ = ngram_overlap(source.text, ia.full_text)
        assert full_share <= FULL_MAX_SHARE, (source.source_id, full_share)


# ----------------------------------------------------------------------------- RSB opinions


def make_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with a text layer (synthetic: no real RSB opinion in the repo)."""
    content = "BT /F1 11 Tf 50 750 Td " + " ".join(f"({x}) Tj 0 -14 Td" for x in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


OPINION_PDF = make_pdf(["REGULATORY SCRUTINY BOARD OPINION", "Overall opinion: POSITIVE",
                        "The report does not show the costs for small widget makers."])  # fmt: skip
WORK = f"{BASE}/cellar/0000aaaa-0000-0000-0000-000000000001"
ANNEX_PHRASE = "who bears the widget registry costs"


def sparql(works: list[str]) -> httpx.Response:
    bindings = [{"work": {"type": "uri", "value": w.replace("https://", "http://")}} for w in works]
    return httpx.Response(200, json={"results": {"bindings": bindings}})


def rsb_client(sparql_response: httpx.Response, routes: dict | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url).startswith(ia_sources.SPARQL_URL):
            return sparql_response
        return (routes or {}).get(str(request.url), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_sec_reference_forms_normalise():
    assert ia_sources.sec_reference("SEC(2099) 7 final") == ("2099", 7)
    assert ia_sources.sec_reference("sec ( 2099 )7") == ("2099", 7)
    assert ia_sources.sec_reference("Ares(2099) 1") is None


def test_opinion_found_as_pi_com_pdf(tmp_path):
    client, seen = rsb_client(sparql([WORK]), {WORK: httpx.Response(200, content=OPINION_PDF)})
    op = ia_sources.fetch_rsb_opinion("SEC(2099) 7 final", tmp_path, client=client)
    assert op.status == "opinion found in Cellar (pi_com PDF)" and op.url == WORK
    assert "costs for small widget makers" in op.text and len(op.sha256) == 64
    query = next(r for r in seen if str(r.url).startswith(ia_sources.SPARQL_URL))
    assert "pi_com:SEC(2099)7" in query.url.params["query"]
    assert next(r for r in seen if str(r.url) == WORK).headers["Accept"] == "application/pdf"


def test_no_work_found_is_reported(tmp_path):
    client, _ = rsb_client(sparql([]))
    op = ia_sources.fetch_rsb_opinion("SEC(2099) 7", tmp_path, client=client)
    assert op.status == "opinion none found in Cellar" and op.text is None


def test_works_off_cellar_are_ignored(tmp_path):
    client, seen = rsb_client(sparql(["https://evil.example/cellar/x"]))
    op = ia_sources.fetch_rsb_opinion("SEC(2099) 7", tmp_path, client=client)
    assert op.status == "opinion none found in Cellar"
    assert not any("evil.example" in str(r.url) for r in seen)


def test_pdf_without_text_or_too_large_is_unreadable(tmp_path):
    blank = make_pdf([])
    client, _ = rsb_client(sparql([WORK]), {WORK: httpx.Response(200, content=blank)})
    op = ia_sources.fetch_rsb_opinion("SEC(2099) 7", tmp_path, client=client)
    assert op.status.startswith("opinion found in Cellar but not readable") and op.text is None
    with pytest.raises(IaSourceError, match="size limit"):
        ia_sources.pdf_text(b"%PDF" + b"0" * ia_sources.MAX_PDF_BYTES)
    with pytest.raises(IaSourceError):
        ia_sources.pdf_text(b"<html>not a pdf</html>")


def test_lookup_failure_is_reported_not_raised(tmp_path):
    client, _ = rsb_client(httpx.Response(503))
    op = ia_sources.fetch_rsb_opinion("SEC(2099) 7", tmp_path, client=client)
    assert op.status.startswith("opinion none found in Cellar (lookup failed")


def test_cache_keeps_opinion_and_annex_apart_and_checks_both(tmp_path):
    routes = multipart_routes() | {WORK: httpx.Response(200, content=OPINION_PDF)}
    client, _ = rsb_client(sparql([WORK]), routes)
    cached = cache_ia("widgets", IA_CELEX, "SEC(2099) 7", root=tmp_path, client=client)
    directory = tmp_path / "widgets"
    assert "costs for small widget makers" in (directory / "rsb_opinion.txt").read_text()
    assert "who bears the widget registry costs" in (directory / "rsb.txt").read_text()
    assert cached.rsb_status == (
        "opinion found in Cellar (pi_com PDF); the IA's procedural-information annex kept"
    )
    # Anchors of source: rsb items are checked against the opinion and the annex together.
    assert "costs for small widget makers" in cached.rsb_text
    assert "who bears the widget registry costs" in cached.rsb_text
    assert "who bears" not in cached.rsb_opinion
    again = load_cached_ia("widgets", tmp_path)
    assert again.rsb_text == cached.rsb_text and again.rsb_opinion == cached.rsb_opinion
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["rsb_opinion"]["url"] == WORK


def test_refresh_rsb_adds_the_opinion_and_keeps_old_annex_anchors(tmp_path):
    client, _ = serve(multipart_routes())  # no opinion at first: the annex stands in
    before = cache_ia("widgets", IA_CELEX, "SEC(2099) 7", root=tmp_path, client=client)
    assert before.rsb_opinion == "" and ANNEX_PHRASE in before.rsb_text
    client, _ = rsb_client(sparql([WORK]), {WORK: httpx.Response(200, content=OPINION_PDF)})
    after = ia_sources.refresh_rsb("widgets", "SEC(2099) 7", root=tmp_path, client=client)
    assert "costs for small widget makers" in after.rsb_opinion
    assert ANNEX_PHRASE in after.rsb_text  # an omission anchored in the annex still verifies
    assert after.full_text == before.full_text  # the IA itself is not refetched
    assert after.rsb_status.startswith("opinion found in Cellar (pi_com PDF)")
    # A later failed lookup never throws away the cached opinion.
    client, _ = rsb_client(httpx.Response(503))
    kept = ia_sources.refresh_rsb("widgets", "SEC(2099) 7", root=tmp_path, client=client)
    assert kept.rsb_opinion == after.rsb_opinion


def test_refresh_rsb_needs_a_cached_ia(tmp_path):
    with pytest.raises(IaSourceError, match="no cached IA"):
        ia_sources.refresh_rsb("widgets", "SEC(2099) 7", root=tmp_path)


def test_a_failed_refresh_never_discards_the_cached_opinion(tmp_path):
    client, _ = serve(multipart_routes())
    cache_ia("widgets", IA_CELEX, None, root=tmp_path, client=client)
    good, _ = rsb_client(sparql([WORK]), {WORK: httpx.Response(200, content=OPINION_PDF)})
    first = ia_sources.refresh_rsb("widgets", "SEC(2099) 7", root=tmp_path, client=good)
    for failing in (rsb_client(httpx.Response(503))[0], rsb_client(sparql([]))[0]):
        again = ia_sources.refresh_rsb("widgets", "SEC(2099) 7", root=tmp_path, refresh=True,
                                       client=failing)  # fmt: skip
        assert again.rsb_opinion == first.rsb_opinion and again.rsb_status == first.rsb_status


def test_a_download_failure_is_reported_as_such(tmp_path):
    client, _ = rsb_client(sparql([WORK]), {WORK: httpx.Response(500)})
    op = ia_sources.fetch_rsb_opinion("SEC(2099) 7", tmp_path, client=client)
    assert op.status.startswith("opinion none found in Cellar (1 work(s); download failed")


def test_an_encrypted_or_broken_pdf_is_unreadable_not_a_crash():
    with pytest.raises(IaSourceError, match="unreadable PDF"):
        ia_sources.pdf_text(b"%PDF-1.4\n1 0 obj << /Broken >>\n%%EOF")


def test_a_legacy_cache_whose_rsb_text_held_an_opinion_is_cleaned(tmp_path):
    client, _ = serve(multipart_routes())
    cache_ia("widgets", IA_CELEX, None, root=tmp_path, client=client)
    directory = tmp_path / "widgets"
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["rsb_status"] = "opinion found in Cellar"  # the old layout: rsb.txt = the opinion
    (directory / "manifest.json").write_text(json.dumps(manifest))
    (directory / "rsb.txt").write_text("An old copy of the opinion.")
    good, _ = rsb_client(sparql([WORK]), {WORK: httpx.Response(200, content=OPINION_PDF)})
    cached = ia_sources.refresh_rsb("widgets", "SEC(2099) 7", root=tmp_path, client=good)
    assert "old copy" not in cached.rsb_text and "costs for small widget makers" in cached.rsb_text
    assert cached.rsb_status == "opinion found in Cellar (pi_com PDF)"
