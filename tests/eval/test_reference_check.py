"""The one-time reference-answer check: population, blind sheets and the error-rate gate."""

from __future__ import annotations

import importlib.util
import json
import re
import sys

import pytest

from womm.config import REPO_ROOT
from womm.eval import hosted_labels, review_page
from womm.eval import reference_check as rc
from womm.eval.drafting import GoldenDraft, load_draft, write_draft

from .draft_factory import decide, draft_dict, seal, to_draft
from .test_tiebreak import run, verdict

SCRIPT = REPO_ROOT / "scripts/reference_check.py"
spec = importlib.util.spec_from_file_location("reference_check_script", SCRIPT)
script = importlib.util.module_from_spec(spec)
sys.modules["reference_check_script"] = script
spec.loader.exec_module(script)


def tiebroken(case_id: str = "case_90_widget_switching") -> GoldenDraft:
    data = draft_dict(case_id=case_id)
    data["expected_impacts"][3]["flags"] = ["anchor_not_found"]
    data["expected_impacts"][3]["review"]["decision"] = "pending"
    draft, _ = run(
        to_draft(seal(data)),
        [verdict(f"{data['expected_impacts'][4]['expected_id']}"),
         verdict(data["possibly_missing"][0]["candidate_id"], "drop", reason="repeats e01")],
    )  # fmt: skip
    return draft


def strata(draft: GoldenDraft) -> dict[str, str | None]:
    return {i.item_id: rc.stratum(i) for i in draft.items()}


def test_population_holds_only_tool_judgements():
    draft = tiebroken()
    s = strata(draft)
    assert s["c90_e05"] == "tiebreak_kept"
    assert s["c90_m01"] == "tiebreak_dropped"  # ruled drop
    assert s["c90_m02"] == "tiebreak_dropped"  # no ruling: unsure, dropped by the LLM
    assert s["c90_e04"] is None  # dropped by the deterministic anchor check
    assert s["c90_e01"] == "judges_accepted"
    human = GoldenDraft.model_validate(decide(draft.model_dump(mode="json"), "c90_e01", "verified"))
    assert strata(human)["c90_e01"] is None
    kept = {i.item_id: i.tool_kept for i in rc.population([draft])}
    assert kept["c90_e05"] and kept["c90_e01"] and not kept["c90_m01"]


def test_holdout_drafts_are_refused():
    with pytest.raises(rc.ReferenceCheckError, match="holdout"):
        rc.population([to_draft(draft_dict("holdout"))])


def test_the_sample_spreads_over_strata_and_fills_in_when_one_runs_short():
    pop = rc.population([tiebroken("case_90_a"), tiebroken("case_91_b")])
    by = {s: sum(i.stratum == s for i in pop) for s in rc.STRATA}
    assert by["tiebreak_kept"] == 2  # one per case
    sample = rc.stratified_sample(pop, 9, seed=3)
    got = {s: sum(i.stratum == s for i in sample) for s in rc.STRATA}
    assert got["tiebreak_kept"] == 2 and sum(got.values()) == 9
    assert got["tiebreak_dropped"] >= 3 and got["judges_accepted"] >= 3
    assert {i.case_id for i in sample if i.stratum == "tiebreak_kept"} == {"case_90_a", "case_91_b"}
    assert sample == rc.stratified_sample(pop, 9, seed=3)
    assert len(rc.stratified_sample(pop, 500, seed=3)) == len(pop)


LEAKS = ("tool_kept", "llm_kept", "llm_dropped", "auto_accepted", "repeats e01", "tiebreak",
         "judge", "why_missing")  # fmt: skip


def page_data(path) -> dict:
    return json.loads(re.search(r"const DATA = (.*);\n", path.read_text()).group(1))


def answer_file(path, key: dict, name: str, labels: dict, notes: dict | None = None) -> None:
    """An answer file in the format the page's JavaScript writes (JSON-quoted notes)."""
    rows = [f"sample: {json.dumps(key['sample'])}", f"annotator: {json.dumps(name)}", "answers:"]
    for iid, label in sorted(labels.items()):
        rows += [f"- item: {iid}", f"  label: {label or ''}",
                 f"  note: {json.dumps((notes or {}).get(iid, ''))}"]  # fmt: skip
    path.write_text("\n".join(rows) + "\n")


