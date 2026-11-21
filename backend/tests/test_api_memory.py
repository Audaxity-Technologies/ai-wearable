"""Memory through the pipeline and over HTTP."""

from __future__ import annotations

import json
import uuid

from httpx import AsyncClient

from app.container import Container
from app.core.time_utils import utc_now
from app.models.memory import MemoryKind
from app.models.status import SessionStatus
from app.services.llm.fake_driver import DEFAULT_ANALYSIS


async def record(
    client: AsyncClient,
    container: Container,
    wav_bytes: bytes,
    *,
    user_id: str = "dev-user",
) -> str:
    """Record one conversation as `user_id`.

    Identity now comes from the token, or from the ?user_id= dev fallback the
    test settings leave enabled. It is no longer assertable in the body.
    """
    params = {"user_id": user_id}
    opened = await client.post(
        "/v1/sessions", json={"tz": "Asia/Kolkata"}, params=params
    )
    session_id = opened.json()["id"]
    await client.post(
        "/v1/ingest/audio",
        data={
            "segment_id": str(uuid.uuid4()),
            "session_id": session_id,
            "seq": "0",
            "started_at": utc_now().isoformat(),
            "duration_s": "20.0",
        },
        files={"file": ("seg0.wav", wav_bytes, "audio/wav")},
        params=params,
    )
    await client.post(f"/v1/sessions/{session_id}/close", params=params)
    await container.pipeline.drain()
    return session_id


async def test_pipeline_writes_memory_and_reaches_ready(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    session_id = await record(client, container, wav_bytes)

    assert (await client.get(f"/v1/sessions/{session_id}")).json()[
        "status"
    ] == SessionStatus.READY

    body = (await client.get("/v1/memory")).json()
    assert body["total"] > 0

    kinds = {m["kind"] for m in body["memories"]}
    assert MemoryKind.PERSON in kinds
    assert MemoryKind.PROJECT in kinds
    assert MemoryKind.FACT in kinds


async def test_suggestions_do_not_become_memories(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """The canned analysis has two suggestions; neither should be a fact."""
    await record(client, container, wav_bytes)

    facts = (
        await client.get("/v1/memory", params={"kind": "fact"})
    ).json()["memories"]
    titles = {f["name"] for f in facts}

    assert "Finish the investor pitch" in titles
    assert "Work on the financial projections" in titles
    assert "Contact the V-NEST team" not in titles, "suggestion stored as fact"
    assert "Review the wearable prototype" not in titles


async def test_every_memory_carries_provenance(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    session_id = await record(client, container, wav_bytes)
    memories = (await client.get("/v1/memory")).json()["memories"]

    for memory in memories:
        assert session_id in memory["source_session_ids"]
    for fact in [m for m in memories if m["kind"] == "fact"]:
        assert fact["source_quotes"], "a fact must keep its evidence"


async def test_two_conversations_merge_into_one_memory(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """The cross-conversation case, end to end."""
    first = await record(client, container, wav_bytes)
    second = await record(client, container, wav_bytes)
    assert first != second

    projects = (
        await client.get("/v1/memory", params={"kind": "project"})
    ).json()["memories"]

    pitch = next(m for m in projects if m["name"] == "Investor pitch")
    assert pitch["mention_count"] == 2
    assert pitch["is_cross_conversation"] is True
    assert set(pitch["source_session_ids"]) == {first, second}


async def test_contradicting_deadline_surfaces_a_conflict(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)

    # Second conversation moves the investor-pitch deadline.
    revised = json.loads(json.dumps(DEFAULT_ANALYSIS))
    revised["items"] = [
        {
            **revised["items"][0],
            "due_raw": "by the day after tomorrow",
        }
    ]
    container.llm._responses = [json.dumps(revised)]  # noqa: SLF001
    await record(client, container, wav_bytes)

    conflicted = (
        await client.get("/v1/memory", params={"conflicts_only": "true"})
    ).json()

    assert conflicted["total"] == 2, "both deadlines must survive"
    due_dates = {
        m["attributes"].get("due_date") for m in conflicted["memories"]
    }
    assert len(due_dates) == 2
    for memory in conflicted["memories"]:
        assert memory["has_conflict"] is True
        assert memory["conflicts_with"]


async def test_search_endpoint(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)

    body = (
        await client.get("/v1/memory/search", params={"q": "investor pitch"})
    ).json()

    assert body["query"] == "investor pitch"
    assert body["total"] > 0
    assert body["results"][0]["score"] > 0
    assert body["results"][0]["matched_terms"]


async def test_search_returns_nothing_for_unknown_terms(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    body = (
        await client.get("/v1/memory/search", params={"q": "submarine"})
    ).json()
    assert body["total"] == 0


async def test_memories_are_scoped_by_user_over_http(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """Memory is per-user: listing never crosses accounts."""
    await record(client, container, wav_bytes, user_id="manan")
    await record(client, container, wav_bytes, user_id="pulkit")

    mine = (await client.get("/v1/memory", params={"user_id": "manan"})).json()
    theirs = (await client.get("/v1/memory", params={"user_id": "pulkit"})).json()
    stranger = (
        await client.get("/v1/memory", params={"user_id": "nobody"})
    ).json()

    assert mine["total"] > 0
    assert theirs["total"] > 0
    assert stranger["total"] == 0
    # Neither account can see the other's memories.
    mine_ids = {m["id"] for m in mine["memories"]}
    theirs_ids = {m["id"] for m in theirs["memories"]}
    assert mine_ids.isdisjoint(theirs_ids)


async def test_get_and_delete_a_memory(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    memory_id = (await client.get("/v1/memory")).json()["memories"][0]["id"]

    assert (await client.get(f"/v1/memory/{memory_id}")).status_code == 200
    assert (await client.delete(f"/v1/memory/{memory_id}")).status_code == 204
    assert (await client.get(f"/v1/memory/{memory_id}")).status_code == 404


async def test_deleting_a_session_purges_its_memories(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    session_id = await record(client, container, wav_bytes)
    assert (await client.get("/v1/memory")).json()["total"] > 0

    await client.delete(f"/v1/sessions/{session_id}")

    assert (await client.get("/v1/memory")).json()["total"] == 0


async def test_deleting_one_of_two_sessions_keeps_shared_memories(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """Privacy must not become data loss for the conversation you kept."""
    first = await record(client, container, wav_bytes)
    second = await record(client, container, wav_bytes)

    await client.delete(f"/v1/sessions/{first}")

    body = (await client.get("/v1/memory")).json()
    assert body["total"] > 0
    for memory in body["memories"]:
        assert first not in memory["source_session_ids"]
        assert memory["source_session_ids"] == [second]


async def test_degraded_analysis_writes_no_memory(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    container.llm._responses = ["not json", "still not json"]  # noqa: SLF001
    session_id = await record(client, container, wav_bytes)

    assert (await client.get(f"/v1/sessions/{session_id}/analysis")).json()[
        "degraded"
    ] is True
    assert (await client.get("/v1/memory")).json()["total"] == 0
