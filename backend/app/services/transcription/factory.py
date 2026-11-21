"""Driver selection from configuration."""

from __future__ import annotations

from app.config import Settings
from app.core.errors import ConfigurationError
from app.services.transcription.base import TranscriptionDriver
from app.services.transcription.fake_driver import FakeTranscriptionDriver
from app.services.transcription.groq_driver import GroqTranscriptionDriver
from app.services.transcription.local_whisper_driver import LocalWhisperDriver


def build_transcription_driver(settings: Settings) -> TranscriptionDriver:
    provider = settings.transcription_provider
    if provider == "groq":
        return GroqTranscriptionDriver(
            api_key=settings.groq_api_key,
            model=settings.groq_transcription_model,
            base_url=settings.groq_base_url,
            timeout_s=settings.groq_timeout_s,
        )
    if provider == "local":
        return LocalWhisperDriver(
            model_size=settings.local_whisper_model,
            device=settings.local_whisper_device,
            compute_type=settings.local_whisper_compute_type,
        )
    if provider == "fake":
        return FakeTranscriptionDriver()
    raise ConfigurationError(f"Unknown transcription provider: {provider!r}")