def test_the_pages_are_blind_and_each_annotator_gets_their_own_items(tmp_path):
    out = tmp_path / "check"
    drafts = [tiebroken("case_90_a"), tiebroken("case_91_b")]
    res = rc.write_sample(out, drafts, n=3, seed=1, annotators=["ana", "bo"], ia_root=tmp_path,
                          send_to="Yuxin on Slack")  # fmt: skip
    assert res["items"] == 6 and res["per_annotator"] == {"ana": 3, "bo": 3}
    assert res["missing_ia"] == ["data_act"]
    page = (out / "label_ana.html").read_text()
    assert not re.search(r"__[A-Z_]+__", page) and "Yuxin on Slack" in page
    key = json.loads((out / rc.KEY_FILE).read_text())
    pages = {a: page_data(out / f"label_{a}.html") for a in ("ana", "bo")}
    for name, data in pages.items():
        assert data["annotator"] == name and data["sample"] == key["sample"]
        assert {i["id"] for i in data["items"]} == {
            iid for iid, it in key["items"].items() if it["annotator"] == name
        }
        for leak in LEAKS:
            assert leak not in json.dumps(data)
    ana, bo = ({i["id"] for i in pages[a]["items"]} for a in ("ana", "bo"))
    assert not ana & bo and ana | bo == set(key["items"])
    for name in ("ana", "bo"):  # about a third of each stratum per person
        mine = [it["stratum"] for it in key["items"].values() if it["annotator"] == name]
        assert len(set(mine)) == 3
    with pytest.raises(rc.ReferenceCheckError, match="exists"):
        rc.write_sample(out, drafts, n=3, seed=1, annotators=["ana"])
    with pytest.raises(rc.ReferenceCheckError, match="distinct"):
        rc.write_sample(tmp_path / "x", drafts, n=3, seed=1, annotators=["ana", "ana"])


def _key(rows: list[tuple[str, bool]], annotators=("ana", "bo")) -> dict:
    items = {f"r{n}": {"case_id": "c", "fixture": "f", "item_id": f"i{n}", "stratum": s,
                       "tool_kept": k, "tool_reason": "",
                       "annotator": annotators[n % len(annotators)]}
             for n, (s, k) in enumerate(rows, 1)}  # fmt: skip
    return {"sample": "2026-10-10-test", "annotators": list(annotators), "items": items}


def test_score_counts_wrong_and_not_sure_items_as_errors():
    key = _key([("judges_accepted", True)] * 10 + [("tiebreak_dropped", False)] * 10)
    truth = {f"r{n}": "keep" if n <= 10 else "drop" for n in range(1, 21)}
    res = rc.score(key, truth | {"r11": "keep"})
    assert res["wrong"] == ["r11"] and res["error"] == pytest.approx(0.05) and res["robust"]
    assert res["error_by_stratum"]["tiebreak_dropped"] == pytest.approx(0.1)
    res = rc.score(key, truth | {"r11": "keep", "r1": "unsure", "r2": "drop"})
    assert res["unsure"] == ["r1"] and res["error"] == pytest.approx(0.15) and not res["robust"]


def test_a_blank_blocks_the_verdict():
    key = _key([("judges_accepted", True)] * 4)
    res = rc.score(key, {"r1": "keep", "r2": "keep", "r3": "keep", "r4": None})
    assert res["blank"] == ["r4"] and res["error"] == 0 and not res["robust"]
    assert "INCOMPLETE" in rc.format_result(res, key)


def test_the_upper_bound_shrinks_with_the_sample():
    assert rc.upper_bound(0, 20) == pytest.approx(0.119, abs=0.002)
    assert rc.upper_bound(0, 30) < 0.1 < rc.upper_bound(0, 20)
    assert rc.upper_bound(0, 60) < 0.05


def test_the_result_shows_the_tools_reason_and_the_persons_note():
    key = _key([("judges_accepted", True)] * 9 + [("tiebreak_kept", True)])
    key["items"]["r10"]["tool_reason"] = "tie-break: the IA states it"
    labels = {f"r{n}": "keep" for n in range(1, 10)} | {"r10": "drop", "r1": "unsure"}
    out = rc.format_result(rc.score(key, labels), key, {"r10": "another option", "r1": "vague"})
    assert "wrong: r10 = c i10 (tiebreak_kept, kept by the tool)" in out
    assert "tool: tie-break: the IA states it" in out and "person: another option" in out
    assert "not sure: r1 = c i1" in out and "person: vague" in out
    assert "NOT ROBUST" in out


