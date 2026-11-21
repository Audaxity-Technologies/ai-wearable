"""Groq hosted Whisper driver.

Uses Groq's OpenAI-compatible audio transcriptions endpoint with
`whisper-large-v3-turbo`. Free tier at time of writing: 20 req/min,
2000 req/day, 7200 audio-seconds/hour, 25MB max request — which is why
ingest caps a segment at 20MB.
"""

from __future__ import annotations

import httpx

from app.core.errors import TranscriptionError
from app.core.logging import get_logger, log_event
from app.models.domain import TranscriptSpan
from app.services.transcription.base import TranscriptionResult

logger = get_logger(__name__)

#: Status codes worth retrying: rate limit and transient upstream failures.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class GroqTranscriptionDriver:
    name = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "whisper-large-v3-turbo",
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        # Test seam: lets the wire-format tests point this driver at a stub
        # without a network call. Production always leaves it None.
        self._transport = transport

    async def transcribe(
        self, audio: bytes, *, filename: str, language: str | None = None
    ) -> TranscriptionResult:
        if not self._api_key:
            raise TranscriptionError(
                "GROQ_API_KEY is not set", retryable=False
            )

        data: dict[str, str] = {
            "model": self._model,
            # verbose_json gives us segment timestamps, which the app needs to
            # jump from an extracted quote back to the audio position.
            "response_format": "verbose_json",
        }
        if language:
            data["language"] = language

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    files={"file": (filename, audio, "application/octet-stream")},
                    data=data,
                )
        except httpx.TimeoutException as exc:
            raise TranscriptionError(
                f"Groq request timed out after {self._timeout_s}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"Groq request failed: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            retryable = response.status_code in RETRYABLE_STATUS
            # Body may contain a provider error message, but never audio or
            # transcript content, so it is safe to surface.
            detail = response.text[:500]
            log_event(
                logger,
                "transcription.groq.error",
                status=response.status_code,
                retryable=retryable,
            )
            raise TranscriptionError(
                f"Groq returned {response.status_code}: {detail}",
                retryable=retryable,
            )

        return self._parse(response.json())

    def _parse(self, payload: dict) -> TranscriptionResult:
        spans = [
            TranscriptSpan(
                start_s=float(item.get("start", 0.0)),
                end_s=float(item.get("end", 0.0)),
                text=str(item.get("text", "")).strip(),
            )
            for item in payload.get("segments") or []
            if str(item.get("text", "")).strip()
        ]
        duration = payload.get("duration")
        return TranscriptionResult(
            text=str(payload.get("text", "")).strip(),
            language=payload.get("language"),
            duration_s=float(duration) if duration is not None else None,
            spans=spans,
            provider=self.name,
            model=self._model,
        )

    async def readiness(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "GROQ_API_KEY is not set"
        return True, f"groq:{self._model}"
