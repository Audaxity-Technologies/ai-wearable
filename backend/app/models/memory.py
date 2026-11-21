"""Persistent memory schema.

Memory is entity-centric, not transcript-centric: one row per person, project
or durable fact, accumulating evidence across conversations. That is what lets
the assistant answer "what is still pending on the wearable prototype?" using
Monday's and Wednesday's conversations together.

Three properties the brief insists on, encoded here:

- **Provenance.** Every memory keeps `source_session_ids` and the quotes that
  produced it, so the app can always answer "why do you believe this?".
- **Selectivity.** Not every sentence becomes a memory. See
  `services/memory.py` for the rules.
- **Conflicts are preserved, never overwritten.** When a new conversation
  contradicts an older memory, both rows survive and point at each other via
  `conflicts_with`. Silently replacing the old one would destroy exactly the
  information a user needs ("we moved that deadline, didn't we?").
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.domain import StoredModel


class MemoryKind(StrEnum):
    PERSON = "person"
    PROJECT = "project"
    FACT = "fact"
    PREFERENCE = "preference"  # not yet produced; reserved


class Memory(StoredModel):
    user_id: str
    kind: MemoryKind

    #: Normalised dedupe key, e.g. "person:manan" or
    #: "fact:investor pitch:finish the investor pitch". Stable across
    #: conversations: it is how re-mentions find their existing row.
    key: str
    name: str
    content: str

    #: Free-form structured detail: owner, act, due_date, project, ...
    attributes: dict[str, str] = Field(default_factory=dict)

    # --- provenance ---
    source_session_ids: list[str] = Field(default_factory=list)
    source_quotes: list[str] = Field(default_factory=list)

    # --- accumulated evidence ---
    confidence: float = 0.5
    mention_count: int = 1
    first_seen: dt.datetime | None = None
    last_seen: dt.datetime | None = None

    #: Ids of memories this one contradicts. Mutual: both rows reference each
    #: other, and both stay active.
    conflicts_with: list[str] = Field(default_factory=list)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts_with)

    @property
    def is_cross_conversation(self) -> bool:
        """True once more than one conversation has supported this memory."""
        return len(self.source_session_ids) > 1


class MemoryCandidate(BaseModel):
    """A proposed memory, before resolution against what is already stored."""

    kind: MemoryKind
    key: str
    name: str
    content: str
    attributes: dict[str, str] = Field(default_factory=dict)
    source_quote: str | None = None
    confidence: float = 0.5
    #: Short description of an earlier fact this one changes, from the
    #: extractor. Lets conflict detection work when the new statement is
    #: worded differently from the original ("investor pitch deadline moved"
    #: vs "investor pitch ready by Friday").
    supersedes: str | None = None


class MemoryWriteResult(BaseModel):
    """What a single analysis did to memory. Returned for logging and tests."""

    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    conflicted: list[str] = Field(default_factory=list)
    skipped: int = 0

    @property
    def total_written(self) -> int:
        return len(self.created) + len(self.updated) + len(self.conflicted)


class MemoryHit(BaseModel):
    """A search result: the memory plus why it matched."""

    memory: Memory
    score: float
    matched_terms: list[str] = Field(default_factory=list)
