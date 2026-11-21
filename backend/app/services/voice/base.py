"""The speaker-embedding driver contract.

Behind a Protocol for the same reason transcription is: the model choice is
not settled, and the research does not settle it. TitaNet, ECAPA-TDNN,
ReDimNet2 and pyannote all have defensible claims, all benchmarked on clean
corpora, and an independent 2026 comparison found that domain shift mattered
more than any leaderboard gap - which is exactly the position a wearable is
in. Noisy, distant, codec-compressed audio is nothing like VoxCeleb.

So: pick behind this interface, measure on real recordings from the actual
device, and swap without touching the service. `model_id` exists so a profile
enrolled with one model is never compared against embeddings from another -
that would produce confident, meaningless similarities.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SpeakerEmbeddingDriver(Protocol):
    """Turns a short audio clip into a fixed-length speaker embedding."""

    @property
    def trustworthy(self) -> bool:
        """Whether verdicts from this driver may be acted on.

        False for the development driver. It exists because "a real model is
        not wired up yet" must be impossible to forget: `SpeakerMatch.is_owner`
        consults it, so an untrustworthy driver can never attribute a
        commitment to the user no matter what similarity it computes.
        """
        ...

    @property
    def model_id(self) -> str:
        """Stable identifier, stored on the profile. Changing it invalidates
        existing profiles rather than silently mismatching them."""
        ...

    async def readiness(self) -> tuple[bool, str]:
        """Whether enrolment can be attempted, and a human-readable reason."""
        ...

    async def embed(self, audio: bytes, *, mime_type: str) -> list[float]:
        """Embed one clip. Raises on audio it cannot process."""
        ...
