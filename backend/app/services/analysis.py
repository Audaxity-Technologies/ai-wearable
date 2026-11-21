"""Transcript -> structured conversation analysis.

The pipeline stage that turns text into meaning. Four guarantees, in order:

1. **Schema-first.** The response is validated against `RawAnalysis`. A failure
   gets exactly one repair attempt with the validator error fed back.
2. **Never crash the pipeline.** If repair also fails, the session is marked
   degraded and the transcript is kept. A bad model response must never cost
   the user their conversation.
3. **Nothing invented.** Every item's `source_quote` is checked against the
   transcript. Items that fail are dropped and recorded in `dropped`, so a
   prompt regression is visible rather than silent.
4. **Dates resolved in code.** The model supplies the phrase; `core.dates`
   resolves it against the session's own start time and timezone.
"""

from __future__ import annotations

import json

from pydantic import ValidationError as PydanticValidationError

from app.core.dates import resolve_due
from app.core.logging import get_logger, log_event
from app.core.text import extract_json_object, normalise, verify_quote
from app.core.time_utils import session_now
from app.models.analysis import (
    AnalysisItem,
    ConversationAnalysis,
    DroppedItem,
    DueDate,
    Participant,
    RawAnalysis,
)
from app.models.domain import Session
from app.services.llm.base import LLMDriver
from app.services.llm.prompts import (
    REPAIR_TEMPLATE,
    SYSTEM_PROMPT,
    USER_TEMPLATE,
)

logger = get_logger(__name__)

#: Transcripts shorter than this are not worth an LLM call.
MIN_TRANSCRIPT_CHARS = 20


