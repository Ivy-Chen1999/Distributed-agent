"""The corpus's obligation dates checked against the law: Article 113 of the AI Act.

The date rules in ``scripts/build_corpus.py`` (``DATE_MOVED_ARTICLES``, the amended set) were
written by hand. These tests read the application dates off the corpus's own Article 113 texts
(2024 and consolidated 2026), so a later consolidation that moves a date makes them fail:

- every date the corpus still shows equals the consolidated Article 113 date for that article
  (and the explicit table below, which states what we expect);
- every article whose date the consolidated Article 113 changed from 2024 has its dates withheld
  (``DATE_MOVED_ARTICLES``) or is amended (no 2024 record is shown for it).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import sys
from dataclasses import dataclass

import pytest

from womm.config import REPO_ROOT
from womm.models.regulation import Regulation

if "build_corpus" in sys.modules:
    build_corpus = sys.modules["build_corpus"]
else:
    spec = importlib.util.spec_from_file_location(
        "build_corpus", REPO_ROOT / "scripts/build_corpus.py"
    )
    build_corpus = importlib.util.module_from_spec(spec)
    sys.modules["build_corpus"] = build_corpus  # dataclasses need the module registered
    spec.loader.exec_module(build_corpus)

CORPUS = build_corpus.DEFAULT_CORPUS_DIR
FINAL, CONSOLIDATED = build_corpus.FINAL.version_id, build_corpus.CONSOLIDATED.version_id

# The Act's chapters and sections (Regulation (EU) 2024/1689): article range -> division, in
# the colleague's ``division`` notation. Inserted articles (4a, 60a, 75a) follow their base.
STRUCTURE = [
    (1, 4, "chI"),
    (5, 5, "chII"),
    (6, 7, "chIII.sec1"),
    (8, 15, "chIII.sec2"),
    (16, 27, "chIII.sec3"),
    (28, 39, "chIII.sec4"),
    (40, 49, "chIII.sec5"),
    (50, 50, "chIV"),
    (51, 52, "chV.sec1"),
    (53, 54, "chV.sec2"),
    (55, 55, "chV.sec3"),
    (56, 56, "chV.sec4"),
    (57, 63, "chVI"),
    (64, 69, "chVII.sec1"),
    (70, 70, "chVII.sec2"),
    (71, 71, "chVIII"),
    (72, 72, "chIX.sec1"),
    (73, 73, "chIX.sec2"),
    (74, 84, "chIX.sec3"),
    (85, 87, "chIX.sec4"),
    (88, 94, "chIX.sec5"),
    (95, 96, "chX"),
    (97, 98, "chXI"),
    (99, 101, "chXII"),
    (102, 113, "chXIII"),
]

AUG_2025, AUG_2026 = dt.date(2025, 8, 2), dt.date(2026, 8, 2)

# Every article whose records still show a date, with the date the consolidated Article 113
# gives it. Article 113(3)(b): Chapter III Section 4, Chapter V, Chapter VII, Chapter XII and
# Article 78 from 2 August 2025, except Article 101; second subparagraph: the rest from
# 2 August 2026.
EXPECTED_SHOWN_DATES = {
    **dict.fromkeys(("31", "32", "33", "34", "35", "36", "37", "38", "39"), AUG_2025),  # III.4
    **dict.fromkeys(("41", "44", "45", "46", "47", "48", "49"), AUG_2026),  # III.5
    **dict.fromkeys(("51", "52", "53", "54", "55"), AUG_2025),  # V
    **dict.fromkeys(("59", "61", "62"), AUG_2026),  # VI
    **dict.fromkeys(("65", "66", "67", "68"), AUG_2025),  # VII
    "71": AUG_2026,  # VIII
    **dict.fromkeys(("73", "74", "79", "80", "81", "82", "83", "84"), AUG_2026),  # IX
    **dict.fromkeys(("85", "86", "87", "88", "89", "90", "91", "92", "93", "94"), AUG_2026),
    "78": AUG_2025,  # IX, named in point (b)
    "98": AUG_2026,  # XI
    "100": AUG_2025,  # XII
    "101": AUG_2026,  # XII, excepted from point (b)
    "112": AUG_2026,  # XIII
}

_MONTHS = [
    *("January", "February", "March", "April", "May", "June"),
    *("July", "August", "September", "October", "November", "December"),
]
_DATE = re.compile(rf"(\d{{1,2}}) ({'|'.join(_MONTHS)}) (\d{{4}})")
_MAIN = re.compile(rf"^It shall apply from ({_DATE.pattern})\.$")
_POINT = re.compile(r"^\(([a-h])\) (.*)$")
_SUBPOINT = re.compile(r"^\((i|ii|iii|iv|v)\) (.*)$")
_EXCEPTION = re.compile(
    r",? with the exception of (?P<what>.*?)"
    rf"(?:,?\s+which shall apply from (?P<date>{_DATE.pattern}))?"
    r"(?=,? shall apply from|[;.]?$)"
)
_TARGET = re.compile(
    r"Articles (?P<first>\d+) to (?P<last>\d+)"
    r"|Article (?P<art>\d+[a-z]*)(?P<pars>(?:\(\w+\))?(?: and \(\w+\))?)"
    r"|Chapter (?P<sch>[IVXL]+),? Sections? (?P<secs>\d+(?:,? (?:and )?\d+)*)"
    r"|Chapters (?P<ch1>[IVXL]+) and (?P<ch2>[IVXL]+)"
    r"|Chapter (?P<ch>[IVXL]+)"
)
# Words a scope may hold besides its targets; anything else is phrasing this parser does not
# understand, and fails the test rather than being misread.
_FILLER = re.compile(
    r"[,;:]|\band\b|the corresponding obligations in this Regulation|first subparagraph"
    r"|points? \(\w+\)(?: and \(\w+\))?"
)


def _parse_date(text: str) -> dt.date:
    day, month, year = _DATE.fullmatch(text).groups()
    return dt.date(int(year), _MONTHS.index(month) + 1, int(day))


@dataclass(frozen=True)
class Target:
    """A division prefix ("chIII.sec4", "chV") or an article, optionally some paragraphs."""

    division: str | None = None
    article: str | None = None
    paragraphs: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Rule:
    targets: tuple[Target, ...]
    dates: frozenset[dt.date]
    exceptions: tuple[tuple[Target, ...], ...]
    exception_dates: frozenset[dt.date]  # empty: the exceptions keep the main date


@dataclass(frozen=True)
class Article113:
    main: dt.date
    rules: tuple[Rule, ...]


def _targets(scope: str) -> tuple[Target, ...]:
    targets: list[Target] = []
    for m in _TARGET.finditer(scope):
        if m["first"]:
            targets += [Target(article=str(n)) for n in range(int(m["first"]), int(m["last"]) + 1)]
        elif m["art"]:
            pars = frozenset(re.findall(r"\((\w+)\)", m["pars"]))
            targets.append(Target(article=m["art"], paragraphs=pars))
        elif m["sch"]:
            secs = re.findall(r"\d+", m["secs"])
            targets += [Target(division=f"ch{m['sch']}.sec{s}") for s in secs]
        elif m["ch1"]:
            targets += [Target(division=f"ch{m['ch1']}"), Target(division=f"ch{m['ch2']}")]
        else:
            targets.append(Target(division=f"ch{m['ch']}"))
    residue = _FILLER.sub("", _TARGET.sub("", scope)).strip()
    assert not residue, f"Article 113: unexpected words {residue!r} in {scope!r}"
    assert targets, f"Article 113: no target in {scope!r}"
    return tuple(targets)


def parse_article_113(text: str) -> Article113:
    """The application rules of an Article 113 text as the corpus renders it."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    mains = [_parse_date(m.group(1)) for ln in lines if (m := _MAIN.match(ln))]
    assert len(mains) == 1, f"Article 113: expected one main date, got {mains}"
    points: list[list[str]] = []
    for ln in lines:
        if _SUBPOINT.match(ln) and points and points[-1][0].endswith(":"):
            points[-1].append(ln)
        elif m := _POINT.match(ln):
            points.append([m.group(2)])
    assert points, "Article 113: no points"
    rules = []
    for first, *subpoints in points:
        exceptions: tuple[tuple[Target, ...], ...] = ()
        exception_dates: frozenset[dt.date] = frozenset()
        if m := _EXCEPTION.search(first):
            exceptions = (_targets(m["what"]),)
            if m["date"]:
                exception_dates = frozenset({_parse_date(m["date"])})
            first = first[: m.start()] + first[m.end() :]
        scope, sep, rest = first.partition(" shall apply from")
        assert sep, f"Article 113: no 'shall apply from' in {first!r}"
        dates = [_parse_date(d.group(0)) for d in _DATE.finditer(" ".join([rest, *subpoints]))]
        assert dates, f"Article 113: no date in point {first!r}"
        rules.append(Rule(_targets(scope), frozenset(dates), exceptions, exception_dates))
    return Article113(mains[0], tuple(rules))


