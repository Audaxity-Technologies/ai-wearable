"""Action executors.

Phase 5 executes locally: an approved action is recorded as done in our own
store, and the app surfaces it. Google Calendar and Gmail drivers satisfy the
same Protocol in phase 10, so approval, idempotency and history do not change
when real side effects arrive.

Being explicit about what "executed" means today: a reminder or calendar event
is **recorded, not sent anywhere**. Nothing leaves the machine.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import datetime as dt

from app.core.errors import AudaxityError
from app.models.actions import Action, ActionType
from app.models.analysis import DateResolution
from app.models.tasks import Task, TaskStatus
from app.repositories.base import Repository


def _due_from(action: Action) -> tuple[dt.date | None, dt.time | None]:
    """Parse the date/time an action carries, tolerating a malformed payload."""
    due_date = due_time = None
    raw_date = action.payload.get("date")
    raw_time = action.payload.get("time")
    if raw_date:
        try:
            due_date = dt.date.fromisoformat(raw_date)
        except ValueError:
            due_date = None
    if raw_time:
        try:
            hour, minute = raw_time.split(":")
            due_time = dt.time(int(hour), int(minute))
        except (ValueError, TypeError):
            due_time = None
    return due_date, due_time


class ExecutionError(AudaxityError):
    """An approved action could not be carried out."""

    status_code = 502
    code = "execution_failed"


@runtime_checkable
class ActionExecutor(Protocol):
    action_type: ActionType

    async def execute(self, action: Action) -> str:
        """Perform the action, returning a short human-readable result."""
        ...


class LocalReminderExecutor:
    action_type = ActionType.REMINDER

    async def execute(self, action: Action) -> str:
        when = action.payload.get("date")
        if not when:
            raw = action.payload.get("due_raw")
            return (
                f"Reminder recorded without a date "
                f"({raw!r} still needs confirming)"
                if raw
                else "Reminder recorded without a date"
            )
        at = f"{when} {action.payload.get('time', '')}".strip()
        return f"Reminder recorded for {at} ({action.payload.get('tz', 'UTC')})"


class LocalCalendarExecutor:
    action_type = ActionType.CALENDAR_EVENT

    async def execute(self, action: Action) -> str:
        date = action.payload.get("date")
        time = action.payload.get("time")
        if not date or not time:
            # Refuse rather than invent a slot.
            raise ExecutionError(
                "A calendar event needs both a date and a time; "
                "edit the action before approving it."
            )
        return (
            f"Calendar event recorded for {date} {time} "
            f"({action.payload.get('tz', 'UTC')}). Not yet sent to Google "
            "Calendar — that arrives in phase 10."
        )


class LocalTaskExecutor:
    """Confirms the linked task, or creates one if the action has none."""

    action_type = ActionType.TASK

    def __init__(self, *, tasks: Repository[Task]) -> None:
        self._tasks = tasks

    async def execute(self, action: Action) -> str:
        due_date, due_time = _due_from(action)

        if action.task_id:
            task = await self._tasks.get(action.task_id)
            if task is not None:
                # Carry across any date the user added while editing, which
                # would otherwise be silently discarded.
                if due_date and task.due_date != due_date:
                    task.due_date = due_date
                    task.due_time = due_time
                    task.due_resolution = DateResolution.CONFIDENT
                    task.touch()
                    await self._tasks.put(task)
                suffix = f" (due {due_date.isoformat()})" if due_date else ""
                return f"Task confirmed: {task.title}{suffix}"

        task = Task(
            user_id=action.user_id,
            title=action.title,
            owner=action.payload.get("owner", "unknown"),
            act=action.payload.get("act", "requirement"),  # type: ignore[arg-type]
            project=action.project,
            tz=action.payload.get("tz", "UTC"),
            due_date=due_date,
            due_time=due_time,
            due_resolution=(
                DateResolution.CONFIDENT if due_date else DateResolution.NONE
            ),
            source_session_ids=[action.source_session_id],
            source_quotes=[action.source_quote],
            status=TaskStatus.PENDING,
        )
        await self._tasks.put(task)
        action.task_id = task.id
        suffix = f" (due {due_date.isoformat()})" if due_date else ""
        return f"Task created: {task.title}{suffix}"


def build_executors(*, tasks: Repository[Task]) -> dict[ActionType, ActionExecutor]:
    """Registry of what can actually be executed.

    `EMAIL_DRAFT` is deliberately absent: approving one fails loudly rather
    than silently pretending an email was prepared.
    """
    return {
        ActionType.REMINDER: LocalReminderExecutor(),
        ActionType.CALENDAR_EVENT: LocalCalendarExecutor(),
        ActionType.TASK: LocalTaskExecutor(tasks=tasks),
    }


# --- phase 10: dispatching between real and local execution -----------------


class GoogleOrLocalExecutor:
    """Prefer the real side effect; degrade honestly when there is none.

    The rule, which is a product decision and not a technical one:

    - **No Google account connected** -> run the local executor. The user
      never asked for a calendar write, so recording it is the whole promise
      and the result line says so.
    - **An account connected** -> run the Google executor, *including when
      the stored authorisation has gone stale*. It then fails with a
      reconnect prompt. Falling back to local here would be the dishonest
      path: the user connected a calendar, so a card reading "recorded"
      when nothing reached their calendar is a lie they would only discover
      by missing the meeting.

    That asymmetry is the point. Silence is acceptable when nothing was
    promised, and never acceptable once something was.
    """

    def __init__(
        self,
        *,
        action_type: ActionType,
        remote: ActionExecutor,
        local: ActionExecutor,
        oauth: object,
    ) -> None:
        self.action_type = action_type
        self._remote = remote
        self._local = local
        self._oauth = oauth

    async def execute(self, action: Action) -> str:
        account = await self._oauth.for_user(action.user_id)  # type: ignore[attr-defined]
        if account is None:
            return await self._local.execute(action)
        return await self._remote.execute(action)


class RequiresGoogleExecutor:
    """For action types with no local equivalent, i.e. email drafts.

    There is no sensible local version of "draft an email": recording the
    intention to write one is not writing one. So this refuses clearly
    instead, which is what the phase 5 code did for every email draft.
    """

    def __init__(self, *, action_type: ActionType, remote: ActionExecutor, oauth: object) -> None:
        self.action_type = action_type
        self._remote = remote
        self._oauth = oauth

    async def execute(self, action: Action) -> str:
        account = await self._oauth.for_user(action.user_id)  # type: ignore[attr-defined]
        if account is None:
            raise ExecutionError(
                "Drafting an email needs a connected Google account. "
                "Connect one in Settings, then approve this again."
            )
        return await self._remote.execute(action)


def build_google_executors(
    *,
    tasks: Repository[Task],
    oauth: object,
    llm: object | None = None,
    transport: object | None = None,
    enable_gmail: bool = False,
) -> dict[ActionType, ActionExecutor]:
    """Executor registry with Google wired in behind the local ones.

    Imported lazily so the Google package is not a hard dependency of the
    local-only path, and so a deployment with no OAuth credentials behaves
    exactly as it did in phase 5.

    `enable_gmail` is off by default and the Gmail executor is **not built**
    when it is off - not merely unreachable. With no mail scope requested
    there is no token that could send, which is the only form of "cannot send
    email" this product can honestly claim; see `services/google/gmail.py`
    for the correction that led here. Email-draft approvals then fall to the
    local executor, which records the draft in Audaxity and touches nobody's
    mailbox.
    """
    from app.services.google import (  # noqa: PLC0415 - deliberate, see above
        GmailDraftExecutor,
        GoogleCalendarExecutor,
        GoogleReminderExecutor,
    )

    local = build_executors(tasks=tasks)
    local_reminder = local[ActionType.REMINDER]

    calendar = GoogleCalendarExecutor(oauth=oauth, transport=transport)  # type: ignore[arg-type]
    reminder = GoogleReminderExecutor(
        oauth=oauth,  # type: ignore[arg-type]
        local_fallback=local_reminder,
        transport=transport,  # type: ignore[arg-type]
    )
    gmail = (
        GmailDraftExecutor(
            oauth=oauth,  # type: ignore[arg-type]
            llm=llm,  # type: ignore[arg-type]
            transport=transport,  # type: ignore[arg-type]
        )
        if enable_gmail
        else None
    )

    registry: dict[ActionType, ActionExecutor] = {
        ActionType.TASK: local[ActionType.TASK],
        ActionType.CALENDAR_EVENT: GoogleOrLocalExecutor(
            action_type=ActionType.CALENDAR_EVENT,
            remote=calendar,
            local=local[ActionType.CALENDAR_EVENT],
            oauth=oauth,
        ),
        ActionType.REMINDER: GoogleOrLocalExecutor(
            action_type=ActionType.REMINDER,
            remote=reminder,
            local=local_reminder,
            oauth=oauth,
        ),
    }

    if gmail is not None:
        registry[ActionType.EMAIL_DRAFT] = RequiresGoogleExecutor(
            action_type=ActionType.EMAIL_DRAFT, remote=gmail, oauth=oauth
        )
    # With Gmail off, EMAIL_DRAFT stays absent exactly as in `build_executors`:
    # approving one fails loudly rather than silently pretending an email was
    # prepared. That was already the deliberate local-only behaviour.
    return registry
