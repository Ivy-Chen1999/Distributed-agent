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
