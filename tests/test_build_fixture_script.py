import importlib.util
import json
import sys

import pytest

from womm.config import REPO_ROOT
from womm.data.fixtures import FixtureError

spec = importlib.util.spec_from_file_location(
    "build_fixture", REPO_ROOT / "scripts/build_fixture.py"
)
build_fixture = importlib.util.module_from_spec(spec)
sys.modules["build_fixture"] = build_fixture  # dataclasses need the module registered
spec.loader.exec_module(build_fixture)


def test_downloads_pinned_then_changed_fails(tmp_path):
    path = tmp_path / "downloads.json"
    build_fixture.check_downloads(path, {"https://x/a": b"one"}, accept=False)
    pinned = json.loads(path.read_text())
    build_fixture.check_downloads(path, {"https://x/a": b"one"}, accept=False)
    assert json.loads(path.read_text()) == pinned
    with pytest.raises(FixtureError, match="upstream documents changed"):
        build_fixture.check_downloads(path, {"https://x/a": b"two"}, accept=False)
    build_fixture.check_downloads(path, {"https://x/a": b"two"}, accept=True)
    assert json.loads(path.read_text()) != pinned


def test_fixture_urls_use_https():
    assert build_fixture.PROPOSAL_URL.startswith("https://")
    from womm.data.cellar import CELLAR_BASE

    assert CELLAR_BASE.startswith("https://")


def test_pipeline_urls_are_https_and_pinned():
    urls = [*build_fixture.PIPELINE_UNITS.values(), build_fixture.PIPELINE_CONTAINERS]
    assert len(build_fixture.PIPELINE_COMMIT) == 40
    for url in urls:
        assert url.startswith("https://raw.githubusercontent.com/")
        assert f"/{build_fixture.PIPELINE_COMMIT}/" in url


def test_upstream_failures_become_fixture_errors(monkeypatch):
    import httpx

    from womm.data.cellar import CellarError

    def http_404(url, **_):
        raise CellarError(f"{url} returned HTTP 404")

    monkeypatch.setattr(build_fixture, "fetch", http_404)
    with pytest.raises(FixtureError, match="download failed for https://x/y.*404"):
        build_fixture.fetch_upstream("https://x/y", refresh=False)

    def offline(url, **_):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(build_fixture, "fetch", offline)
    with pytest.raises(FixtureError, match="download failed for https://x/y.*no route"):
        build_fixture.fetch_upstream("https://x/y", refresh=False)


def test_unknown_article_source_is_rejected():
    with pytest.raises(SystemExit):
        build_fixture.main(["--articles-from", "eurlex"])


def test_fidelity_report_verdicts():
    from womm.data.parse_proposal import Article, Paragraph
    from womm.models.regulation import Provision, RegulationVersion

    version = RegulationVersion(
        version_id="v",
        date="2021-04-21",
        status="proposal",
        source="X",
        provisions=[
            Provision(provision_key=k, article=n, paragraph=None, text=t, source_id=f"v/{n}")
            for k, n, t in [("a", "1", "One.\nTwo."), ("b", "2", "Same"), ("c", "3", "Old")]
        ],
    )
    other = {"v": [Article(n, "", (Paragraph(None, t),)) for n, t in
                   [("1", "One. Two."), ("2", "Same"), ("3", "New")]]}  # fmt: skip
    lines = build_fixture.fidelity_report({"v": version}, other, "cellar")
    assert lines[0].endswith("same words, layout differs")
    assert lines[1].endswith("byte-identical")
    assert "differs at 0" in lines[2]


def _pinned_bodies() -> dict[str, bytes] | None:
    from womm.data.cellar import cached

    urls = {build_fixture.PROPOSAL_URL: None}
    urls.update(dict.fromkeys([*build_fixture.PIPELINE_UNITS.values(),
                               build_fixture.PIPELINE_CONTAINERS], build_fixture.ANY))  # fmt: skip
    bodies = {}
    for url, accept in urls.items():
        body = cached(url, accept=accept) if accept else cached(url)
        if body is None:
            return None
        bodies[url] = body
    return bodies


