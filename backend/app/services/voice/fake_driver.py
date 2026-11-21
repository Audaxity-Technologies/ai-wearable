"""A deterministic embedding driver for tests and local development.

Not a model. It hashes the audio bytes into a stable pseudo-random unit
vector, so identical bytes embed identically and different bytes embed
roughly orthogonally. That is enough to test every branch of the service -
enrolment arithmetic, the three-way verdict, threshold boundaries, model
mismatch - without downloading 100MB of weights or depending on a GPU.

It deliberately supports *seeded* clips: bytes that begin with a known marker
embed near a chosen anchor, which is how a test expresses "this is the same
speaker, recorded again" without a real voice.
"""

from __future__ import annotations

import hashlib
import math

DIM = 64

#: Clips whose bytes start with `b"OWNER:"` embed near the owner anchor, and
#: `b"OTHER:"` near a different one. Anything else hashes on its own.
OWNER_MARKER = b"OWNER:"
OTHER_MARKER = b"OTHER:"


def _unit(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return [0.0] * len(values)
    return [value / norm for value in values]


def _hash_vector(seed: bytes, dim: int = DIM) -> list[float]:
    """A stable unit vector derived from `seed`."""
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for index in range(0, len(digest), 2):
            if len(out) >= dim:
                break
            raw = int.from_bytes(digest[index : index + 2], "big")
            out.append((raw / 32767.5) - 1.0)
        counter += 1
    return _unit(out)


def _blend(anchor: list[float], jitter: list[float], weight: float) -> list[float]:
    """Move `anchor` slightly towards `jitter`, keeping it a unit vector.

    Models the real situation: two clips of one person are similar but never
    identical.
    """
    return _unit(
        [
            (1.0 - weight) * a + weight * j
            for a, j in zip(anchor, jitter, strict=True)
        ]
    )


class FakeSpeakerEmbeddingDriver:
    """Deterministic, offline, no weights."""

    def __init__(
        self,
        *,
        model_id: str = "fake-embed-v1",
        jitter: float = 0.25,
        trustworthy: bool = False,
    ):
        self._model_id = model_id
        self._jitter = jitter
        # Tests of the threshold logic pass True. Production never does.
        self._trustworthy = trustworthy
        self.calls = 0

    @property
    def trustworthy(self) -> bool:
        return self._trustworthy

    @property
    def model_id(self) -> str:
        return self._model_id

    async def readiness(self) -> tuple[bool, str]:
        if self._trustworthy:
            return True, "fake speaker embeddings (test mode)"
        return (
            True,
            "No speaker model is wired up yet, so voice matching cannot be trusted. Enrolment is stored but never used to attribute a commitment.",
        )

    async def embed(self, audio: bytes, *, mime_type: str) -> list[float]:
        self.calls += 1
        if not audio:
            raise ValueError("empty audio")

        for marker in (OWNER_MARKER, OTHER_MARKER):
            if audio.startswith(marker):
                anchor = _hash_vector(marker)
                return _blend(
                    anchor, _hash_vector(audio), self._jitter
                )
        return _hash_vector(audio)
