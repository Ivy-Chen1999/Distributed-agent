import json
import shutil

import pytest

from womm.data import fixtures as fixtures_module

from ..graph.conftest import fixture  # noqa: F401


@pytest.fixture
def second_fixture(tmp_path, monkeypatch):
    """A fixture root holding ``ai_act`` plus a copy of it registered as regulation ``other``,
    so tests can score cases against two fixtures without depending on an imported proposal."""
    root = tmp_path / "fixtures"
    shutil.copytree(fixtures_module.DEFAULT_FIXTURE_DIR, root / "ai_act")
    shutil.copytree(fixtures_module.DEFAULT_FIXTURE_DIR, root / "other")
    for name in fixtures_module.VERSION_FILES:
        path = root / "other" / name
        data = json.loads(path.read_text())
        data["regulation_id"] = "other"
        path.write_text(json.dumps(data))
    monkeypatch.setattr(fixtures_module, "FIXTURES_ROOT", root)
    return root
