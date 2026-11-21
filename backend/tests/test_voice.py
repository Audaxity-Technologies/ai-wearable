"""Tests for owner voice enrolment.

Two things are being protected here, and only one of them is accuracy:

1. An unvalidated model must never be able to attribute a commitment.
2. "Uncertain" must be reachable, and must not collapse into "owner".

Both are properties of the code rather than of any model, which is why they
can be tested against a deterministic driver.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.models.voice import (
    MIN_ENROLMENT_CLIPS,
    SpeakerMatch,
    SpeakerVerdict,
    VoiceProfile,
)
from app.repositories.json_repo import JsonRepository
from app.services.voice.enrolment import EnrolmentClip, VoiceService, cosine
from app.services.voice.fake_driver import FakeSpeakerEmbeddingDriver


@pytest.fixture
def repo(tmp_path: Path) -> JsonRepository[VoiceProfile]:
    return JsonRepository(tmp_path / "voice_profiles.json", VoiceProfile)


def _service(repo, *, trustworthy: bool = True, **kwargs) -> VoiceService:
    return VoiceService(
        profiles=repo,
        driver=FakeSpeakerEmbeddingDriver(trustworthy=trustworthy, **kwargs),
    )


def _clips(prefix: bytes = b"OWNER:", count: int = 3, duration: float = 6.0):
    return [
        EnrolmentClip(
            prefix + bytes([index]), mime_type="audio/m4a", duration_s=duration
        )
        for index in range(count)
    ]


# --- the guarantee that does not depend on a model ----------------------


async def test_an_untrustworthy_driver_can_never_attribute_a_commitment(
    repo,
) -> None:
    """The central safety property.

    No real speaker model is wired up. Enrolment still works and the
    similarity is still computed - but `is_owner` must stay False, so no code
    path can turn an unvalidated verdict into "you promised this".
    """
    service = _service(repo, trustworthy=False)
    await service.enrol("u1", _clips())

    match = await service.verify("u1", b"OWNER:same-voice", mime_type="audio/m4a")

    assert match.verdict is SpeakerVerdict.OWNER
    assert match.similarity is not None and match.similarity > 0.7
    assert match.trustworthy is False
    assert match.is_owner is False, "an unvalidated model attributed speech"


async def test_is_owner_requires_both_the_verdict_and_trust() -> None:
    """Guards the property directly, independent of any service."""
    assert SpeakerMatch(
        verdict=SpeakerVerdict.OWNER, trustworthy=True
    ).is_owner
    assert not SpeakerMatch(
        verdict=SpeakerVerdict.OWNER, trustworthy=False
    ).is_owner
    for verdict in (
        SpeakerVerdict.OTHER,
        SpeakerVerdict.UNCERTAIN,
        SpeakerVerdict.NOT_ENROLLED,
    ):
        assert not SpeakerMatch(verdict=verdict, trustworthy=True).is_owner


async def test_status_says_plainly_when_matching_is_not_trusted(repo) -> None:
    service = _service(repo, trustworthy=False)
    status = await service.enrol("u1", _clips())
    assert status.enrolled
    assert status.verification_trustworthy is False
    assert "not trusted" in status.detail.lower()


# --- the three-way verdict ----------------------------------------------


async def test_the_owners_voice_is_recognised(repo) -> None:
    service = _service(repo)
    await service.enrol("u1", _clips())
    match = await service.verify("u1", b"OWNER:later", mime_type="audio/m4a")
    assert match.verdict is SpeakerVerdict.OWNER
    assert match.is_owner


async def test_a_different_voice_is_rejected(repo) -> None:
    service = _service(repo)
    await service.enrol("u1", _clips())
    match = await service.verify("u1", b"OTHER:someone", mime_type="audio/m4a")
    assert match.verdict is SpeakerVerdict.OTHER
    assert not match.is_owner


async def test_the_uncertain_band_is_reachable(repo) -> None:
    """If uncertainty were unreachable, every borderline clip would be
    forced into a confident verdict - which is the failure the band exists
    to prevent."""
    service = _service(repo)
    await service.enrol("u1", _clips())
    profile = await repo.get("u1")
    assert profile is not None

    # Place the thresholds around whatever this clip actually scores, which
    # is how a real deployment sets them: from measured distributions.
    match = await service.verify("u1", b"OTHER:x", mime_type="audio/m4a")
    midpoint = match.similarity or 0.0
    profile.owner_threshold = midpoint + 0.05
    profile.other_threshold = midpoint - 0.05
    await repo.put(profile)

    again = await service.verify("u1", b"OTHER:x", mime_type="audio/m4a")
    assert again.verdict is SpeakerVerdict.UNCERTAIN
    assert not again.is_owner
    assert "unknown" in again.detail.lower()


async def test_thresholds_are_inclusive_at_the_owner_boundary(repo) -> None:
    """Exactly at the threshold counts as the owner, as documented."""
    service = _service(repo)
    await service.enrol("u1", _clips())
    profile = await repo.get("u1")
    assert profile is not None
    probe = await service.verify("u1", b"OWNER:probe", mime_type="audio/m4a")
    profile.owner_threshold = probe.similarity
    await repo.put(profile)
    assert (
        await service.verify("u1", b"OWNER:probe", mime_type="audio/m4a")
    ).verdict is SpeakerVerdict.OWNER


# --- not enrolled, and model changes ------------------------------------


async def test_unenrolled_verification_is_not_an_error(repo) -> None:
    """Callers' honest fallback is already `owner = "unknown"`."""
    service = _service(repo)
    match = await service.verify("nobody", b"audio", mime_type="audio/m4a")
    assert match.verdict is SpeakerVerdict.NOT_ENROLLED
    assert not match.is_owner


