"""Groq chat-completions driver.

Same API key as the transcription driver, which is the main reason Groq is the
phase-1 default: one free account covers both STT and the LLM.
"""

from __future__ import annotations

import httpx

from app.core.errors import LLMError
from app.core.logging import get_logger, log_event
from app.services.llm.base import EXTRACTION_SEED, LLMResponse

logger = get_logger(__name__)

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class GroqLLMDriver:
    name = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "openai/gpt-oss-120b",
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_s: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        # Test seam; see the transcription driver for the rationale.
        self._transport = transport

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        seed: int | None = EXTRACTION_SEED,
    ) -> LLMResponse:
        if not self._api_key:
            raise LLMError("GROQ_API_KEY is not set", retryable=False)

        payload: dict[str, object] = {
            "model": self._model,
            "temperature": temperature,
            # Constrains the model to emit a JSON object. It does not validate
            # against our schema, which is why the repair loop still exists.
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if seed is not None:
            payload["seed"] = seed

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMError(f"Groq timed out after {self._timeout_s}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Groq request failed: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            retryable = response.status_code in RETRYABLE_STATUS
            log_event(
                logger,
                "llm.groq.error",
                status=response.status_code,
                retryable=retryable,
            )
            raise LLMError(
                f"Groq returned {response.status_code}: {response.text[:500]}",
                retryable=retryable,
            )

        body = response.json()
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected Groq response shape: {body}") from exc

        return LLMResponse(text=text or "", provider=self.name, model=self._model)

    async def readiness(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "GROQ_API_KEY is not set"
        return True, f"groq:{self._model}"
