"""The utterance index: memory phase C.

## Why this grain, and not the session

Retrieval has worked on whole *sessions* since phase 6. "What did Pulkit and I
decide that day" needs one sentence out of a forty-minute recording, and a
session-grained index can only return the whole thing — or score it below a
session that merely says "Pulkit" five times.

Three things also change *during* a conversation, and none of them means
anything at session grain:

- **who is speaking** — the whole product is who committed to what
- **where you are** — you walk from the office to a cafe mid-sentence
- **when** — "that day" and "before lunch" need a timestamp, not a session id

So the utterance is the grain the questions are actually asked at. It is also
the row an embedding belongs on (phase F) and the endpoint a bi-temporal edge
points at (phase E), which is why C comes before both.

## Derived, and rebuildable on purpose

An `Utterance` is computed from `Segment.spans` plus the segment's absolute
start and place. Nothing here is a source of truth: the audio and the
transcript are, and this index can be deleted and rebuilt at any time. That
matters because the linking rules below will change as phase D learns what
questions actually need, and a layer you cannot rebuild is a layer you cannot
fix.

## What it refuses to infer

**`speaker_person_id` stays `None`.** It is tempting to fill it from the
analysis — if an item's owner resolved to Pulkit, surely Pulkit said it? No:
`owner` is who took the work on, not who spoke. "You should send the forms"
has owner Pulkit and was spoken by someone else. Until voice enrolment is
measured and trusted (roadmap 0a), the speaker of a given span is genuinely
unknown, and `None` is the honest value — exactly as `OWNER_UNKNOWN` is for
owners.

**A span with no matching item gets no act.** Most speech is not a commitment.
`act` is `None` for the ordinary case and is not a gap.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from app.models.analysis import SpeechAct
from app.models.domain import StoredModel
from app.models.places import CoarsePlace


class Utterance(StoredModel):
    """One timestamped span of speech, with everything known about it."""

    user_id: str
    session_id: str
    segment_id: str

    #: Order within the session: segment sequence, then span order. Stable, so
    #: "the sentence before this one" is answerable without timestamps.
    seq: int

    text: str

    # --- when ---
    #: Absolute wall-clock start, i.e. the segment's start plus the span's
    #: offset. The whole point of this layer: a span offset is meaningless on
    #: its own, and "that day" cannot be resolved against it.
    started_at: dt.datetime
    ended_at: dt.datetime
    #: Offsets within the owning segment, kept so a player can seek.
    offset_start_s: float = 0.0
    offset_end_s: float = 0.0

    # --- where ---
    #: Copied from the segment. `None` whenever place capture was off, which
    #: is the default, and never inferred from a neighbouring segment.
    place: CoarsePlace | None = None

    # --- who ---
    #: Who SPOKE this. Not who owns any commitment in it - those are
    #: different questions, and conflating them would attribute speech to
    #: whoever was named. Stays `None` until voice enrolment is trusted.
    speaker_person_id: str | None = None

    # --- what it was ---
    #: Set when an extracted item's `source_quote` matched this span. `None`
    #: for ordinary speech, which is most of it.
    act: SpeechAct | None = None
    #: The matched item's owner string, as extracted. Carried rather than
    #: resolved so this layer stays mechanical.
    item_owner: str | None = None
    item_title: str | None = None
    project: str | None = None

    @property
    def duration_s(self) -> float:
        return round((self.ended_at - self.started_at).total_seconds(), 3)

    @property
    def is_actionable(self) -> bool:
        from app.models.analysis import ACTIONABLE_ACTS

        return self.act is not None and self.act in ACTIONABLE_ACTS


class UtteranceResponse(BaseModel):
    """What the API returns. Flat, because clients filter and display."""

    id: str
    session_id: str
    segment_id: str
    seq: int
    text: str
    started_at: dt.datetime
    ended_at: dt.datetime
    offset_start_s: float
    offset_end_s: float
    duration_s: float
    place_name: str | None = None
    speaker_person_id: str | None = None
    act: SpeechAct | None = None
    item_owner: str | None = None
    item_title: str | None = None
    project: str | None = None

    @classmethod
    def from_domain(cls, utterance: Utterance) -> UtteranceResponse:
        return cls(
            id=utterance.id,
            session_id=utterance.session_id,
            segment_id=utterance.segment_id,
            seq=utterance.seq,
            text=utterance.text,
            started_at=utterance.started_at,
            ended_at=utterance.ended_at,
            offset_start_s=utterance.offset_start_s,
            offset_end_s=utterance.offset_end_s,
            duration_s=utterance.duration_s,
            # A name, never coordinates: nobody asks what they said at
            # 28.524, 77.186.
            place_name=(
                utterance.place.describe() if utterance.place else None
            ),
            speaker_person_id=utterance.speaker_person_id,
            act=utterance.act,
            item_owner=utterance.item_owner,
            item_title=utterance.item_title,
            project=utterance.project,
        )


class UtteranceList(BaseModel):
    utterances: list[UtteranceResponse] = Field(default_factory=list)
    total: int = 0
