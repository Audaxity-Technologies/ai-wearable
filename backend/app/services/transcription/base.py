"""Transcription driver interface.

Phase-2 contract. Drivers are interchangeable: Groq's hosted Whisper (default),
faster-whisper on this machine (offline dev / cost escape hatch), and a
deterministic fake used by the tests.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.models.domain import TranscriptSpan


class TranscriptionResult(BaseModel):
    text: str
    language: str | None = None
    duration_s: float | None = None
    spans: list[TranscriptSpan] = Field(default_factory=list)
    provider: str = "unknown"
    model: str = "unknown"


@runtime_checkable
class TranscriptionDriver(Protocol):
    name: str

    async def transcribe(
        self, audio: bytes, *, filename: str, language: str | None = None
    ) -> TranscriptionResult:
        """Transcribe audio bytes.

        Raises `TranscriptionError` on failure, with `retryable` set so the
        pipeline can decide between a retry and a permanent failure.
        """
        ...

    async def readiness(self) -> tuple[bool, str]:
        """`(ready, detail)` for the health endpoint. Must not raise."""
        ...