def test_answer_files_are_checked_against_the_sample(tmp_path):
    key = _key([("tiebreak_kept", True)] * 4)  # r1, r3 -> bo; r2, r4 -> ana
    answer_file(tmp_path / "answers_ana.yaml", key, "ana", {"r2": "keep", "r4": "drop"})
    with pytest.raises(rc.ReferenceCheckError, match="no answers yet from bo"):
        rc.read_answers(tmp_path, key)
    answer_file(tmp_path / "answers_bo.yaml", key, "bo", {"r1": "keep"}, {"r1": "fine"})
    labels, notes = rc.read_answers(tmp_path, key)
    assert labels == {"r1": "keep", "r2": "keep", "r3": None, "r4": "drop"}  # missing = blank
    assert notes == {"r1": "fine"}
    answer_file(tmp_path / "answers_bo.yaml", key, "bo", {"r2": "keep"})
    with pytest.raises(rc.ReferenceCheckError, match="r2 is not on bo's page"):
        rc.read_answers(tmp_path, key)
    answer_file(tmp_path / "answers_bo.yaml", key, "bo", {"r1": "maybe"})
    with pytest.raises(rc.ReferenceCheckError, match="use one of"):
        rc.read_answers(tmp_path, key)
    (tmp_path / "answers_bo.yaml").unlink()
    answer_file(tmp_path / "answers_test.yaml", key, "test", {"r1": "keep"})
    with pytest.raises(rc.ReferenceCheckError, match="test is not an annotator"):
        rc.read_answers(tmp_path, key)
    answer_file(tmp_path / "answers_test.yaml", key | {"sample": "other"}, "bo", {"r1": "keep"})
    with pytest.raises(rc.ReferenceCheckError, match="belongs to sample other"):
        rc.read_answers(tmp_path, key)


def test_quoted_names_survive_and_malformed_files_are_refused(tmp_path):
    key = _key([("tiebreak_kept", True)] * 2, annotators=("yes", "007"))
    answer_file(tmp_path / "answers_yes.yaml", key, "yes", {"r2": "keep"})
    answer_file(tmp_path / "answers_007.yaml", key, "007", {"r1": "keep"})
    labels, _ = rc.read_answers(tmp_path, key)
    assert labels == {"r1": "keep", "r2": "keep"}  # not True and 7
    (tmp_path / "answers_007.yaml").write_text("annotator: '007'\nanswers: []\n")
    with pytest.raises(rc.ReferenceCheckError, match="belongs to sample None"):
        rc.read_answers(tmp_path, key)
    (tmp_path / "answers_007.yaml").write_text("- just a list\n")
    with pytest.raises(rc.ReferenceCheckError, match="not an answer file"):
        rc.read_answers(tmp_path, key)


def test_the_script_samples_and_scores(tmp_path, capsys):
    drafts_dir = tmp_path / "drafts"
    paths = [write_draft(tiebroken(c), drafts_dir) for c in ("case_90_a", "case_91_b")]
    out = tmp_path / "check"
    argv = ["sample", *map(str, paths), "--annotator", "ana", "--annotator", "bo", "--out",
            str(out), "--n", "3", "--ia-root", str(tmp_path), "--holdout-registry",
            str(tmp_path / "x.yaml")]  # fmt: skip
    assert script.main(argv) == 0
    capsys.readouterr()
    key = json.loads((out / rc.KEY_FILE).read_text())
    truth = {i: "keep" if it["tool_kept"] else "drop" for i, it in key["items"].items()}
    for name in ("ana", "bo"):
        mine = {i: truth[i] for i, it in key["items"].items() if it["annotator"] == name}
        answer_file(out / f"answers_{name}.yaml", key, name, mine)
    score = ["score", "--dir", str(out)]
    assert script.main(score) == 0
    assert "tool error 0.0%" in capsys.readouterr().out
    first = min(i for i, it in key["items"].items() if it["annotator"] == "ana")
    flip = {"keep": "drop", "drop": "keep"}
    mine = {i: truth[i] for i, it in key["items"].items() if it["annotator"] == "ana"}
    answer_file(out / "answers_ana.yaml", key, "ana", mine | {first: flip[truth[first]]},
                {first: "the quote is about another option"})  # fmt: skip
    assert script.main(score) == 1
    printed = capsys.readouterr().out
    assert f"wrong: {first}" in printed and "    tool: " in printed
    # One error in the first round: not decided yet, the reserve goes out.
    assert "person: the quote is about another option" in printed and "NOT YET" in printed
    review = (out / "review.html").read_text()
    assert first in review and "__DATA__" not in review and "another option" in review
    # Send the reserve, answer it correctly: one error in all items is within the limit.
    assert script.main(["extend", "--dir", str(out)]) == 0
    capsys.readouterr()
    key = json.loads((out / rc.KEY_FILE).read_text())
    assert key["extended"] and not key["reserve"] and len(key["items"]) > 6
    truth = {i: "keep" if it["tool_kept"] else "drop" for i, it in key["items"].items()}
    for name in ("ana", "bo"):
        mine = {i: truth[i] for i, it in key["items"].items() if it["annotator"] == name}
        if name == "ana":
            mine[first] = flip[truth[first]]
        answer_file(out / f"answers_{name}.yaml", key, name, mine)
    errors_allowed = len(key["items"]) * rc.MAX_ERROR >= 1
    assert script.main(score) == (0 if errors_allowed else 1)
    assert ("ROBUST" if errors_allowed else "NOT ROBUST") in capsys.readouterr().out
    assert script.main(["extend", "--dir", str(out)]) == 2  # only once
    (out / "answers_bo.yaml").unlink()
    assert script.main(score) == 2


