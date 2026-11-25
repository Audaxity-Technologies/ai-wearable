"""Shared fixtures.

Every test runs against a temp data directory and the fake transcription
driver, so the suite never touches the network or the developer's real store.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.container import Container, build_container
from app.main import create_app
from app.services.llm.fake_driver import FakeLLMDriver
from app.services.transcription.fake_driver import FakeTranscriptionDriver


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        log_level="WARNING",
        data_dir=tmp_path / "data",
        transcription_provider="fake",
        llm_provider="fake",
        pipeline_workers=1,
        job_max_attempts=3,
        # No sleeping in tests; backoff itself is covered separately.
        job_retry_backoff_s=0.0,
        # Auth and push are exercised by their own tests; everything else
        # keeps the user_id fallback so the suite stays focused.
        auth_required=False,
        push_provider="none",
        jwt_secret="test-secret-at-least-32-characters-long",
        # Explicitly blank. `Settings` is pydantic-settings, so any field
        # NOT passed here falls through to the developer's real .env -
        # which meant the suite changed behaviour the moment someone
        # configured Google locally, and
        # `test_status_reports_not_configured_without_credentials`
        # started failing on their machine and nobody else's.
        google_client_id="",
        google_client_secret="",
        google_enable_gmail=False,
    )


@pytest.fixture
def driver() -> FakeTranscriptionDriver:
    return FakeTranscriptionDriver()


@pytest.fixture
def llm() -> FakeLLMDriver:
    return FakeLLMDriver()


@pytest.fixture
async def container(
    settings: Settings, driver: FakeTranscriptionDriver, llm: FakeLLMDriver
) -> AsyncIterator[Container]:
    built = build_container(settings, driver=driver, llm=llm)
    # Lifespan is bypassed here so tests control the worker explicitly.
    await built.pipeline.start()
    try:
        yield built
    finally:
        await built.pipeline.stop()


@pytest.fixture
async def client(container: Container) -> AsyncIterator[AsyncClient]:
    app = create_app(container.settings, container=container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def wav_bytes() -> bytes:
    """A minimal valid 16-bit mono WAV header plus a little silence.

    The fake driver never decodes it; this exists so uploads carry realistic
    bytes and a realistic size.
    """
    import struct

    sample_rate = 16000
    frames = b"\x00\x00" * sample_rate  # 1 second of silence
    header = b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(frames))
    return header + frames
