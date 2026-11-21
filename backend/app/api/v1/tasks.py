"""Task routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AuthServiceDep, CurrentUserId, TaskServiceDep
from app.models.dto import TaskList, TaskResponse, UpdateTaskRequest
from app.models.tasks import TaskStatus
from app.services.loops import OpenLoopLedger

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=TaskList, summary="List tasks")
async def list_tasks(
    service: TaskServiceDep,
    user_id: CurrentUserId,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    owner: Annotated[str | None, Query()] = None,
    project: Annotated[str | None, Query()] = None,
    overdue: Annotated[
        bool, Query(description="Only tasks past a confidently resolved date")
    ] = False,
    needs_clarification: Annotated[
        bool, Query(description="Only tasks whose deadline is ambiguous")
    ] = False,
) -> TaskList:
    tasks = await service.list_tasks(
        user_id=user_id,
        status=task_status,
        owner=owner,
        project=project,
        overdue_only=overdue,
        needs_clarification_only=needs_clarification,
    )
    return TaskList(
        tasks=[
            TaskResponse.from_domain(task, overdue=service.is_overdue(task))
            for task in tasks
        ],
        total=len(tasks),
    )


@router.get(
    "/open-loops",
    response_model=OpenLoopLedger,
    summary="What you owe, and what you are owed",
)
async def open_loops(
    service: TaskServiceDep,
    auth: AuthServiceDep,
    user_id: CurrentUserId,
) -> OpenLoopLedger:
    """The cross-conversation view of outstanding commitments.

    The user's own names are resolved here rather than in the service, so
    that attribution depends on the account record and never on a string the
    client supplied.
    """
    self_names: list[str] = []
    tz = "UTC"
    try:
        user = await auth.get_user(user_id)
    except Exception:
        # Dev mode with `auth_required` off has no user record. The ledger
        # still works; everything simply lands in `unattributed`, which is
        # the honest outcome when we do not know who the user is.
        user = None
    if user is not None:
        tz = user.tz
        self_names = [name for name in (user.display_name,) if name]

    return await service.open_loops(
        user_id=user_id, self_names=self_names, tz=tz
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, service: TaskServiceDep) -> TaskResponse:
    task = await service.get(task_id)
    return TaskResponse.from_domain(task, overdue=service.is_overdue(task))


@router.patch(
    "/{task_id}",
    response_model=TaskResponse,
    summary="Edit or complete a task (user correction)",
)
async def update_task(
    task_id: str, body: UpdateTaskRequest, service: TaskServiceDep
) -> TaskResponse:
    task = await service.update(
        task_id,
        title=body.title,
        owner=body.owner,
        status=body.status,
        due_date=body.due_date,
        due_time=body.due_time,
        project=body.project,
        clear_due=body.clear_due,
    )
    return TaskResponse.from_domain(task, overdue=service.is_overdue(task))
