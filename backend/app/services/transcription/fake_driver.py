"""Deterministic transcription driver for tests and offline UI work.

Returns a stable fake transcript derived from the audio length, so pipeline
tests never touch the network. Set TRANSCRIPTION_PROVIDER=fake to use it while
building the mobile app without burning API quota.
"""

from __future__ import annotations

from app.core.errors import TranscriptionError
from app.models.domain import TranscriptSpan
from app.services.transcription.base import TranscriptionResult

#: Lines are cycled to build a transcript, so output length tracks input size.
FAKE_LINES = (
    "We need to finish the investor pitch by Friday.",
    "I'll work on the financial projections.",
    "Let's meet tomorrow at 5 PM and review the wearable prototype.",
    "We should contact the V-NEST team next week about the incubation update.",
)


class FakeTranscriptionDriver:
    name = "fake"

    def __init__(self, *, fail: bool = False, retryable: bool = True) -> None:
        # `fail` lets tests exercise the pipeline's failure path.
        self._fail = fail
        self._retryable = retryable
        self.calls: list[str] = []

    async def transcribe(
        self, audio: bytes, *, filename: str, language: str | None = None
    ) -> TranscriptionResult:
        self.calls.append(filename)
        if self._fail:
            raise TranscriptionError(
                "fake driver failure", retryable=self._retryable
            )

        line_count = max(1, min(len(FAKE_LINES), len(audio) // 1024 + 1))
        spans: list[TranscriptSpan] = []
        parts: list[str] = []
        for index in range(line_count):
            text = FAKE_LINES[index % len(FAKE_LINES)]
            parts.append(text)
            spans.append(
                TranscriptSpan(
                    start_s=float(index * 5),
                    end_s=float(index * 5 + 5),
                    text=text,
                )
            )
        return TranscriptionResult(
            text=" ".join(parts),
            language=language or "en",
            duration_s=float(line_count * 5),
            spans=spans,
            provider=self.name,
            model="fake-v1",
        )

    async def readiness(self) -> tuple[bool, str]:
        return True, "fake:fake-v1"
