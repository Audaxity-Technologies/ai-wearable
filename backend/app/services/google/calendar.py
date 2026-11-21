"""Google Calendar execution.

**Why this one is genuinely idempotent.** Calendar's `events.insert` accepts a
client-supplied event `id`. We derive that id from the action's idempotency
key, so a retry inserts the *same* id and Google answers 409 - which this
executor treats as success, because the event it was asked to create already
exists. That turns "did my approval go through?" from a guess into a fact,
without us keeping a side table of what we have sent.

Google requires the id to be base32hex: characters `a-v` and `0-9`, 5 to 1024
long. A hex digest satisfies that by construction, which is why the key is
hashed rather than used raw (our ids contain characters outside that set).

What this executor will not do: invent a time. An event with no time is a
reminder, not a meeting, and guessing a slot puts a wrong appointment on a
real calendar - the single most expensive mistake this product can make.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core.logging import get_logger, log_event
from app.models.actions import Action, ActionType
from app.services.executors import ExecutionError
from app.services.google.oauth import (
    GoogleOAuthService,
    GoogleTokenError,
    describe_error,
)

logger = get_logger(__name__)

CALENDAR_API = "https://www.googleapis.com/calendar/v3"

#: How long an event lasts when the conversation only gave a start time.
#: An hour is the conventional default and is visibly editable in Calendar.
DEFAULT_DURATION = dt.timedelta(hours=1)


def event_id_for(action: Action) -> str:
    """A stable, Calendar-legal event id for this action.

    Derived from the idempotency key when there is one, else the action id -
    both are stable across retries of the same approval, which is the whole
    requirement.
    """
    basis = action.idempotency_key or action.id
    return hashlib.sha256(f"audaxity:{basis}".encode()).hexdigest()


class GoogleCalendarExecutor:
    """Creates a real calendar event. Falls back to nothing - it raises."""

    action_type = ActionType.CALENDAR_EVENT

    def __init__(
        self,
        *,
        oauth: GoogleOAuthService,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 30.0,
        calendar_id: str = "primary",
    ) -> None:
        self._oauth = oauth
        self._transport = transport
        self._timeout_s = timeout_s
        self._calendar_id = calendar_id

    async def execute(self, action: Action) -> str:
        start, end, tz = self._window(action)

        try:
            token = await self._oauth.access_token(action.user_id)
        except GoogleTokenError as exc:
            # Surfaced to the user as a failed action with a reconnect
            # prompt, which is honest. Silently recording it locally while
            # the card says "added to your calendar" would not be.
            raise ExecutionError(exc.message) from exc

        body = {
            "id": event_id_for(action),
            "summary": action.title,
            "description": self._description(action),
            "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end": {"dateTime": end.isoformat(), "timeZone": tz},
            "source": {"title": "Audaxity", "url": "https://audaxity.local"},
            # Reminders are the user's own Calendar defaults; overriding them
            # would be us deciding how loudly to interrupt someone.
            "reminders": {"useDefault": True},
        }

        url = f"{CALENDAR_API}/calendars/{self._calendar_id}/events"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as client:
                response = await client.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            raise ExecutionError(f"Could not reach Google Calendar: {exc}") from exc

        if response.status_code == httpx.codes.CONFLICT:
            # Already created by an earlier attempt. This is the idempotency
            # guarantee paying off, not an error.
            log_event(
                logger,
                "google.calendar.duplicate",
                action_id=action.id,
                user_id=action.user_id,
            )
            return (
                f"Already on your Google Calendar for "
                f"{start.strftime('%Y-%m-%d %H:%M')} ({tz})"
            )

        if response.status_code not in (httpx.codes.OK, httpx.codes.CREATED):
            raise ExecutionError(
                f"Google Calendar refused the event: {describe_error(response)}"
            )

        try:
            created = response.json()
        except ValueError:
            created = {}
        link = created.get("htmlLink", "") if isinstance(created, dict) else ""

        log_event(
            logger,
            "google.calendar.created",
            action_id=action.id,
            user_id=action.user_id,
        )
        suffix = f" — {link}" if link else ""
        return (
            f"Added to your Google Calendar for "
            f"{start.strftime('%Y-%m-%d %H:%M')} ({tz}){suffix}"
        )

    # --- helpers ----------------------------------------------------------

    def _window(self, action: Action) -> tuple[dt.datetime, dt.datetime, str]:
        raw_date = action.payload.get("date")
        raw_time = action.payload.get("time")
        if not raw_date or not raw_time:
            raise ExecutionError(
                "A calendar event needs both a date and a time. Edit the "
                "action to add them before approving it — a guessed slot on "
                "a real calendar is worse than no event."
            )
        try:
            day = dt.date.fromisoformat(raw_date)
            hour, minute = (int(part) for part in raw_time.split(":")[:2])
            clock = dt.time(hour, minute)
        except (ValueError, TypeError) as exc:
            raise ExecutionError(
                f"The action carries a date/time this executor cannot read: "
                f"{raw_date!r} {raw_time!r}"
            ) from exc

        tz_name = action.payload.get("tz") or "UTC"
        try:
            zone = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            # A bad timezone must not become a silently shifted appointment.
            raise ExecutionError(
                f"Unknown timezone {tz_name!r}; refusing to guess the offset."
            ) from None

        start = dt.datetime.combine(day, clock, tzinfo=zone)
        return start, start + DEFAULT_DURATION, tz_name

    @staticmethod
    def _description(action: Action) -> str:
        """The evidence travels with the event.

        Someone looking at this in Calendar three weeks later should be able
        to see what was said to produce it, not just trust it.
        """
        lines = [action.reason.strip()]
        if action.source_quote:
            lines.append("")
            lines.append(f"From the conversation: “{action.source_quote.strip()}”")
        if action.project:
            lines.append(f"Project: {action.project}")
        lines.append("")
        lines.append("Proposed by Audaxity and approved by you.")
        return "\n".join(lines)


class GoogleReminderExecutor:
    """A dated reminder becomes an all-day Calendar event.

    Google's own Reminders API was retired and folded into Tasks, and Tasks
    needs a separate scope for a worse result: a task has no time and shows up
    in a side panel most people never open. An all-day event on the calendar
    the user already looks at every morning is the better product answer, and
    it costs no extra permission.

    A reminder with no date resolves to nothing to schedule, so it stays
    local - there is no "someday" slot on a calendar, and inventing today
    would be a lie.
    """

    action_type = ActionType.REMINDER

    def __init__(
        self,
        *,
        oauth: GoogleOAuthService,
        local_fallback: object,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 30.0,
        calendar_id: str = "primary",
    ) -> None:
        self._oauth = oauth
        self._local = local_fallback
        self._transport = transport
        self._timeout_s = timeout_s
        self._calendar_id = calendar_id

    async def execute(self, action: Action) -> str:
        raw_date = action.payload.get("date")
        if not raw_date:
            # Nothing to put on a calendar. Record it and say so plainly.
            return await self._local.execute(action)  # type: ignore[attr-defined]

        if action.payload.get("time"):
            # It has a time after all, so it is an appointment, not an
            # all-day marker. Use the timed path.
            return await GoogleCalendarExecutor(
                oauth=self._oauth,
                transport=self._transport,
                timeout_s=self._timeout_s,
                calendar_id=self._calendar_id,
            ).execute(action)

        try:
            day = dt.date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ExecutionError(
                f"The reminder carries a date this executor cannot read: "
                f"{raw_date!r}"
            ) from exc

        try:
            token = await self._oauth.access_token(action.user_id)
        except GoogleTokenError as exc:
            raise ExecutionError(exc.message) from exc

        body = {
            "id": event_id_for(action),
            "summary": action.title,
            "description": GoogleCalendarExecutor._description(action),
            # An all-day event's `end` is exclusive, so a one-day event ends
            # the next day. Getting this wrong produces a zero-length event
            # that some clients simply do not render.
            "start": {"date": day.isoformat()},
            "end": {"date": (day + dt.timedelta(days=1)).isoformat()},
            "transparency": "transparent",  # does not make the user look busy
            "source": {"title": "Audaxity", "url": "https://audaxity.local"},
            "reminders": {"useDefault": True},
        }

        url = f"{CALENDAR_API}/calendars/{self._calendar_id}/events"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as client:
                response = await client.post(
                    url, json=body, headers={"Authorization": f"Bearer {token}"}
                )
        except httpx.HTTPError as exc:
            raise ExecutionError(f"Could not reach Google Calendar: {exc}") from exc

        if response.status_code == httpx.codes.CONFLICT:
            return f"Already on your Google Calendar for {day.isoformat()}"
        if response.status_code not in (httpx.codes.OK, httpx.codes.CREATED):
            raise ExecutionError(
                f"Google Calendar refused the reminder: {describe_error(response)}"
            )

        log_event(
            logger,
            "google.calendar.reminder_created",
            action_id=action.id,
            user_id=action.user_id,
        )
        return f"All-day reminder added to your Google Calendar for {day.isoformat()}"