def division_of(article: str) -> str:
    n = int(re.match(r"\d+", article).group(0))
    return next(div for lo, hi, div in STRUCTURE if lo <= n <= hi)


def _matches(t: Target, article: str, paragraph: str | None) -> bool:
    if t.division is not None:
        div = division_of(article)
        return div == t.division or div.startswith(f"{t.division}.")
    if t.article != article:
        return False
    return not t.paragraphs or paragraph in t.paragraphs


def dates_for(rules: Article113, article: str, paragraph: str | None = None) -> frozenset:
    """Application dates of an article, or of one of its paragraphs (None: the paragraphs no
    rule names). Later points override earlier ones, as none overlap in the Act."""
    out = frozenset({rules.main})
    for rule in rules.rules:
        if not any(_matches(t, article, paragraph) for t in rule.targets):
            continue
        if any(_matches(t, article, paragraph) for ts in rule.exceptions for t in ts):
            if rule.exception_dates:
                out = rule.exception_dates
            continue
        out = rule.dates
    return out


def named_paragraphs(*rules: Article113) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in rules:
        for rule in r.rules:
            for t in (*rule.targets, *(t for ts in rule.exceptions for t in ts)):
                if t.article and t.paragraphs:
                    out.setdefault(t.article, set()).update(t.paragraphs)
    return out


