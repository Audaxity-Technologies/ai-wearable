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

    # --- bi-temporal validity (phase E) ---------------------------------
    #
    # Two independent time axes, because they come apart constantly and the
    # gap between them is where human memory questions live.
    #
    #     observed_at  when WE HEARD it
    #     valid_from   when it became TRUE IN THE WORLD
    #     valid_to     when it stopped being true (None = still believed)
    #
    # On 3 March Pulkit says "we moved the launch to June". The new fact's
    # `observed_at` is 3 March; the old "launch is in April" gets
    # `valid_to` = 3 March - invalidated, not deleted. `conflicts_with`
    # could say the two disagreed; it could never say which was true in
    # February.

    #: When this was heard. `None` on rows written before phase E, so read
    #: it through `heard_at`.
    observed_at: dt.datetime | None = None

    #: When the fact became true. **Defaults to `observed_at`, and that is an
    #: admission rather than a measurement**: we almost never learn when
    #: something became true, only when we were told. Pretending otherwise
    #: would be false precision on the axis the whole feature rests on.
    #: A date in `attributes` (a deadline, say) is the fact's *content* and
    #: says nothing about when the fact itself held.
    valid_from: dt.datetime | None = None

    #: When a later statement replaced this. `None` means still believed.
    valid_to: dt.datetime | None = None

    #: The memory that invalidated this one. Directional, unlike
    #: `conflicts_with`, which is mutual - and direction is what makes a
    #: chain of changes walkable in order.
    superseded_by: str | None = None

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts_with)

    @property
    def heard_at(self) -> dt.datetime:
        """When we were told, falling back to the row's creation."""
        return self.observed_at or self.created_at

    @property
    def became_true_at(self) -> dt.datetime:
        return self.valid_from or self.heard_at

    @property
    def is_current(self) -> bool:
        """Still believed, i.e. nothing has replaced it."""
        return self.valid_to is None

    def was_valid_at(self, when: dt.datetime) -> bool:
        """Whether this was the believed version at `when`.

        Half-open: a fact is valid from the moment it was learned up to, but
        not including, the moment it was replaced. Otherwise the instant of a
        change would report both versions as true.
        """
        if when < self.became_true_at:
            return False
        return self.valid_to is None or when < self.valid_to

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
