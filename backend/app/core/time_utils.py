"""Time and timezone helpers.

The architecture doc is explicit that the backend never guesses a user's
timezone: clients send an IANA name and the backend validates it. Relative
date resolution (phase 3) will build on `session_now`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import ValidationError


def utc_now() -> datetime:
    """Timezone-aware current time in UTC."""
    return datetime.now(UTC)


def validate_timezone(tz: str) -> str:
    """Return `tz` if it is a known IANA zone, else raise ValidationError."""
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(f"Unknown timezone: {tz!r}") from exc
    return tz


def require_aware(value: datetime, field: str) -> datetime:
    """Reject naive datetimes; an offset-less timestamp is ambiguous."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValidationError(f"{field} must include a UTC offset")
    return value


def to_utc(value: datetime) -> datetime:
    return require_aware(value, "timestamp").astimezone(UTC)


def session_now(started_at: datetime, tz: str) -> datetime:
    """The session's start time as local wall-clock time in its own timezone.

    This is the anchor for resolving phrases like "tomorrow" or "Friday".
    """
    return to_utc(started_at).astimezone(ZoneInfo(validate_timezone(tz)))
