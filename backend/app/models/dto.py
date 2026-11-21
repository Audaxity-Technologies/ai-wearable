"""API request and response shapes."""

from __future__ import annotations

import datetime as dt
from datetime import date, datetime, time

from pydantic import BaseModel, Field

from app.models.actions import Action, ActionEvent, ActionStatus, ActionType
from app.models.analysis import (
    AnalysisItem,
    ConversationAnalysis,
    DateResolution,
    DroppedItem,
    Participant,
    SpeechAct,
)
from app.models.domain import Segment, Session, TranscriptSpan
from app.models.memory import Memory, MemoryKind
from app.models.tasks import Task, TaskStatus
from app.models.places import CoarsePlace, SessionPlace
from app.models.status import SegmentStatus, SessionStatus


class OpenSessionRequest(BaseModel):
    tz: str = Field(description="IANA timezone, e.g. Asia/Kolkata")
    started_at: datetime | None = Field(
        default=None,
        description="Client start time with UTC offset. Defaults to now.",
    )
    title: str | None = None


class SessionSummary(BaseModel):
    id: str
    user_id: str
    status: SessionStatus
    tz: str
    title: str | None
    started_at: datetime
    closed_at: datetime | None
    duration_s: float
    segment_count: int
    language: str | None
    updated_at: datetime
    failed_stage: str | None = None
    error: str | None = None
    #: Where the conversation happened, busiest first. Empty whenever place
    #: capture is off, which is the default.
    places: list[SessionPlace] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, session: Session) -> SessionSummary:
        return cls(
            id=session.id,
            user_id=session.user_id,
            status=session.status,
            tz=session.tz,
            title=session.title,
            started_at=session.started_at,
            closed_at=session.closed_at,
            duration_s=session.duration_s,
            segment_count=len(session.segment_ids),
            language=session.language,
            updated_at=session.updated_at,
            failed_stage=session.failed_stage,
            error=session.error,
            places=session.places,
        )


class SessionList(BaseModel):
    sessions: list[SessionSummary]


class IngestAccepted(BaseModel):
    """Returned by the ingest endpoint.

    `duplicate` is true when this segment_id was already stored, which is the
    normal outcome of a client retry.
    """

    segment_id: str
    session_id: str
    status: SegmentStatus
    duplicate: bool = False


class SegmentTranscript(BaseModel):
    segment_id: str
    seq: int
    started_at: datetime
    duration_s: float
    status: SegmentStatus
    text: str | None
    language: str | None
    spans: list[TranscriptSpan]
    error: str | None = None
    place: CoarsePlace | None = None

    @classmethod
    def from_domain(cls, segment: Segment) -> SegmentTranscript:
        return cls(
            segment_id=segment.id,
            seq=segment.seq,
            started_at=segment.started_at,
            duration_s=segment.duration_s,
            status=segment.status,
            text=segment.text,
            language=segment.language,
            spans=segment.spans,
            error=segment.error,
            place=segment.place,
        )


class TranscriptResponse(BaseModel):
    session_id: str
    status: SessionStatus
    language: str | None
    transcript: str | None
    segments: list[SegmentTranscript]


class AnalysisResponse(BaseModel):
    """Structured analysis for one session.

    `needs_clarification` is surfaced per item so the app can ask instead of
    scheduling a guess, and `dropped` is exposed so prompt regressions are
    visible during development rather than silent.
    """

    session_id: str
    title: str
    summary: str
    participants: list[Participant]
    projects: list[str]
    topics: list[str]
    items: list[AnalysisItem]
    actionable_count: int
    needs_clarification_count: int
    dropped: list[DroppedItem]
    degraded: bool
    degraded_reason: str | None
    provider: str
    model: str
    repair_attempted: bool

    @classmethod
    def from_domain(cls, analysis: ConversationAnalysis) -> AnalysisResponse:
        return cls(
            session_id=analysis.session_id,
            title=analysis.title,
            summary=analysis.summary,
            participants=analysis.participants,
            projects=analysis.projects,
            topics=analysis.topics,
            items=analysis.items,
            actionable_count=len(analysis.actionable_items),
            needs_clarification_count=sum(
                1 for item in analysis.items if item.needs_clarification
            ),
            dropped=analysis.dropped,
            degraded=analysis.degraded,
            degraded_reason=analysis.degraded_reason,
            provider=analysis.provider,
            model=analysis.model,
            repair_attempted=analysis.repair_attempted,
        )