def profile(rules: Article113, article: str, paragraphs: set[str]) -> dict:
    return {p: dates_for(rules, article, p) for p in [None, *sorted(paragraphs)]}


# --- fixtures -----------------------------------------------------------------------------------


def _version(name: str):
    reg = Regulation.model_validate(json.loads((CORPUS / name).read_text(encoding="utf-8")))
    (version,) = reg.versions
    return version


@pytest.fixture(scope="module")
def versions():
    return {FINAL: _version("final.json"), CONSOLIDATED: _version("consolidated.json")}


@pytest.fixture(scope="module")
def article_113(versions):
    return {
        vid: parse_article_113(v.by_key()["ai_act/art/113"].text) for vid, v in versions.items()
    }


@pytest.fixture(scope="module")
def records():
    data = json.loads((CORPUS / "obligations.json").read_text(encoding="utf-8"))
    return [r for rs in data[FINAL].values() for r in rs]


@pytest.fixture(scope="module")
def amended():
    index = json.loads((CORPUS / "index.json").read_text(encoding="utf-8"))
    return {
        r["number"]
        for r in index["rows"]
        if r["version"] == CONSOLIDATED and r["kind"] == "article" and r["delta"] != "unchanged"
    }


def _paragraph(record) -> str | None:
    m = re.search(r":art\d+[a-z]*\.par(\d+[a-z]*)", record["unit_id"])
    return m.group(1) if m else None


