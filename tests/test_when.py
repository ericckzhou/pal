from datetime import datetime

import pytest

from pal import when

NOW = datetime(2026, 11, 24, 10, 0)  # a Tuesday


@pytest.mark.parametrize(
    ("text", "expected", "has_time"),
    [
        ("Remind me to call mom tomorrow at 6pm.", datetime(2026, 11, 25, 18, 0), True),
        ("finish the chat UI by Friday", datetime(2026, 11, 27, 9, 0), False),
        ("Remind me in 2 hours to take the laundry out.", datetime(2026, 11, 24, 12, 0), True),
        ("in half an hour", datetime(2026, 11, 24, 10, 30), True),
        ("in 3 days", datetime(2026, 11, 27, 9, 0), False),
        ("Remind me tonight to stretch.", datetime(2026, 11, 24, 20, 0), True),
        ("15 minutes before my 3pm meeting today", datetime(2026, 11, 24, 14, 45), True),
        ("due next Wednesday", datetime(2026, 12, 2, 9, 0), False),
        ("on Dec 1 to pay rent", datetime(2026, 12, 1, 9, 0), False),
        ("the 3rd of March", datetime(2027, 3, 3, 9, 0), False),
        ("2026-12-24 at 14:30", datetime(2026, 12, 24, 14, 30), True),
        ("12/31", datetime(2026, 12, 31, 9, 0), False),
        ("taxes in April", datetime(2027, 4, 1, 9, 0), False),
        ("at 9", datetime(2026, 11, 25, 9, 0), True),  # 9:00 already passed today
        ("at 5", datetime(2026, 11, 24, 17, 0), True),  # a bare small hour means pm
        ("at noon", datetime(2026, 11, 24, 12, 0), True),
        ("Tuesday at 8am", datetime(2026, 12, 1, 8, 0), True),  # today's 8am is gone, so next week
        ("this weekend", datetime(2026, 11, 28, 9, 0), False),
        ("next week", datetime(2026, 11, 30, 9, 0), False),
        ("day after tomorrow in the morning", datetime(2026, 11, 26, 9, 0), True),
        ("6:30 p.m. tomorrow", datetime(2026, 11, 25, 18, 30), True),
    ],
)
def test_parse_when(text, expected, has_time):
    found = when.parse_when(text, NOW)
    assert found is not None
    assert (found.at, found.has_time) == (expected, has_time)


@pytest.mark.parametrize("text", ["What's the capital of Peru?", "Add a task to renew my passport", "", None,
                                  "I may go", "on Feb 30"])
def test_parse_when_finds_nothing(text):
    assert when.parse_when(text, NOW) is None


def test_find_when_removes_the_date_words():
    found, rest = when.find_when("renew passport by friday", NOW)
    assert found.at.date().isoformat() == "2026-11-27"
    assert rest == "renew passport"
    found, rest = when.find_when("tomorrow 6pm to call mom", NOW)
    assert (found.at, rest) == (datetime(2026, 11, 25, 18, 0), "to call mom")


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("every day", "FREQ=DAILY"),
        ("daily standup", "FREQ=DAILY"),
        ("every weekday at 8", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("every Monday at 9", "FREQ=WEEKLY;BYDAY=MO"),
        ("every Monday and Thursday", "FREQ=WEEKLY;BYDAY=MO,TH"),
        ("on Sundays", "FREQ=WEEKLY;BYDAY=SU"),
        ("every other week", "FREQ=WEEKLY;INTERVAL=2"),
        ("every 3 days", "FREQ=DAILY;INTERVAL=3"),
        ("and every month after that", "FREQ=MONTHLY"),
        ("annually", "FREQ=YEARLY"),
        ("on Monday", None),  # a single date, not a repeat
        ("tomorrow", None),
        (None, None),
    ],
)
def test_parse_recurrence(text, rule):
    assert when.parse_recurrence(text) == rule


def test_next_occurrence():
    anchor = datetime(2026, 11, 24, 9, 0)  # Tuesday 9:00
    assert when.next_occurrence("FREQ=DAILY", anchor, anchor) == datetime(2026, 11, 25, 9, 0)
    assert when.next_occurrence("FREQ=WEEKLY;BYDAY=MO,TH", anchor, anchor) == datetime(2026, 11, 26, 9, 0)
    assert when.next_occurrence("FREQ=WEEKLY;INTERVAL=2", anchor, anchor) == datetime(2026, 12, 8, 9, 0)
    assert when.next_occurrence("FREQ=HOURLY;INTERVAL=2", anchor, datetime(2026, 11, 24, 10, 0)) == datetime(
        2026, 11, 24, 11, 0)
    jan31 = datetime(2027, 1, 31, 9, 0)
    assert when.next_occurrence("FREQ=MONTHLY", jan31, jan31) == datetime(2027, 3, 31, 9, 0)  # no Feb 31
    # Missed occurrences are skipped: the next one is after `after`, not right after the anchor.
    assert when.next_occurrence("FREQ=DAILY", anchor, datetime(2026, 12, 1, 12, 0)) == datetime(2026, 12, 2, 9, 0)


def test_first_occurrence_matches_the_rule():
    # "every Monday at 9" said on a Tuesday: the anchor (today 9:00) isn't a Monday, so it starts next Monday.
    assert when.first_occurrence("FREQ=WEEKLY;BYDAY=MO", datetime(2026, 11, 24, 9), NOW) == datetime(2026, 11, 30, 9)
    # A future anchor that matches fires on the anchor itself.
    dec1 = datetime(2026, 12, 1, 9)
    assert when.first_occurrence("FREQ=MONTHLY", dec1, NOW) == dec1


@pytest.mark.parametrize("bad", ["FREQ=SECONDLY", "FREQ=DAILY;INTERVAL=0", "FREQ=WEEKLY;BYDAY=XX", "nonsense"])
def test_validate_rrule_rejects_bad_rules(bad):
    with pytest.raises(ValueError):
        when.validate_rrule(bad)


def test_describe_rrule():
    assert when.describe_rrule("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR") == "every weekday"
    assert when.describe_rrule("FREQ=WEEKLY;BYDAY=MO") == "every Monday"
    assert when.describe_rrule("FREQ=DAILY;INTERVAL=3") == "every 3 days"
    assert when.describe_rrule(None) == ""