def test_a_clean_first_round_stops_early_and_the_reserve_stays_hidden(tmp_path):
    out = tmp_path / "check"
    rc.write_sample(out, [tiebroken("case_90_a"), tiebroken("case_91_b")], n=3, seed=1,
                    annotators=["ana", "bo"], ia_root=tmp_path, extra=2)  # fmt: skip
    key = json.loads((out / rc.KEY_FILE).read_text())
    pages = json.loads((out / "pages.json").read_text())
    assert len(key["reserve"]) == 4 and not set(key["reserve"]) & set(key["items"])
    shown = {c["id"] for cards in pages["annotators"].values() for c in cards}
    assert shown == set(key["items"])
    for name in ("ana", "bo"):  # the reserve is not on any page
        page = (out / f"label_{name}.html").read_text()
        assert not any(f'"id": "{r}"' in page for r in key["reserve"])
    labels = {i: "keep" if it["tool_kept"] else "drop" for i, it in key["items"].items()}
    result = rc.score(key, labels)
    assert result["verdict"] == "robust" and result["first_look"]
    one_wrong = labels | {min(labels): "unsure"}
    assert rc.score(key, one_wrong)["verdict"] == "extend"


def test_the_passage_keeps_the_case_and_marks_the_quote():
    text = "Intro.  The Widget Providers would face   cost number 5 under the option. Rest."
    p = rc._passage("widget providers would face cost number 5 under the option", text, 10)
    # The highlight grows to the whole sentence, case kept.
    assert p["quote"] == "The Widget Providers would face cost number 5 under the option."
    assert p["before"].endswith("Intro.") and p["after"].startswith(" Rest")
    assert rc._passage("nowhere to be found in it", text, 10) is None


def test_the_key_keeps_the_tools_reason():
    reasons = {i.item_id: i.tool_reason for i in rc.population([tiebroken()])}
    assert any(r.startswith("tie-break: ") for r in reasons.values())
    assert any(r.startswith("judges: anchor: ") for r in reasons.values())


def test_hosted_upload_and_fetch_round_trip(tmp_path, monkeypatch, capsys):
    """upload sends pages.json (never the key); fetch writes answer files that score reads."""
    import httpx

    drafts = [tiebroken("case_90_a"), tiebroken("case_91_b")]
    out = tmp_path / "check"
    rc.write_sample(out, drafts, n=3, seed=1, annotators=["ana", "bo"], ia_root=tmp_path)
    key = json.loads((out / rc.KEY_FILE).read_text())
    truth = {i: "keep" if it["tool_kept"] else "drop" for i, it in key["items"].items()}
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer admin-0123456789abcdef"
        if request.method == "PUT":
            sent.update(json.loads(request.content))
            return httpx.Response(200, json={})
        answers = {a: {"cards": 3, "submitted_at": "2026-10-10T10:00:00Z" if a == "ana" else None,
                       "answers": [{"item": i, "label": truth[i], "note": "ok: fine"}
                                   for i, it in key["items"].items() if it["annotator"] == a]}
                   for a in ("ana", "bo")}  # fmt: skip
        return httpx.Response(200, json={"sample": key["sample"], "annotators": answers})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("WOMM_LABEL_ADMIN_TOKEN", "admin-0123456789abcdef")
    base = ["--dir", str(out), "--url", "https://app.example/", "--holdout-registry",
            str(tmp_path / "x.yaml")]  # fmt: skip
    assert script.hosted(script.parse_args(["upload", *base]), client) == 0
    assert f"https://app.example/label/{key['sample']}" in capsys.readouterr().out
    assert set(sent["annotators"]) == {"ana", "bo"}
    for leak in LEAKS:
        assert leak not in json.dumps(sent)
    assert script.hosted(script.parse_args(["fetch", *base]), client) == 0
    assert "waiting for bo" in capsys.readouterr().out
    labels, notes = rc.read_answers(out, key)
    assert labels == truth and set(notes.values()) == {"ok: fine"}
    monkeypatch.delenv("WOMM_LABEL_ADMIN_TOKEN")
    monkeypatch.setattr(hosted_labels, "ENV_FILE", tmp_path / "no.env")
    assert script.main(["fetch", *base]) == 2


