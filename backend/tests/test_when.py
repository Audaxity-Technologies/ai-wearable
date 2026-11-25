"""Tests for question-side time resolution.

The risk is direction. `core/dates.py` resolves deadlines and faces forward:
bare "Friday" is the coming Friday. A question faces backward, and getting
that wrong returns nothing while looking like a retrieval bug rather than a
parser bug.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.core.when import DAY_PARTS, TimeRange, resolve_when

IST = ZoneInfo("Asia/Kolkata")
#: A Wednesday, chosen so weekday arithmetic is checkable by hand.
NOW = dt.datetime(2026, 11, 25, 19, 0, tzinfo=IST)


def at(day: int, hour: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, day, hour, tzinfo=IST)


# --- direction -----------------------------------------------------------


def test_a_bare_weekday_means_the_most_recent_one_not_the_next() -> None:
    """The whole reason this module is not `resolve_due`.

    Asked on Wednesday the 25th, "Friday" is the 20th. Resolving it forward
    to the 27th would return nothing and look like a broken index.
    """
    window = resolve_when("what did we decide on Friday", now=NOW).resolved
    assert window is not None
    assert window.start == at(20)
    assert window.end == at(21)


def test_last_weekday_on_the_same_weekday_goes_back_a_week() -> None:
    """Asked on a Wednesday, "last Wednesday" is the 18th, not today."""
    window = resolve_when("last Wednesday", now=NOW).resolved
    assert window is not None
    assert window.start == at(18)


def test_todays_weekday_without_last_means_today() -> None:
    window = resolve_when("what did we discuss Wednesday", now=NOW).resolved
    assert window is not None
    assert window.start == at(25)


# --- plain days ----------------------------------------------------------


@pytest.mark.parametrize(
    "phrase,day",
    [
        ("today", 25),
        ("yesterday", 24),
        ("the day before yesterday", 23),
        ("3 days ago", 22),
    ],
)
def test_named_days(phrase: str, day: int) -> None:
    window = resolve_when(f"what happened {phrase}", now=NOW).resolved
    assert window is not None
    assert window.start == at(day)
    assert window.end == at(day + 1)


def test_day_before_yesterday_beats_yesterday() -> None:
    """Both patterns match the string; the longer phrase must win."""
    window = resolve_when("the day before yesterday", now=NOW).resolved
    assert window is not None and window.start == at(23)


# --- weeks and months ----------------------------------------------------


def test_last_week_is_the_previous_monday_to_sunday() -> None:
    window = resolve_when("what did we agree last week", now=NOW).resolved
    assert window is not None
    assert window.start == at(16)  # Monday
    assert window.end == at(23)
    assert window.days == pytest.approx(7)


def test_this_week_starts_on_monday() -> None:
    window = resolve_when("anything this week", now=NOW).resolved
    assert window is not None
    assert window.start == at(23)


def test_last_month_is_the_whole_previous_month() -> None:
    window = resolve_when("what about last month", now=NOW).resolved
    assert window is not None
    assert window.start == dt.datetime(2026, 10, 1, tzinfo=IST)
    assert window.end == dt.datetime(2026, 11, 1, tzinfo=IST)


def test_a_named_month_covers_the_month() -> None:
    window = resolve_when("anything in November", now=NOW).resolved
    assert window is not None
    assert window.start == dt.datetime(2026, 11, 1, tzinfo=IST)
    assert window.end == dt.datetime(2026, 12, 1, tzinfo=IST)


def test_a_month_later_than_now_is_last_year() -> None:
    """Backward again: in November, "December" is last December."""
    window = resolve_when("what did we decide in December", now=NOW).resolved
    assert window is not None
    assert window.start.year == 2025
    assert window.start.month == 12


def test_a_month_with_a_day() -> None:
    window = resolve_when("what happened on November 21", now=NOW).resolved
    assert window is not None
    assert window.start == at(21)
    assert window.end == at(22)


def test_an_impossible_date_resolves_to_nothing_rather_than_crashing() -> None:
    result = resolve_when("what about February 31", now=NOW)
    assert result.resolved is None


# --- parts of the day ----------------------------------------------------


def test_a_part_of_the_day_narrows_a_named_day() -> None:
    window = resolve_when("what did we say yesterday evening", now=NOW).resolved
    assert window is not None
    assert window.start == at(24, 17)
    assert window.end == at(24, 21)


def test_this_morning_means_today_narrowed() -> None:
    window = resolve_when("what did we talk about this morning", now=NOW).resolved
    assert window is not None
    assert window.start == at(25, 5)
    assert window.end == at(25, 12)


def test_a_bare_part_of_the_day_is_left_unresolved() -> None:
    """It needs a day. Defaulting to today would answer about the wrong
    morning whenever the question was about last week."""
    assert resolve_when("in the morning", now=NOW).resolved is None


def test_night_runs_to_midnight_not_past_it() -> None:
    window = resolve_when("yesterday night", now=NOW).resolved
    assert window is not None
    assert window.end == at(25)


def test_a_part_of_the_day_does_not_shrink_a_month() -> None:
    """"Last month, in the morning" must not collapse to one morning."""
    window = resolve_when("last month in the morning", now=NOW).resolved
    assert window is not None
    assert window.days > 25


def test_every_day_part_is_a_valid_window() -> None:
    for part, (low, high) in DAY_PARTS.items():
        assert 0 <= low < high <= 24, part


# --- deixis --------------------------------------------------------------


def test_that_day_without_an_anchor_is_ambiguous_not_guessed() -> None:
    """A guessed range returns real sentences from the wrong moment, which
    reads as an answer rather than as a mistake."""
    result = resolve_when("what did we decide that day", now=NOW)
    assert result.ambiguous
    assert result.resolved is None
    assert result.phrase == "that day"


def test_that_day_resolves_against_a_single_anchor() -> None:
    anchor = [TimeRange(at(21), at(22), "21 November")]
    result = resolve_when(
        "what did we decide that day", now=NOW, anchor_ranges=anchor
    )
    assert not result.ambiguous
    assert result.resolved is not None
    assert result.resolved.start == at(21)


def test_several_anchors_stay_ambiguous_with_the_options() -> None:
    anchors = [
        TimeRange(at(20), at(21), "20 November"),
        TimeRange(at(21), at(22), "21 November"),
    ]
    result = resolve_when("that day", now=NOW, anchor_ranges=anchors)
    assert result.ambiguous
    assert len(result.ranges) == 2


# --- nothing at all ------------------------------------------------------


def test_a_question_with_no_time_expression_resolves_to_nothing() -> None:
    """The ordinary case, and not a failure."""
    result = resolve_when("what did Pulkit commit to", now=NOW)
    assert result.resolved is None
    assert not result.ambiguous
    assert result.phrase is None


def test_timezone_is_preserved_so_days_are_local() -> None:
    """A day boundary resolved in UTC answers about the wrong day for
    anyone east of Greenwich for part of every evening."""
    window = resolve_when("yesterday", now=NOW).resolved
    assert window is not None
    assert window.start.tzinfo == IST
    assert window.start.hour == 0
