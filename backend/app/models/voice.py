"""Owner voice enrolment.

## Scope, which is the whole design

**One voiceprint per account: the user's own.** We never enrol anyone else.

The temptation is obvious - identify every speaker and attribute every
commitment perfectly. The cost is a biometric database of people who never
consented to being measured, assembled by a device they did not buy. Forth
designed exactly that and then refused to ship it ("Owner voice enrollment is
not available"). Enrolling only the owner gets the part that matters -
distinguishing "I'll send it" from "you'll send it", which is our entire
differentiator - and leaves us holding one voiceprint, belonging to the person
who chose to give it.

## Why verification has THREE outcomes

A binary owner/not-owner decision would be a guess dressed as a fact. The
extractor already treats `"unknown"` as a correct, first-class answer
(prompt rule 2), so an uncertain speaker match has somewhere honest to go:
it stays unknown, and nobody is told they promised something.

    OWNER      -> confidently the user
    OTHER      -> confidently not the user
    UNCERTAIN  -> we do not know; owner remains "unknown"

The uncertain band is not a failure mode, it is the point. Published speaker
verification error rates on clean benchmark audio are around 1-3% EER, and
real wearable audio is far from clean - noisy, distant, codec-compressed,
cross-talk. A system with no uncertain band converts every one of those
errors into a false accusation.

## What is stored

The embedding, never the audio. Enrolment clips are used to compute the
centroid and are then discarded, because a stored enrolment recording is a
voice sample of the user sitting in a database for no operational reason.
Deletion deletes: this is biometric data, so there is no soft-delete here.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.domain import StoredModel

#: Cosine similarity at or above which a clip is confidently the owner.
#: Deliberately conservative: a false "this was you" attributes someone
#: else's promise to the user, which is the costliest error in the product.
OWNER_THRESHOLD = 0.72

#: Below this, confidently not the owner. Between the two: UNCERTAIN.
OTHER_THRESHOLD = 0.45

#: Enrolment needs enough audio to average out a single odd clip, and the
#: centroid of one noisy sample is not a voiceprint.
MIN_ENROLMENT_CLIPS = 3
MIN_ENROLMENT_SECONDS = 15.0


class SpeakerVerdict(StrEnum):
    OWNER = "owner"
    OTHER = "other"
    UNCERTAIN = "uncertain"
    #: No profile enrolled. Distinct from UNCERTAIN: nothing was attempted,
    #: so the UI should offer enrolment rather than report a poor match.
    NOT_ENROLLED = "not_enrolled"


class VoiceProfile(StoredModel):
    """The owner's voiceprint. `id` is the user id: one profile per account."""

    user_id: str

    #: Mean of the enrolment clip embeddings, L2-normalised.
    centroid: list[float] = Field(default_factory=list)
    dim: int = 0

    #: Which embedding model produced it. A profile is only comparable with
    #: embeddings from the same model, so changing models invalidates it
    #: rather than silently producing nonsense similarities.
    model_id: str = "unknown"

    clip_count: int = 0
    total_seconds: float = 0.0
    enrolled_at: dt.datetime | None = None

    #: Per-profile override, for a user whose voice sits awkwardly against
    #: the default. Falls back to the module constants.
    owner_threshold: float | None = None
    other_threshold: float | None = None

    @property
    def is_usable(self) -> bool:
        return bool(self.centroid) and self.dim > 0

    def thresholds(self) -> tuple[float, float]:
        return (
            self.owner_threshold
            if self.owner_threshold is not None
            else OWNER_THRESHOLD,
            self.other_threshold
            if self.other_threshold is not None
            else OTHER_THRESHOLD,
        )


class SpeakerMatch(BaseModel):
    """The result of comparing one clip against the owner's profile."""

    verdict: SpeakerVerdict
    similarity: float | None = None
    model_id: str | None = None
    detail: str = ""

    #: Whether the driver that produced this verdict may be acted on. False
    #: while no real speaker model is wired up. Carried on the result rather
    #: than checked by callers, so that it cannot be forgotten.
    trustworthy: bool = False

    @property
    def is_owner(self) -> bool:
        """True ONLY for a confident match from a trustworthy driver.

        Both halves matter. Written as a property, rather than left to
        callers, so nobody reimplements it as `verdict != OTHER` - which
        would quietly treat every uncertain clip as the user - and so an
        unvalidated model cannot attribute a commitment to anybody.
        """
        return self.verdict is SpeakerVerdict.OWNER and self.trustworthy


class VoiceStatus(BaseModel):
    """What the app shows in Settings. Carries no biometric material."""

    enrolled: bool = False
    clip_count: int = 0
    total_seconds: float = 0.0
    enrolled_at: dt.datetime | None = None
    model_id: str | None = None
    provider_ready: bool = False
    #: False while no real speaker model is wired up. The app must say so
    #: rather than showing a working-looking "Enrolled" state.
    verification_trustworthy: bool = False
    detail: str = ""

    #: Stated here because this is where the user grants it, and it is the
    #: one promise the feature makes about other people.
    others_enrolled: int = 0
