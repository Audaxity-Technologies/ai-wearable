"""Relative date resolution.

The anchor throughout is Thursday 2026-11-19 17:00 IST, matching the brief's
co-founder conversation, so "by Friday" and "tomorrow" have obvious answers.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.core.dates import resolve_due
from app.models.analysis import DateResolution

IST = ZoneInfo("Asia/Kolkata")
#: A Thursday.
ANCHOR = datetime(2026, 11, 19, 17, 0, tzinfo=IST)


def test_anchor_is_a_thursday() -> None:
    assert ANCHOR.strftime("%A") == "Thursday"


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("tomorrow", date(2026, 11, 20)),
        ("by tomorrow", date(2026, 11, 20)),
        ("today", date(2026, 11, 19)),
        ("yesterday", date(2026, 11, 18)),
        ("day after tomorrow", date(2026, 11, 21)),
        ("by Friday", date(2026, 11, 20)),
        ("on Monday", date(2026, 11, 23)),
        ("in 3 days", date(2026, 11, 22)),
        ("in two days", date(2026, 11, 21)),
        ("5 days from now", date(2026, 11, 24)),
        ("2026-12-01", date(2026, 12, 1)),
        ("November 21", date(2026, 11, 21)),
        ("Nov 21st", date(2026, 11, 21)),
        ("21 November", date(2026, 11, 21)),
    ],
)
def test_confident_dates(phrase: str, expected: date) -> None:
    result = resolve_due(phrase, ANCHOR)
    assert result.date == expected, phrase
    assert result.resolution is DateResolution.CONFIDENT, phrase


@pytest.mark.parametrize(
    "phrase",
    [
        "next week",
        "next month",
        "sometime next week",
        "end of the week",
        "soon",
        "asap",
        "in the coming week",
        "next Friday",  # genuinely ambiguous in English
        "this weekend",
        "at 5 PM",  # time without a date
    ],
)
def test_ambiguous_phrases_are_not_guessed(phrase: str) -> None:
    """The brief requires these to surface as 'needs date clarification'."""
    result = resolve_due(phrase, ANCHOR)
    assert result.resolution is DateResolution.AMBIGUOUS, phrase


@pytest.mark.parametrize("phrase", [None, "", "   ", "the report", "blah"])
def test_no_date_present(phrase: str | None) -> None:
    result = resolve_due(phrase, ANCHOR)
    assert result.resolution is DateResolution.NONE
    assert result.date is None


def test_weekday_spoken_on_that_weekday_is_ambiguous() -> None:
    """Said on a Thursday, 'by Thursday' could mean today or a week out."""
    result = resolve_due("by Thursday", ANCHOR)
    assert result.date == date(2026, 11, 19)
    assert result.resolution is DateResolution.AMBIGUOUS


def test_weekday_rolls_into_next_week() -> None:
    # Wednesday is before Thursday, so it must be next week's Wednesday.
    result = resolve_due("Wednesday", ANCHOR)
    assert result.date == date(2026, 11, 25)
    assert result.resolution is DateResolution.CONFIDENT


@pytest.mark.parametrize(
    ("phrase", "expected_date", "expected_time"),
    [
        ("tomorrow at 5 PM", date(2026, 11, 20), time(17, 0)),
        ("tomorrow at 5:30 pm", date(2026, 11, 20), time(17, 30)),
        ("Friday at 09:15", date(2026, 11, 20), time(9, 15)),
        ("tomorrow at noon", date(2026, 11, 20), time(12, 0)),
        # Whisper renders spoken "5 PM" as "5 p.m." - found in live testing.
        ("tomorrow at 5 p.m.", date(2026, 11, 20), time(17, 0)),
        ("tomorrow at 5 P.M.", date(2026, 11, 20), time(17, 0)),
        ("tomorrow at 9 a.m.", date(2026, 11, 20), time(9, 0)),
        ("tomorrow at 5:30 p.m.", date(2026, 11, 20), time(17, 30)),
    ],
)
def test_time_is_extracted_with_the_date(
    phrase: str, expected_date: date, expected_time: time
) -> None:
    result = resolve_due(phrase, ANCHOR)
    assert result.date == expected_date
    assert result.time == expected_time
    assert result.resolution is DateResolution.CONFIDENT


def test_vague_time_of_day_keeps_the_date_but_is_not_confident() -> None:
    """'Friday morning' pins the day, not the hour."""
    result = resolve_due("Friday morning", ANCHOR)
    assert result.date == date(2026, 11, 20)
    assert result.time is None
    assert result.resolution is DateResolution.AMBIGUOUS


def test_past_month_day_rolls_to_next_year() -> None:
    result = resolve_due("January 5", ANCHOR)
    assert result.date == date(2027, 1, 5)


def test_resolution_follows_the_users_timezone_not_utc() -> None:
    """Recorded 23:30 IST = 18:00 UTC. 'Tomorrow' must follow the user's day.

    Resolving against UTC here would return the wrong date, which is exactly
    the bug this module exists to prevent.
    """
    late_night = datetime(2026, 11, 19, 23, 30, tzinfo=IST)
    assert resolve_due("tomorrow", late_night).date == date(2026, 11, 20)

    utc_equivalent = late_night.astimezone(ZoneInfo("UTC"))
    assert utc_equivalent.date() == date(2026, 11, 19)
    assert resolve_due("tomorrow", utc_equivalent).date == date(2026, 11, 20)