def test_the_hosted_page_starts_empty_and_signs_in():
    page = rc.render_hosted_page("2026-10-10-0123abcd", api="/label/api/2026-10-10-0123abcd")
    assert not re.search(r"__[A-Z_]+__", page)
    data = json.loads(re.search(r"const DATA = (.*);\n", page).group(1))
    assert data["items"] == [] and data["api"].endswith("0123abcd")
    for tag in ("<script>", "</script>", "<body>", "</body>", "</style>", 'id="welcome"'):
        assert page.count(tag) == 1, tag
    assert page.rstrip().endswith("</html>")


def test_a_card_for_an_rsb_item_shows_the_opinion_around_its_quote():
    """Plan 2026-10-12-001 U2: RSB-anchored items get context like IA-anchored ones."""
    from .draft_factory import draft_dict, omission, to_draft

    data = draft_dict("train")
    rsb = omission(9) | {"source": "rsb", "ia_anchor": "small widget makers are not costed"}
    rsb["anchor"] = {"status": "verified", "against": "rsb"}
    data["important_omissions"].append(rsb)
    draft = to_draft(data)
    it = rc.Item(draft.case_id, draft.fixture, "c90_o09", "judges_accepted", True)
    opinion = "The Board notes that small widget makers are not costed in the report at all."
    with_rsb = rc.card("x1", it, {draft.case_id: draft}, {draft.fixture: "IA text"},
                       {draft.fixture: opinion})  # fmt: skip
    assert with_rsb["source"] == "scrutiny board opinion"
    assert with_rsb["passage"]["quote"] == opinion  # the whole sentence
    assert with_rsb["passage"]["before"] == ""
    without = rc.card("x1", it, {draft.case_id: draft}, {draft.fixture: "IA text"})
    assert without["passage"] is None and without["quote"] == "small widget makers are not costed"


def _companion_of(case: GoldenDraft, quotes: list[str]) -> GoldenDraft:
    """A real companion (plan 2026-10-12-001) of ``case`` with judge-accepted RSB omissions."""
    import asyncio

    from womm.data.fixtures import fixture_dir, load_fixture
    from womm.eval.drafting import draft_rsb_companion
    from womm.llm.fake import FakeBackend

    from .test_drafting import config, omission
    from .test_rsb_companion import OPINION, companion_inputs, script

    fixture = load_fixture(fixture_dir("data_act"))
    inp = companion_inputs(fixture).model_copy(update={"case_id": case.case_id})
    fake = FakeBackend(script([omission(q, "rsb") for q in quotes]))
    assert all(q in OPINION for q in quotes)
    return asyncio.run(draft_rsb_companion(case, inp, config(), {"fake": fake}))


QUOTES = [
    "the report does not show how the switching deadlines affect small widget resellers",
    "who would lose their margin on bundled exit services",
]


def test_companions_are_labelled_in_full_and_never_mixed_into_a_sample(tmp_path, capsys):
    drafts_dir = tmp_path / "drafts"
    case = tiebroken()
    case_path = write_draft(case, drafts_dir)
    comp_path = write_draft(_companion_of(case, QUOTES), drafts_dir)
    base = ["--annotator", "ana", "--annotator", "bo", "--ia-root", str(tmp_path),
            "--holdout-registry", str(tmp_path / "x.yaml")]  # fmt: skip
    # A census: every companion item, once, split between the two; nothing from the case.
    out = tmp_path / "census"
    argv = ["sample", str(case_path), str(comp_path), *base, "--out", str(out), "--companions"]
    assert script.main(argv) == 0
    key = json.loads((out / rc.KEY_FILE).read_text())
    comp_ids = sorted(o.omission_id for o in load_draft(comp_path).important_omissions)
    assert sorted(it["item_id"] for it in key["items"].values()) == comp_ids
    assert {it["annotator"] for it in key["items"].values()} == {"ana", "bo"}
    assert not key.get("reserve")
    cards = page_data(out / "label_ana.html")["items"]
    assert cards and all(c["source"] == "scrutiny board opinion" for c in cards)
    # An ordinary sample leaves companions out and says so.
    capsys.readouterr()
    argv = ["sample", str(case_path), str(comp_path), *base, "--out", str(tmp_path / "s"),
            "--n", "3"]  # fmt: skip
    assert script.main(argv) == 0
    assert "1 companion draft(s) left out" in capsys.readouterr().err
    key = json.loads((tmp_path / "s" / rc.KEY_FILE).read_text())
    assert not set(comp_ids) & {it["item_id"] for it in key["items"].values()}


