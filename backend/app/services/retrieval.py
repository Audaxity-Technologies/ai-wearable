"""Retrieval over conversations, memories and tasks.

Keyword and metadata scoring, as the architecture doc commits to. With one
person's conversations this beats a vector database on every axis that matters
early: it is debuggable (you can see exactly which term matched), it has no
index to keep in sync, and it fails visibly rather than returning plausible
nonsense. Revisit when "what did we decide about the pitch" starts missing
"investor deck" — alias lists first, embeddings only if those stop being
enough.

Three source types are searched, because a question can be answered by
different kinds of evidence:

- **conversations** - what was actually said, and when
- **memories**      - what we concluded across conversations
- **tasks**         - what is still outstanding
"""

from __future__ import annotations

import datetime as dt

from app.core.logging import get_logger, log_event
from app.core.text import normalise
from app.core.time_utils import utc_now
from app.models.analysis import ConversationAnalysis
from app.models.ask import RetrievalResult, RetrievedItem, SourceKind
from app.models.domain import Session
from app.models.memory import Memory
from app.models.tasks import Task, TaskStatus
from app.repositories.base import Repository
from app.services.memory import STOPWORDS

logger = get_logger(__name__)

DEFAULT_LIMIT = 8

#: Field weights. A hit in a title or a name is a stronger signal than one
#: buried in a transcript.
WEIGHT_TITLE = 3.0
WEIGHT_CONTENT = 2.0
WEIGHT_QUOTE = 1.5
WEIGHT_TRANSCRIPT = 1.0

#: Questions about outstanding work should surface tasks even when the
#: wording does not match any particular task's text.
TASK_INTENT_TERMS = frozenset(
    {
        "pending", "outstanding", "overdue", "todo", "todos", "task", "tasks",
        "owe", "owes", "due", "deadline", "deadlines", "promised", "promise",
        "commitments", "commitment", "forget", "forgot", "follow", "remaining",
        "left", "supposed",
    }
)


def query_terms(query: str) -> list[str]:
    """Meaningful tokens from a question, stopwords removed."""
    return [
        term
        for term in normalise(query).split()
        if term and term not in STOPWORDS and len(term) > 1
    ]


def _score(terms: list[str], text: str, weight: float) -> tuple[float, list[str]]:
    if not text:
        return 0.0, []
    haystack = normalise(text)
    score = 0.0
    matched: list[str] = []
    for term in terms:
        if term in haystack:
            score += weight
            matched.append(term)
    return score, matched


