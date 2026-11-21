"""Conversation analysis schema.

Two layers on purpose:

- `RawAnalysis` is what the LLM is asked to produce. It carries the *verbatim*
  due phrase ("by Friday") and never a calculated date, because LLMs are
  unreliable at date arithmetic.
- `ConversationAnalysis` is what we store, after Python has resolved dates and
  verified every quote against the transcript.

Keeping them separate is what lets the analysis service drop or downgrade items
without the LLM getting a say in it.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.models.domain import StoredModel


class SpeechAct(StrEnum):
    """Why an utterance matters. The distinction the product is built on.

    COMMITMENT  "I'll send the report."      -> the speaker took it on
    ASSIGNMENT  "You should send it."        -> directed at someone else
    SUGGESTION  "Maybe we should send it."   -> floated, not agreed
    DECISION    "We're going with option B." -> a settled choice
    REQUIREMENT "We need to send it."        -> must happen, owner may be unknown
    QUESTION    "Should we send it?"         -> open question
    DISCUSSION  anything else worth keeping
    """

    COMMITMENT = "commitment"
    ASSIGNMENT = "assignment"
    SUGGESTION = "suggestion"
    DECISION = "decision"
    REQUIREMENT = "requirement"
    QUESTION = "question"
    DISCUSSION = "discussion"


#: Acts that should become a task/action proposal. A suggestion or an open
#: question must not silently turn into someone's to-do.
ACTIONABLE_ACTS = frozenset(
    {SpeechAct.COMMITMENT, SpeechAct.ASSIGNMENT, SpeechAct.REQUIREMENT}
)


class DateResolution(StrEnum):
    CONFIDENT = "confident"  # unambiguous, safe to put on a calendar
    AMBIGUOUS = "ambiguous"  # a date was implied but not pinned down -> ask
    NONE = "none"  # no date mentioned, or unparseable


#: Owner sentinels. `UNKNOWN` is a first-class, expected answer: inventing an
#: owner is worse than admitting we don't know.
OWNER_UNKNOWN = "unknown"
OWNER_GROUP = "group"


class DueDate(BaseModel):
    # `dt.date` / `dt.time` are qualified because the field names shadow the
    # bare type names inside this class body.
    raw: str | None = None
    date: dt.date | None = None
    time: dt.time | None = None
    resolution: DateResolution = DateResolution.NONE

    @property
    def needs_clarification(self) -> bool:
        return self.resolution is DateResolution.AMBIGUOUS


class RawItem(BaseModel):
    """One extracted item, exactly as the LLM returns it."""

    title: str = Field(description="Short imperative phrase")
    act: SpeechAct
    owner: str = Field(
        default=OWNER_UNKNOWN,
        description="Person's name, or 'unknown', or 'group'",
    )
    source_quote: str = Field(description="Verbatim span from the transcript")
    due_raw: str | None = Field(
        default=None, description="Verbatim time phrase, e.g. 'by Friday'"
    )
    project: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    supersedes: str | None = Field(
        default=None,
        description=(
            "If this item CHANGES something agreed earlier, a short "
            "description of that earlier thing. Memory uses it to link the "
            "old and new versions instead of silently replacing one."
        ),
    )

    @field_validator("title", "source_quote")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("owner")
    @classmethod
    def normalise_owner(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned or cleaned.lower() in {"", "none", "null", "n/a", "?"}:
            return OWNER_UNKNOWN
        return cleaned


class RawParticipant(BaseModel):
    name: str
    speaking: bool = Field(
        default=True,
        description="False if only mentioned, not present in the conversation",
    )

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class RawAnalysis(BaseModel):
    """The LLM's output contract. Extra fields are rejected, not ignored."""

    model_config = {"extra": "forbid"}

    title: str = Field(default="", description="Short conversation title")
    summary: str = Field(default="")
    participants: list[RawParticipant] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    items: list[RawItem] = Field(default_factory=list)
    #: Verbatim spans the model judged to be non-conversational audio - a TV,
    #: an advert, a bystander at the next table. Recorded rather than deleted
    #: (see `DroppedItem`), so that over-suppression is visible instead of
    #: silent. Prompt rule 13 governs what belongs here.
    media_quotes: list[str] = Field(default_factory=list)


class AnalysisItem(BaseModel):
    """A stored item: dates resolved, quote verified."""

    title: str
    act: SpeechAct
    owner: str
    source_quote: str
    due: DueDate = Field(default_factory=DueDate)
    project: str | None = None
    confidence: float = 0.5
    #: What earlier commitment this one changes, if any. Drives conflict
    #: detection in memory when the wording differs from the original.
    supersedes: str | None = None

    @property
    def is_actionable(self) -> bool:
        return self.act in ACTIONABLE_ACTS

    @property
    def needs_clarification(self) -> bool:
        return self.due.needs_clarification


class Participant(BaseModel):
    name: str
    speaking: bool = True


class DroppedItem(BaseModel):
    """An item the LLM produced that we refused to store, and why.

    Kept so prompt regressions are visible instead of silent.
    """

    title: str
    reason: str
    source_quote: str | None = None


class ConversationAnalysis(StoredModel):
    """Stored analysis. `id` is the session id: one analysis per session."""

    session_id: str
    title: str = ""
    summary: str = ""
    participants: list[Participant] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    items: list[AnalysisItem] = Field(default_factory=list)

    dropped: list[DroppedItem] = Field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None

    provider: str = "unknown"
    model: str = "unknown"
    repair_attempted: bool = False

    @property
    def actionable_items(self) -> list[AnalysisItem]:
        return [item for item in self.items if item.is_actionable]
