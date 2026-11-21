"""Proposed actions and the approval state machine.

The product's signature behaviour: the assistant *prepares* actions and asks
before doing anything consequential. Two rules are enforced structurally
rather than by convention:

1. **Nothing executes without explicit approval.** `EXECUTING` is reachable
   only from `APPROVED`, and only `approve()` performs that transition.
2. **Nothing executes twice.** Approval mints an `idempotency_key`, and the
   approval service holds a per-action lock, so a double-tap or a retried
   request cannot send two emails or create two calendar events.

Every transition is appended to `history`, so "who approved this and when" is
always answerable.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.errors import InvalidTransitionError
from app.models.domain import StoredModel


class ActionType(StrEnum):
    REMINDER = "reminder"
    CALENDAR_EVENT = "calendar_event"
    TASK = "task"
    #: Reserved. Not proposed in phase 5: drafting a body needs an LLM call
    #: and sending needs Gmail, both of which arrive in phase 10.
    EMAIL_DRAFT = "email_draft"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"


_ACTION_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.PROPOSED: frozenset(
        {ActionStatus.APPROVED, ActionStatus.REJECTED}
    ),
    # Only approval opens the door to execution.
    ActionStatus.APPROVED: frozenset({ActionStatus.EXECUTING}),
    ActionStatus.EXECUTING: frozenset(
        {ActionStatus.EXECUTED, ActionStatus.FAILED}
    ),
    # A failed execution can be approved again for another attempt.
    ActionStatus.FAILED: frozenset({ActionStatus.APPROVED}),
    ActionStatus.EXECUTED: frozenset(),
    ActionStatus.REJECTED: frozenset(),
}

#: Statuses from which an action may no longer be edited.
LOCKED_STATUSES = frozenset(
    {
        ActionStatus.APPROVED,
        ActionStatus.EXECUTING,
        ActionStatus.EXECUTED,
        ActionStatus.REJECTED,
    }
)


def can_transition(current: ActionStatus, target: ActionStatus) -> bool:
    return target in _ACTION_TRANSITIONS[current]


def assert_transition(current: ActionStatus, target: ActionStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(
            f"Cannot move action from {current.value!r} to {target.value!r}"
        )


class ActionEvent(BaseModel):
    at: dt.datetime
    event: str
    detail: str | None = None


class Action(StoredModel):
    user_id: str
    type: ActionType
    title: str
    status: ActionStatus = ActionStatus.PROPOSED

    #: Plain-language justification shown on the approval card. The brief
    #: requires every proposed action to carry why it exists.
    reason: str
    payload: dict[str, str] = Field(default_factory=dict)

    # --- provenance ---
    source_session_id: str
    source_quote: str
    task_id: str | None = None
    project: str | None = None

    #: True when the date could not be resolved confidently. The app asks
    #: rather than scheduling a guess.
    needs_clarification: bool = False

    #: Prevents re-proposing the same action from a later conversation.
    dedupe_key: str = ""

    edited: bool = False
    edited_at: dt.datetime | None = None
    decided_at: dt.datetime | None = None
    executed_at: dt.datetime | None = None
    rejection_reason: str | None = None
    execution_result: str | None = None

    #: Minted at approval; carried into execution so a retry is a no-op.
    idempotency_key: str | None = None

    history: list[ActionEvent] = Field(default_factory=list)

    @property
    def is_pending(self) -> bool:
        return self.status is ActionStatus.PROPOSED

    @property
    def is_editable(self) -> bool:
        return self.status not in LOCKED_STATUSES

    def record(self, event: str, detail: str | None = None, *, at: dt.datetime) -> None:
        self.history.append(ActionEvent(at=at, event=event, detail=detail))
