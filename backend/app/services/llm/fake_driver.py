"""Scripted LLM driver for tests and offline work.

Returns queued responses in order, so a test can drive the repair loop by
queueing a malformed response followed by a valid one.
"""

from __future__ import annotations

import json

from app.core.errors import LLMError
from app.services.llm.base import EXTRACTION_SEED, LLMResponse

#: Matches the fake transcription driver's output, so the two compose into a
#: believable end-to-end run without any network access.
DEFAULT_ANALYSIS: dict = {
    "title": "Investor pitch and prototype review",
    "summary": (
        "Pulkit and Manan agreed to finish the investor pitch by Friday, "
        "review the wearable prototype tomorrow, and contact V-NEST."
    ),
    "participants": [
        {"name": "Pulkit", "speaking": True},
        {"name": "Manan", "speaking": True},
    ],
    "projects": ["Investor pitch", "Wearable prototype", "V-NEST incubation"],
    "topics": ["fundraising", "hardware"],
    "items": [
        {
            "title": "Finish the investor pitch",
            "act": "requirement",
            "owner": "group",
            "source_quote": "We need to finish the investor pitch by Friday.",
            "due_raw": "by Friday",
            "project": "Investor pitch",
            "confidence": 0.9,
        },
        {
            "title": "Work on the financial projections",
            "act": "commitment",
            "owner": "Manan",
            "source_quote": "I'll work on the financial projections.",
            "due_raw": None,
            "project": "Investor pitch",
            "confidence": 0.95,
        },
        {
            "title": "Review the wearable prototype",
            "act": "suggestion",
            "owner": "group",
            "source_quote": (
                "Let's meet tomorrow at 5 PM and review the wearable prototype."
            ),
            "due_raw": "tomorrow at 5 PM",
            "project": "Wearable prototype",
            "confidence": 0.8,
        },
        {
            "title": "Contact the V-NEST team",
            "act": "suggestion",
            "owner": "group",
            "source_quote": (
                "We should contact the V-NEST team next week about the "
                "incubation update."
            ),
            "due_raw": "next week",
            "project": "V-NEST incubation",
            "confidence": 0.7,
        },
    ],
}


class FakeLLMDriver:
    name = "fake"

    def __init__(
        self,
        *,
        responses: list[str] | None = None,
        fail: bool = False,
        retryable: bool = True,
    ) -> None:
        self._responses = list(responses or [json.dumps(DEFAULT_ANALYSIS)])
        self._fail = fail
        self._retryable = retryable
        self.calls: list[tuple[str, str]] = []

    def queue(self, response: str) -> None:
        self._responses.append(response)

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        seed: int | None = EXTRACTION_SEED,
    ) -> LLMResponse:
        self.calls.append((system, user))
        if self._fail:
            raise LLMError("fake llm failure", retryable=self._retryable)
        # The last queued response repeats once exhausted.
        text = (
            self._responses.pop(0)
            if len(self._responses) > 1
            else self._responses[0]
        )
        return LLMResponse(text=text, provider=self.name, model="fake-llm-v1")

    async def readiness(self) -> tuple[bool, str]:
        return True, "fake:fake-llm-v1"
