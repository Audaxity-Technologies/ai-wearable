"""Ask: grounded question answering over the user's own history."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AskServiceDep, CurrentUserId
from app.models.ask import AskResult
from app.models.dto import AskRequest

router = APIRouter(tags=["ask"])


@router.post(
    "/ask",
    response_model=AskResult,
    summary="Ask a question about your conversations, memories and tasks",
)
async def ask(
    body: AskRequest, service: AskServiceDep, user_id: CurrentUserId
) -> AskResult:
    """Answers only from retrieved material.

    When nothing relevant is found the response says so, with
    `grounded: false` — no model call is made at all.
    """
    return await service.ask(
        body.question,
        user_id=user_id,
        tz=body.tz,
        limit=body.limit,
    )
