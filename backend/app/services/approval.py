"""Approval workflow.

The guarantees this module is responsible for:

- **Nothing executes without explicit approval.** Execution is reachable only
  through `approve()`, and the action state machine permits `EXECUTING` only
  from `APPROVED`.
- **Nothing executes twice.** Each action has a lock and an idempotency key
  minted at approval. Approving an already-executed action returns the
  original result instead of running it again, so a double-tap or a retried
  HTTP request is harmless.
- **Rejections stick.** A rejected action is terminal, and the action engine
  will not re-propose it from a later conversation.
- **Edits are only possible before a decision**, and an edited action records
  that it was edited.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections import defaultdict

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.ids import new_id
from app.core.logging import get_logger, log_event
from app.core.time_utils import utc_now
from app.models.actions import (
    Action,
    ActionStatus,
    ActionType,
    assert_transition,
)
from app.repositories.base import Repository
from app.services.executors import ActionExecutor, ExecutionError

logger = get_logger(__name__)


class ApprovalService:
    def __init__(
        self,
        *,
        actions: Repository[Action],
        executors: dict[ActionType, ActionExecutor],
    ) -> None:
        self._actions = actions
        self._executors = executors
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # --- reading ----------------------------------------------------------

    async def get(self, action_id: str) -> Action:
        action = await self._actions.get(action_id)
        if action is None:
            raise NotFoundError(f"Action not found: {action_id}")
        return action

    async def list_actions(
        self,
        *,
        user_id: str | None = None,
        status: ActionStatus | None = None,
        session_id: str | None = None,
    ) -> list[Action]:
        filters: dict[str, object] = {}
        if user_id:
            filters["user_id"] = user_id
        if status:
            filters["status"] = status
        if session_id:
            filters["source_session_id"] = session_id
        actions = await self._actions.list(**filters)
        return sorted(actions, key=lambda a: a.created_at, reverse=True)

    async def list_pending(self, *, user_id: str | None = None) -> list[Action]:
        return await self.list_actions(
            user_id=user_id, status=ActionStatus.PROPOSED
        )

    # --- decisions --------------------------------------------------------

    async def edit(
        self,
        action_id: str,
        *,
        title: str | None = None,
        date: dt.date | None = None,
        time: dt.time | None = None,
        action_type: ActionType | None = None,
    ) -> Action:
        """Amend a proposed action before deciding on it."""
        async with self._locks[action_id]:
            action = await self.get(action_id)
            if not action.is_editable:
                raise ConflictError(
                    f"Action is {action.status.value} and can no longer be edited"
                )

            if title is not None:
                if not title.strip():
                    raise ValidationError("title must not be blank")
                action.title = title.strip()
            if date is not None:
                action.payload["date"] = date.isoformat()
                # A date the user set is theirs: it no longer needs confirming.
                action.needs_clarification = False
                action.payload.pop("due_raw", None)
            if time is not None:
                action.payload["time"] = time.strftime("%H:%M")

            if action_type is not None:
                action.type = action_type
            elif date is not None or time is not None:
                # Adding a date to a plain task means "remind me then", and
                # adding a time means "put it in the calendar". Without this
                # the executor would quietly discard the detail the user just
                # supplied.
                action.type = self._type_for(action.payload, action.type)

            action.edited = True
            action.edited_at = utc_now()
            action.record("edited", at=action.edited_at)
            action.touch()
            await self._actions.put(action)

        log_event(logger, "action.edited", action_id=action_id)
        return action

    @staticmethod
    def _type_for(payload: dict[str, str], current: ActionType) -> ActionType:
        """Re-derive the action type after a user edit adds date/time detail.

        Mirrors the action engine's rule. Email drafts are never re-derived.
        """
        if current is ActionType.EMAIL_DRAFT:
            return current
        if payload.get("date") and payload.get("time"):
            return ActionType.CALENDAR_EVENT
        if payload.get("date"):
            return ActionType.REMINDER
        return current

    async def reject(
        self, action_id: str, *, reason: str | None = None
    ) -> Action:
        async with self._locks[action_id]:
            action = await self.get(action_id)
            if action.status is ActionStatus.REJECTED:
                return action  # idempotent
            assert_transition(action.status, ActionStatus.REJECTED)

            now = utc_now()
            action.status = ActionStatus.REJECTED
            action.decided_at = now
            action.rejection_reason = reason
            action.record("rejected", reason, at=now)
            action.touch()
            await self._actions.put(action)

        log_event(logger, "action.rejected", action_id=action_id)
        return action

    async def approve(self, action_id: str) -> Action:
        """Approve and execute. Safe to call more than once.

        The lock is held across approval *and* execution, so two concurrent
        approvals cannot both reach the executor.
        """
        async with self._locks[action_id]:
            action = await self.get(action_id)

            if action.status is ActionStatus.EXECUTED:
                # Already done. Return the original outcome untouched.
                return action
            if action.status in (ActionStatus.APPROVED, ActionStatus.EXECUTING):
                return action
            if action.status is ActionStatus.REJECTED:
                raise ConflictError(
                    "Action was rejected and cannot be approved; "
                    "it would have to be proposed again."
                )

            now = utc_now()
            assert_transition(action.status, ActionStatus.APPROVED)
            action.status = ActionStatus.APPROVED
            action.decided_at = now
            action.idempotency_key = action.idempotency_key or new_id()
            action.record("approved", at=now)
            action.touch()
            await self._actions.put(action)

            return await self._execute(action)

    async def _execute(self, action: Action) -> Action:
        """Run an approved action. Caller must hold the action's lock."""
        executor = self._executors.get(action.type)
        now = utc_now()

        assert_transition(action.status, ActionStatus.EXECUTING)
        action.status = ActionStatus.EXECUTING
        action.record("executing", at=now)
        await self._actions.put(action)

        if executor is None:
            return await self._fail(
                action,
                f"No executor for action type {action.type.value!r}. "
                "Email drafting and sending arrive in phase 10.",
            )

        try:
            result = await executor.execute(action)
        except ExecutionError as exc:
            return await self._fail(action, exc.message)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("executor crashed for action %s", action.id)
            return await self._fail(action, repr(exc))

        finished = utc_now()
        assert_transition(action.status, ActionStatus.EXECUTED)
        action.status = ActionStatus.EXECUTED
        action.executed_at = finished
        action.execution_result = result
        action.record("executed", result, at=finished)
        action.touch()
        await self._actions.put(action)

        log_event(
            logger,
            "action.executed",
            action_id=action.id,
            action_type=action.type.value,
            idempotency_key=action.idempotency_key,
        )
        return action

    async def _fail(self, action: Action, reason: str) -> Action:
        now = utc_now()
        assert_transition(action.status, ActionStatus.FAILED)
        action.status = ActionStatus.FAILED
        action.execution_result = reason[:500]
        action.record("failed", reason[:500], at=now)
        action.touch()
        await self._actions.put(action)
        log_event(
            logger,
            "action.failed",
            action_id=action.id,
            action_type=action.type.value,
            reason=reason[:200],
        )
        return action

    # --- maintenance ------------------------------------------------------

    async def delete_for_session(self, session_id: str) -> int:
        """Remove a conversation's actions.

        Executed actions are kept: they are a record of something that already
        happened, and erasing that would misrepresent history. Only undecided
        proposals are removed.
        """
        removed = 0
        for action in await self._actions.list(source_session_id=session_id):
            if action.status in (ActionStatus.EXECUTED, ActionStatus.EXECUTING):
                continue
            await self._actions.delete(action.id)
            removed += 1
        return removed
