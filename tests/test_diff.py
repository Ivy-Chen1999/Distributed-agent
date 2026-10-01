import datetime as dt

import pytest

from womm.diff import diff_versions
from womm.models.regulation import Provision, RegulationVersion


def _v(vid: str, *provs: tuple[str, str, str]) -> RegulationVersion:
    return RegulationVersion(
        version_id=vid,
        date=dt.date(2024, 1, 1),
        status="proposal",
        source="x",
        provisions=[
            Provision(provision_key=k, article=a, paragraph=None, text=t, source_id=f"s/{a}")
            for k, a, t in provs
        ],
    )


def test_no_prior_version_all_added():
    d = diff_versions(None, _v("p", ("k1", "9", "a"), ("k2", "10", "b")))
    assert [c.kind for c in d.changes] == ["added", "added"]
    assert d.before_version is None


def test_renumbered_same_text_is_no_change():
    d = diff_versions(_v("p", ("pen", "71", "fines apply")), _v("f", ("pen", "99", "fines apply")))
    assert d.is_empty


def test_modified_carries_both_texts():
    d = diff_versions(_v("p", ("pen", "71", "old")), _v("f", ("pen", "99", "new")))
    (c,) = d.changes
    assert c.kind == "modified"
    assert (c.before.text, c.after.text) == ("old", "new")


def test_removed_key():
    d = diff_versions(_v("p", ("gone", "5", "x")), _v("f"))
    assert [c.kind for c in d.changes] == ["removed"]


def test_identical_versions_empty():
    v = _v("p", ("k", "1", "same"))
    assert diff_versions(v, v).is_empty


def test_whitespace_only_difference_is_not_modified():
    d = diff_versions(_v("p", ("k", "1", "a  b\nc")), _v("f", ("k", "1", "a b c")))
    assert d.is_empty


def test_key_subset_and_unknown_key():
    after = _v("p", ("k1", "1", "a"), ("k2", "2", "b"))
    assert diff_versions(None, after, keys=["k2"]).keys() == ["k2"]
    with pytest.raises(KeyError, match="nope"):
        diff_versions(None, after, keys=["nope"])
