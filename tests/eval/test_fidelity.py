"""womm.eval.fidelity: the AI answers the page's own question, blind to the golden decision."""

import asyncio
import json

import pytest

from womm.eval import fidelity
from womm.eval import reference_check as rc
from womm.llm.fake import FakeBackend

from .test_reference_check import _disputed_companion, page_data, tiebroken
from .test_tiebreak import config


def _sample(tmp_path):
    case = tiebroken()
    comp = _disputed_companion(case)
    line = {f"{case.case_id}/{comp.important_omissions[0].omission_id}": "The resellers' margin."}
    out = tmp_path / "s"
    rc.write_sample(out, [case], n=3, seed=1, annotators=["ana", "bo"], ia_root=tmp_path,
                    extra=0, with_companions=[comp], board_concerns=line)  # fmt: skip
    return out


def test_the_model_sees_the_card_as_the_person_does_and_never_the_golden_decision(tmp_path):
    out = _sample(tmp_path)
    key = json.loads((out / rc.KEY_FILE).read_text())
    golden = {i: it["tool_kept"] for i, it in key["items"].items()}
    flip = sorted(golden)[0]
    answers = [{"item_id": i, "verdict": ("drop" if golden[i] else "keep") if i == flip
                else ("keep" if golden[i] else "drop"), "reason": "r"} for i in golden]  # fmt: skip
    fake = FakeBackend({fidelity.ROLE: [{"verdicts": answers}]})
    res = asyncio.run(fidelity.rejudge(out, config(), {"fake": fake}))
    assert res == {"items": len(golden), "differs_from_golden": [flip]}
    sent = fake.calls[0].user_content
    for leak in ("tool_kept", "tool_reason", "tie-break", "judges:", "stratum"):
        assert leak not in sent
    board = next(c for n in ("ana", "bo") for c in page_data(out / f"label_{n}.html")["items"]
                 if c["source"] != "report")  # fmt: skip
    cards = json.loads(sent.split("## Cards\n\n", 1)[1])
    seen = next(c for c in cards if c["item_id"] == board["id"])
    assert seen["point"] == {"effect_or_risk": "The resellers' margin."}
    assert seen["context_only"] == {"ai_reasoning": board["claim"]["description"]}
    new = json.loads((out / rc.KEY_FILE).read_text())
    assert new["comparison"] == "fidelity"
    for i, it in new["items"].items():
        assert it["golden_kept"] == golden[i]
        assert it["tool_kept"] == (golden[i] != (i == flip))
        assert it["tool_reason"].startswith("page question: r | answer key: ")
    with pytest.raises(fidelity.FidelityError, match="already re-judged"):
        asyncio.run(fidelity.rejudge(out, config(), {"fake": fake}))


def test_a_missing_verdict_writes_nothing(tmp_path):
    out = _sample(tmp_path)
    before = (out / rc.KEY_FILE).read_text()
    fake = FakeBackend({fidelity.ROLE: [{"verdicts": []}]})
    with pytest.raises(fidelity.FidelityError, match="no verdict for"):
        asyncio.run(fidelity.rejudge(out, config(), {"fake": fake}))
    assert (out / rc.KEY_FILE).read_text() == before