def test_pipeline_build_reproduces_committed_fixture(tmp_path, monkeypatch):
    """Offline rebuild from the download cache; skipped where the pinned bodies are not cached
    (CI), where tests/data/test_fixtures.py covers the committed fixture instead."""
    import shutil

    from womm.data.fixtures import DEFAULT_FIXTURE_DIR, load_fixture
    from womm.diff import diff_versions

    bodies = _pinned_bodies()
    if bodies is None:
        pytest.skip("pinned upstream bodies are not in .cache/cellar")
    for name in ("crosswalk.yaml", "downloads.json"):
        shutil.copy(DEFAULT_FIXTURE_DIR / name, tmp_path / name)

    def from_cache(url, **_):
        if url not in bodies:
            raise AssertionError(f"unexpected download {url}")
        return bodies[url]

    monkeypatch.setattr(build_fixture, "fetch", from_cache)
    assert build_fixture.main(["--out", str(tmp_path)]) == 0

    built, committed = load_fixture(tmp_path), load_fixture(DEFAULT_FIXTURE_DIR)
    for name in ("proposal.json", "final.json", "sources.json", "scenarios.yaml"):
        assert (tmp_path / name).read_bytes() == (DEFAULT_FIXTURE_DIR / name).read_bytes()
    demo = "demo_penalties_amended"
    kinds = [
        [c.kind for c in diff_versions(*f.scenario_versions(demo)).changes]
        for f in (built, committed)
    ]
    assert kinds[0] == kinds[1] == ["modified", "modified", "modified"]


def test_moving_the_pipeline_pin_needs_acceptance(tmp_path):
    path = tmp_path / "downloads.json"
    repo = build_fixture.PIPELINE_REPO_RAW
    old, new = f"{repo}aaa/units.jsonl", f"{repo}bbb/units.jsonl"
    cellar = "https://publications.europa.eu/resource/x"
    build_fixture.check_downloads(path, {old: b"v1", cellar: b"m"}, accept=False, supersedes=repo)
    with pytest.raises(FixtureError, match="upstream documents changed.*aaa/units.jsonl"):
        build_fixture.check_downloads(
            path, {new: b"v2", cellar: b"m"}, accept=False, supersedes=repo
        )
    build_fixture.check_downloads(path, {new: b"v2", cellar: b"m"}, accept=True, supersedes=repo)
    assert set(json.loads(path.read_text())) == {new, cellar}  # the superseded pin is dropped


def test_cellar_build_keeps_pipeline_pins(tmp_path):
    path = tmp_path / "downloads.json"
    pipeline = f"{build_fixture.PIPELINE_REPO_RAW}aaa/units.jsonl"
    build_fixture.check_downloads(path, {pipeline: b"v1"}, accept=False)
    build_fixture.check_downloads(path, {"https://x/doc": b"m"}, accept=False)  # no supersedes
    assert set(json.loads(path.read_text())) == {pipeline, "https://x/doc"}


FIXTURES = REPO_ROOT / "tests" / "fixtures"
SAMPLE_CROSSWALK = """entries:
  - provision_key: ai_act/high_risk/compliance_with_requirements
    articles: {com2021_206: "8", reg2024_1689: "8"}
  - provision_key: ai_act/high_risk/risk_management
    articles: {com2021_206: "9", reg2024_1689: "9"}
  - provision_key: ai_act/penalties/penalties
    articles: {com2021_206: "71", reg2024_1689: "99"}
"""
SAMPLE_SCENARIOS = [
    {
        "scenario_id": "eval_sample",
        "kind": "evaluation",
        "description": "Sample evaluation scenario.",
        "before_version": None,
        "after_version": "com2021_206",
        "articles": ["8", "9"],
        "ia_reference": None,
    },
    {
        "scenario_id": "demo_sample",
        "kind": "demo",
        "description": "Sample demo scenario.",
        "before_version": "com2021_206",
        "after_version": "reg2024_1689",
        "articles": ["99"],
        "ia_reference": None,
    },
]


@pytest.fixture
def offline_build(tmp_path, monkeypatch):
    """main() against the committed samples: units and containers for the pipeline, the XHTML
    samples for Cellar. No network, no download cache."""
    from womm.data.cellar import celex_url

    bodies = {
        build_fixture.PROPOSAL_URL: (FIXTURES / "com2021_206_sample.xhtml").read_bytes(),
        celex_url("32024R1689"): (FIXTURES / "reg2024_1689_sample.xhtml").read_bytes(),
        build_fixture.PIPELINE_UNITS["com2021_206"]: (
            FIXTURES / "units_proposal_sample.jsonl"
        ).read_bytes(),
        build_fixture.PIPELINE_UNITS["reg2024_1689"]: (
            FIXTURES / "units_final_sample.jsonl"
        ).read_bytes(),
        build_fixture.PIPELINE_CONTAINERS: (FIXTURES / "containers_sample.csv").read_bytes(),
    }
    fetched: list[str] = []

    def fake_fetch(url, **_):
        fetched.append(url)
        return bodies[url]

    cache: dict[str, bytes] = {}
    monkeypatch.setattr(build_fixture, "fetch", fake_fetch)
    monkeypatch.setattr(build_fixture, "cached", lambda url, **_: cache.get(url))
    monkeypatch.setattr(build_fixture, "SCENARIOS", SAMPLE_SCENARIOS)
    (tmp_path / "crosswalk.yaml").write_text(SAMPLE_CROSSWALK, encoding="utf-8")
    return tmp_path, bodies, cache, fetched