def test_a_companion_without_its_case_cannot_be_sampled(tmp_path):
    comp = _companion_of(tiebroken(), QUOTES)
    with pytest.raises(rc.ReferenceCheckError, match="need their case drafts"):
        rc.write_sample(tmp_path / "x", [], n=3, seed=1, annotators=["ana"], companions=[comp])
    with pytest.raises(rc.ReferenceCheckError, match="checked on their own"):
        rc.write_sample(tmp_path / "y", [tiebroken(), comp], n=3, seed=1, annotators=["ana"])


def test_shared_items_measure_the_people_and_never_count_in_the_gate(tmp_path):
    out = tmp_path / "check"
    rc.write_sample(out, [tiebroken("case_90_a"), tiebroken("case_91_b")], n=3, seed=1,
                    annotators=["ana", "bo"], ia_root=tmp_path, extra=0, overlap=2)  # fmt: skip
    key = json.loads((out / rc.KEY_FILE).read_text())
    shared = key["overlap"]
    assert len(shared) == 2 and not set(shared) & set(key["items"])
    for rid, it in shared.items():  # each copy goes to the other person, same item, blind
        orig = key["items"][it["overlap_of"]]
        assert it["annotator"] != orig["annotator"]
        assert (it["case_id"], it["item_id"]) == (orig["case_id"], orig["item_id"])
        page = page_data(out / f"label_{it['annotator']}.html")
        assert rid in {c["id"] for c in page["items"]}
        assert "overlap" not in json.dumps(page)
    truth = {i: "keep" if it["tool_kept"] else "drop" for i, it in key["items"].items()}
    a, b = sorted(shared)
    labels = truth | {a: truth[shared[a]["overlap_of"]],
                      b: "unsure"}  # fmt: skip
    res = rc.score(key, labels)
    assert res["items"] == 6 and res["error"] == 0 and res["robust"]
    assert res["overlap"] == {"pairs": 2, "agree": 1, "disagree": [b]}
    text = rc.format_result(res, key)
    assert "people agree with each other on 1 of 2 shared items" in text
    page = review_page.reference_check(out, key, res, labels, {}, limit=rc.MAX_ERROR).read_text()
    assert "People agree with each other" in page and "1 of 2" in page
    for name in ("ana", "bo"):  # the answer files accept the shared items
        mine = {
            i: labels[i] for i, it in (key["items"] | shared).items() if it["annotator"] == name
        }
        answer_file(out / f"answers_{name}.yaml", key, name, mine)
    got, _ = rc.read_answers(out, key)
    assert rc.score(key, got)["overlap"]["agree"] == 1


def test_without_shared_items_the_sample_is_unchanged(tmp_path):
    kw = dict(n=3, seed=1, annotators=["ana", "bo"], ia_root=tmp_path, extra=2)
    drafts = [tiebroken("case_90_a"), tiebroken("case_91_b")]
    rc.write_sample(tmp_path / "a", drafts, **kw)
    rc.write_sample(tmp_path / "b", drafts, overlap=0, **kw)
    ka, kb = (json.loads((tmp_path / d / rc.KEY_FILE).read_text()) for d in "ab")
    assert ka["items"] == kb["items"] and "overlap" not in ka
    assert "overlap" not in rc.score(ka, {i: "keep" for i in ka["items"]})


def test_a_pass_with_a_wide_worst_case_is_called_borderline():
    key = _key([("judges_accepted", True)] * 60)
    truth = {f"r{n}": "keep" for n in range(1, 61)}
    five = truth | {f"r{n}": "drop" for n in range(1, 6)}
    res = rc.score(key, five)
    assert res["robust"] and res["borderline"]
    assert "borderline" in rc.format_result(res, key)
    assert not rc.score(key, truth)["borderline"]


