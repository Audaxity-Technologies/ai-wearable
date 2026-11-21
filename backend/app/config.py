"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

TranscriptionProvider = Literal["groq", "local", "fake"]
LLMProvider = Literal["groq", "gemini", "fake"]
PushProvider = Literal["expo", "none"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    data_dir: Path = Path("./data")

    # Transcription
    transcription_provider: TranscriptionProvider = "groq"
    groq_api_key: str = ""
    groq_transcription_model: str = "whisper-large-v3-turbo"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_s: float = 120.0

    # Conversation analysis (phase 3)
    llm_provider: LLMProvider = "groq"
    groq_llm_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_timeout_s: float = 90.0

    local_whisper_model: str = "base"
    local_whisper_device: str = "cpu"
    local_whisper_compute_type: str = "int8"

    # Ingest limits
    max_segment_bytes: int = 20 * 1024 * 1024
    max_segment_duration_s: float = 300.0
    session_idle_timeout_s: float = 90.0

    # Auth
    #: Change this in production. The app refuses to start with the default
    #: when APP_ENV=prod.
    jwt_secret: str = "dev-secret-change-me"
    jwt_expires_minutes: int = 60 * 24 * 30  # 30 days; this is a personal app
    #: When false, endpoints fall back to a user_id query parameter. Tests use
    #: this; production must not.
    auth_required: bool = True

    # Notifications
    push_provider: PushProvider = "expo"
    #: How often the scheduler checks whether anyone is due a briefing.
    briefing_check_seconds: int = 60

    # Google execution (phase 10)
    #: Empty by default: with no credentials the backend behaves exactly as
    #: it did in phase 5, recording approved actions locally.
    google_client_id: str = ""
    google_client_secret: str = ""
    #: Must match a redirect URI registered on the OAuth client, character
    #: for character, or Google rejects the flow with redirect_uri_mismatch.
    google_redirect_uri: str = "http://localhost:8010/v1/integrations/google/callback"
    #: Request the Gmail scope as well as Calendar. OFF by default.
    #:
    #: `gmail.compose` is a RESTRICTED scope that permits `drafts.send` and
    #: `messages.send`, so turning this on grants a token that CAN send mail
    #: as the user - there is no draft-only Gmail scope. Audaxity never calls
    #: send, but that is a property of this code, not of the token, and the
    #: app says so when it is on.
    #:
    #: It also commits you to restricted-scope verification and a security
    #: assessment before shipping to anyone but yourself.
    google_enable_gmail: bool = False

    google_timeout_s: float = 30.0
    #: Which calendar approved events land on.
    google_calendar_id: str = "primary"

    # Pipeline
    pipeline_workers: int = 1
    job_max_attempts: int = 3
    # Base delay before a retry; doubles per attempt. Mainly for 429s.
    job_retry_backoff_s: float = 2.0

    @property
    def store_dir(self) -> Path:
        return self.data_dir / "store"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.app_env == "prod":
        if settings.jwt_secret == "dev-secret-change-me":
            raise RuntimeError(
                "JWT_SECRET must be set to a real secret when APP_ENV=prod. "
                "Generate one with: python -c \"import secrets; "
                "print(secrets.token_urlsafe(48))\""
            )
        # HS256 keys shorter than the hash output weaken the signature
        # (RFC 7518 section 3.2).
        if len(settings.jwt_secret) < 32:
            raise RuntimeError(
                "JWT_SECRET must be at least 32 characters when APP_ENV=prod."
            )
        if not settings.auth_required:
            raise RuntimeError(
                "AUTH_REQUIRED cannot be false when APP_ENV=prod."
            )
        # An authorization code arrives on the redirect URI as a query
        # parameter. Over plain HTTP to a non-local host, that code is
        # readable in transit by anything on the path.
        redirect = settings.google_redirect_uri
        if (
            settings.google_client_id
            and redirect.startswith("http://")
            and "localhost" not in redirect
            and "127.0.0.1" not in redirect
        ):
            raise RuntimeError(
                "GOOGLE_REDIRECT_URI must use https when APP_ENV=prod: an "
                f"authorization code would otherwise travel in clear text "
                f"({redirect!r})."
            )
    return settings
