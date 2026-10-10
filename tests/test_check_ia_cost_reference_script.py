"""scripts/check_ia_cost_reference.py: the IA cost reference checked against cached IA text."""

import datetime as dt
import importlib.util
import json
import sys

import pytest
import yaml

from womm.config import REPO_ROOT

spec = importlib.util.spec_from_file_location(
    "check_ia_cost_reference", REPO_ROOT / "scripts/check_ia_cost_reference.py"
)
check = importlib.util.module_from_spec(spec)
sys.modules["check_ia_cost_reference"] = check
spec.loader.exec_module(check)

FIXTURE = "synthetic"
IA_TEXT = """Table 1: Fixed costs

Costs of widget labelling | €2 763
Costs of widget registers | €4 390

The cost of supervising the widgets is estimated at €5000 – €8000 per year (around 0.1 FTE).

The widget board has a secretariat at EU level. This could amount to 10 FTE at EU level.

Set up widget offices | Direct costs |  | 1-25 FTE per MS; 5 FTE at EU

The review of each widget file amounts to a range of €3,000-7,500 for the body.

Large widget sellers would pay in total around € 1,5 million each.

For widget users the costs of keeping logs would be negligible.
"""

HEADER = """# Synthetic reference.
reference_id: synthetic_ref
ia: synthetic IA
proposal_version: v1
status: unverified
verified_by: null
verified_on: null
notes: synthetic
items:
"""


def item(item_id, anchors, unit="per_application", **figures):
    d = {
        "item_id": item_id, "label": "x", "ia_section": "x", "proposal_articles": ["1"],
        "payer": "provider", "payers": ["provider"], "recurrence": "one_off",
        "figure_text": "x", "unit": unit, "comparable_unit": False, "ia_anchors": anchors,
    }  # fmt: skip
    return d | figures


GOOD = [
    item("labelling", ["Costs of widget labelling"], eur_low=2763, eur_high=2763),
    item(
        "supervision", ["cost of supervising the widgets"], "per_year", eur_low=5000, eur_high=8000
    ),
    item("review", ["The review of each widget file"], eur_low=3000, eur_high=7500),
    item("sellers", ["Large widget sellers"], eur_low=1_500_000, eur_high=1_500_000),
    item("states", ["Set up widget offices"], "fte_per_member_state", fte_low=1, fte_high=25),
    item(
        "eu",
        ["a secretariat at EU level", "Set up widget offices"],
        "fte_eu",
        fte_low=5,
        fte_high=10,
        fte_options={"section_6_2": 10, "annex3_table5": 5},
        fte_basis=None,
    ),
    item("logs", ["the costs of keeping logs would be negligible"], "none", negligible=True),
]


def write_reference(path, items):
    body = "".join(
        "  " + yaml.safe_dump([i], sort_keys=False, width=200).replace("\n", "\n  ").rstrip() + "\n"
        for i in items
    )
    path.write_text(HEADER + body, encoding="utf-8")
    return path


@pytest.fixture
def ia_root(tmp_path):
    root = tmp_path / "ia"
    (root / FIXTURE).mkdir(parents=True)
    (root / FIXTURE / "ia_full.txt").write_text(IA_TEXT, encoding="utf-8")
    (root / FIXTURE / "manifest.json").write_text(json.dumps({"fixture": FIXTURE}))
    return root


def run(ref, ia_root, *extra):
    argv = ["--reference", str(ref), "--fixture", FIXTURE, "--ia-root", str(ia_root), *extra]
    return check.main(argv, today=dt.date(2026, 10, 10))


def test_every_matching_item_is_ok(tmp_path, ia_root, capsys):
    ref = write_reference(tmp_path / "ref.yaml", GOOD)
    assert run(ref, ia_root) == 0
    out = capsys.readouterr().out
    assert out.count("OK ") == len(GOOD) and "7/7 OK" in out
    assert "status: unverified" in ref.read_text()  # no --write, no change


def test_a_wrong_figure_is_a_mismatch_listing_the_numbers_found(tmp_path, ia_root, capsys):
    # 4 390 belongs to the next table row: the window ends at the anchor's line.
    bad = item("labelling", ["Costs of widget labelling"], eur_low=4390, eur_high=4390)
    ref = write_reference(tmp_path / "ref.yaml", [bad, *GOOD[1:]])
    assert run(ref, ia_root) == 1
    line = next(x for x in capsys.readouterr().out.splitlines() if "labelling" in x)
    assert line.startswith("MISMATCH") and "EUR 4390" in line and "EUR 2763" in line


def test_an_anchor_absent_from_the_ia_is_not_found(tmp_path, ia_root, capsys):
    missing = item("labelling", ["Costs of gadget labelling"], eur_low=2763, eur_high=2763)
    ref = write_reference(tmp_path / "ref.yaml", [missing])
    assert run(ref, ia_root) == 1
    assert capsys.readouterr().out.startswith("NOT FOUND labelling")


