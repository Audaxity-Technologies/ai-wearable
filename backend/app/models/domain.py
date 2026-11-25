"""Persisted domain models.

These are the shapes written to storage. API request/response shapes live in
`app.models.dto` and are deliberately separate, so a storage change does not
silently reshape the public API.
"""

from __future__ import annotations

from datetime import datetime

from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.ids import new_id
from app.core.time_utils import utc_now
from app.models.places import CoarsePlace, SessionPlace
from app.models.status import (
    JobStatus,
    JobType,
    SegmentStatus,
    SessionStatus,
)


class StoredModel(BaseModel):
    """Common envelope for anything in the JSON store."""

    id: str = Field(default_factory=new_id)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def touch(self) -> None:
        self.updated_at = utc_now()


class TranscriptSpan(BaseModel):
    """One timestamped span of transcribed speech, relative to its segment."""

    start_s: float
    end_s: float
    text: str


class ParticipationRole(StrEnum):
    """How someone was involved. Not who said which sentence."""

    #: Took part in the conversation.
    SPOKE = "spoke"
    #: Talked about but not present. Worth recording: "what did we say about
    #: Priya" is a real question, and she was not in the room.
    MENTIONED = "mentioned"
    #: The account holder. Kept explicit so the own/owed split does not have
    #: to re-derive it from the people index on every query.
    SELF = "self"


class Participation(BaseModel):
    """One person's involvement in one conversation."""

    person_id: str
    name: str
    role: ParticipationRole = ParticipationRole.SPOKE

    #: "user" when the user said so, "extraction" when the analysis named
    #: them. Kept apart because only the first is certain, and a later
    #: feature that weighs evidence must be able to tell them apart.
    source: str = "user"
    added_at: datetime = Field(default_factory=utc_now)


class Segment(StoredModel):
    """One uploaded audio chunk. `id` is the client-generated UUID."""

    session_id: str
    user_id: str
    seq: int
    started_at: datetime
    duration_s: float
    mime_type: str
    size_bytes: int
    blob_key: str
    source: str = "phone"  # phone | band | upload

    status: SegmentStatus = SegmentStatus.UPLOADED
    text: str | None = None
    language: str | None = None
    spans: list[TranscriptSpan] = Field(default_factory=list)
    error: str | None = None

    #: Roughly where this chunk was recorded. `None` whenever the user has not
    #: opted in, or the device had no fix - and `None` stays `None`: a place
    #: is never inferred from a neighbouring segment. Lives on the segment
    #: rather than the session because people walk from the office to a cafe
    #: mid-conversation.
    place: CoarsePlace | None = None


class Session(StoredModel):
    """One conversation. Groups segments and carries the pipeline status."""

    user_id: str
    tz: str
    started_at: datetime
    status: SessionStatus = SessionStatus.OPEN
    title: str | None = None
    closed_at: datetime | None = None

    segment_ids: list[str] = Field(default_factory=list)
    duration_s: float = 0.0
    language: str | None = None
    transcript: str | None = None

    #: Where this conversation happened, busiest first. Derived from the
    #: segments at close time; the per-segment records remain the truth.
    places: list[SessionPlace] = Field(default_factory=list)

    #: Who was in this conversation, as told to us by the user.
    #:
    #: This is the one piece of attribution evidence that is **certain**. A
    #: voiceprint is probabilistic and an extracted owner is the model's
    #: reading of the words; "Pulkit was here" is a fact only the user has,
    #: and it does not degrade with noise, distance or accent.
    #:
    #: It does NOT claim who spoke which sentence. A three-person
    #: conversation labelled with three people says all three were present,
    #: nothing more - which is why the query it powers reads "in
    #: conversations with Pulkit" rather than "said by Pulkit".
    participants: list[Participation] = Field(default_factory=list)

    failed_stage: str | None = None
    error: str | None = None
    retry_count: int = 0

    @property
    def is_open(self) -> bool:
        return self.status is SessionStatus.OPEN


class Job(StoredModel):
    """A persisted unit of pipeline work.

    The in-memory queue is only a scheduler; this record is what makes the
    pipeline resumable after a crash.
    """

    type: JobType
    session_id: str
    segment_id: str | None = None
    status: JobStatus = JobStatus.PENDING
    attempts: int = 0
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
