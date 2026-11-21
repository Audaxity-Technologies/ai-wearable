"""Grounded question answering.

The pipeline is retrieve -> ground -> answer, with two hard rules:

1. **No retrieval means no LLM call.** When nothing relevant is found the
   service answers honestly and never reaches the model. That is cheaper, it
   is deterministic, and it removes the only situation in which a model is
   most likely to invent something: being asked a question with no material.

2. **Citations are verified.** The model cites sources by number. Any number
   outside the supplied range is dropped and recorded in
   `dropped_citations`, so an invented citation can never become a
   tap-through to a conversation that does not support the claim.
"""

from __future__ import annotations

import json
from zoneinfo import ZoneInfo

from pydantic import ValidationError as PydanticValidationError

from app.core.logging import get_logger, log_event
from app.core.text import extract_json_object
from app.core.time_utils import utc_now
from app.models.ask import (
    AnswerSource,
    AskResult,
    RawAnswer,
    RetrievalResult,
)
from app.services.llm.ask_prompts import (
    ASK_SYSTEM_PROMPT,
    ASK_USER_TEMPLATE,
    format_sources,
)
from app.services.llm.base import LLMDriver
from app.services.retrieval import RetrievalService

logger = get_logger(__name__)

NO_MATCH_ANSWER = (
    "I don't have anything about that in your conversations yet."
)


class AskService:
    def __init__(
        self, *, retrieval: RetrievalService, driver: LLMDriver
    ) -> None:
        self._retrieval = retrieval
        self._driver = driver

    async def ask(
        self,
        question: str,
        *,
        user_id: str | None = None,
        tz: str = "UTC",
        limit: int = 8,
    ) -> AskResult:
        retrieved = await self._retrieval.retrieve(
            question, user_id=user_id, limit=limit
        )

        if retrieved.is_empty:
            log_event(
                logger,
                "ask.no_match",
                terms=retrieved.terms,
                user_id=user_id,
            )
            return AskResult(
                question=question,
                answer=NO_MATCH_ANSWER,
                grounded=False,
                confident=False,
                retrieved_count=0,
            )

        now = utc_now().astimezone(self._zone(tz))
        user_prompt = ASK_USER_TEMPLATE.format(
            today=now.date().isoformat(),
            weekday=now.strftime("%A"),
            tz=tz,
            question=question.strip(),
            sources=format_sources(retrieved.items),
        )

        try:
            response = await self._driver.complete_json(
                system=ASK_SYSTEM_PROMPT, user=user_prompt
            )
        except Exception as exc:
            # An LLM outage must not look like "you never said that".
            log_event(logger, "ask.llm_failed", reason=repr(exc)[:200])
            return self._degraded(question, retrieved, repr(exc)[:300])

        raw, error = self._parse(response.text)
        if raw is None:
            return self._degraded(
                question, retrieved, error, provider=response.provider,
                model=response.model,
            )

        sources, dropped = self._resolve_citations(raw, retrieved)

        log_event(
            logger,
            "ask.answered",
            retrieved=len(retrieved.items),
            cited=len(sources),
            dropped_citations=len(dropped),
            confident=raw.confident,
            provider=response.provider,
        )
        return AskResult(
            question=question,
            answer=raw.answer.strip() or NO_MATCH_ANSWER,
            grounded=bool(sources),
            confident=raw.confident and bool(sources),
            sources=sources,
            retrieved_count=len(retrieved.items),
            dropped_citations=dropped,
            provider=response.provider,
            model=response.model,
        )

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _zone(tz: str) -> ZoneInfo:
        try:
            return ZoneInfo(tz)
        except Exception:  # pragma: no cover - defensive
            return ZoneInfo("UTC")

    @staticmethod
    def _parse(text: str) -> tuple[RawAnswer | None, str | None]:
        try:
            payload = json.loads(extract_json_object(text or ""))
        except json.JSONDecodeError as exc:
            return None, f"Response is not valid JSON: {exc}"
        if not isinstance(payload, dict):
            return None, "Response JSON is not an object"
        try:
            return RawAnswer.model_validate(payload), None
        except PydanticValidationError as exc:
            return None, f"Schema validation failed: {exc.errors()}"

    @staticmethod
    def _resolve_citations(
        raw: RawAnswer, retrieved: RetrievalResult
    ) -> tuple[list[AnswerSource], list[int]]:
        """Map cited numbers back to real sources, dropping invented ones."""
        by_label = {item.label: item for item in retrieved.items}
        sources: list[AnswerSource] = []
        dropped: list[int] = []
        seen: set[int] = set()

        for label in raw.sources:
            if label in seen:
                continue
            seen.add(label)
            item = by_label.get(label)
            if item is None:
                dropped.append(label)
                continue
            sources.append(
                AnswerSource(
                    label=item.label,
                    kind=item.kind,
                    id=item.id,
                    session_id=item.session_id,
                    title=item.title,
                    occurred_at=item.occurred_at,
                    quotes=item.quotes[:3],
                )
            )
        return sources, dropped

    @staticmethod
    def _degraded(
        question: str,
        retrieved: RetrievalResult,
        reason: str | None,
        *,
        provider: str = "unknown",
        model: str = "unknown",
    ) -> AskResult:
        """Something went wrong after retrieval succeeded.

        The honest report is that we found material but could not summarise
        it — not that there is nothing. The matched sources are still
        returned so the user can read them.
        """
        return AskResult(
            question=question,
            answer=(
                "I found relevant conversations but could not generate an "
                "answer just now. The sources below are what matched."
            ),
            grounded=False,
            confident=False,
            sources=[
                AnswerSource(
                    label=item.label,
                    kind=item.kind,
                    id=item.id,
                    session_id=item.session_id,
                    title=item.title,
                    occurred_at=item.occurred_at,
                    quotes=item.quotes[:3],
                )
                for item in retrieved.items
            ],
            retrieved_count=len(retrieved.items),
            provider=provider,
            model=model,
            degraded=True,
            degraded_reason=reason,
        )
