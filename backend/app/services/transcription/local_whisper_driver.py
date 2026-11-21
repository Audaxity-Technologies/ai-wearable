"""faster-whisper driver, for offline development.

Deliberately a lazy import: faster-whisper is a heavy optional dependency
(~2-4GB RAM, CPU-bound) and is not in requirements.txt. Install it only if you
want this driver:

    pip install faster-whisper

Because transcription here is CPU-bound, it runs in a worker thread so it does
not block the event loop.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from app.core.errors import TranscriptionError
from app.models.domain import TranscriptSpan
from app.services.transcription.base import TranscriptionResult


class LocalWhisperDriver:
    name = "local"

    def __init__(
        self,
        *,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    def _load_model_sync(self) -> Any:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on env
            raise TranscriptionError(
                "faster-whisper is not installed. "
                "Run `pip install faster-whisper` or set "
                "TRANSCRIPTION_PROVIDER=groq.",
                retryable=False,
            ) from exc
        return WhisperModel(
            self._model_size,
            device=self._device,
            compute_type=self._compute_type,
        )

    async def _ensure_model(self) -> Any:
        async with self._load_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_model_sync)
            return self._model

    async def transcribe(
        self, audio: bytes, *, filename: str, language: str | None = None
    ) -> TranscriptionResult:
        model = await self._ensure_model()
        suffix = Path(filename).suffix or ".wav"

        def _run() -> TranscriptionResult:
            # faster-whisper wants a path, so the bytes land in a temp file that
            # is removed immediately afterwards.
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            try:
                tmp.write(audio)
                tmp.close()
                segments, info = model.transcribe(
                    tmp.name, language=language, vad_filter=True
                )
                spans: list[TranscriptSpan] = []
                parts: list[str] = []
                for segment in segments:
                    text = segment.text.strip()
                    if not text:
                        continue
                    parts.append(text)
                    spans.append(
                        TranscriptSpan(
                            start_s=float(segment.start),
                            end_s=float(segment.end),
                            text=text,
                        )
                    )
                return TranscriptionResult(
                    text=" ".join(parts).strip(),
                    language=getattr(info, "language", None),
                    duration_s=getattr(info, "duration", None),
                    spans=spans,
                    provider=self.name,
                    model=self._model_size,
                )
            finally:
                Path(tmp.name).unlink(missing_ok=True)

        try:
            return await asyncio.to_thread(_run)
        except TranscriptionError:
            raise
        except Exception as exc:  # pragma: no cover - driver internals
            raise TranscriptionError(
                f"faster-whisper failed: {exc}"
            ) from exc

    async def readiness(self) -> tuple[bool, str]:
        try:
            import importlib.util

            if importlib.util.find_spec("faster_whisper") is None:
                return False, "faster-whisper is not installed"
        except Exception as exc:  # pragma: no cover
            return False, f"faster-whisper check failed: {exc}"
        loaded = "loaded" if self._model is not None else "not yet loaded"
        return True, f"local:{self._model_size} ({loaded})"
