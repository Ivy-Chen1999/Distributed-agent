"""scripts/holdout_review.py: the holdout spot check in a local web page."""

import http.client
import importlib.util
import json
import sys
import threading

import pytest
import yaml

from womm.config import REPO_ROOT
from womm.eval import drafting as drafting_module

from . import test_verify_golden_case_script as tvg
from .eval.draft_factory import write

CASE, DROPPED, verify = tvg.CASE, tvg.DROPPED, tvg.verify
_reference_round = tvg._reference_round

_spec = importlib.util.spec_from_file_location(
    "holdout_review", REPO_ROOT / "scripts/holdout_review.py"
)
hr = importlib.util.module_from_spec(_spec)
sys.modules["holdout_review"] = hr
_spec.loader.exec_module(hr)


@pytest.fixture(autouse=True)
def cache_root(tmp_path, monkeypatch):
    monkeypatch.setattr(drafting_module, "CACHE_DIR", tmp_path / "cache")


@pytest.fixture
def settled(tmp_path):
    """A tie-broken holdout draft (the verify_golden_case tests' own)."""
    draft = tmp_path / "cache" / "drafts" / f"{CASE}.yaml"
    write(draft, tvg._tiebroken_holdout())
    return draft


LEAKS = {"decision", "review", "tool_kept", "tool_reason", "stratum", "judge", "tiebreak"}


def _review(tmp_path, draft, *, wrong=0, full=None, name="round"):
    return hr.Review({draft: verify.load_holdout_draft(draft)}, reviewer="jdoe",
                     reference_dir=_reference_round(tmp_path, wrong=wrong, name=name), full=full,
                     out_dir=tmp_path / "cache" / "handoff", state_dir=tmp_path / "cache" / "hr",
                     ia_root=tmp_path / "ia")  # fmt: skip


def _agree_all(review):
    for c in review.cards:
        review.answer(c["id"], "drop" if c["id"] in DROPPED else "keep", "")


def test_the_page_asks_the_seeded_sample_blind(tmp_path, settled):
    review = _review(tmp_path, settled)
    draft = verify.load_holdout_draft(settled)
    assert [c["id"] for c in review.cards] == [i.item_id for i in verify.spot_sample(draft)]
    assert len(review.cards) == verify.SPOT_SAMPLE
    for c in review.cards:
        assert not LEAKS & c.keys() and c["case"] == CASE
        assert c["articles"] == []  # a holdout scenario is not in the public fixture


def test_answers_are_checked_and_kept_across_restarts(tmp_path, settled):
    review = _review(tmp_path, settled)
    first = review.cards[0]["id"]
    with pytest.raises(ValueError, match="label must be one of"):
        review.answer(first, "maybe", "")
    with pytest.raises(LookupError):
        review.answer("nope", "keep", "")
    review.answer(first, "keep", " fine ")
    again = _review(tmp_path, settled, name="round2")
    assert again.me()["answers"] == {first: {"label": "keep", "note": "fine"}}
    with pytest.raises(ValueError, match="still open"):
        again.submit()


def test_agreeing_on_the_sample_hands_the_draft_off(tmp_path, settled):
    review = _review(tmp_path, settled)
    _agree_all(review)
    result = review.submit()
    assert result["done"] == 1 and result["refused"] == [] and review.submitted
    assert f"{CASE}: done" in result["message"]
    handoff = yaml.safe_load((tmp_path / "cache" / "handoff" / f"{CASE}.yaml").read_text())
    assert handoff["verified_by"] == "jdoe" and handoff["spot_check"]["disagreements"] == []


def test_a_disagreement_refuses_the_draft_and_points_at_the_full_check(tmp_path, settled):
    review = _review(tmp_path, settled)
    _agree_all(review)
    kept = next(c["id"] for c in review.cards if c["id"] not in DROPPED)
    review.answer(kept, "drop", "not in the IA")
    result = review.submit()
    assert result["refused"] == [CASE] and not review.submitted
    assert "--full" in result["message"] and "disagreed with the tool" in result["message"]
    assert not (tmp_path / "cache" / "handoff").exists()


def test_full_mode_asks_every_item_of_one_draft(tmp_path, settled):
    review = _review(tmp_path, settled, full=CASE)
    draft = verify.load_holdout_draft(settled)
    assert [c["id"] for c in review.cards] == [i.item_id for i in draft.items()]
    _agree_all(review)
    result = review.submit()
    assert result["done"] == 1, result["message"]
    with pytest.raises(verify.VerifyError, match="no holdout draft"):
        _review(tmp_path, settled, full="case_99_none", name="round2")


def test_a_reference_check_that_is_not_robust_is_refused(tmp_path, settled):
    with pytest.raises(verify.VerifyError, match="not ROBUST"):
        _review(tmp_path, settled, wrong=5)


def test_the_server_is_local_only_and_serves_the_page_and_api(tmp_path, settled):
    review = _review(tmp_path, settled)
    server = hr.serve(review)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:

        def call(method, path, body=None, host=f"127.0.0.1:{port}"):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request(method, path, body=json.dumps(body) if body else None,
                      headers={"Host": host, "Content-Type": "application/json"})  # fmt: skip
            r = c.getresponse()
            return r.status, r.read().decode()

        status, html = call("GET", "/")
        assert status == 200 and '"api": "/api"' in html and "__DATA__" not in html
        assert '"title": "Holdout check"' in html and '<html lang="en">' in html  # translatable
        assert call("GET", "/", host="evil.example:80")[0] == 403
        status, me = call("GET", "/api/me")
        assert status == 200 and json.loads(me)["annotator"] == "jdoe"
        first = review.cards[0]["id"]
        assert call("PUT", f"/api/answers/{first}", {"label": "keep", "note": ""})[0] == 200
        assert call("PUT", f"/api/answers/{first}", {"label": "x"})[0] == 422
        assert call("POST", "/api/submit")[0] == 409
        _agree_all(review)
        status, body = call("POST", "/api/submit")
        assert status == 200 and json.loads(body)["done"] == 1
    finally:
        server.shutdown()
        server.server_close()
