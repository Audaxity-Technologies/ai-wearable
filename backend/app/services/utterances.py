"""Building and querying the utterance index.

See `app/models/utterances.py` for why this grain exists and what it refuses
to infer.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection

from app.core.logging import get_logger, log_event
from app.core.text import normalise
from app.models.analysis import ConversationAnalysis, SpeechAct
from app.models.domain import Segment, Session
from app.models.utterances import Utterance
from app.repositories.base import Repository

logger = get_logger(__name__)

#: How much of the shorter side must overlap for a span to carry an item's
#: act. Applied to word overlap, not character similarity - see `link_score`.
LINK_THRESHOLD = 0.5

#: A span shorter than this cannot carry an act on containment alone.
#: Without it, `quote_similarity("Also", "So remind me")` scores 0.67 - it
#: compares "also" against the one-word window "so" - and a four-character
#: span gets tagged as an assignment. That is exactly what the first run
#: against real recordings produced.
MIN_LINK_CHARS = 6

#: Significant words a span must share with the quote when neither contains
#: the other. Two is enough to rule out the single-short-word coincidences
#: while still linking a quote that runs across a span boundary.
MIN_SHARED_WORDS = 2


def link_score(quote: str, span: str) -> float:
    """How strongly `span` carries `quote`, from 0 to 1.

    Word overlap rather than character similarity, because character
    similarity on short strings is noise: "also" and "so" are 67% similar and
    share no meaning.
    """
    quote_norm, span_norm = normalise(quote), normalise(span)
    if not quote_norm or not span_norm:
        return 0.0

    shorter, longer = sorted((span_norm, quote_norm), key=len)

    # Containment is the strong signal - a quote verified against the whole
    # transcript usually sits inside one span, or vice versa - but only when
    # the shorter side is substantial enough to mean something.
    if shorter in longer:
        if len(shorter) >= MIN_LINK_CHARS:
            return 1.0
        return 0.0

    quote_words = set(quote_norm.split())
    span_words = set(span_norm.split())
    shared = quote_words & span_words
    if len(shared) < MIN_SHARED_WORDS:
        return 0.0
    return len(shared) / min(len(quote_words), len(span_words))


class UtteranceService:
    def __init__(self, *, utterances: Repository[Utterance]) -> None:
        self._utterances = utterances

    # --- building ---------------------------------------------------------

    async def rebuild_for_session(
        self,
        session: Session,
        segments: list[Segment],
        analysis: ConversationAnalysis | None = None,
    ) -> list[Utterance]:
        """Rebuild this session's utterances from its segments.

        Idempotent: existing rows for the session are deleted first, so
        re-running after a re-analysis replaces rather than duplicates. That
        is what makes this layer rebuildable, which the linking rules below
        will need as phase D learns what questions require.
        """
        await self.delete_for_session(session.id)

        built: list[Utterance] = []
        seq = 0
        for segment in sorted(segments, key=lambda s: (s.started_at, s.seq)):
            for span in segment.spans:
                text = span.text.strip()
                if not text:
                    continue
                started = segment.started_at + dt.timedelta(
                    seconds=span.start_s
                )
                # Guard against a span whose end precedes its start, which a
                # provider can emit on a mis-segmented chunk. A negative
                # duration would poison every time filter downstream.
                end_offset = max(span.end_s, span.start_s)
                built.append(
                    Utterance(
                        user_id=segment.user_id,
                        session_id=session.id,
                        segment_id=segment.id,
                        seq=seq,
                        text=text,
                        started_at=started,
                        ended_at=segment.started_at
                        + dt.timedelta(seconds=end_offset),
                        offset_start_s=round(span.start_s, 3),
                        offset_end_s=round(end_offset, 3),
                        place=segment.place,
                    )
                )
                seq += 1

        if analysis is not None:
            self._link_items(built, analysis)

        for utterance in built:
            await self._utterances.put(utterance)

        log_event(
            logger,
            "utterances.rebuilt",
            session_id=session.id,
            count=len(built),
            linked=sum(1 for u in built if u.act is not None),
            with_place=sum(1 for u in built if u.place is not None),
        )
        return built

    @staticmethod
    def _link_items(
        utterances: list[Utterance], analysis: ConversationAnalysis
    ) -> None:
        """Attach each extracted item to the span it was quoted from.

        This is the join that makes "what did we decide that day" a filter
        rather than a search: `act == decision` plus a time range, at the
        grain of a sentence.

        One item may span several utterances - a quote can run across a
        span boundary - so every span above the threshold is linked, not just
        the best. Conversely a span matching nothing stays unlinked, which is
        the ordinary case: most speech is not a commitment.
        """
        for item in analysis.items:
            quote = item.source_quote.strip()
            if not quote:
                continue

            scored = [
                (link_score(quote, utterance.text), utterance)
                for utterance in utterances
            ]
            matches = [
                utterance
                for score, utterance in scored
                if score >= LINK_THRESHOLD
            ]
            if not matches:
                # The quote was verified against the whole transcript, not
                # against spans, so a quote assembled across a silence can
                # fail here. Falling back to the single best span keeps the
                # item locatable instead of dropping it from the index.
                best_score, best = max(scored, key=lambda pair: pair[0])
                if best_score <= 0:
                    continue
                matches = [best]

            for utterance in matches:
                # First writer wins: when two items quote the same span, the
                # earlier one in the analysis keeps it rather than being
                # silently overwritten by the later.
                if utterance.act is not None:
                    continue
                utterance.act = item.act
                utterance.item_owner = item.owner
                utterance.item_title = item.title
                utterance.project = item.project

    # --- reads ------------------------------------------------------------

    async def for_session(self, session_id: str) -> list[Utterance]:
        rows = await self._utterances.list(session_id=session_id)
        return sorted(rows, key=lambda u: u.seq)

    async def query(
        self,
        *,
        user_id: str,
        since: dt.datetime | None = None,
        until: dt.datetime | None = None,
        act: SpeechAct | None = None,
        actionable_only: bool = False,
        place: str | None = None,
        speaker_person_id: str | None = None,
        session_ids: Collection[str] | None = None,
        contains: str | None = None,
        limit: int = 100,
    ) -> tuple[list[Utterance], int]:
        """Structured retrieval over the index.

        Deliberately filters rather than ranks. Phase D builds the question
        parser that decides *which* filter to apply, and phase F adds
        embeddings as a ranker **inside** the result of one. Getting the
        filter right first is what keeps a wrong answer visibly wrong.

        Returns `(page, total_matched)` so a caller can tell "nothing
        matched" from "a lot matched and here are the first hundred" - the
        difference between answering honestly and answering confidently.
        """
        rows = await self._utterances.list(user_id=user_id)

        if since is not None:
            rows = [u for u in rows if u.ended_at >= since]
        if until is not None:
            rows = [u for u in rows if u.started_at <= until]
        if act is not None:
            rows = [u for u in rows if u.act is act]
        if actionable_only:
            rows = [u for u in rows if u.is_actionable]
        if session_ids is not None:
            # Narrowing to named conversations. Used by participant
            # labelling: an EMPTY set means 'no conversation qualifies',
            # which must return nothing rather than everything - the
            # difference between an honest empty and a silent widening.
            wanted_sessions = set(session_ids)
            rows = [u for u in rows if u.session_id in wanted_sessions]
        if speaker_person_id is not None:
            rows = [
                u for u in rows if u.speaker_person_id == speaker_person_id
            ]
        if place:
            wanted = normalise(place)
            rows = [
                u
                for u in rows
                if u.place is not None and wanted in normalise(u.place.describe())
            ]
        if contains:
            needle = normalise(contains)
            rows = [u for u in rows if needle in normalise(u.text)]

        rows.sort(key=lambda u: u.started_at)
        return rows[:limit], len(rows)

    # --- deletion ---------------------------------------------------------

    async def delete_for_session(self, session_id: str) -> int:
        """Drop this session's rows.

        Called on rebuild and when a session is deleted. A derived index that
        outlives its source would answer questions about audio the user
        believes they erased.
        """
        rows = await self._utterances.list(session_id=session_id)
        for row in rows:
            await self._utterances.delete(row.id)
        return len(rows)
