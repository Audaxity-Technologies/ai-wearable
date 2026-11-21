"""Persistent memory: entity resolution, dedup, conflicts, retrieval.

## What gets remembered

Selectivity is the whole game. A system that remembers everything is a
transcript archive; a system that remembers the right things is an assistant.
Three categories are stored, and nothing else:

1. **People** who took part in or were named in a conversation.
2. **Projects** that were discussed.
3. **Durable facts** - decisions, commitments, assignments and requirements.

Deliberately NOT stored: suggestions ("maybe we should..."), open questions,
and general discussion. A floated idea is not a fact about the world, and
promoting one to memory is how an assistant starts confidently telling you
things you never agreed to.

## What happens on a re-mention

The same project discussed on Monday and Wednesday is one memory with two
source sessions, a higher confidence and a bumped `last_seen` - not two rows.

## What happens on a contradiction

Nothing is overwritten. If Monday's "ship by Friday" becomes Wednesday's "ship
by next Tuesday", both rows survive and reference each other through
`conflicts_with`. The app shows both and lets the user resolve it. Silently
replacing the old value destroys the one thing that makes the contradiction
worth surfacing.
"""

from __future__ import annotations

from datetime import datetime

from app.core.errors import NotFoundError
from app.core.logging import get_logger, log_event
from app.core.text import normalise, quote_similarity
from app.models.analysis import (
    ACTIONABLE_ACTS,
    ConversationAnalysis,
    DateResolution,
    SpeechAct,
)
from app.models.domain import Session
from app.models.memory import (
    Memory,
    MemoryCandidate,
    MemoryHit,
    MemoryKind,
    MemoryWriteResult,
)
from app.repositories.base import Repository

logger = get_logger(__name__)

#: Acts durable enough to remember as facts. Suggestions, questions and
#: general discussion are deliberately excluded.
DURABLE_ACTS = frozenset(ACTIONABLE_ACTS | {SpeechAct.DECISION})

#: Items below this confidence are not worth remembering.
MIN_FACT_CONFIDENCE = 0.4

#: Name similarity above which two entities are treated as the same ("Manan"
#: vs "Manan Dudeja"). Below it they stay separate.
ENTITY_MATCH_THRESHOLD = 0.85

#: How closely a `supersedes` hint must match an existing fact before the two
#: are linked as a conflict. Lower than entity matching because the hint is a
#: paraphrase, not a name; still high enough to avoid false conflicts.
SUPERSEDES_MATCH_THRESHOLD = 0.5

#: Words ignored when scoring a search query.
STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "to", "of", "in", "on", "at", "for", "with", "about", "and", "or",
        "what", "when", "who", "did", "do", "does", "i", "we", "you", "my",
        "our", "it", "that", "this", "any", "all", "have", "has", "had",
    }
)


