"""FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.container import Container
from app.services.approval import ApprovalService
from app.services.auth import AuthService
from app.services.briefing import BriefingService
from app.services.ask import AskService
from app.services.memory import MemoryService
from app.services.pipeline import PipelineService
from app.services.sessions import SessionService
from app.services.people import PersonService
from app.services.tasks import TaskService
from app.services.utterances import UtteranceService
from app.services.voice.enrolment import VoiceService


def get_container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


def get_session_service(
    container: Annotated[Container, Depends(get_container)],
) -> SessionService:
    return container.session_service


def get_pipeline(
    container: Annotated[Container, Depends(get_container)],
) -> PipelineService:
    return container.pipeline


def get_memory_service(
    container: Annotated[Container, Depends(get_container)],
) -> MemoryService:
    return container.memory_service


def get_task_service(
    container: Annotated[Container, Depends(get_container)],
) -> TaskService:
    return container.task_service


def get_approval_service(
    container: Annotated[Container, Depends(get_container)],
) -> ApprovalService:
    return container.approval_service


def get_ask_service(
    container: Annotated[Container, Depends(get_container)],
) -> AskService:
    return container.ask_service


ContainerDep = Annotated[Container, Depends(get_container)]
AskServiceDep = Annotated[AskService, Depends(get_ask_service)]
TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]
ApprovalServiceDep = Annotated[ApprovalService, Depends(get_approval_service)]
MemoryServiceDep = Annotated[MemoryService, Depends(get_memory_service)]
SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
PipelineDep = Annotated[PipelineService, Depends(get_pipeline)]


def get_voice_service(
    container: Annotated[Container, Depends(get_container)],
) -> VoiceService:
    return container.voice_service


def get_person_service(
    container: Annotated[Container, Depends(get_container)],
) -> PersonService:
    return container.person_service


def get_utterance_service(
    container: Annotated[Container, Depends(get_container)],
) -> UtteranceService:
    return container.utterance_service


def get_auth_service(
    container: Annotated[Container, Depends(get_container)],
) -> AuthService:
    return container.auth_service


def get_briefing_service(
    container: Annotated[Container, Depends(get_container)],
) -> BriefingService:
    return container.briefing_service


async def resolve_user_id(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> str:
    """The user this request acts for.

    A bearer token always wins. When `auth_required` is off - tests, and local
    development before you have made an account - a `user_id` query parameter
    is accepted instead. That fallback is never available in production,
    because `auth_required` defaults to true and prod refuses to start with
    the development signing secret.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        user = await container.auth_service.user_from_token(token)
        return user.id

    if container.settings.auth_required:
        from app.core.security import AuthError

        raise AuthError("Sign in to continue.")

    return request.query_params.get("user_id") or "dev-user"


VoiceServiceDep = Annotated[VoiceService, Depends(get_voice_service)]
PersonServiceDep = Annotated[PersonService, Depends(get_person_service)]
UtteranceServiceDep = Annotated[
    UtteranceService, Depends(get_utterance_service)
]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
BriefingServiceDep = Annotated[BriefingService, Depends(get_briefing_service)]
CurrentUserId = Annotated[str, Depends(resolve_user_id)]
