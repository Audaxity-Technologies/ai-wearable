"""Utterance routes, and the pipeline building the index end to end."""

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


async def test_the_pipeline_builds_the_index(
    client: AsyncClient, container: Container
) -> None:
    """The index must exist without anyone asking for it."""
    session_id = await _record(client)

    body = (
        await client.get(
            f"/v1/utterances/by-session/{session_id}",
            params={"user_id": "dev-user"},
        )
    ).json()
    assert body["total"] > 0
    first = body["utterances"][0]
    assert first["text"]
    assert first["started_at"]
    assert first["duration_s"] >= 0
    # Place rides along from the segment, as a name.
    assert first["place_name"] == "Office"
    # Never inferred.
    assert first["speaker_person_id"] is None


async def test_the_response_carries_no_coordinates(
    client: AsyncClient, container: Container
) -> None:
    session_id = await _record(client)
    raw = (
        await client.get(
            f"/v1/utterances/by-session/{session_id}",
            params={"user_id": "dev-user"},
        )
    ).text
    assert "28.524" not in raw
    assert "77.185" not in raw


async def test_filtering_by_place_and_text(
    client: AsyncClient, container: Container
) -> None:
    await _record(client)
    hit = (
        await client.get(
            "/v1/utterances",
            params={"user_id": "dev-user", "place": "office"},
        )
    ).json()
    assert hit["total"] > 0

    miss = (
        await client.get(
            "/v1/utterances",
            params={"user_id": "dev-user", "place": "nowhere"},
        )
    ).json()
    assert miss["total"] == 0


async def test_another_users_session_is_reported_as_missing(
    client: AsyncClient, container: Container
) -> None:
    """404, not 403: a 403 would confirm the id exists."""
    session_id = await _record(client, user_id="pulkit")
    response = await client.get(
        f"/v1/utterances/by-session/{session_id}",
        params={"user_id": "manan"},
    )
    assert response.status_code == 404


async def test_the_index_is_scoped_to_the_acting_user(
    client: AsyncClient, container: Container
) -> None:
    await _record(client, user_id="pulkit")
    body = (
        await client.get("/v1/utterances", params={"user_id": "manan"})
    ).json()
    assert body["total"] == 0


async def test_deleting_a_session_drops_its_utterances(
    client: AsyncClient, container: Container
) -> None:
    """A derived index must not outlive the audio it describes."""
    session_id = await _record(client)
    before = (
        await client.get(
            f"/v1/utterances/by-session/{session_id}",
            params={"user_id": "dev-user"},
        )
    ).json()
    assert before["total"] > 0

    assert (
        await client.delete(
            f"/v1/sessions/{session_id}", params={"user_id": "dev-user"}
        )
    ).status_code == 204

    remaining = await container.utterances_repo.list(session_id=session_id)
    assert remaining == []


async def test_an_absurd_limit_is_refused(client: AsyncClient) -> None:
    response = await client.get(
        "/v1/utterances", params={"user_id": "dev-user", "limit": "100000"}
    )
    assert response.status_code == 422
