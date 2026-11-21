"""Enrolment and verification against the owner's voiceprint.

See `app/models/voice.py` for why there is exactly one profile per account and
why verification has three outcomes rather than two.
"""

from __future__ import annotations

import math

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.core.time_utils import utc_now
from app.models.voice import (
    MIN_ENROLMENT_CLIPS,
    MIN_ENROLMENT_SECONDS,
    SpeakerMatch,
    SpeakerVerdict,
    VoiceProfile,
    VoiceStatus,
)
from app.repositories.base import Repository
from app.services.voice.base import SpeakerEmbeddingDriver

logger = get_logger(__name__)


class EnrolmentClip:
    """One enrolment recording. Audio is held only for the duration of the
    call that computes the centroid, then dropped."""

    __slots__ = ("audio", "mime_type", "duration_s")

    def __init__(self, audio: bytes, *, mime_type: str, duration_s: float):
        self.audio = audio
        self.mime_type = mime_type
        self.duration_s = duration_s


def cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity, clamped to [-1, 1].

    Clamped because floating-point error on normalised vectors can produce
    1.0000000000000002, and a similarity above 1 would look like a bug in
    whatever reads it.
    """
    if not left or not right or len(left) != len(right):
        raise ValueError("embeddings must be non-empty and the same length")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_left * norm_right)))


def _centroid(embeddings: list[list[float]]) -> list[float]:
    """L2-normalised mean. Normalised so cosine against it is comparable
    regardless of how many clips went in."""
    dim = len(embeddings[0])
    summed = [0.0] * dim
    for embedding in embeddings:
        for index, value in enumerate(embedding):
            summed[index] += value
    norm = math.sqrt(sum(value * value for value in summed))
    if norm == 0:
        # Antipodal clips cancelling out is not a voiceprint.
        raise ValidationError(
            "Enrolment clips did not produce a usable voiceprint. "
            "Please record again."
        )
    return [value / norm for value in summed]


class VoiceService:
    def __init__(
        self,
        *,
        profiles: Repository[VoiceProfile],
        driver: SpeakerEmbeddingDriver,
    ) -> None:
        self._profiles = profiles
        self._driver = driver

    # --- status ---------------------------------------------------------

    async def status(self, user_id: str) -> VoiceStatus:
        ready, detail = await self._driver.readiness()
        trustworthy = self._driver.trustworthy
        profile = await self._profiles.get(user_id)
        if profile is None or not profile.is_usable:
            return VoiceStatus(
                enrolled=False,
                provider_ready=ready,
                verification_trustworthy=trustworthy,
                detail=(
                    detail
                    if not ready
                    else "Not enrolled. Approved items cannot yet tell your "
                    "voice from anyone else's."
                ),
            )
        stale = profile.model_id != self._driver.model_id
        return VoiceStatus(
            enrolled=not stale,
            clip_count=profile.clip_count,
            total_seconds=profile.total_seconds,
            enrolled_at=profile.enrolled_at,
            model_id=profile.model_id,
            provider_ready=ready,
            verification_trustworthy=trustworthy,
            detail=(
                "The voice model has changed since you enrolled. "
                "Please enrol again."
                if stale
                else "Enrolled, but voice matching is not trusted yet: "
                + detail
                if not trustworthy
                else "Enrolled. Only your own voice is stored."
            ),
            # Stated as a fact, not a promise: there is one row per account
            # and no table for anyone else.
            others_enrolled=0,
        )

    # --- enrolment ------------------------------------------------------

    async def enrol(
        self, user_id: str, clips: list[EnrolmentClip]
    ) -> VoiceStatus:
        """Replace the user's voiceprint from a fresh set of clips.

        Replaces rather than appends: a user re-enrolling is usually fixing a
        bad first attempt, and averaging the bad one back in would defeat
        them.
        """
        ready, detail = await self._driver.readiness()
        if not ready:
            raise ValidationError(f"Voice enrolment is unavailable: {detail}")

        if len(clips) < MIN_ENROLMENT_CLIPS:
            raise ValidationError(
                f"Enrolment needs at least {MIN_ENROLMENT_CLIPS} recordings; "
                f"got {len(clips)}."
            )
        total_seconds = sum(clip.duration_s for clip in clips)
        if total_seconds < MIN_ENROLMENT_SECONDS:
            raise ValidationError(
                f"Enrolment needs at least {MIN_ENROLMENT_SECONDS:.0f} "
                f"seconds of speech; got {total_seconds:.1f}."
            )

        embeddings: list[list[float]] = []
        for clip in clips:
            try:
                embedding = await self._driver.embed(
                    clip.audio, mime_type=clip.mime_type
                )
            except Exception as exc:
                raise ValidationError(
                    f"A recording could not be processed: {exc}"
                ) from exc
            if not embedding:
                raise ValidationError("A recording produced no voiceprint.")
            embeddings.append(embedding)

        dims = {len(embedding) for embedding in embeddings}
        if len(dims) != 1:
            raise ValidationError(
                "The voice model returned inconsistent voiceprints."
            )

        centroid = _centroid(embeddings)
        profile = VoiceProfile(
            id=user_id,
            user_id=user_id,
            centroid=centroid,
            dim=len(centroid),
            model_id=self._driver.model_id,
            clip_count=len(clips),
            total_seconds=round(total_seconds, 2),
            enrolled_at=utc_now(),
        )
        await self._profiles.put(profile)

        # Note what is NOT logged: no embedding, no audio, no durations per
        # clip. This is biometric data.
        log_event(
            logger,
            "voice.enrolled",
            user_id=user_id,
            clips=len(clips),
            model=self._driver.model_id,
        )
        return await self.status(user_id)

    # --- verification ---------------------------------------------------

    async def verify(
        self, user_id: str, audio: bytes, *, mime_type: str
    ) -> SpeakerMatch:
        """Is this clip the owner speaking?

        Never raises for an unenrolled user or unprocessable audio: a
        verification failure must degrade to "we do not know", because every
        caller's honest fallback is already `owner = "unknown"`.
        """
        profile = await self._profiles.get(user_id)
        if profile is None or not profile.is_usable:
            return SpeakerMatch(
                verdict=SpeakerVerdict.NOT_ENROLLED,
                trustworthy=self._driver.trustworthy,
                detail="No voiceprint enrolled for this account.",
            )
        if profile.model_id != self._driver.model_id:
            # Comparing across models yields confident nonsense. Refuse.
            return SpeakerMatch(
                verdict=SpeakerVerdict.NOT_ENROLLED,
                trustworthy=self._driver.trustworthy,
                model_id=profile.model_id,
                detail=(
                    "Enrolled with a different voice model "
                    f"({profile.model_id}); please enrol again."
                ),
            )

        try:
            embedding = await self._driver.embed(audio, mime_type=mime_type)
            similarity = cosine(profile.centroid, embedding)
        except Exception as exc:
            return SpeakerMatch(
                verdict=SpeakerVerdict.UNCERTAIN,
                trustworthy=self._driver.trustworthy,
                model_id=profile.model_id,
                detail=f"Could not compare this audio: {exc}",
            )

        owner_at, other_below = profile.thresholds()
        if similarity >= owner_at:
            verdict = SpeakerVerdict.OWNER
        elif similarity < other_below:
            verdict = SpeakerVerdict.OTHER
        else:
            verdict = SpeakerVerdict.UNCERTAIN

        return SpeakerMatch(
            verdict=verdict,
            trustworthy=self._driver.trustworthy,
            similarity=round(similarity, 4),
            model_id=profile.model_id,
            detail=(
                "Confidently you."
                if verdict is SpeakerVerdict.OWNER
                else "Confidently not you."
                if verdict is SpeakerVerdict.OTHER
                else "Too close to call - treated as unknown."
            ),
        )

    # --- deletion -------------------------------------------------------

    async def forget(self, user_id: str) -> None:
        """Delete the voiceprint outright.

        No soft-delete and no tombstone carrying the centroid: this is
        biometric data, and "deleted" has to mean deleted. Deleting something
        that is not there is a success, so that the UI can offer the control
        unconditionally.
        """
        await self._profiles.delete(user_id)
        log_event(logger, "voice.forgotten", user_id=user_id)

    async def require_profile(self, user_id: str) -> VoiceProfile:
        profile = await self._profiles.get(user_id)
        if profile is None:
            raise NotFoundError("No voiceprint enrolled for this account.")
        return profile