def test_a_disputed_only_sample_holds_only_what_the_tie_break_ruled(tmp_path):
    drafts = [tiebroken("case_90_a"), tiebroken("case_91_b")]
    rc.write_sample(tmp_path / "d", drafts, n=3, seed=1, annotators=["ana", "bo"],
                    ia_root=tmp_path, extra=0, disputed_only=True)  # fmt: skip
    key = json.loads((tmp_path / "d" / rc.KEY_FILE).read_text())
    assert key["disputed_only"] and key["items"]
    assert {it["stratum"] for it in key["items"].values()} <= set(rc.DISPUTED)
    # Companions the judges all accepted leave nothing disputed to label.
    case = tiebroken()
    with pytest.raises(rc.ReferenceCheckError, match="no tool-decided item"):
        rc.write_sample(tmp_path / "c", [case], n=3, seed=1, annotators=["ana", "bo"],
                        ia_root=tmp_path, companions=[_companion_of(case, QUOTES)],
                        disputed_only=True)  # fmt: skip


def _disputed_companion(case: GoldenDraft) -> GoldenDraft:
    """A companion whose first omission the tie-break dropped (its judges disagreed)."""
    comp = _companion_of(case, QUOTES)
    ruled = next(i for i in case.items() if rc.stratum(i) == "tiebreak_dropped")
    first, *rest = comp.important_omissions
    first = first.model_copy(update={"tiebreak": ruled.tiebreak,
                                     "review": ruled.review})  # fmt: skip
    return comp.model_copy(update={"important_omissions": [first, *rest]})


def test_the_companion_check_rides_on_the_same_pages_and_is_scored_apart(tmp_path, capsys):
    case = tiebroken()
    comp = _disputed_companion(case)
    kw = dict(n=3, seed=1, annotators=["ana", "bo"], ia_root=tmp_path, extra=2)
    rc.write_sample(tmp_path / "plain", [case], **kw)
    rc.write_sample(tmp_path / "both", [case], with_companions=[comp], **kw)
    plain, both = (json.loads((tmp_path / d / rc.KEY_FILE).read_text()) for d in ("plain", "both"))
    added = {i: it for i, it in both["items"].items() if it["check"] == "companion"}
    # The case sample is drawn exactly as without companions; only the disputed point is added.
    assert {i: it | {"check": "reference"} for i, it in plain["items"].items()} == {
        i: it for i, it in both["items"].items() if it["check"] == "reference"
    }
    assert both["reserve"].keys() == plain["reserve"].keys()
    assert [it["item_id"] for it in added.values()] == [comp.important_omissions[0].omission_id]
    [(rid, it)] = added.items()
    page = page_data(tmp_path / "both" / f"label_{it['annotator']}.html")
    assert rid in {c["id"] for c in page["items"]}
    assert rc.parts(both) == ["reference", "companion"] and rc.parts(plain) == ["reference"]
    assert rc.part(both, "companion")["reserve"] == {}
    # Scored apart: a wrong companion label leaves the answer-key verdict alone.
    truth = {i: "keep" if x["tool_kept"] else "drop" for i, x in both["items"].items()}
    labels = truth | {rid: "keep"}
    assert rc.score(rc.part(both, "reference"), labels)["verdict"] == "robust"
    assert rc.score(rc.part(both, "companion"), labels)["wrong"] == [rid]
    for name in ("ana", "bo"):
        mine = {i: labels[i] for i, x in both["items"].items() if x["annotator"] == name}
        answer_file(tmp_path / "both" / f"answers_{name}.yaml", both, name, mine)
    capsys.readouterr()
    assert script.main(["score", "--dir", str(tmp_path / "both")]) == 1
    out = capsys.readouterr().out
    assert "== Answer key check" in out and "== Expert (RSB) points check" in out
    assert (tmp_path / "both" / "review_companion.html").exists()


def test_cards_carry_the_articles_and_the_first_card_is_from_the_report(tmp_path):
    case = tiebroken()
    comp = _disputed_companion(case)
    rc.write_sample(tmp_path / "s", [case], n=3, seed=1, annotators=["ana", "bo"],
                    ia_root=tmp_path, extra=0, with_companions=[comp])  # fmt: skip
    for name in ("ana", "bo"):
        cards = page_data(tmp_path / "s" / f"label_{name}.html")["items"]
        assert cards[0]["source"] == "report"  # the guided first point is an ordinary one
        for c in cards:
            assert c["articles"] and all(a["label"].startswith("Article ") for a in c["articles"])
            assert all(a["text"].strip() for a in c["articles"])
    page = (tmp_path / "s" / "label_ana.html").read_text()
    assert 'id="decide"' in page and "Does the board ask for this effect or risk to be assessed?" in page


def test_a_case_whose_scenario_is_not_public_shows_no_articles():
    draft = tiebroken().model_copy(update={"scenario_id": "eval_not_in_the_fixture"})
    assert rc._articles(draft, ["data_act/proposal/art/24"]) == []


