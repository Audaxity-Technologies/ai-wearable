"""The open-loops ledger: what you owe, and what you are owed.

## Why this is the feature no competitor can build

Every competing product produces a flat to-do list that resets daily. A flat
list cannot express the thing people actually forget, which is not their own
tasks - those they write down - but **promises made to them in passing**:

> "Pulkit said three weeks ago he'd send the compliance forms, and has not
>  mentioned it since."

That sentence requires three things to have been stored at capture time:
whether the utterance was a commitment or a mere suggestion (`act`), who took
it on (`owner`), and which conversations have touched it since
(`source_session_ids`). We store all three. Nobody else does, because nobody
else records speech acts - so for them this view is not hard, it is
impossible.

## The honesty rules

Two, and they are the whole design:

1. **An unattributed loop is never silently assigned to the user.** `owner`
   is `"unknown"` whenever nobody clearly took something on. Filing those
   under "you owe" would invent obligations, which is the one error this
   codebase refuses everywhere else (see prompt rule 2). They get their own
   bucket and the user can attribute them.
2. **"Gone quiet" is a statement about our records, not about the person.**
   It means: one conversation mentioned this, it was a while ago, and nothing
   since. It does not mean they have forgotten, and the wording the client
   shows must not imply they have.

## Known limitation, and what fixes it

Today `owner` is a free string, and with no speaker identification the
extractor usually cannot tell who "I" was. So in practice most loops land in
`unattributed` rather than in `owed_by_me`. That is correct behaviour given
what we know - and it is precisely why owner voice enrolment (Tier 1
proposal 2) is what turns this screen from honest into useful. Building the
ledger first is deliberate: it makes the value of enrolment measurable rather
than assumed.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from typing import Protocol
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from app.core.text import normalise
from app.models.analysis import (
    OWNER_GROUP,
    OWNER_UNKNOWN,
    DateResolution,
    SpeechAct,
)
from app.models.tasks import Task, TaskStatus

#: After this long with no further mention, a loop is described as having gone
#: quiet. Two weeks: long enough that a week's silence on a normal task is not
#: flagged, short enough to surface something before it is forgotten.
QUIET_AFTER_DAYS = 14

#: Owner strings that mean "the user", beyond their own display name. The
#: extractor is told to use a name or a sentinel, but a transcript where the
#: speaker is unnamed can still yield a first-person owner.
SELF_ALIASES = frozenset({"me", "i", "myself", "self"})


class LoopSide(StrEnum):
    MINE = "mine"  # the user took it on
    THEIRS = "theirs"  # a named other person took it on
    SHARED = "shared"  # "we"/"let's" - owner is the group, user included
    UNATTRIBUTED = "unattributed"  # nobody clearly owns it; do NOT guess


class OpenLoop(BaseModel):
    """One outstanding commitment, with everything needed to judge it."""

    task_id: str
    title: str
    side: LoopSide
    act: SpeechAct
    #: The other person, when there is a named one. `None` for the user's own
    #: loops, for group loops and for unattributed ones.
    counterparty: str | None = None
    project: str | None = None

    opened_at: dt.datetime
    last_mentioned_at: dt.datetime
    #: How many distinct conversations have touched this.
    mention_sessions: int = 1
    quote: str | None = None

    due_date: dt.date | None = None
    due_resolution: DateResolution = DateResolution.NONE
    is_overdue: bool = False
    days_open: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def raised_again(self) -> bool:
        """More than one conversation has supported this loop."""
        return self.mention_sessions > 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def gone_quiet(self) -> bool:
        """Old, and never mentioned again.

        A statement about our records, not about anyone's intentions.
        """
        return self.days_open >= QUIET_AFTER_DAYS and not self.raised_again


class OpenLoopLedger(BaseModel):
    """The two lists, plus the two the product refuses to fake."""

    owed_by_me: list[OpenLoop] = Field(default_factory=list)
    owed_to_me: list[OpenLoop] = Field(default_factory=list)
    shared: list[OpenLoop] = Field(default_factory=list)
    #: Loops with no clear owner. Surfaced, not assigned.
    unattributed: list[OpenLoop] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        return (
            len(self.owed_by_me)
            + len(self.owed_to_me)
            + len(self.shared)
            + len(self.unattributed)
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def quiet_count(self) -> int:
        return sum(
            1
            for loop in (
                *self.owed_by_me,
                *self.owed_to_me,
                *self.shared,
                *self.unattributed,
            )
            if loop.gone_quiet
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def overdue_count(self) -> int:
        return sum(
            1
            for loop in (
                *self.owed_by_me,
                *self.owed_to_me,
                *self.shared,
                *self.unattributed,
            )
            if loop.is_overdue
        )


def _is_self(owner: str, self_names: Sequence[str]) -> bool:
    cleaned = normalise(owner)
    if not cleaned:
        return False
    if cleaned in SELF_ALIASES:
        return True
    return any(cleaned == normalise(name) for name in self_names if name)


def classify_person(person: "PersonLike | None") -> LoopSide | None:
    """Bucket from a resolved identity, which beats any string comparison.

    `is_self` is a decision the user made - one tap, "this is me" - so it is
    trusted over the display-name matching below, which fails on a nickname,
    a surname, or any recording where the speaker is never named.

    Returns `None` when there is no resolved person, so the caller falls back
    to the string rules rather than treating "unidentified" as "not me".
    """
    if person is None:
        return None
    return LoopSide.MINE if person.is_self else LoopSide.THEIRS


def classify(owner: str, self_names: Sequence[str]) -> LoopSide:
    """Which bucket a loop belongs to, from its owner string alone.

    Order matters: the sentinels are checked before the name comparison, so a
    user unwise enough to be called "Group" is not mistaken for one.
    """
    cleaned = (owner or "").strip()
    if not cleaned or cleaned == OWNER_UNKNOWN:
        return LoopSide.UNATTRIBUTED
    if cleaned == OWNER_GROUP:
        return LoopSide.SHARED
    if _is_self(cleaned, self_names):
        return LoopSide.MINE
    return LoopSide.THEIRS


def _sort_key(loop: OpenLoop) -> tuple[int, int, float]:
    """Most actionable first.

    Overdue, then gone quiet, then longest open. Deliberately NOT sorted by
    due date alone: a three-week-old promise with no date is more likely to be
    the thing the user forgot than tomorrow's dated task, which they can see
    coming.
    """
    return (
        0 if loop.is_overdue else 1,
        0 if loop.gone_quiet else 1,
        -loop.days_open,
    )


class PersonLike(Protocol):
    """The slice of `Person` the ledger needs.

    A Protocol rather than an import so this module stays a pure function
    over data and does not reach into the people service.
    """

    id: str
    canonical_name: str
    is_self: bool


def build_ledger(
    tasks: Iterable[Task],
    *,
    self_names: Sequence[str] = (),
    people: Mapping[str, "PersonLike"] | None = None,
    today: dt.date,
    now: dt.datetime,
) -> OpenLoopLedger:
    """Build the ledger from open tasks.

    Pure: no repository, no clock. `today` and `now` are passed in so that
    "overdue" and "days open" are testable and are judged in the caller's
    chosen timezone rather than in UTC.
    """
    ledger = OpenLoopLedger()
    people = dict(people or {})

    for task in tasks:
        if task.status is not TaskStatus.PENDING:
            continue

        # When the commitment was first recorded. `updated_at` moves whenever
        # a later conversation re-mentions it, so the two together are what
        # "since when, and heard of lately?" needs.
        opened_at = task.created_at
        last_mentioned = max(task.updated_at, opened_at)
        days_open = max(0, (now.date() - opened_at.date()).days)

        # Identity first, strings second.
        person = people.get(task.owner_person_id or "")
        side = classify_person(person) or classify(task.owner, self_names)
        counterparty = (
            person.canonical_name
            if person is not None and side is LoopSide.THEIRS
            else task.owner
            if side is LoopSide.THEIRS
            else None
        )
        loop = OpenLoop(
            task_id=task.id,
            title=task.title,
            side=side,
            act=task.act,
            counterparty=counterparty,
            project=task.project,
            opened_at=opened_at,
            last_mentioned_at=last_mentioned,
            mention_sessions=max(1, len(task.source_session_ids)),
            quote=task.source_quotes[0] if task.source_quotes else None,
            due_date=task.due_date,
            due_resolution=task.due_resolution,
            is_overdue=task.is_overdue(today),
            days_open=days_open,
        )

        match side:
            case LoopSide.MINE:
                ledger.owed_by_me.append(loop)
            case LoopSide.THEIRS:
                ledger.owed_to_me.append(loop)
            case LoopSide.SHARED:
                ledger.shared.append(loop)
            case LoopSide.UNATTRIBUTED:
                ledger.unattributed.append(loop)

    for bucket in (
        ledger.owed_by_me,
        ledger.owed_to_me,
        ledger.shared,
        ledger.unattributed,
    ):
        bucket.sort(key=_sort_key)

    return ledger