def test_offline_pipeline_build(offline_build, capsys):
    from womm.data.fixtures import load_fixture

    out, bodies, _, fetched = offline_build
    assert build_fixture.main(["--out", str(out)]) == 0
    fixture = load_fixture(out)
    assert set(fixture.scenarios) == {"eval_sample", "demo_sample"}
    art9 = fixture.version("com2021_206").by_key()["ai_act/high_risk/risk_management"].text
    assert "[…]" not in art9 and "\n(a) elimination" in art9
    assert any(s.kind == "memorandum" for s in fixture.sources.values())
    pins = json.loads((out / "downloads.json").read_text())
    assert set(pins) == set(fetched)
    assert "the other source is not cached; skipped" in capsys.readouterr().out


def test_offline_pipeline_build_fails_on_crosswalk_disagreement(offline_build):
    out, *_ = offline_build
    text = SAMPLE_CROSSWALK.replace('{com2021_206: "9", reg2024_1689: "9"}',
                                    '{com2021_206: "9", reg2024_1689: "10"}')  # fmt: skip
    (out / "crosswalk.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(FixtureError, match="risk_management.*Art 9 to reg2024_1689 Art 10"):
        build_fixture.main(["--out", str(out)])


def test_offline_cellar_build_with_fidelity_report(offline_build, capsys):
    from womm.data.fixtures import load_fixture

    out, bodies, cache, fetched = offline_build
    for url in build_fixture.PIPELINE_UNITS.values():
        cache[url] = bodies[url]
    assert build_fixture.main(["--out", str(out), "--articles-from", "cellar"]) == 0
    assert not any(url.startswith(build_fixture.PIPELINE_REPO_RAW) for url in fetched)
    penalties = load_fixture(out).version("reg2024_1689").by_key()["ai_act/penalties/penalties"]
    assert "EUR 35 000 000" in penalties.text  # Cellar keeps the digit grouping
    report = capsys.readouterr().out
    assert "fidelity against pipeline:" in report
    assert "com2021_206 Art 8: byte-identical" in report
    assert "reg2024_1689 Art 99: differs at" in report


def test_other_source_articles_from_cache_only(monkeypatch):
    from womm.data.cellar import celex_url

    monkeypatch.setattr(build_fixture, "cached", lambda url, **_: None)
    assert build_fixture.other_source_articles("pipeline", {}) is None
    assert build_fixture.other_source_articles("cellar", {}) is None
    bodies = {
        build_fixture.PROPOSAL_URL: (FIXTURES / "com2021_206_sample.xhtml").read_bytes(),
        celex_url("32024R1689"): (FIXTURES / "reg2024_1689_sample.xhtml").read_bytes(),
    }
    other = build_fixture.other_source_articles("pipeline", bodies)
    assert [a.number for a in other["reg2024_1689"]] == ["7", "16", "99", "103"]


def test_committed_scenarios_match_scenarios_in_the_script():
    from womm.data.fixtures import DEFAULT_FIXTURE_DIR, load_crosswalk, load_fixture

    built = build_fixture.build_scenarios(load_crosswalk(DEFAULT_FIXTURE_DIR / "crosswalk.yaml"))
    assert {s.scenario_id: s for s in built} == load_fixture().scenarios
    explore = [s for s in built if s.mode == "explore"]
    assert {s.after_version for s in explore} == {
        build_fixture.PROPOSAL.version_id, build_fixture.CONSOLIDATED_VERSION_ID,
        build_fixture.FINAL.version_id,  # final_vs_proposal (EU cost plan U7, demo)
    }  # fmt: skip
    (r7,) = [s for s in explore if s.scenario_id == "final_vs_proposal"]
    assert (r7.kind, r7.before_version) == ("demo", build_fixture.PROPOSAL.version_id)