class AnalysisService:
    def __init__(self, *, driver: LLMDriver) -> None:
        self._driver = driver

    async def analyze(self, session: Session) -> ConversationAnalysis:
        transcript = (session.transcript or "").strip()
        if len(transcript) < MIN_TRANSCRIPT_CHARS:
            return ConversationAnalysis(
                id=session.id,
                session_id=session.id,
                degraded=True,
                degraded_reason="Transcript too short to analyse",
                provider=self._driver.name,
            )

        anchor = session_now(session.started_at, session.tz)
        user_prompt = USER_TEMPLATE.format(
            recorded_on=anchor.date().isoformat(),
            weekday=anchor.strftime("%A"),
            tz=session.tz,
            transcript=transcript,
        )

        response = await self._driver.complete_json(
            system=SYSTEM_PROMPT, user=user_prompt
        )
        raw, error, repaired = await self._parse_with_repair(
            response.text, user_prompt
        )

        if raw is None:
            log_event(
                logger,
                "analysis.degraded",
                session_id=session.id,
                reason=(error or "unknown")[:200],
            )
            return ConversationAnalysis(
                id=session.id,
                session_id=session.id,
                degraded=True,
                degraded_reason=error,
                provider=response.provider,
                model=response.model,
                repair_attempted=True,
            )

        analysis = self._build(
            session=session,
            raw=raw,
            transcript=transcript,
            provider=response.provider,
            model=response.model,
            repaired=repaired,
        )
        log_event(
            logger,
            "analysis.completed",
            session_id=session.id,
            provider=response.provider,
            model=response.model,
            items=len(analysis.items),
            dropped=len(analysis.dropped),
            repaired=repaired,
        )
        return analysis

    # --- parsing ----------------------------------------------------------

    async def _parse_with_repair(
        self, text: str, user_prompt: str
    ) -> tuple[RawAnalysis | None, str | None, bool]:
        """Parse, and on failure retry once with the error fed back."""
        raw, error = self._parse(text)
        if raw is not None:
            return raw, None, False

        repair_prompt = (
            user_prompt
            + "\n\n"
            + REPAIR_TEMPLATE.format(error=error, previous=text[:2000])
        )
        try:
            retry = await self._driver.complete_json(
                system=SYSTEM_PROMPT, user=repair_prompt
            )
        except Exception as exc:  # the repair call itself failed
            return None, f"{error}; repair call failed: {exc}", True

        repaired_raw, repair_error = self._parse(retry.text)
        if repaired_raw is not None:
            return repaired_raw, None, True
        return None, f"{error}; repair also failed: {repair_error}", True

    @staticmethod
    def _parse(text: str) -> tuple[RawAnalysis | None, str | None]:
        candidate = extract_json_object(text or "")
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            return None, f"Response is not valid JSON: {exc}"
        if not isinstance(payload, dict):
            return None, "Response JSON is not an object"
        try:
            return RawAnalysis.model_validate(payload), None
        except PydanticValidationError as exc:
            return None, f"Schema validation failed: {exc.errors()}"

    # --- assembly ---------------------------------------------------------

    def _build(
        self,
        *,
        session: Session,
        raw: RawAnalysis,
        transcript: str,
        provider: str,
        model: str,
        repaired: bool,
    ) -> ConversationAnalysis:
        anchor = session_now(session.started_at, session.tz)
        items: list[AnalysisItem] = []
        dropped: list[DroppedItem] = []
        seen: set[tuple[str, str]] = set()

        # Prompt rule 13: spans the model judged to be a TV, an advert or a
        # bystander. Recorded, not deleted, so over-suppression shows up in
        # the integrity receipt. Each one is held to the same standard as an
        # item: if it is not in the transcript, the model invented it and we
        # ignore the claim rather than hiding real speech on its word.
        media: set[str] = set()
        for quote in raw.media_quotes:
            cleaned = quote.strip()
            if not cleaned or not verify_quote(cleaned, transcript):
                continue
            media.add(normalise(cleaned))
            dropped.append(
                DroppedItem(
                    title=cleaned[:60],
                    reason="media: not part of the user's conversation",
                    source_quote=cleaned,
                )
            )

        for candidate in raw.items:
            if media and normalise(candidate.source_quote) in media:
                # The model both suppressed a span and extracted from it.
                # Suppression wins: it is the more specific judgement.
                dropped.append(
                    DroppedItem(
                        title=candidate.title,
                        reason="media: not part of the user's conversation",
                        source_quote=candidate.source_quote,
                    )
                )
                continue

            if not verify_quote(candidate.source_quote, transcript):
                # The model produced something it cannot point at. Drop it.
                dropped.append(
                    DroppedItem(
                        title=candidate.title,
                        reason="source_quote not found in transcript",
                        source_quote=candidate.source_quote,
                    )
                )
                continue

            key = (candidate.title.strip().lower(), candidate.owner.lower())
            if key in seen:
                dropped.append(
                    DroppedItem(
                        title=candidate.title,
                        reason="duplicate of an earlier item",
                        source_quote=candidate.source_quote,
                    )
                )
                continue
            seen.add(key)

            resolved = resolve_due(candidate.due_raw, anchor)
            items.append(
                AnalysisItem(
                    title=candidate.title,
                    act=candidate.act,
                    owner=candidate.owner,
                    source_quote=candidate.source_quote,
                    project=candidate.project,
                    confidence=candidate.confidence,
                    supersedes=candidate.supersedes,
                    due=DueDate(
                        raw=candidate.due_raw,
                        date=resolved.date,
                        time=resolved.time,
                        resolution=resolved.resolution,
                    ),
                )
            )

        if dropped:
            log_event(
                logger,
                "analysis.items_dropped",
                session_id=session.id,
                count=len(dropped),
                reasons=sorted({item.reason for item in dropped}),
            )

        return ConversationAnalysis(
            id=session.id,
            session_id=session.id,
            title=raw.title.strip(),
            summary=raw.summary.strip(),
            participants=[
                Participant(name=person.name, speaking=person.speaking)
                for person in raw.participants
            ],
            projects=[value.strip() for value in raw.projects if value.strip()],
            topics=[value.strip() for value in raw.topics if value.strip()],
            items=items,
            dropped=dropped,
            provider=provider,
            model=model,
            repair_attempted=repaired,
        )
