"""The /v1/ask endpoint, over the full pipeline."""

from __future__ import annotations

import json
import uuid

from httpx import AsyncClient

from app.container import Container
from app.core.time_utils import utc_now
from app.services.ask import NO_MATCH_ANSWER


async def record(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> str:
    opened = await client.post(
        "/v1/sessions", json={"tz": "Asia/Kolkata"}
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
    )
    await client.post(f"/v1/sessions/{session_id}/close")
    await container.pipeline.drain()
    return session_id


def queue_answer(container: Container, answer: str, sources: list[int]) -> None:
    """Point the shared fake LLM at an answer payload for the next call."""
    container.llm._responses = [  # noqa: SLF001
        json.dumps({"answer": answer, "sources": sources, "confident": True})
    ]


async def test_ask_answers_from_a_recorded_conversation(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    session_id = await record(client, container, wav_bytes)
    queue_answer(container, "Manan is handling the financial projections.", [1])

    body = (
        await client.post(
            "/v1/ask",
            json={
                "question": "who is doing the financial projections?",
                                "tz": "Asia/Kolkata",
            },
        )
    ).json()

    assert body["grounded"] is True
    assert body["answer"] == "Manan is handling the financial projections."
    assert body["retrieved_count"] > 0
    assert body["sources"]
    assert body["dropped_citations"] == []
    # Every citation must point at something real.
    for source in body["sources"]:
        assert source["id"]
        if source["kind"] == "conversation":
            assert source["session_id"] == session_id


async def test_ask_admits_ignorance_without_calling_the_model(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    calls_before = len(container.llm.calls)

    body = (
        await client.post(
            "/v1/ask",
            json={"question": "what did we decide about submarines?"},
        )
    ).json()

    assert body["answer"] == NO_MATCH_ANSWER
    assert body["grounded"] is False
    assert body["confident"] is False
    assert body["sources"] == []
    assert len(container.llm.calls) == calls_before, "no model call"


async def test_ask_on_an_empty_corpus(client: AsyncClient) -> None:
    body = (
        await client.post("/v1/ask", json={"question": "what is pending?"})
    ).json()
    assert body["grounded"] is False
    assert body["retrieved_count"] == 0


async def test_ask_surfaces_pending_tasks(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    queue_answer(container, "Two things are outstanding.", [1])

    body = (
        await client.post(
            "/v1/ask", json={"question": "what tasks are still pending?"}
        )
    ).json()

    assert body["retrieved_count"] > 0
    assert body["grounded"] is True


async def test_ask_is_scoped_per_user(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """One user's question must never retrieve another user's conversations."""
    await record(client, container, wav_bytes)

    body = (
        await client.post(
            "/v1/ask",
            json={"question": "who is doing the financial projections?"},
            params={"user_id": "someone-else"},
        )
    ).json()

    assert body["retrieved_count"] == 0
    assert body["grounded"] is False


async def test_ask_rejects_an_empty_question(client: AsyncClient) -> None:
    response = await client.post("/v1/ask", json={"question": ""})
    assert response.status_code == 422


async def test_ask_drops_invented_citations_over_http(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    queue_answer(container, "An answer.", [1, 42])

    body = (
        await client.post(
            "/v1/ask", json={"question": "financial projections"}
        )
    ).json()

    assert body["dropped_citations"] == [42]
    assert all(s["label"] != 42 for s in body["sources"])
