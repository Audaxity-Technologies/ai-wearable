"""Health endpoint: liveness plus driver readiness."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import ContainerDep
from app.models.dto import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(container: ContainerDep) -> HealthResponse:
    stt_ready, stt_detail = await container.driver.readiness()
    llm_ready, llm_detail = await container.llm.readiness()
    return HealthResponse(
        # The process is up either way; the readiness flags are what tell you
        # whether the pipeline can actually do work.
        status="ok" if (stt_ready and llm_ready) else "degraded",
        app_env=container.settings.app_env,
        transcription_provider=container.settings.transcription_provider,
        transcription_ready=stt_ready,
        transcription_detail=stt_detail,
        llm_provider=container.settings.llm_provider,
        llm_ready=llm_ready,
        llm_detail=llm_detail,
        pipeline_pending_jobs=container.pipeline.pending_jobs,
    )
