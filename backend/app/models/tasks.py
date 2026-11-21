"""Tasks: commitments the user is on the hook for.

A task is created only from an *actionable* speech act - a commitment, an
assignment or a requirement. Suggestions and open questions never become
tasks, because telling someone they owe work they never agreed to is the
fastest way to make an assistant untrustworthy.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import Field

from app.models.analysis import DateResolution, SpeechAct
from app.models.domain import StoredModel


class TaskStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    CANCELLED = "cancelled"


class Task(StoredModel):
    user_id: str
    title: str
    owner: str
    act: SpeechAct
    status: TaskStatus = TaskStatus.PENDING
    project: str | None = None

    #: The timezone of the conversation that produced this task. Overdue is
    #: judged in the user's own day, not UTC's.
    #: The resolved `Person`, when the owner string denoted somebody we
    #: could identify. `None` for "unknown", "group" and for a name seen for
    #: the first time in a conversation we could not attribute - all of which
    #: are correct answers, not gaps.
    owner_person_id: str | None = None

    tz: str = "UTC"
    due_date: dt.date | None = None
    due_time: dt.time | None = None
    due_raw: str | None = None
    due_resolution: DateResolution = DateResolution.NONE

    # --- provenance ---
    source_session_ids: list[str] = Field(default_factory=list)
    source_quotes: list[str] = Field(default_factory=list)

    #: Stable identity across conversations, so re-discussing the same
    #: commitment updates one task instead of creating a second.
    dedupe_key: str = ""

    completed_at: dt.datetime | None = None
    edited: bool = False

    @property
    def needs_clarification(self) -> bool:
        """A deadline was implied but could not be pinned down."""
        return self.due_resolution is DateResolution.AMBIGUOUS

    @property
    def is_open(self) -> bool:
        return self.status is TaskStatus.PENDING

    def is_overdue(self, today: dt.date) -> bool:
        """Overdue only when a date was resolved confidently.

        An ambiguous "next week" must never be reported as overdue: we do not
        know when it was due, so we cannot claim it has passed.
        """
        if not self.is_open or self.due_date is None:
            return False
        if self.due_resolution is not DateResolution.CONFIDENT:
            return False
        return self.due_date < today
