"""Daily briefing and wake requests.

Two user-facing features share this module because both are "the backend
decides it is time to interrupt you":

- **Wake request** - something outside the app asks you to start recording.
  It sends an interrupting notification; it does NOT start the microphone.
  Starting recording silently from a remote trigger would be covert
  recording, which the product explicitly forbids and which Android blocks
  from the background anyway. The notification is the wake-up call; the tap
  is the consent.

- **Daily briefing** - a morning summary of what is due, what is overdue and
  what still needs a date confirmed, so the day starts with the things that
  would otherwise be forgotten.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.core.logging import get_logger, log_event
from app.core.time_utils import utc_now
from app.models.tasks import Task, TaskStatus
from app.models.users import User
from app.services.auth import AuthService
from app.services.notifications import (
    NotificationKind,
    PushMessage,
    PushResult,
)
from app.services.tasks import TaskService

logger = get_logger(__name__)


class Briefing(BaseModel):
    """What the user needs to know this morning."""

    user_id: str
    local_date: dt.date
    greeting: str
    due_today: list[str] = Field(default_factory=list)
    overdue: list[str] = Field(default_factory=list)
    needs_clarification: list[str] = Field(default_factory=list)
    pending_approvals: int = 0

    @property
    def is_empty(self) -> bool:
        return not (
            self.due_today
            or self.overdue
            or self.needs_clarification
            or self.pending_approvals
        )

    def headline(self) -> str:
        if self.is_empty:
            return "Nothing on your plate"
        bits: list[str] = []
        if self.due_today:
            bits.append(f"{len(self.due_today)} due today")
        if self.overdue:
            bits.append(f"{len(self.overdue)} overdue")
        if self.pending_approvals:
            bits.append(f"{self.pending_approvals} to approve")
        if self.needs_clarification:
            bits.append(f"{len(self.needs_clarification)} need a date")
        return " · ".join(bits)

    def body(self) -> str:
        if self.is_empty:
            return "No commitments recorded for today."
        lines: list[str] = []
        for title in self.overdue[:3]:
            lines.append(f"Overdue: {title}")
        for title in self.due_today[:3]:
            lines.append(f"Today: {title}")
        for title in self.needs_clarification[:2]:
            lines.append(f"Needs a date: {title}")
        remaining = (
            len(self.overdue) + len(self.due_today) + len(self.needs_clarification)
        ) - len(lines)
        if remaining > 0:
            lines.append(f"and {remaining} more")
        return "\n".join(lines)


class BriefingService:
    def __init__(
        self,
        *,
        auth: AuthService,
        tasks: TaskService,
        actions,  # ApprovalService; imported lazily to avoid a cycle
        push,  # ExpoPushDriver | NullPushDriver
    ) -> None:
        self._auth = auth
        self._tasks = tasks
        self._actions = actions
        self._push = push

    # --- briefing ---------------------------------------------------------

    async def build(self, user: User) -> Briefing:
        local_now = utc_now().astimezone(self._zone(user.tz))
        today = local_now.date()

        open_tasks = await self._tasks.list_tasks(
            user_id=user.id, status=TaskStatus.PENDING
        )
        pending = await self._actions.list_pending(user_id=user.id)

        return Briefing(
            user_id=user.id,
            local_date=today,
            greeting=self._greeting(local_now, user.display_name),
            due_today=[t.title for t in open_tasks if t.due_date == today],
            overdue=[t.title for t in open_tasks if self._tasks.is_overdue(t)],
            needs_clarification=[
                t.title for t in open_tasks if t.needs_clarification
            ],
            pending_approvals=len(pending),
        )

    async def send_briefing(self, user: User) -> PushResult:
        briefing = await self.build(user)
        tokens = await self._auth.push_tokens_for(user.id)
        if not tokens:
            log_event(logger, "briefing.no_devices", user_id=user.id)
            return PushResult()

        result = await self._push.send(
            PushMessage(
                to=tokens,
                title=f"{briefing.greeting} — {briefing.headline()}",
                body=briefing.body(),
                data={
                    "kind": NotificationKind.DAILY_BRIEFING.value,
                    "route": "/tasks",
                },
            )
        )
        await self._prune(result)
        log_event(
            logger,
            "briefing.sent",
            user_id=user.id,
            due_today=len(briefing.due_today),
            overdue=len(briefing.overdue),
            sent=result.sent,
            failed=result.failed,
        )
        return result

    async def due_for_briefing(self, user: User, *, now: dt.datetime | None = None) -> bool:
        """True when the user's local clock has just passed their briefing time.

        A five-minute window, so a scheduler that ticks every minute fires
        once rather than not at all if a tick is missed.
        """
        if not user.briefing_enabled:
            return False
        local = (now or utc_now()).astimezone(self._zone(user.tz))
        hour, minute = (int(part) for part in user.briefing_time.split(":"))
        target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta = (local - target).total_seconds()
        return 0 <= delta < 300

    # --- wake request -----------------------------------------------------

    async def send_wake_request(
        self, user: User, *, reason: str = "", source: str = "api"
    ) -> PushResult:
        """Ask the user to start recording. Never starts it.

        The notification interrupts; tapping it opens the app ready to
        record, and the user still presses the button. That tap is the
        consent, and it is what keeps this from being covert recording.
        """
        tokens = await self._auth.push_tokens_for(user.id)
        if not tokens:
            return PushResult()

        detail = reason.strip() or "Tap to start recording."
        result = await self._push.send(
            PushMessage(
                to=tokens,
                title="Start recording?",
                body=detail,
                data={
                    "kind": NotificationKind.WAKE_REQUEST.value,
                    "route": "/",
                    "arm_recording": "true",
                    "reason": detail,
                },
                sound="default",
                priority="high",
                channel_id="wake",
            )
        )
        await self._prune(result)
        log_event(
            logger,
            "wake.requested",
            user_id=user.id,
            source=source,
            devices=len(tokens),
            sent=result.sent,
        )
        return result

    async def notify_actions_ready(self, user_id: str, count: int) -> PushResult:
        """Tell the user a conversation finished and needs review."""
        if count <= 0:
            return PushResult()
        tokens = await self._auth.push_tokens_for(user_id)
        if not tokens:
            return PushResult()

        result = await self._push.send(
            PushMessage(
                to=tokens,
                title="Conversation processed",
                body=(
                    f"{count} action{'' if count == 1 else 's'} waiting for "
                    "your approval."
                ),
                data={
                    "kind": NotificationKind.ACTIONS_READY.value,
                    "route": "/approvals",
                },
            )
        )
        await self._prune(result)
        return result

    # --- helpers ----------------------------------------------------------

    async def _prune(self, result: PushResult) -> None:
        """Clear push tokens Expo reports as dead."""
        for token in result.invalid_tokens:
            for device in await self._auth._devices.list(push_token=token):  # noqa: SLF001
                device.push_token = None
                device.touch()
                await self._auth._devices.put(device)  # noqa: SLF001
            log_event(logger, "push.token_pruned")

    @staticmethod
    def _zone(tz: str) -> ZoneInfo:
        try:
            return ZoneInfo(tz)
        except Exception:  # pragma: no cover - defensive
            return ZoneInfo("UTC")

    @staticmethod
    def _greeting(local: dt.datetime, name: str) -> str:
        hour = local.hour
        part = (
            "Good morning"
            if hour < 12
            else "Good afternoon"
            if hour < 18
            else "Good evening"
        )
        return f"{part}, {name}" if name else part
