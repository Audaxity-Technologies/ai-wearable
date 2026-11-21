"""Choosing a speaker-embedding driver.

Only the fake driver ships today, and that is a deliberate statement rather
than an omission.

The research does not identify a winner for this use case. TitaNet, ECAPA-TDNN,
ReDimNet2 and pyannote all report 0.1-3% equal error rates, all on clean
benchmark corpora, and an independent 2026 comparison found domain shift
mattered more than the gaps between them. Wearable audio is the worst case for
domain shift: distant mic, room noise, lossy codec, cross-talk.

Choosing from a leaderboard would therefore be choosing in the dark. The
sequence that is actually defensible:

1. Ship enrolment, verification and the three-way verdict - done, and tested
   against the deterministic driver, so every branch of the logic is proven
   before any model is involved.
2. Record 20-30 clips on the real device: the user, other people, both in a
   noisy room.
3. Measure candidate models on *that*, and set `OWNER_THRESHOLD` and
   `OTHER_THRESHOLD` from the measured distributions rather than from a
   paper's.
4. Add the driver here.

Until step 3, a real driver would be shipping an unvalidated biometric
decision about who promised what - the single costliest error this product
can make. `provider_ready` is false, the UI says so, and owner attribution
stays `"unknown"`, which is already a first-class honest answer.

When adding a driver, give it a NEW `model_id`. Profiles record the model that
produced them and refuse cross-model comparison, so a new id invalidates old
profiles instead of silently producing meaningless similarities.
"""

from __future__ import annotations

from app.config import Settings
from app.services.voice.base import SpeakerEmbeddingDriver
from app.services.voice.fake_driver import FakeSpeakerEmbeddingDriver


def build_speaker_embedding_driver(
    settings: Settings,
) -> SpeakerEmbeddingDriver:
    # `voice_provider` defaults to "fake". Enrolment through the fake driver
    # is real enrolment of a meaningless voiceprint, so the status endpoint
    # reports the provider and the app says plainly that verification is not
    # yet trustworthy.
    return FakeSpeakerEmbeddingDriver()
