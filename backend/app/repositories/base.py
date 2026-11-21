"""Repository interfaces.

Everything above this layer depends only on these Protocols, which is what
makes the JSON -> SQL migration a one-file change. Migration trigger (see
docs/ARCHITECTURE.md §2): >3 concurrent users, >2000 sessions, or the first
need for full-text search.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class Repository(Protocol[ModelT]):
    """A collection of records of one model type, keyed by `id`."""

    async def get(self, record_id: str) -> ModelT | None: ...

    async def exists(self, record_id: str) -> bool: ...

    async def put(self, record: ModelT) -> ModelT:
        """Insert or replace by id."""
        ...

    async def add_if_absent(self, record: ModelT) -> tuple[ModelT, bool]:
        """Insert only if the id is new.

        Returns `(record, created)`. `created=False` means the existing record
        is returned untouched — the basis of idempotent ingest.
        """
        ...

    async def list(self, **filters: Any) -> list[ModelT]:
        """All records matching equality filters on top-level fields."""
        ...

    async def delete(self, record_id: str) -> bool: ...

    async def count(self, **filters: Any) -> int: ...


class BlobStore(Protocol):
    """Binary storage for audio. Local disk now, S3/R2 later."""

    async def put(self, key: str, data: bytes) -> str:
        """Store bytes under `key`, returning the stored key."""
        ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> bool: ...

    async def exists(self, key: str) -> bool: ...
