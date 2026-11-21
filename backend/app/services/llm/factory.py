"""LLM driver selection from configuration."""

from __future__ import annotations

from app.config import Settings
from app.core.errors import ConfigurationError
from app.services.llm.base import LLMDriver
from app.services.llm.fake_driver import FakeLLMDriver
from app.services.llm.gemini_driver import GeminiLLMDriver
from app.services.llm.groq_driver import GroqLLMDriver


def build_llm_driver(settings: Settings) -> LLMDriver:
    provider = settings.llm_provider
    if provider == "groq":
        return GroqLLMDriver(
            api_key=settings.groq_api_key,
            model=settings.groq_llm_model,
            base_url=settings.groq_base_url,
            timeout_s=settings.llm_timeout_s,
        )
    if provider == "gemini":
        return GeminiLLMDriver(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            base_url=settings.gemini_base_url,
            timeout_s=settings.llm_timeout_s,
        )
    if provider == "fake":
        return FakeLLMDriver()
    raise ConfigurationError(f"Unknown LLM provider: {provider!r}")