def test_decisions_people_cannot_judge_are_taken_out_and_listed(tmp_path):
    case = tiebroken()
    kw = dict(n=3, seed=1, annotators=["ana", "bo"], ia_root=tmp_path, extra=2)
    rc.write_sample(tmp_path / "all", [case], **kw)
    every = json.loads((tmp_path / "all" / rc.KEY_FILE).read_text())
    drawn = list(every["items"].values()) + list(every["reserve"].values())
    dropped = [it for it in drawn if it["stratum"] == "tiebreak_dropped"]
    scope = {f"{it['case_id']}/{it['item_id']}": {"basis": "duplicate", "of": ["c90_e01"]}
             for it in dropped}  # fmt: skip
    res = rc.write_sample(tmp_path / "scoped", [case], out_of_scope=scope, **kw)
    key = json.loads((tmp_path / "scoped" / rc.KEY_FILE).read_text())
    left = list(key["items"].values()) + list(key["reserve"].values())
    assert res["out_of_scope"] == len(dropped) and len(key["out_of_scope"]) == len(dropped)
    assert all(o["basis"] == "duplicate" and o["of"] == ["c90_e01"] for o in key["out_of_scope"])
    assert not any(it["stratum"] == "tiebreak_dropped" for it in left)
    assert len(left) == len(drawn) - len(dropped)
    pages = hosted_labels.read_pages(tmp_path / "scoped")["annotators"]
    shown = {c["id"] for cards in pages.values() for c in cards}
    assert shown == set(key["items"])
    # A "text" basis stays with people.
    keep = {k: {"basis": "text", "of": []} for k in scope}
    rc.write_sample(tmp_path / "text", [case], out_of_scope=keep, **kw)
    assert json.loads((tmp_path / "text" / rc.KEY_FILE).read_text())["out_of_scope"] == []


def test_a_board_point_is_judged_on_its_one_line_and_its_text_is_context(tmp_path):
    case = tiebroken()
    comp = _disputed_companion(case)
    point = comp.important_omissions[0]
    kw = dict(n=3, seed=1, annotators=["ana", "bo"], ia_root=tmp_path, extra=0,
              with_companions=[comp])  # fmt: skip
    with pytest.raises(rc.ReferenceCheckError, match="no line to judge for board points"):
        rc.write_sample(tmp_path / "none", [case], judge_lines={}, **kw)
    assert not (tmp_path / "none").exists()
    line = "The effect on small widget resellers."
    rc.write_sample(tmp_path / "s", [case],
                    judge_lines={f"{case.case_id}/{point.omission_id}": line}, **kw)  # fmt: skip
    cards = [
        c for n in ("ana", "bo") for c in page_data(tmp_path / "s" / f"label_{n}.html")["items"]
    ]
    [board] = [c for c in cards if c["source"] != "report"]
    assert board["claim"]["concern"] == line
    assert board["claim"]["description"] == point.description
    assert all("concern" not in c["claim"] for c in cards if c["source"] == "report")
    page = (tmp_path / "s" / "label_ana.html").read_text()
    assert "The AI's reasoning (context, not judged)" in page


def test_other_wording_for_a_board_point_is_another_sample(tmp_path):
    case = tiebroken()
    comp = _disputed_companion(case)
    key = f"{case.case_id}/{comp.important_omissions[0].omission_id}"
    kw = dict(n=3, seed=1, annotators=["ana", "bo"], ia_root=tmp_path, extra=0,
              with_companions=[comp])  # fmt: skip
    a = rc.write_sample(tmp_path / "a", [case], judge_lines={key: "One line."}, **kw)
    b = rc.write_sample(tmp_path / "b", [case], judge_lines={key: "Another line."}, **kw)
    assert a["sample"] != b["sample"]


def test_table_residue_is_tidied_and_a_cut_quote_reads_as_a_sentence():
    text = "Costs: EUR 1. 7. Compliance officer | \\ | \\ | Very large platforms 1-5 FTEs. Next."
    p = rc._passage("compliance officer | \\ | \\ | very large platforms", text, 20)
    # A table: not grown, and empty cells keep their place.
    assert p["quote"] == "Compliance officer | — | — | Very large platforms"
    assert "\\" not in p["before"] + p["after"]


def test_an_abbreviation_does_not_end_the_highlighted_sentence():
    text = "Before. It is already set up under EU law (e.g. the GDPR), which absorbs it. After."
    p = rc._passage("already set up under eu law", text, 20)
    assert p["quote"] == "It is already set up under EU law (e.g. the GDPR), which absorbs it."
