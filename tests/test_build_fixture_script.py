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