async def test_a_changed_model_invalidates_the_profile_rather_than_guessing(
    repo,
) -> None:
    """Cross-model similarity is confident nonsense. Refuse it."""
    await _service(repo, model_id="embed-a").enrol("u1", _clips())

    moved_on = _service(repo, model_id="embed-b")
    match = await moved_on.verify("u1", b"OWNER:same", mime_type="audio/m4a")
    assert match.verdict is SpeakerVerdict.NOT_ENROLLED
    assert "different voice model" in match.detail

    status = await moved_on.status("u1")
    assert status.enrolled is False
    assert "enrol again" in status.detail.lower()


async def test_undecodable_audio_degrades_to_uncertain(repo) -> None:
    service = _service(repo)
    await service.enrol("u1", _clips())
    match = await service.verify("u1", b"", mime_type="audio/m4a")
    assert match.verdict is SpeakerVerdict.UNCERTAIN
    assert not match.is_owner


# --- enrolment requirements ---------------------------------------------


async def test_too_few_clips_is_refused(repo) -> None:
    service = _service(repo)
    with pytest.raises(ValidationError, match="at least"):
        await service.enrol("u1", _clips(count=MIN_ENROLMENT_CLIPS - 1))


async def test_too_little_audio_is_refused(repo) -> None:
    """The centroid of three two-second clips is not a voiceprint."""
    service = _service(repo)
    with pytest.raises(ValidationError, match="seconds"):
        await service.enrol("u1", _clips(count=3, duration=2.0))


async def test_re_enrolling_replaces_rather_than_averages(repo) -> None:
    """A user re-enrolling is usually fixing a bad attempt.

    Averaging the bad clips back in would defeat them.
    """
    service = _service(repo)
    await service.enrol("u1", _clips(prefix=b"OTHER:", count=4, duration=8.0))
    first = await repo.get("u1")
    assert first is not None and first.clip_count == 4

    await service.enrol("u1", _clips(prefix=b"OWNER:", count=3, duration=6.0))
    second = await repo.get("u1")
    assert second is not None and second.clip_count == 3

    # And it now matches the owner, not the voice enrolled first.
    match = await service.verify("u1", b"OWNER:test", mime_type="audio/m4a")
    assert match.verdict is SpeakerVerdict.OWNER


async def test_no_audio_is_stored_on_the_profile(repo) -> None:
    """The embedding, never the recording.

    Checked structurally: the stored JSON must not contain the clip bytes.
    """
    service = _service(repo)
    marker = b"OWNER:unmistakable-marker"
    await service.enrol(
        "u1",
        [
            EnrolmentClip(marker + bytes([i]), mime_type="audio/m4a", duration_s=6.0)
            for i in range(3)
        ],
    )
    stored = Path(repo._path).read_text(encoding="utf-8")  # noqa: SLF001
    assert "unmistakable-marker" not in stored
    profile = await repo.get("u1")
    assert profile is not None and profile.centroid


async def test_forget_really_deletes_and_is_idempotent(repo) -> None:
    service = _service(repo)
    await service.enrol("u1", _clips())
    assert await repo.get("u1") is not None

    await service.forget("u1")
    assert await repo.get("u1") is None
    # Deleting nothing is a success, so the UI can offer it unconditionally.
    await service.forget("u1")

    stored = Path(repo._path).read_text(encoding="utf-8")  # noqa: SLF001
    assert "centroid" not in stored


async def test_only_one_profile_exists_per_account(repo) -> None:
    """There is no table for other people, and the id is the user id."""
    service = _service(repo)
    await service.enrol("u1", _clips())
    await service.enrol("u1", _clips(prefix=b"OTHER:", count=3, duration=9.0))
    assert len(await repo.list()) == 1

    status = await service.status("u1")
    assert status.others_enrolled == 0


# --- the maths -----------------------------------------------------------


def test_cosine_is_clamped_and_symmetric() -> None:
    vector = [1.0, 0.0, 0.0]
    assert cosine(vector, vector) == 1.0  # never 1.0000000000000002
    assert cosine(vector, [-1.0, 0.0, 0.0]) == -1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([1.0, 2.0], [2.0, 1.0]) == cosine([2.0, 1.0], [1.0, 2.0])


def test_cosine_of_a_zero_vector_is_zero_not_an_error() -> None:
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_rejects_mismatched_dimensions() -> None:
    with pytest.raises(ValueError):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])


async def test_the_centroid_is_normalised(repo) -> None:
    """So that cosine against it is comparable regardless of clip count."""
    service = _service(repo)
    await service.enrol("u1", _clips(count=5, duration=5.0))
    profile = await repo.get("u1")
    assert profile is not None
    norm = math.sqrt(sum(value * value for value in profile.centroid))
    assert norm == pytest.approx(1.0, abs=1e-9)
