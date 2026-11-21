"""JSON repository: the guarantees the rest of the system assumes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.time_utils import utc_now
from app.models.domain import Session
from app.models.status import SessionStatus
from app.repositories.json_repo import JsonRepository


def make_session(**overrides) -> Session:
    data = {
        "user_id": "dev-user",
        "tz": "Asia/Kolkata",
        "started_at": utc_now(),
    }
    data.update(overrides)
    return Session(**data)


@pytest.fixture
def repo(tmp_path: Path) -> JsonRepository[Session]:
    return JsonRepository(tmp_path / "sessions.json", Session)


async def test_put_and_get_roundtrip(repo: JsonRepository[Session]) -> None:
    session = make_session(title="Investor pitch")
    await repo.put(session)

    loaded = await repo.get(session.id)
    assert loaded is not None
    assert loaded.title == "Investor pitch"
    assert loaded.tz == "Asia/Kolkata"


async def test_get_missing_returns_none(repo: JsonRepository[Session]) -> None:
    assert await repo.get("nope") is None


async def test_add_if_absent_is_idempotent(repo: JsonRepository[Session]) -> None:
    first = make_session(title="original")
    stored, created = await repo.add_if_absent(first)
    assert created is True

    duplicate = make_session(title="overwrite attempt")
    duplicate.id = first.id
    stored, created = await repo.add_if_absent(duplicate)

    assert created is False
    assert stored.title == "original", "existing record must not be replaced"


async def test_survives_a_fresh_instance(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    session = make_session(title="persisted")
    await JsonRepository(path, Session).put(session)

    reloaded = await JsonRepository(path, Session).get(session.id)
    assert reloaded is not None
    assert reloaded.title == "persisted"


async def test_concurrent_writes_do_not_lose_records(
    repo: JsonRepository[Session],
) -> None:
    sessions = [make_session() for _ in range(50)]
    await asyncio.gather(*(repo.put(item) for item in sessions))

    assert await repo.count() == 50
    stored_ids = {item.id for item in await repo.list()}
    assert stored_ids == {item.id for item in sessions}


async def test_write_leaves_no_temp_file_and_valid_json(
    repo: JsonRepository[Session], tmp_path: Path
) -> None:
    await repo.put(make_session())

    leftovers = list(tmp_path.glob("*.tmp*"))
    assert leftovers == [], f"atomic write left temp files: {leftovers}"

    payload = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert len(payload) == 1


async def test_list_filters_on_top_level_fields(
    repo: JsonRepository[Session],
) -> None:
    await repo.put(make_session(user_id="pulkit"))
    await repo.put(make_session(user_id="manan"))
    await repo.put(make_session(user_id="manan", status=SessionStatus.READY))

    assert len(await repo.list(user_id="manan")) == 2
    assert len(await repo.list(user_id="manan", status=SessionStatus.READY)) == 1
    assert len(await repo.list()) == 3


async def test_delete(repo: JsonRepository[Session]) -> None:
    session = make_session()
    await repo.put(session)

    assert await repo.delete(session.id) is True
    assert await repo.get(session.id) is None
    assert await repo.delete(session.id) is False