class MemoryService:
    def __init__(self, *, memories: Repository[Memory]) -> None:
        self._memories = memories

    # --- writing ----------------------------------------------------------

    async def write_from_analysis(
        self, session: Session, analysis: ConversationAnalysis
    ) -> MemoryWriteResult:
        result = MemoryWriteResult()
        if analysis.degraded:
            # Nothing trustworthy to remember.
            return result

        existing = await self._memories.list(user_id=session.user_id)
        for candidate in self._candidates(analysis):
            await self._apply(
                candidate,
                session=session,
                existing=existing,
                result=result,
            )

        log_event(
            logger,
            "memory.written",
            session_id=session.id,
            created=len(result.created),
            updated=len(result.updated),
            conflicted=len(result.conflicted),
            skipped=result.skipped,
        )
        return result

    def _candidates(
        self, analysis: ConversationAnalysis
    ) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []

        for person in analysis.participants:
            if not person.name.strip():
                continue
            candidates.append(
                MemoryCandidate(
                    kind=MemoryKind.PERSON,
                    key=f"person:{normalise(person.name)}",
                    name=person.name.strip(),
                    content=(
                        f"{person.name.strip()} took part in a conversation."
                        if person.speaking
                        else f"{person.name.strip()} was mentioned in a "
                        "conversation."
                    ),
                    attributes={"speaking": str(person.speaking).lower()},
                    confidence=0.7 if person.speaking else 0.5,
                )
            )

        for project in analysis.projects:
            if not project.strip():
                continue
            candidates.append(
                MemoryCandidate(
                    kind=MemoryKind.PROJECT,
                    key=f"project:{normalise(project)}",
                    name=project.strip(),
                    content=f"{project.strip()} is an active project.",
                    confidence=0.7,
                )
            )

        for item in analysis.items:
            if item.act not in DURABLE_ACTS:
                continue  # suggestions and questions are not facts
            if item.confidence < MIN_FACT_CONFIDENCE:
                continue
            candidates.append(self._fact_candidate(item))

        return candidates

    @staticmethod
    def _fact_candidate(item) -> MemoryCandidate:
        project = (item.project or "").strip()
        attributes = {
            "act": item.act.value,
            "owner": item.owner,
        }
        if project:
            attributes["project"] = project
        if item.due.date:
            attributes["due_date"] = item.due.date.isoformat()
        if item.due.raw:
            attributes["due_raw"] = item.due.raw
        attributes["due_resolution"] = item.due.resolution.value

        if item.act is SpeechAct.COMMITMENT:
            content = f"{item.owner} committed to: {item.title}"
        elif item.act is SpeechAct.ASSIGNMENT:
            content = f"{item.title} — assigned to {item.owner}"
        elif item.act is SpeechAct.DECISION:
            content = f"Decided: {item.title}"
        else:
            content = f"Needs doing: {item.title}"

        if item.due.date:
            content += f" (due {item.due.date.isoformat()})"
        elif item.due.resolution is DateResolution.AMBIGUOUS and item.due.raw:
            content += f" (due {item.due.raw} — needs clarification)"

        return MemoryCandidate(
            kind=MemoryKind.FACT,
            key=f"fact:{normalise(project)}:{normalise(item.title)}",
            name=item.title,
            content=content,
            attributes=attributes,
            source_quote=item.source_quote,
            confidence=item.confidence,
            supersedes=item.supersedes,
        )

    async def _apply(
        self,
        candidate: MemoryCandidate,
        *,
        session: Session,
        existing: list[Memory],
        result: MemoryWriteResult,
    ) -> None:
        match = self._resolve(candidate, existing)

        if match is None:
            # A change is usually worded differently from the thing it
            # changes ("investor pitch deadline moved" vs "investor pitch
            # ready by Friday"), so the key never matches. The extractor's
            # `supersedes` hint is what links them.
            superseded = self._find_superseded(candidate, existing)
            memory = self._create(candidate, session)

            if superseded is not None:
                memory.conflicts_with = [superseded.id]
                await self._memories.put(memory)
                superseded.conflicts_with = sorted(
                    {*superseded.conflicts_with, memory.id}
                )
                superseded.touch()
                await self._memories.put(superseded)
                existing.append(memory)
                result.conflicted.append(memory.id)
                log_event(
                    logger,
                    "memory.superseded",
                    session_id=session.id,
                    existing_id=superseded.id,
                    new_id=memory.id,
                    hint=candidate.supersedes,
                )
                return

            await self._memories.put(memory)
            existing.append(memory)
            result.created.append(memory.id)
            return

        if candidate.kind is MemoryKind.FACT and self._contradicts(
            match, candidate
        ):
            conflict = self._create(candidate, session)
            conflict.conflicts_with = [match.id]
            await self._memories.put(conflict)

            match.conflicts_with = sorted({*match.conflicts_with, conflict.id})
            match.touch()
            await self._memories.put(match)

            existing.append(conflict)
            result.conflicted.append(conflict.id)
            log_event(
                logger,
                "memory.conflict",
                session_id=session.id,
                existing_id=match.id,
                new_id=conflict.id,
                field=self._contradiction_field(match, candidate),
            )
            return

        self._merge(match, candidate, session)
        await self._memories.put(match)
        result.updated.append(match.id)

    @staticmethod
    def _create(candidate: MemoryCandidate, session: Session) -> Memory:
        return Memory(
            user_id=session.user_id,
            kind=candidate.kind,
            key=candidate.key,
            name=candidate.name,
            content=candidate.content,
            attributes=dict(candidate.attributes),
            source_session_ids=[session.id],
            source_quotes=(
                [candidate.source_quote] if candidate.source_quote else []
            ),
            confidence=candidate.confidence,
            mention_count=1,
            first_seen=session.started_at,
            last_seen=session.started_at,
        )

    @staticmethod
    def _merge(
        memory: Memory, candidate: MemoryCandidate, session: Session
    ) -> None:
        if session.id not in memory.source_session_ids:
            memory.source_session_ids.append(session.id)
            # Only a genuinely new conversation counts as another mention.
            memory.mention_count += 1
            # Corroboration across conversations raises confidence, with a
            # ceiling so nothing ever reads as certain.
            memory.confidence = min(0.99, memory.confidence + 0.1)

        if (
            candidate.source_quote
            and candidate.source_quote not in memory.source_quotes
        ):
            memory.source_quotes.append(candidate.source_quote)

        # Prefer the fuller name: "Manan Dudeja" over "Manan".
        if len(candidate.name) > len(memory.name):
            memory.name = candidate.name

        for key, value in candidate.attributes.items():
            memory.attributes.setdefault(key, value)

        if memory.first_seen is None or session.started_at < memory.first_seen:
            memory.first_seen = session.started_at
        if memory.last_seen is None or session.started_at > memory.last_seen:
            memory.last_seen = session.started_at
            # The newest statement wins for display; older ones survive in the
            # quotes and, on a contradiction, as their own row.
            memory.content = candidate.content

        memory.touch()

    def _resolve(
        self, candidate: MemoryCandidate, existing: list[Memory]
    ) -> Memory | None:
        """Find the memory this candidate refers to, if any."""
        same_kind = [m for m in existing if m.kind is candidate.kind]

        for memory in same_kind:
            if memory.key == candidate.key:
                return memory

        # People and projects get fuzzy name matching, so "Manan" and "Manan
        # Dudeja" do not become two entities. Facts do not: two differently
        # worded facts are usually genuinely different.
        if candidate.kind not in (MemoryKind.PERSON, MemoryKind.PROJECT):
            return None

        best: Memory | None = None
        best_ratio = 0.0
        for memory in same_kind:
            ratio = self._name_similarity(candidate.name, memory.name)
            if ratio > best_ratio:
                best, best_ratio = memory, ratio
        return best if best_ratio >= ENTITY_MATCH_THRESHOLD else None

    @staticmethod
    def _name_similarity(left: str, right: str) -> float:
        a, b = normalise(left), normalise(right)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        a_tokens, b_tokens = set(a.split()), set(b.split())
        # "manan" vs "manan dudeja": one name's tokens contain the other's.
        if a_tokens and b_tokens and (a_tokens <= b_tokens or b_tokens <= a_tokens):
            return 1.0
        return quote_similarity(a, b)

    def _find_superseded(
        self, candidate: MemoryCandidate, existing: list[Memory]
    ) -> Memory | None:
        """Find the earlier fact that `candidate.supersedes` describes.

        Conservative on purpose: with no hint from the extractor, or no clear
        match, this returns None and the item is simply stored as new. A
        missed conflict is a far smaller harm than wrongly telling someone two
        unrelated commitments contradict each other.
        """
        hint = (candidate.supersedes or "").strip()
        if not hint or candidate.kind is not MemoryKind.FACT:
            return None

        project = normalise(candidate.attributes.get("project", ""))
        best: Memory | None = None
        best_ratio = 0.0

        for memory in existing:
            if memory.kind is not MemoryKind.FACT:
                continue
            if memory.conflicts_with:
                continue  # already superseded once; leave the chain alone
            if project and normalise(
                memory.attributes.get("project", "")
            ) not in (project, ""):
                continue

            # Compare the hint against the stored fact's own wording and the
            # quote that produced it.
            haystack = f"{memory.name} {memory.content} {' '.join(memory.source_quotes)}"
            ratio = max(
                quote_similarity(hint, memory.name),
                quote_similarity(hint, haystack),
            )
            if ratio > best_ratio:
                best, best_ratio = memory, ratio

        return best if best_ratio >= SUPERSEDES_MATCH_THRESHOLD else None

    @staticmethod
    def _contradiction_field(memory: Memory, candidate: MemoryCandidate) -> str:
        for field in ("due_date", "owner"):
            old, new = memory.attributes.get(field), candidate.attributes.get(field)
            if old and new and old != new:
                return field
        return "unknown"

    @staticmethod
    def _contradicts(memory: Memory, candidate: MemoryCandidate) -> bool:
        """True when the same fact now carries a different date or owner.

        Only a *changed* value counts. Newly learning a date we did not have
        before is new information, not a contradiction.
        """
        for field in ("due_date", "owner"):
            old = memory.attributes.get(field)
            new = candidate.attributes.get(field)
            if not old or not new or old == new:
                continue
            if field == "owner" and "unknown" in (old, new):
                # Learning the owner of a previously unowned item is not a
                # contradiction.
                continue
            return True
        return False

    # --- reading ----------------------------------------------------------

    async def get(self, memory_id: str) -> Memory:
        memory = await self._memories.get(memory_id)
        if memory is None:
            raise NotFoundError(f"Memory not found: {memory_id}")
        return memory

    async def list_memories(
        self,
        *,
        user_id: str | None = None,
        kind: MemoryKind | None = None,
        project: str | None = None,
        conflicts_only: bool = False,
    ) -> list[Memory]:
        filters: dict[str, object] = {}
        if user_id:
            filters["user_id"] = user_id
        if kind:
            filters["kind"] = kind
        memories = await self._memories.list(**filters)

        if project:
            wanted = normalise(project)
            memories = [
                m
                for m in memories
                if normalise(m.attributes.get("project", "")) == wanted
                or (m.kind is MemoryKind.PROJECT and normalise(m.name) == wanted)
            ]
        if conflicts_only:
            memories = [m for m in memories if m.has_conflict]

        return sorted(
            memories,
            key=lambda m: (m.last_seen or m.created_at),
            reverse=True,
        )

    async def search(
        self, query: str, *, user_id: str | None = None, limit: int = 20
    ) -> list[MemoryHit]:
        """Keyword search across names, content and source quotes.

        Deliberately simple: with a corpus of one person's conversations,
        token overlap is debuggable and good enough. Revisit when "what did we
        decide about the pitch" starts missing "investor deck".
        """
        terms = [
            term
            for term in normalise(query).split()
            if term and term not in STOPWORDS
        ]
        if not terms:
            return []

        memories = await self.list_memories(user_id=user_id)
        hits: list[MemoryHit] = []
        for memory in memories:
            name = normalise(memory.name)
            content = normalise(memory.content)
            quotes = normalise(" ".join(memory.source_quotes))

            score = 0.0
            matched: list[str] = []
            for term in terms:
                if term in name:
                    score += 3.0
                    matched.append(term)
                elif term in content:
                    score += 2.0
                    matched.append(term)
                elif term in quotes:
                    score += 1.0
                    matched.append(term)

            if score <= 0:
                continue
            # Corroborated memories outrank one-off mentions on equal matches.
            score += min(memory.mention_count, 5) * 0.1 + memory.confidence * 0.1
            hits.append(
                MemoryHit(memory=memory, score=round(score, 3), matched_terms=matched)
            )

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    # --- deletion ---------------------------------------------------------

    async def delete(self, memory_id: str) -> None:
        """User correction: remove a memory and unlink it from its conflicts."""
        memory = await self.get(memory_id)
        for other_id in memory.conflicts_with:
            other = await self._memories.get(other_id)
            if other is None:
                continue
            other.conflicts_with = [
                value for value in other.conflicts_with if value != memory_id
            ]
            other.touch()
            await self._memories.put(other)
        await self._memories.delete(memory_id)
        log_event(logger, "memory.deleted", memory_id=memory_id)

    async def delete_for_session(self, session_id: str) -> int:
        """Remove this session's contribution to memory.

        A memory supported only by this conversation is deleted. One supported
        by others survives with this session detached - deleting it would
        destroy information the user never asked to remove.
        """
        removed = 0
        for memory in await self._memories.list():
            if session_id not in memory.source_session_ids:
                continue
            if len(memory.source_session_ids) == 1:
                await self.delete(memory.id)
                removed += 1
                continue
            memory.source_session_ids = [
                value for value in memory.source_session_ids if value != session_id
            ]
            memory.mention_count = max(1, memory.mention_count - 1)
            memory.touch()
            await self._memories.put(memory)
        if removed:
            log_event(
                logger,
                "memory.session_purged",
                session_id=session_id,
                deleted=removed,
            )
        return removed
