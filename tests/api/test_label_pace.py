"""Answer pace: a rough reading speed per annotator, to spot clicking through."""

import datetime as dt

from womm.api.db import pace
from womm.eval import hosted_labels

T0 = dt.datetime(2026, 10, 11, 12, 0, tzinfo=dt.UTC)


def _at(*seconds):
    return [T0 + dt.timedelta(seconds=s) for s in seconds]


def test_pace_is_the_median_gap_without_breaks():
    assert pace(_at(0, 30, 50, 90)) == 30.0  # gaps 30, 20, 40
    assert pace(_at(0, 30, 50, 50 + 3600, 50 + 3640)) == 30.0  # the hour-long break is left out
    assert pace(_at(40, 0, 20)) == 20.0  # order does not matter


def test_pace_needs_three_answers():
    assert pace([]) is None and pace(_at(0, 10)) is None
    assert pace(_at(0, 10, 10_000)) is None  # one gap left after the break


def test_progress_flags_a_very_fast_annotator():
    got = {
        "ann": {"cards": 20, "submitted_at": "x", "answers": [{}] * 20, "seconds_per_item": 31.0},
        "bob": {"cards": 20, "submitted_at": "x", "answers": [{}] * 20, "seconds_per_item": 3.0},
        "cat": {"cards": 20, "submitted_at": None, "answers": [], "seconds_per_item": None},
    }
    lines, pending = hosted_labels.progress(got)
    assert lines[0] == "ann: 20 of 20 answered, submitted, about 31 s per item"
    assert lines[1].startswith("bob: 20 of 20 answered, submitted, about 3 s per item (very fast")
    assert lines[2] == "cat: 0 of 20 answered, not submitted yet" and pending == ["cat"]
