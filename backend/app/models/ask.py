"""Question answering over the user's own history.

Retrieval returns labelled sources; the model cites them by **number**, not by
UUID. Asking a model to reproduce a 36-character identifier invites silent
transcription errors, and a citation that does not resolve is worse than no
citation at all.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field


class SourceKind(StrEnum):
    CONVERSATION = "conversation"
    MEMORY = "memory"
    TASK = "task"


class RetrievedItem(BaseModel):
    """One piece of grounding material, with where it came from."""

    #: 1-based label the model cites. Stable within a single answer only.
    label: int
    kind: SourceKind
    id: str
    session_id: str | None = None
    title: str
    content: str
    quotes: list[str] = Field(default_factory=list)
    occurred_at: dt.datetime | None = None
    score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    query: str
    terms: list[str] = Field(default_factory=list)
    items: list[RetrievedItem] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.items


class RawAnswer(BaseModel):
    """The model's output contract for an answer."""

    model_config = {"extra": "forbid"}

    answer: str = ""
    #: Labels from the supplied sources. Anything outside the supplied range
    #: is dropped as an invented citation.
    sources: list[int] = Field(default_factory=list)
    #: False when the sources do not actually settle the question.
    confident: bool = True


class AnswerSource(BaseModel):
    """A citation the app can turn into a tap-through."""

    label: int
    kind: SourceKind
    id: str
    session_id: str | None
    title: str
    occurred_at: dt.datetime | None
    quotes: list[str] = Field(default_factory=list)


class AskResult(BaseModel):
    question: str
    answer: str
    #: True when the answer rests on retrieved material. False means the
    #: system is saying "I don't have that", which is a valid outcome.
    grounded: bool
    confident: bool = True
    sources: list[AnswerSource] = Field(default_factory=list)
    retrieved_count: int = 0
    #: Citations the model produced that did not resolve to a supplied source.
    dropped_citations: list[int] = Field(default_factory=list)
    provider: str = "none"
    model: str = "none"
    degraded: bool = False
    degraded_reason: str | None = None