def test_an_item_without_anchors_is_not_found(tmp_path, ia_root, capsys):
    ref = write_reference(tmp_path / "ref.yaml", [item("x", [], eur_low=1, eur_high=1)])
    assert run(ref, ia_root) == 1
    assert "no ia_anchors" in capsys.readouterr().out


def test_a_missing_period_word_is_a_mismatch(tmp_path, ia_root):
    per_day = item("labelling", ["Costs of widget labelling"], "per_day",
                   eur_low=2763, eur_high=2763)  # fmt: skip
    assert run(write_reference(tmp_path / "ref.yaml", [per_day]), ia_root) == 1


def test_an_eur_figure_needs_a_currency_mark_and_an_fte_figure_an_fte(tmp_path, ia_root):
    as_fte = item("labelling", ["Costs of widget labelling"], fte_low=2763, fte_high=2763)
    assert run(write_reference(tmp_path / "a.yaml", [as_fte]), ia_root) == 1
    as_eur = item("states", ["Set up widget offices"], eur_low=1, eur_high=25)
    assert run(write_reference(tmp_path / "b.yaml", [as_eur]), ia_root) == 1


@pytest.mark.parametrize(
    "text, value",
    [
        ("€2 763", 2763), ("€2,763", 2763), ("€2.763", 2763), ("€2763", 2763),
        ("EUR 10 733.33", 10733), ("€ 1,5 million", 1_500_000), ("EUR 3 m", 3_000_000),
        ("€5000 – 8 000", 8000), ("€3,000-7,500", 7500), ("2763 euro", 2763),
        ("€ 1.000.000", 1_000_000),
    ],
)  # fmt: skip
def test_number_formats_are_read_with_their_currency(text, value):
    nums = check.numbers_in(check.normalize(text))
    assert any(n.currency and any(check._same(v, value) for v in n.values) for n in nums)


def test_a_trailing_group_is_never_read_alone():
    nums = check.numbers_in(check.normalize("€2 763"))
    assert [n.values for n in nums] == [{2763.0}]


def test_fte_and_decimal_comma_are_read():
    nums = check.numbers_in(check.normalize("around 0,1 FTE; 1 to 25 Full Time Equivalent"))
    assert [n.fte for n in nums] == [True, True, True]
    assert 0.1 in nums[0].values and 25 in nums[2].values


def test_write_is_refused_on_a_mismatch(tmp_path, ia_root):
    bad = item("labelling", ["Costs of widget labelling"], eur_low=999, eur_high=999)
    ref = write_reference(tmp_path / "ref.yaml", [bad, *GOOD[1:]])
    before = ref.read_text()
    assert run(ref, ia_root, "--write", "--fte-basis", "section_6_2") == 1
    assert ref.read_text() == before


def test_write_is_refused_without_the_fte_basis(tmp_path, ia_root, capsys):
    ref = write_reference(tmp_path / "ref.yaml", GOOD)
    before = ref.read_text()
    assert run(ref, ia_root, "--write") == 2
    assert "--fte-basis" in capsys.readouterr().err and ref.read_text() == before


def test_an_unknown_fte_basis_is_bad_input(tmp_path, ia_root):
    ref = write_reference(tmp_path / "ref.yaml", GOOD)
    assert run(ref, ia_root, "--write", "--fte-basis", "table_9") == 2


def test_write_marks_the_reference_verified_by_the_machine_check(tmp_path, ia_root, capsys):
    ref = write_reference(tmp_path / "ref.yaml", GOOD)
    assert run(ref, ia_root, "--write", "--fte-basis", "annex3_table5", "--by", "Owner") == 0
    text = ref.read_text()
    assert text.startswith("# Synthetic reference.")  # comments are kept
    data = yaml.safe_load(text)
    assert data["status"] == "verified" and data["verified_on"] == "2026-10-10"
    assert "machine check" in data["verified_by"] and "Owner" in data["verified_by"]
    eu = next(i for i in data["items"] if i["item_id"] == "eu")
    assert eu["fte_basis"] == "annex3_table5"
    assert check.load(ref)[0].status == "verified"


def test_a_missing_ia_cache_is_bad_input(tmp_path, capsys):
    ref = write_reference(tmp_path / "ref.yaml", GOOD)
    assert run(ref, tmp_path / "empty") == 2
    assert "no cached IA" in capsys.readouterr().err


def test_an_unreadable_reference_is_bad_input(tmp_path, ia_root):
    ref = tmp_path / "ref.yaml"
    ref.write_text("items: [", encoding="utf-8")
    assert run(ref, ia_root) == 2


def test_the_committed_reference_parses_and_names_anchors_for_every_item():
    ref, _ = check.load(check.REFERENCE_PATH)
    assert ref.status == "unverified"
    assert all(i.ia_anchors for i in ref.items)
    eu = [i for i in ref.items if i.fte_options]
    assert len(eu) == 1 and eu[0].fte_options == {"section_6_2": 10, "annex3_table5": 5}
