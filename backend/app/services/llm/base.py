"""LLM driver interface.

Deliberately narrow: one method that takes a system + user prompt and returns
raw text expected to be JSON. Validation, repair and quote verification all
live in the analysis service, so every provider gets identical treatment.
"""

from __future__ import annotations

#: Fixed so that re-analysing a conversation is reproducible. Chosen
#: arbitrarily; its value carries no meaning.
EXTRACTION_SEED = 7

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class LLMResponse(BaseModel):
    text: str
    provider: str = "unknown"
    model: str = "unknown"


@runtime_checkable
class LLMDriver(Protocol):
    name: str

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        seed: int | None = EXTRACTION_SEED,
    ) -> LLMResponse:
        """Return the model's raw response, requested as a JSON object.

        Defaults are tuned for extraction rather than for generation:
        `temperature` 0.0 and a fixed `seed`, because the same transcript
        producing the same items is a feature. A user asking "why did it
        change?" deserves an answer other than "sampling".

        Neither makes a mixture-of-experts model fully deterministic - expert
        routing varies with how requests are batched on the provider's side -
        so this reduces variance without removing it. Measured, not assumed:
        see `docs/ARCHITECTURE.md` on rule 13's reliability.

        Raises `LLMError` on transport/provider failure.
        """
        ...

    async def readiness(self) -> tuple[bool, str]:
        """`(ready, detail)` for the health endpoint. Must not raise."""
        ...