class RetrievalService:
    def __init__(
        self,
        *,
        sessions: Repository[Session],
        analyses: Repository[ConversationAnalysis],
        memories: Repository[Memory],
        tasks: Repository[Task],
    ) -> None:
        self._sessions = sessions
        self._analyses = analyses
        self._memories = memories
        self._tasks = tasks

    async def retrieve(
        self,
        query: str,
        *,
        user_id: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> RetrievalResult:
        terms = query_terms(query)
        if not terms:
            # A question made entirely of stopwords retrieves nothing, and the
            # caller will answer honestly rather than guessing.
            return RetrievalResult(query=query, terms=[])

        wants_tasks = bool(set(terms) & TASK_INTENT_TERMS)
        candidates: list[RetrievedItem] = []
        candidates += await self._score_conversations(terms, user_id)
        candidates += await self._score_memories(terms, user_id)
        candidates += await self._score_tasks(terms, user_id, wants_tasks)

        candidates.sort(key=lambda item: item.score, reverse=True)
        top = candidates[:limit]
        for index, item in enumerate(top, start=1):
            item.label = index

        log_event(
            logger,
            "retrieval.completed",
            terms=terms,
            candidates=len(candidates),
            returned=len(top),
            kinds=sorted({item.kind.value for item in top}),
        )
        return RetrievalResult(query=query, terms=terms, items=top)

    # --- per-source scoring ------------------------------------------------

    async def _score_conversations(
        self, terms: list[str], user_id: str | None
    ) -> list[RetrievedItem]:
        filters = {"user_id": user_id} if user_id else {}
        sessions = {s.id: s for s in await self._sessions.list(**filters)}
        items: list[RetrievedItem] = []

        for analysis in await self._analyses.list():
            session = sessions.get(analysis.session_id)
            if session is None:
                continue  # belongs to another user

            item_lines = [
                f"{entry.owner}: {entry.title}"
                + (
                    f" (due {entry.due.date.isoformat()})"
                    if entry.due.date
                    else ""
                )
                for entry in analysis.items
            ]
            content = "\n".join(
                filter(None, [analysis.summary, *item_lines, *analysis.projects])
            )
            quotes = [entry.source_quote for entry in analysis.items]

            title_score, title_terms = _score(
                terms,
                f"{analysis.title} {' '.join(analysis.projects)} "
                f"{' '.join(p.name for p in analysis.participants)}",
                WEIGHT_TITLE,
            )
            content_score, content_terms = _score(terms, content, WEIGHT_CONTENT)
            quote_score, quote_terms = _score(
                terms, " ".join(quotes), WEIGHT_QUOTE
            )
            transcript_score, transcript_terms = _score(
                terms, session.transcript or "", WEIGHT_TRANSCRIPT
            )

            total = title_score + content_score + quote_score + transcript_score
            if total <= 0:
                continue

            items.append(
                RetrievedItem(
                    label=0,
                    kind=SourceKind.CONVERSATION,
                    id=analysis.session_id,
                    session_id=analysis.session_id,
                    title=analysis.title or "Conversation",
                    content=content or (session.transcript or "")[:800],
                    quotes=quotes[:6],
                    occurred_at=session.started_at,
                    score=round(
                        total + self._recency_bonus(session.started_at), 3
                    ),
                    matched_terms=sorted(
                        set(
                            title_terms
                            + content_terms
                            + quote_terms
                            + transcript_terms
                        )
                    ),
                )
            )
        return items

    async def _score_memories(
        self, terms: list[str], user_id: str | None
    ) -> list[RetrievedItem]:
        filters = {"user_id": user_id} if user_id else {}
        items: list[RetrievedItem] = []

        for memory in await self._memories.list(**filters):
            name_score, name_terms = _score(terms, memory.name, WEIGHT_TITLE)
            content_score, content_terms = _score(
                terms, memory.content, WEIGHT_CONTENT
            )
            quote_score, quote_terms = _score(
                terms, " ".join(memory.source_quotes), WEIGHT_QUOTE
            )
            total = name_score + content_score + quote_score
            if total <= 0:
                continue

            # Something several conversations corroborated outranks a one-off.
            total += min(memory.mention_count, 5) * 0.2
            if memory.has_conflict:
                # A contradiction is exactly what a user asking about status
                # needs to see.
                total += 1.0

            items.append(
                RetrievedItem(
                    label=0,
                    kind=SourceKind.MEMORY,
                    id=memory.id,
                    session_id=(
                        memory.source_session_ids[-1]
                        if memory.source_session_ids
                        else None
                    ),
                    title=memory.name,
                    content=(
                        memory.content
                        + (
                            " [CONFLICTS with another recorded version]"
                            if memory.has_conflict
                            else ""
                        )
                    ),
                    quotes=memory.source_quotes[:4],
                    occurred_at=memory.last_seen,
                    score=round(total, 3),
                    matched_terms=sorted(
                        set(name_terms + content_terms + quote_terms)
                    ),
                )
            )
        return items

    async def _score_tasks(
        self, terms: list[str], user_id: str | None, wants_tasks: bool
    ) -> list[RetrievedItem]:
        filters = {"user_id": user_id} if user_id else {}
        items: list[RetrievedItem] = []

        for task in await self._tasks.list(**filters):
            title_score, title_terms = _score(terms, task.title, WEIGHT_TITLE)
            meta_score, meta_terms = _score(
                terms, f"{task.owner} {task.project or ''}", WEIGHT_CONTENT
            )
            quote_score, quote_terms = _score(
                terms, " ".join(task.source_quotes), WEIGHT_QUOTE
            )
            total = title_score + meta_score + quote_score

            # "What is still pending?" names no task in particular, so open
            # tasks are included on intent alone.
            if wants_tasks and task.status is TaskStatus.PENDING:
                total += 2.0

            if total <= 0:
                continue

            due = (
                f"due {task.due_date.isoformat()}"
                if task.due_date
                else (
                    f"due {task.due_raw!r} (unconfirmed)"
                    if task.due_raw
                    else "no deadline"
                )
            )
            items.append(
                RetrievedItem(
                    label=0,
                    kind=SourceKind.TASK,
                    id=task.id,
                    session_id=(
                        task.source_session_ids[-1]
                        if task.source_session_ids
                        else None
                    ),
                    title=task.title,
                    content=(
                        f"Task [{task.status.value}] owner={task.owner}, "
                        f"{due}, from {task.act.value}"
                    ),
                    quotes=task.source_quotes[:3],
                    occurred_at=task.created_at,
                    score=round(total, 3),
                    matched_terms=sorted(
                        set(title_terms + meta_terms + quote_terms)
                    ),
                )
            )
        return items

    @staticmethod
    def _recency_bonus(when: dt.datetime | None) -> float:
        """A small nudge toward recent conversations.

        Capped low on purpose: recency should break ties, not outrank a
        conversation that actually answers the question.
        """
        if when is None:
            return 0.0
        age_days = (utc_now() - when).days
        if age_days <= 1:
            return 0.5
        if age_days <= 7:
            return 0.25
        return 0.0
