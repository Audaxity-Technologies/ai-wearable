"""Memory routes: browse, search, inspect conflicts, delete."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUserId, MemoryServiceDep
from app.models.dto import (
    MemoryChange,
    MemoryList,
    MemoryResponse,
    MemorySearchResponse,
)
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


# --- bi-temporal reads (phase E) ------------------------------------------


@router.get(
    "/as-of",
    response_model=MemoryList,
    summary="What was believed true at a moment in the past",
)
async def memories_as_of(
    service: MemoryServiceDep,
    user_id: CurrentUserId,
    when: Annotated[datetime, Query(description="ISO-8601 with an offset")],
) -> MemoryList:
    """The question `conflicts_with` could never answer.

    "What did we think the deadline was in February" needs the version whose
    validity interval contained February, not the version that survived.
    """
    rows = await service.as_of(user_id=user_id, when=when)
    return MemoryList(
        memories=[MemoryResponse.from_domain(row) for row in rows],
        total=len(rows),
    )


@router.get(
    "/changes",
    response_model=list[MemoryChange],
    summary="Facts that keep moving, most-revised first",
)
async def changed_memories(
    service: MemoryServiceDep, user_id: CurrentUserId
) -> list[MemoryChange]:
    """What a weekly review is built on.

    The things that keep changing are the things worth looking at, and
    nothing else in the product surfaces them.
    """
    changes = await service.changed_facts(user_id=user_id)
    out: list[MemoryChange] = []
    for key, count in changes:
        versions = await service.history(user_id=user_id, key=key)
        out.append(
            MemoryChange(
                key=key,
                name=versions[-1].name if versions else key,
                change_count=count,
                current=(
                    MemoryResponse.from_domain(versions[-1])
                    if versions
                    else None
                ),
            )
        )
    return out


@router.get(
    "/history",
    response_model=MemoryList,
    summary="Every version of one fact, oldest first",
)
async def memory_history(
    service: MemoryServiceDep,
    user_id: CurrentUserId,
    key: Annotated[str, Query(description="The memory's dedupe key")],
) -> MemoryList:
    """Matched on `key` rather than by walking `superseded_by`: a chain can
    be broken by a hand-deleted row, and the key survives that."""
    rows = await service.history(user_id=user_id, key=key)
    return MemoryList(
        memories=[MemoryResponse.from_domain(row) for row in rows],
        total=len(rows),
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
