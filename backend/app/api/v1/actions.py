"""Action and approval routes.

`/approve` is the only path to execution anywhere in the system.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ApprovalServiceDep, CurrentUserId
from app.models.actions import ActionStatus
from app.models.dto import (
    ActionList,
    ActionResponse,
    RejectActionRequest,
    UpdateActionRequest,
)

router = APIRouter(prefix="/actions", tags=["actions"])


def _to_list(actions) -> ActionList:
    return ActionList(
        actions=[ActionResponse.from_domain(a) for a in actions],
        total=len(actions),
        needs_clarification_count=sum(
            1 for a in actions if a.needs_clarification
        ),
    )


@router.get(
    "/pending",
    response_model=ActionList,
    summary="The approval inbox: actions awaiting a decision",
)
async def list_pending(
    service: ApprovalServiceDep,
    user_id: CurrentUserId,
) -> ActionList:
    return _to_list(await service.list_pending(user_id=user_id))


@router.get("", response_model=ActionList, summary="List actions")
async def list_actions(
    service: ApprovalServiceDep,
    user_id: CurrentUserId,
    action_status: Annotated[ActionStatus | None, Query(alias="status")] = None,
    session_id: Annotated[str | None, Query()] = None,
) -> ActionList:
    return _to_list(
        await service.list_actions(
            user_id=user_id, status=action_status, session_id=session_id
        )
    )


@router.get("/{action_id}", response_model=ActionResponse)
async def get_action(
    action_id: str, service: ApprovalServiceDep
) -> ActionResponse:
    return ActionResponse.from_domain(await service.get(action_id))


@router.patch(
    "/{action_id}",
    response_model=ActionResponse,
    summary="Edit a proposed action before deciding on it",
)
async def edit_action(
    action_id: str, body: UpdateActionRequest, service: ApprovalServiceDep
) -> ActionResponse:
    action = await service.edit(
        action_id,
        title=body.title,
        date=body.date,
        time=body.time,
        action_type=body.type,
    )
    return ActionResponse.from_domain(action)


@router.post(
    "/{action_id}/approve",
    response_model=ActionResponse,
    summary="Approve and execute. Idempotent: safe to retry.",
)
async def approve_action(
    action_id: str, service: ApprovalServiceDep
) -> ActionResponse:
    return ActionResponse.from_domain(await service.approve(action_id))


@router.post(
    "/{action_id}/reject",
    response_model=ActionResponse,
    summary="Reject an action. It will not be proposed again.",
)
async def reject_action(
    action_id: str,
    body: RejectActionRequest,
    service: ApprovalServiceDep,
) -> ActionResponse:
    return ActionResponse.from_domain(
        await service.reject(action_id, reason=body.reason)
    )
