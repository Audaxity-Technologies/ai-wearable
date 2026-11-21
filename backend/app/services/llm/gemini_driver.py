"""Gemini driver.

Secondary provider. Free-tier daily quotas for Gemini have moved around a lot,
which is precisely why the system talks to an interface rather than to a
vendor: switching is a config change, not a rewrite.
"""

from __future__ import annotations

import httpx

from app.core.errors import LLMError
from app.core.logging import get_logger, log_event
from app.services.llm.base import EXTRACTION_SEED, LLMResponse

logger = get_logger(__name__)

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class GeminiLLMDriver:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-2.5-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_s: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        # Test seam; see the Groq transcription driver for the rationale.
        self._transport = transport

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        seed: int | None = EXTRACTION_SEED,
    ) -> LLMResponse:
        # `seed` is accepted and ignored: the Gemini REST generateContent
        # endpoint has no equivalent, and silently dropping it is better
        # than a signature that lies about which drivers honour it.
        del seed
        if not self._api_key:
            raise LLMError("GEMINI_API_KEY is not set", retryable=False)

        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            },
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._base_url}/models/{self._model}:generateContent",
                    # Key in a header, not the query string, so it cannot leak
                    # into logs or proxy access records.
                    headers={"x-goog-api-key": self._api_key},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMError(f"Gemini timed out after {self._timeout_s}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini request failed: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            retryable = response.status_code in RETRYABLE_STATUS
            log_event(
                logger,
                "llm.gemini.error",
                status=response.status_code,
                retryable=retryable,
            )
            raise LLMError(
                f"Gemini returned {response.status_code}: {response.text[:500]}",
                retryable=retryable,
            )

        body = response.json()
        try:
            parts = body["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected Gemini response shape: {body}") from exc

        return LLMResponse(text=text, provider=self.name, model=self._model)

    async def readiness(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "GEMINI_API_KEY is not set"
        return True, f"gemini:{self._model}"