class MemoryResponse(BaseModel):
    """One memory, with the provenance that justifies it."""

    id: str
    kind: MemoryKind
    name: str
    content: str
    attributes: dict[str, str]
    confidence: float
    mention_count: int
    first_seen: datetime | None
    last_seen: datetime | None
    source_session_ids: list[str]
    source_quotes: list[str]
    conflicts_with: list[str]
    has_conflict: bool
    is_cross_conversation: bool
    #: Only set on search results.
    score: float | None = None
    matched_terms: list[str] | None = None

    @classmethod
    def from_domain(
        cls,
        memory: Memory,
        *,
        score: float | None = None,
        matched_terms: list[str] | None = None,
    ) -> MemoryResponse:
        return cls(
            id=memory.id,
            kind=memory.kind,
            name=memory.name,
            content=memory.content,
            attributes=memory.attributes,
            confidence=memory.confidence,
            mention_count=memory.mention_count,
            first_seen=memory.first_seen,
            last_seen=memory.last_seen,
            source_session_ids=memory.source_session_ids,
            source_quotes=memory.source_quotes,
            conflicts_with=memory.conflicts_with,
            has_conflict=memory.has_conflict,
            is_cross_conversation=memory.is_cross_conversation,
            score=score,
            matched_terms=matched_terms,
        )


class MemoryList(BaseModel):
    memories: list[MemoryResponse]
    total: int


class MemorySearchResponse(BaseModel):
    query: str
    results: list[MemoryResponse]
    total: int


class TaskResponse(BaseModel):
    id: str
    title: str
    owner: str
    act: SpeechAct
    status: TaskStatus
    project: str | None
    tz: str
    due_date: date | None
    due_time: time | None
    due_raw: str | None
    due_resolution: DateResolution
    needs_clarification: bool
    is_overdue: bool
    source_session_ids: list[str]
    source_quotes: list[str]
    edited: bool
    completed_at: datetime | None

    @classmethod
    def from_domain(cls, task: Task, *, overdue: bool = False) -> TaskResponse:
        return cls(
            id=task.id,
            title=task.title,
            owner=task.owner,
            act=task.act,
            status=task.status,
            project=task.project,
            tz=task.tz,
            due_date=task.due_date,
            due_time=task.due_time,
            due_raw=task.due_raw,
            due_resolution=task.due_resolution,
            needs_clarification=task.needs_clarification,
            is_overdue=overdue,
            source_session_ids=task.source_session_ids,
            source_quotes=task.source_quotes,
            edited=task.edited,
            completed_at=task.completed_at,
        )


class TaskList(BaseModel):
    tasks: list[TaskResponse]
    total: int


class UpdateTaskRequest(BaseModel):
    title: str | None = None
    owner: str | None = None
    status: TaskStatus | None = None
    due_date: date | None = None
    due_time: time | None = None
    project: str | None = None
    clear_due: bool = False


class ActionResponse(BaseModel):
    """A proposed action, with everything the approval card needs."""

    id: str
    type: ActionType
    title: str
    status: ActionStatus
    reason: str
    payload: dict[str, str]
    project: str | None
    needs_clarification: bool
    source_session_id: str
    source_quote: str
    task_id: str | None
    edited: bool
    decided_at: datetime | None
    executed_at: datetime | None
    rejection_reason: str | None
    execution_result: str | None
    history: list[ActionEvent]

    @classmethod
    def from_domain(cls, action: Action) -> ActionResponse:
        return cls(
            id=action.id,
            type=action.type,
            title=action.title,
            status=action.status,
            reason=action.reason,
            payload=action.payload,
            project=action.project,
            needs_clarification=action.needs_clarification,
            source_session_id=action.source_session_id,
            source_quote=action.source_quote,
            task_id=action.task_id,
            edited=action.edited,
            decided_at=action.decided_at,
            executed_at=action.executed_at,
            rejection_reason=action.rejection_reason,
            execution_result=action.execution_result,
            history=action.history,
        )


class ActionList(BaseModel):
    actions: list[ActionResponse]
    total: int
    needs_clarification_count: int = 0


class UpdateActionRequest(BaseModel):
    # `dt.date` / `dt.time` are qualified because these field names shadow the
    # bare type names inside the class body.
    title: str | None = None
    date: dt.date | None = None
    time: dt.time | None = None
    type: ActionType | None = None


class RejectActionRequest(BaseModel):
    reason: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    #: Used to resolve "today" when the answer involves dates.
    tz: str = "UTC"
    limit: int = Field(default=8, ge=1, le=20)


class HealthResponse(BaseModel):
    status: str
    app_env: str
    transcription_provider: str
    transcription_ready: bool
    transcription_detail: str
    llm_provider: str
    llm_ready: bool
    llm_detail: str
    pipeline_pending_jobs: int


class ErrorResponse(BaseModel):
    code: str
    message: str
