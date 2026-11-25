"""FastAPI application.

Run with exactly one worker: the JSON repository is single-process safe only
(see docs/ARCHITECTURE.md §2).

    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import (
    actions,
    ask,
    auth,
    health,
    ingest,
    integrations,
    memory,
    people,
    questions,
    sessions,
    tasks,
    utterances,
    voice,
)
from app.config import Settings, get_settings
from app.container import Container, build_container
from app.core.errors import AudaxityError
from app.core.logging import configure_logging, get_logger, log_event
from app.models.dto import ErrorResponse

logger = get_logger(__name__)


async def _briefing_loop(container) -> None:
    """Send each user their briefing once their local time arrives.

    Deliberately a simple in-process loop rather than a job scheduler: there
    is one process, the work is tiny, and a missed tick costs at most one
    briefing. `due_for_briefing` uses a five-minute window so a late tick
    still fires.
    """
    interval = container.settings.briefing_check_seconds
    sent_on: dict[str, str] = {}
    while True:
        try:
            await asyncio.sleep(interval)
            for user in await container.users_repo.list():
                if not await container.briefing_service.due_for_briefing(user):
                    continue
                today = str(
                    (await container.briefing_service.build(user)).local_date
                )
                # One briefing per local day, even if several ticks land
                # inside the window.
                if sent_on.get(user.id) == today:
                    continue
                sent_on[user.id] = today
                await container.briefing_service.send_briefing(user)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - the loop must outlive failures
            logger.exception("briefing scheduler tick failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    container = app.state.container
    await container.pipeline.start()
    scheduler = asyncio.create_task(_briefing_loop(container))
    requeued = await container.pipeline.requeue_unfinished()
    log_event(
        logger,
        "app.started",
        app_env=container.settings.app_env,
        transcription_provider=container.settings.transcription_provider,
        requeued_jobs=requeued,
    )
    try:
        yield
    finally:
        scheduler.cancel()
        try:
            await scheduler
        except asyncio.CancelledError:
            pass
        await container.pipeline.stop()


def create_app(
    settings: Settings | None = None, *, container: Container | None = None
) -> FastAPI:
    """Build the app. Tests inject a container wired to a temp dir + fake driver."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Audaxity API",
        version="0.1.0",
        description=(
            "Conversation-aware assistant backend. Phase 0-2: ingest, "
            "storage and transcription."
        ),
        lifespan=lifespan,
    )
    app.state.container = container or build_container(settings)

    # The Expo app runs from a different origin in development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.app_env != "prod" else [],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AudaxityError)
    async def handle_domain_error(
        _request: Request, exc: AudaxityError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(code=exc.code, message=exc.message).model_dump(),
        )

    app.include_router(health.router, prefix="/v1")
    app.include_router(sessions.router, prefix="/v1")
    app.include_router(ingest.router, prefix="/v1")
    app.include_router(memory.router, prefix="/v1")
    app.include_router(tasks.router, prefix="/v1")
    app.include_router(actions.router, prefix="/v1")
    app.include_router(ask.router, prefix="/v1")
    app.include_router(auth.router, prefix="/v1")
    app.include_router(integrations.router, prefix="/v1")
    app.include_router(voice.router, prefix="/v1")
    app.include_router(people.router, prefix="/v1")
    app.include_router(utterances.router, prefix="/v1")
    app.include_router(questions.router, prefix="/v1")
    return app


app = create_app()