# --- the parser ---------------------------------------------------------------------------------


def test_consolidated_article_113_parses_into_its_points(article_113):
    rules = article_113[CONSOLIDATED]
    assert rules.main == AUG_2026
    assert dates_for(rules, "3") == {dt.date(2025, 2, 2)}  # (a), Chapter I
    assert dates_for(rules, "5", "1a") == {dt.date(2026, 12, 2)}  # (a), exception with a date
    assert dates_for(rules, "31") == {AUG_2025}  # (b)
    assert dates_for(rules, "101") == {AUG_2026}  # (b), exception without a date
    assert dates_for(rules, "9") == {dt.date(2027, 12, 2), dt.date(2028, 8, 2)}  # (c)(i)-(ii)
    assert dates_for(rules, "6", "5") == {AUG_2026}  # (c), excepted
    assert dates_for(rules, "105") == {dt.date(2026, 7, 27)}  # (d)
    assert dates_for(rules, "111") == {AUG_2026}


def test_adopted_article_113_parses_into_its_points(article_113):
    rules = article_113[FINAL]
    assert rules.main == AUG_2026
    assert dates_for(rules, "5") == {dt.date(2025, 2, 2)}
    assert dates_for(rules, "6", "1") == {dt.date(2027, 8, 2)}
    assert dates_for(rules, "9") == {AUG_2026}
    assert dates_for(rules, "105") == {AUG_2026}


def test_unknown_phrasing_fails_rather_than_being_misread():
    text = "It shall apply from 2 August 2026.\nHowever:\n(a) Title II shall apply from 2 May 2025;"
    with pytest.raises(AssertionError, match="unexpected words 'Title II'"):
        parse_article_113(text)


def test_structure_agrees_with_the_record_divisions(records):
    for r in records:
        if r["article"] and r["division"].startswith("ch"):
            assert division_of(r["article"]) == r["division"], r["obligation_id"]


# --- the corpus against the law -----------------------------------------------------------------


def test_expected_table_follows_the_consolidated_article_113(article_113):
    rules = article_113[CONSOLIDATED]
    for article, date in EXPECTED_SHOWN_DATES.items():
        assert dates_for(rules, article) == {date}, article


def test_every_shown_date_is_the_consolidated_article_113_date(records, article_113):
    rules = article_113[CONSOLIDATED]
    shown: dict[str, set[dt.date]] = {}
    for r in records:
        if not r["applies_from"]:
            continue
        date = dt.date.fromisoformat(r["applies_from"])
        assert dates_for(rules, r["article"], _paragraph(r)) == {date}, r["obligation_id"]
        shown.setdefault(r["article"], set()).add(date)
    assert shown == {a: {d} for a, d in EXPECTED_SHOWN_DATES.items()}


def test_date_rules_cover_every_article_article_113_dates_differently(
    versions, article_113, amended
):
    old, new = article_113[FINAL], article_113[CONSOLIDATED]
    paragraphs = named_paragraphs(old, new)
    articles = {p.article for v in versions.values() for p in v.provisions}
    articles = {a for a in articles if not a.startswith("Annex ")}
    changed = {
        a
        for a in articles
        if profile(old, a, paragraphs.get(a, set())) != profile(new, a, paragraphs.get(a, set()))
    }
    assert changed  # 2026/1744 did move dates
    uncovered = changed - build_corpus.DATE_MOVED_ARTICLES - amended
    assert not uncovered, f"dates moved by the consolidated Article 113: {sorted(uncovered)}"
    # A paragraph kept out of a moved article must really keep its date.
    for article, kept in build_corpus.DATE_KEPT_PARAGRAPHS.items():
        for p in kept:
            assert dates_for(old, article, p) == dates_for(new, article, p), (article, p)
