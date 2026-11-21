"""Status machine and timezone handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.errors import InvalidTransitionError, ValidationError
from app.core.ids import validate_uuid
from app.core.time_utils import (
    require_aware,
    session_now,
    to_utc,
    validate_timezone,
)
from app.models.status import SessionStatus, assert_transition, can_transition


def test_legal_forward_transitions() -> None:
    assert can_transition(SessionStatus.OPEN, SessionStatus.QUEUED)
    assert can_transition(SessionStatus.QUEUED, SessionStatus.TRANSCRIBING)
    assert can_transition(SessionStatus.TRANSCRIBING, SessionStatus.TRANSCRIBED)
    assert can_transition(SessionStatus.TRANSCRIBED, SessionStatus.READY)


def test_illegal_transitions_are_rejected() -> None:
    assert not can_transition(SessionStatus.OPEN, SessionStatus.TRANSCRIBED)
    assert not can_transition(SessionStatus.READY, SessionStatus.QUEUED)
    with pytest.raises(InvalidTransitionError):
        assert_transition(SessionStatus.OPEN, SessionStatus.READY)


def test_failed_is_reachable_from_any_non_terminal_state() -> None:
    for status in (
        SessionStatus.OPEN,
        SessionStatus.QUEUED,
        SessionStatus.TRANSCRIBING,
        SessionStatus.ANALYZING,
    ):
        assert can_transition(status, SessionStatus.FAILED)
    assert not can_transition(SessionStatus.READY, SessionStatus.FAILED)


def test_timezone_validation() -> None:
    assert validate_timezone("Asia/Kolkata") == "Asia/Kolkata"
    with pytest.raises(ValidationError):
        validate_timezone("Mars/Olympus_Mons")


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        require_aware(datetime(2026, 11, 19, 17, 0), "started_at")


def test_to_utc_normalises_offsets() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 11, 19, 17, 0, tzinfo=ist)
    assert to_utc(local) == datetime(2026, 11, 19, 11, 30, tzinfo=UTC)


def test_session_now_anchors_relative_dates_in_the_users_zone() -> None:
    """A session recorded at 00:30 UTC is already 'tomorrow' in Kolkata.

    This is exactly the case that makes LLM date arithmetic unsafe and why
    resolution happens here instead.
    """
    started = datetime(2026, 11, 19, 0, 30, tzinfo=UTC)
    local = session_now(started, "Asia/Kolkata")
    assert (local.hour, local.minute) == (6, 0)
    assert local.date().isoformat() == "2026-11-19"


def test_validate_uuid() -> None:
    value = "f81d4fae-7dec-11d0-a765-00a0c91e6bf6"
    assert validate_uuid(value, "segment_id") == value
    with pytest.raises(ValidationError):
        validate_uuid("not-a-uuid", "segment_id")
