"""JSON-file repository.

Phase-1 storage, with the three mitigations the architecture doc commits to:

1. one `asyncio.Lock` per collection, so concurrent handlers serialise;
2. atomic writes (temp file in the same directory, then `os.replace`), so a
   crash mid-write cannot truncate the collection;
3. an in-memory cache that is the single source of truth once loaded, so reads
   do not re-parse the file on every request.

This is safe for a single process. It is NOT safe for multiple processes
writing the same file — run exactly one uvicorn worker.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Generic

from pydantic import BaseModel

from app.repositories.base import ModelT


class JsonRepository(Generic[ModelT]):
    def __init__(self, path: Path, model: type[ModelT]) -> None:
        self._path = path
        self._model = model
        self._lock = asyncio.Lock()
        self._cache: dict[str, ModelT] | None = None
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # --- internals -------------------------------------------------------

    def _load_unlocked(self) -> dict[str, ModelT]:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = {}
            return self._cache
        raw = self._path.read_text(encoding="utf-8").strip()
        if not raw:
            self._cache = {}
            return self._cache
        data: dict[str, Any] = json.loads(raw)
        self._cache = {
            key: self._model.model_validate(value) for key, value in data.items()
        }
        return self._cache

    def _flush_unlocked(self) -> None:
        """Write the cache atomically: temp file in the same dir, then replace."""
        assert self._cache is not None
        payload = {
            key: json.loads(record.model_dump_json())
            for key, record in self._cache.items()
        }
        tmp = self._path.with_suffix(self._path.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(tmp, self._path)  # atomic on POSIX and Windows

    @staticmethod
    def _matches(record: BaseModel, filters: dict[str, Any]) -> bool:
        for field, expected in filters.items():
            if getattr(record, field, None) != expected:
                return False
        return True

    # --- Repository protocol ---------------------------------------------

    async def get(self, record_id: str) -> ModelT | None:
        async with self._lock:
            return self._load_unlocked().get(record_id)

    async def exists(self, record_id: str) -> bool:
        async with self._lock:
            return record_id in self._load_unlocked()

    async def put(self, record: ModelT) -> ModelT:
        async with self._lock:
            cache = self._load_unlocked()
            cache[record.id] = record  # type: ignore[attr-defined]
            self._flush_unlocked()
            return record

    async def add_if_absent(self, record: ModelT) -> tuple[ModelT, bool]:
        async with self._lock:
            cache = self._load_unlocked()
            existing = cache.get(record.id)  # type: ignore[attr-defined]
            if existing is not None:
                return existing, False
            cache[record.id] = record  # type: ignore[attr-defined]
            self._flush_unlocked()
            return record, True

    async def list(self, **filters: Any) -> list[ModelT]:
        async with self._lock:
            records = list(self._load_unlocked().values())
        if not filters:
            return records
        return [r for r in records if self._matches(r, filters)]

    async def delete(self, record_id: str) -> bool:
        async with self._lock:
            cache = self._load_unlocked()
            if record_id not in cache:
                return False
            del cache[record_id]
            self._flush_unlocked()
            return True

    async def count(self, **filters: Any) -> int:
        return len(await self.list(**filters))

    # --- test / maintenance helper ---------------------------------------

    def invalidate_cache(self) -> None:
        self._cache = None
