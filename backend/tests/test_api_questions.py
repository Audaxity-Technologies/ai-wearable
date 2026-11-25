"""Phase D routes."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.container import Container
from app.core.time_utils import utc_now


async def _record(client: AsyncClient, user_id: str = "dev-user") -> str:
    params = {"user_id": user_id}
    session_id = (
        await client.post(
            "/v1/sessions", json={"tz": "Asia/Kolkata"}, params=params
        )
    ).json()["id"]
    await client.post(
        "/v1/ingest/audio",
        data={
            "segment_id": str(uuid.uuid4()),
            "session_id": session_id,
            "seq": "0",
            "started_at": utc_now().isoformat(),
            "duration_s": "20.0",
            "place_lat": "28.524",
            "place_lon": "77.185",
            "place_label": "Office",
        },
        files={"file": ("seg0.wav", b"0" * 4096, "audio/wav")},
        params=params,
    )
    await client.post(f"/v1/sessions/{session_id}/close", params=params)
    return session_id


async def test_parse_returns_the_filter_without_running_it(
    client: AsyncClient,
) -> None:
    """How a wrong answer gets debugged: read the filter, not the result."""
    response = await client.post(
        "/v1/questions/parse",
        params={"user_id": "dev-user"},
        json={"text": "what did I decide yesterday"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "temporal"
    assert body["about_self"] is True
    assert "decision" in body["acts"]
    assert body["time_range"]["label"] == "yesterday"
    assert body["clarifications"] == []


async def test_an_ambiguous_question_asks_rather_than_answering(
    client: AsyncClient,
) -> None:
    await _record(client)
    response = await client.post(
        "/v1/questions",
        params={"user_id": "dev-user"},
        json={"text": "what did we decide that day"},
    )
    body = response.json()
    assert body["utterances"] == []
    assert "which day" in body["verdict"].lower()
    assert body["resolved"]["clarifications"][0]["kind"] == "time"


async def test_an_unparseable_question_is_admitted(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/v1/questions",
        params={"user_id": "dev-user"},
        json={"text": "what happened"},
    )
    body = response.json()
    assert body["resolved"]["kind"] == "unknown"
    assert "could not turn that into a search" in body["verdict"]


async def test_a_place_question_matches_a_recorded_place(
    client: AsyncClient,
) -> None:
    await _record(client)
    response = await client.post(
        "/v1/questions",
        params={"user_id": "dev-user"},
        json={"text": "what did we talk about at the Office"},
    )
    body = response.json()
    assert body["resolved"]["place"] == "Office"
    assert body["total"] >= 1


async def test_nothing_matching_says_which_filter_found_nothing(
    client: AsyncClient,
) -> None:
    await _record(client)
    response = await client.post(
        "/v1/questions",
        params={"user_id": "dev-user"},
        json={"text": "what did we decide about the budget"},
    )
    body = response.json()
    assert body["total"] == 0
    assert "nothing recorded matches" in body["verdict"].lower()


async def test_another_user_sees_nothing(client: AsyncClient) -> None:
    await _record(client, user_id="pulkit")
    response = await client.post(
        "/v1/questions",
        params={"user_id": "manan"},
        json={"text": "what did we talk about at the Office"},
    )
    assert response.json()["total"] == 0


async def test_an_absurd_limit_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/questions",
        params={"user_id": "dev-user", "limit": "5000"},
        json={"text": "what did I decide yesterday"},
    )
    assert response.status_code == 422
