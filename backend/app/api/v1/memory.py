"""Memory routes: browse, search, inspect conflicts, delete."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUserId, MemoryServiceDep
from app.models.dto import MemoryList, MemoryResponse, MemorySearchResponse
from app.models.memory import MemoryKind

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("", response_model=MemoryList, summary="Browse memories")
async def list_memories(
    service: MemoryServiceDep,
    user_id: CurrentUserId,
    kind: Annotated[MemoryKind | None, Query()] = None,
    project: Annotated[str | None, Query()] = None,
    conflicts_only: Annotated[
        bool, Query(description="Only memories that contradict another")
    ] = False,
) -> MemoryList:
    memories = await service.list_memories(
        user_id=user_id,
        kind=kind,
        project=project,
        conflicts_only=conflicts_only,
    )
    return MemoryList(
        memories=[MemoryResponse.from_domain(item) for item in memories],
        total=len(memories),
    )


@router.get(
    "/search",
    response_model=MemorySearchResponse,
    summary="Keyword search across names, content and source quotes",
)
async def search_memories(
    service: MemoryServiceDep,
    user_id: CurrentUserId,
    q: Annotated[str, Query(min_length=1, description="Search terms")],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MemorySearchResponse:
    hits = await service.search(q, user_id=user_id, limit=limit)
    return MemorySearchResponse(
        query=q,
        results=[
            MemoryResponse.from_domain(
                hit.memory, score=hit.score, matched_terms=hit.matched_terms
            )
            for hit in hits
        ],
        total=len(hits),
    )


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str, service: MemoryServiceDep
) -> MemoryResponse:
    return MemoryResponse.from_domain(await service.get(memory_id))


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a memory (user correction)",
)
async def delete_memory(
    memory_id: str, service: MemoryServiceDep
) -> Response:
    await service.delete(memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
