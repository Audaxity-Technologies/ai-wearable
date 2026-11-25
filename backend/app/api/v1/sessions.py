"""Session routes.

Every route resolves the acting user through `CurrentUserId`, and every
per-session route checks ownership. A session belonging to someone else is
reported as missing rather than forbidden: "403" would confirm that the id
exists, which is itself a leak.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Query, Response, status

from app.api.deps import CurrentUserId, SessionServiceDep
from app.models.dto import (
    AnalysisResponse,
    OpenSessionRequest,
    SegmentTranscript,
    SessionList,
    SessionSummary,
    TranscriptResponse,
)
from app.models.domain import ParticipationRole
from app.models.status import SessionStatus
from app.services.integrity import CaptureReceipt

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post(
    "",
    response_model=SessionSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Open a recording session",
)
async def open_session(
    body: OpenSessionRequest,
    service: SessionServiceDep,
    user_id: CurrentUserId,
) -> SessionSummary:
    session = await service.open_session(
        tz=body.tz,
        started_at=body.started_at,
        title=body.title,
        user_id=user_id,
    )
    return SessionSummary.from_domain(session)


@router.get("", response_model=SessionList, summary="List sessions")
async def list_sessions(
    service: SessionServiceDep,
    user_id: CurrentUserId,
    session_status: Annotated[SessionStatus | None, Query(alias="status")] = None,
    updated_since: Annotated[datetime | None, Query()] = None,
) -> SessionList:
    sessions = await service.list_sessions(
        user_id=user_id, status=session_status, updated_since=updated_since
    )
    return SessionList(
        sessions=[SessionSummary.from_domain(item) for item in sessions]
    )


@router.get("/{session_id}", response_model=SessionSummary)
async def get_session(
    session_id: str, service: SessionServiceDep, user_id: CurrentUserId
) -> SessionSummary:
    return SessionSummary.from_domain(
        await service.get_session(session_id, user_id=user_id)
    )


@router.post(
    "/{session_id}/close",
    response_model=SessionSummary,
    summary="Close a session and trigger the pipeline",
)
async def close_session(
    session_id: str, service: SessionServiceDep, user_id: CurrentUserId
) -> SessionSummary:
    return SessionSummary.from_domain(
        await service.close_session(session_id, user_id=user_id)
    )


@router.get(
    "/{session_id}/transcript",
    response_model=TranscriptResponse,
    summary="Full transcript plus per-segment timestamps",
)
async def get_transcript(
    session_id: str, service: SessionServiceDep, user_id: CurrentUserId
) -> TranscriptResponse:
    session = await service.get_session(session_id, user_id=user_id)
    segments = await service.get_segments(session_id)
    return TranscriptResponse(
        session_id=session.id,
        status=session.status,
        language=session.language,
        transcript=session.transcript,
        segments=[SegmentTranscript.from_domain(item) for item in segments],
    )


@router.get(
    "/{session_id}/analysis",
    response_model=AnalysisResponse,
    summary="Structured analysis: items, owners, speech acts, resolved dates",
)
async def get_analysis(
    session_id: str, service: SessionServiceDep, user_id: CurrentUserId
) -> AnalysisResponse:
    return AnalysisResponse.from_domain(
        await service.get_analysis(session_id, user_id=user_id)
    )


@router.post(
    "/{session_id}/participants",
    response_model=SessionSummary,
    summary="Say who was in this conversation",
)
async def add_participant(
    session_id: str,
    service: SessionServiceDep,
    user_id: CurrentUserId,
    name: Annotated[str, Body(embed=True)],
    role: Annotated[ParticipationRole, Body(embed=True)] = (
        ParticipationRole.SPOKE
    ),
) -> SessionSummary:
    """Works after the fact, which is the point.

    The name resolves through the people index, so "Pull kit" lands on the
    existing Pulkit, and the session is recorded on that person - which is
    how the relationship accumulates across conversations.

    This is the only piece of attribution evidence that is **certain**: a
    voiceprint is probabilistic and an extracted owner is the model's reading
    of the words, while "Pulkit was here" is a fact only the user has.
    """
    return SessionSummary.from_domain(
        await service.add_participant(
            session_id, name, role=role, user_id=user_id
        )
    )


@router.delete(
    "/{session_id}/participants/{person_id}",
    response_model=SessionSummary,
    summary="Remove a participant label",
)
async def remove_participant(
    session_id: str,
    person_id: str,
    service: SessionServiceDep,
    user_id: CurrentUserId,
) -> SessionSummary:
    """Unlabels this conversation only. The person and their other
    conversations are untouched."""
    return SessionSummary.from_domain(
        await service.remove_participant(
            session_id, person_id, user_id=user_id
        )
    )


@router.get(
    "/{session_id}/receipt",
    response_model=CaptureReceipt,
    summary="What was captured for this session, and what was not",
)
async def get_receipt(
    session_id: str, service: SessionServiceDep, user_id: CurrentUserId
) -> CaptureReceipt:
    """Deliberately available at any status, including failed sessions.

    This is the one route whose job is to report our own failures, so it must
    answer while things are going wrong - which is exactly when the user wants
    to know whether to trust what they are reading.
    """
    return await service.get_receipt(session_id, user_id=user_id)


@router.post(
    "/{session_id}/analyze",
    response_model=SessionSummary,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run analysis on an existing transcript (prompt iteration)",
)
async def reanalyze(
    session_id: str, service: SessionServiceDep, user_id: CurrentUserId
) -> SessionSummary:
    return SessionSummary.from_domain(
        await service.reanalyze(session_id, user_id=user_id)
    )


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session, its audio and everything derived from it",
)
async def delete_session(
    session_id: str, service: SessionServiceDep, user_id: CurrentUserId
) -> Response:
    await service.delete_session(session_id, user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
