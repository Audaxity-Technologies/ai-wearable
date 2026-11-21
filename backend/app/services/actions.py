"""Action engine: conversations become *proposed* actions.

This module prepares work and stops. It never executes anything, and it has no
access to an executor - that separation is deliberate, so a bug here cannot
send an email. Execution lives in `services/approval.py` and only runs after
explicit user approval.

Action type is chosen deterministically from what the conversation actually
established:

- a confident date **and** a time  -> calendar event (something happens at a moment)
- a confident date, no time        -> reminder
- an ambiguous date                -> reminder flagged `needs_clarification`
- no date at all                   -> plain task

`EMAIL_DRAFT` is defined but never proposed in phase 5: drafting a body needs
an LLM call and sending needs Gmail, both of which belong to phase 10.
"""

from __future__ import annotations

from app.core.logging import get_logger, log_event
from app.core.text import normalise
from app.models.actions import Action, ActionStatus, ActionType
from app.models.analysis import (
    ACTIONABLE_ACTS,
    AnalysisItem,
    ConversationAnalysis,
    DateResolution,
    SpeechAct,
)
from app.models.domain import Session
from app.models.tasks import Task
from app.repositories.base import Repository

logger = get_logger(__name__)

#: An action already in one of these states must not be proposed again.
LIVE_STATUSES = frozenset(
    {
        ActionStatus.PROPOSED,
        ActionStatus.APPROVED,
        ActionStatus.EXECUTING,
        ActionStatus.EXECUTED,
    }
)

MIN_ACTION_CONFIDENCE = 0.4


def action_dedupe_key(action_type: ActionType, title: str, owner: str) -> str:
    return f"{action_type.value}|{normalise(title)}|{normalise(owner)}"


class ActionEngine:
    def __init__(self, *, actions: Repository[Action]) -> None:
        self._actions = actions

    async def propose_from_analysis(
        self,
        session: Session,
        analysis: ConversationAnalysis,
        tasks: list[Task],
    ) -> list[Action]:
        if analysis.degraded:
            return []

        existing = await self._actions.list(user_id=session.user_id)
        live_keys = {
            action.dedupe_key
            for action in existing
            if action.dedupe_key and action.status in LIVE_STATUSES
        }
        # Rejected actions are not re-proposed either: the user already said
        # no, and asking again every time the topic comes up is nagging.
        rejected_keys = {
            action.dedupe_key
            for action in existing
            if action.dedupe_key and action.status is ActionStatus.REJECTED
        }

        tasks_by_key = {task.dedupe_key: task for task in tasks}
        proposed: list[Action] = []

        for item in analysis.items:
            if item.act not in ACTIONABLE_ACTS:
                continue
            if item.confidence < MIN_ACTION_CONFIDENCE:
                continue

            action_type = self._action_type(item)
            key = action_dedupe_key(action_type, item.title, item.owner)
            if key in live_keys or key in rejected_keys:
                continue

            action = self._build(item, session, action_type, key, tasks_by_key)
            await self._actions.put(action)
            live_keys.add(key)
            proposed.append(action)

        if proposed:
            log_event(
                logger,
                "actions.proposed",
                session_id=session.id,
                count=len(proposed),
                types=sorted({a.type.value for a in proposed}),
                needs_clarification=sum(
                    1 for a in proposed if a.needs_clarification
                ),
            )
        return proposed

    @staticmethod
    def _action_type(item: AnalysisItem) -> ActionType:
        if item.due.resolution is DateResolution.CONFIDENT and item.due.date:
            if item.due.time is not None:
                return ActionType.CALENDAR_EVENT
            return ActionType.REMINDER
        if item.due.resolution is DateResolution.AMBIGUOUS:
            return ActionType.REMINDER
        return ActionType.TASK

    def _build(
        self,
        item: AnalysisItem,
        session: Session,
        action_type: ActionType,
        key: str,
        tasks_by_key: dict[str, Task],
    ) -> Action:
        from app.services.tasks import task_dedupe_key

        task = tasks_by_key.get(
            task_dedupe_key(item.title, item.owner, item.project)
        )

        payload: dict[str, str] = {"owner": item.owner, "act": item.act.value}
        if item.project:
            payload["project"] = item.project
        if item.due.date:
            payload["date"] = item.due.date.isoformat()
        if item.due.time:
            payload["time"] = item.due.time.strftime("%H:%M")
        if item.due.raw:
            payload["due_raw"] = item.due.raw
        payload["tz"] = session.tz

        return Action(
            user_id=session.user_id,
            type=action_type,
            title=item.title,
            reason=self._reason(item, action_type),
            payload=payload,
            source_session_id=session.id,
            source_quote=item.source_quote,
            task_id=task.id if task else None,
            project=item.project,
            needs_clarification=(
                item.due.resolution is DateResolution.AMBIGUOUS
            ),
            dedupe_key=key,
        )

    @staticmethod
    def _reason(item: AnalysisItem, action_type: ActionType) -> str:
        """Why this action exists, in the user's own terms.

        Shown on the approval card. It quotes the conversation rather than
        asserting something the user has to take on trust.
        """
        if item.act is SpeechAct.COMMITMENT:
            who = "You" if item.owner.lower() in {"unknown", "group"} else item.owner
            basis = f"{who} committed to this"
        elif item.act is SpeechAct.ASSIGNMENT:
            basis = f"This was assigned to {item.owner}"
        else:
            basis = "This was stated as something that needs to happen"

        when = ""
        if item.due.resolution is DateResolution.CONFIDENT and item.due.date:
            when = f", due {item.due.date.isoformat()}"
        elif item.due.resolution is DateResolution.AMBIGUOUS:
            raw = item.due.raw or "an unclear date"
            when = f", due {raw!r} — the exact date needs confirming"

        kind = {
            ActionType.CALENDAR_EVENT: "a calendar event",
            ActionType.REMINDER: "a reminder",
            ActionType.TASK: "a task",
            ActionType.EMAIL_DRAFT: "an email draft",
        }[action_type]

        return f"{basis}{when}. Proposing {kind}."
