import shutil
import subprocess
from pathlib import Path

import pytest

from womm.config import REPO_ROOT
from womm.data.ia_index import (
    IA_INDEX_PATH,
    IaIndexError,
    IaRecord,
    load_ia_index,
    update_ia_index,
)

SAMPLE = Path(__file__).parent / "fixtures" / "ia_index_sample.yaml"


def test_sample_index_loads():
    index = load_ia_index(SAMPLE)
    assert sorted(index) == ["sample_act", "sample_directive"]
    act = index["sample_act"]
    assert (act.celex, act.ia_celex, str(act.ia_date), act.rsb_ref) == (
        "51999PC0901",
        "51999SC0902",
        "1999-03-04",
        "SEC(1999) 903",
    )
    assert index["sample_directive"].ia_celex == "51999SC0912(01)"


def test_missing_index_is_empty(tmp_path):
    assert load_ia_index(tmp_path / "absent.yaml") == {}


def test_update_replaces_one_entry_and_keeps_the_rest(tmp_path):
    path = tmp_path / "ia_index.yaml"
    shutil.copy(SAMPLE, path)
    update_ia_index("sample_act", IaRecord(celex="51999PC0901", rsb_ref="SEC(1999) 999"), path)
    update_ia_index("new_act", IaRecord(celex="51999PC0921"), path)
    index = load_ia_index(path)
    assert list(index) == ["new_act", "sample_act", "sample_directive"]
    assert index["sample_act"].ia_celex is None and index["sample_act"].rsb_ref == "SEC(1999) 999"
    assert path.read_text().startswith("# LOCAL ONLY")


@pytest.mark.parametrize(
    "body",
    ["- a list\n", "x:\n  celex: 51999PC0901\n  ia_celex: SWD(1999) 2\n", "x: {celex: C, y: 1}\n"],
)
def test_malformed_index_fails(tmp_path, body):
    path = tmp_path / "ia_index.yaml"
    path.write_text(body)
    with pytest.raises(IaIndexError):
        load_ia_index(path)


def test_private_index_is_gitignored():
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    rel = IA_INDEX_PATH.relative_to(REPO_ROOT)
    ignored = subprocess.run(["git", "check-ignore", "-q", str(rel)], cwd=REPO_ROOT, check=False)
    assert ignored.returncode == 0, f"{rel} must be gitignored"
    tracked = subprocess.run(
        ["git", "ls-files", "evals/private"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert tracked.stdout == "", "nothing under evals/private may be tracked"
