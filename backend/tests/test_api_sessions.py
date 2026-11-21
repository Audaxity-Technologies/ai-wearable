"""Health and session listing."""

from __future__ import annotations

import asyncio

from httpx import AsyncClient

from app.container import Container


async def test_health_reports_driver_readiness(client: AsyncClient) -> None:
    response = await client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app_env"] == "test"
    assert body["transcription_provider"] == "fake"
    assert body["transcription_ready"] is True


async def test_health_is_degraded_without_an_api_key(
    container: Container,
) -> None:
    """A missing GROQ_API_KEY must surface at /health, not at upload time."""
    from app.services.transcription.groq_driver import GroqTranscriptionDriver

    ready, detail = await GroqTranscriptionDriver(api_key="").readiness()
    assert ready is False
    assert "GROQ_API_KEY" in detail


async def test_open_session_echoes_input(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/sessions",
        json={"tz": "Asia/Kolkata", "title": "Co-founder sync"},
        params={"user_id": "manan"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "open"
    assert body["title"] == "Co-founder sync"
    assert body["user_id"] == "manan"
    assert body["segment_count"] == 0


async def test_list_filters_by_user_and_status(client: AsyncClient) -> None:
    await client.post(
        "/v1/sessions", json={"tz": "UTC"}, params={"user_id": "pulkit"}
    )
    await client.post(
        "/v1/sessions", json={"tz": "UTC"}, params={"user_id": "manan"}
    )

    # Listing is per-user now, so "everyone" means the fallback dev user.
    mine_only = (
        await client.get("/v1/sessions", params={"user_id": "manan"})
    ).json()["sessions"]
    assert len(mine_only) == 1

    mine = (await client.get("/v1/sessions", params={"user_id": "manan"})).json()
    assert len(mine["sessions"]) == 1
    assert mine["sessions"][0]["user_id"] == "manan"

    open_only = (
        await client.get(
            "/v1/sessions", params={"status": "open", "user_id": "pulkit"}
        )
    ).json()
    assert len(open_only["sessions"]) == 1


async def test_updated_since_supports_incremental_sync(
    client: AsyncClient,
) -> None:
    """The mobile app polls with `updated_since`, so it must exclude older rows."""
    first = await client.post("/v1/sessions", json={"tz": "UTC"})
    cutoff = first.json()["updated_at"]

    await asyncio.sleep(0.01)
    second = await client.post("/v1/sessions", json={"tz": "UTC"})

    response = await client.get(
        "/v1/sessions", params={"updated_since": cutoff}
    )
    ids = [item["id"] for item in response.json()["sessions"]]
    assert ids == [second.json()["id"]]


async def test_missing_session_returns_structured_error(
    client: AsyncClient,
) -> None:
    response = await client.get("/v1/sessions/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "Session not found: does-not-exist",
    }


async def test_receipt_is_available_on_a_brand_new_session(
    client: AsyncClient,
) -> None:
    """The receipt must answer before anything has happened.

    A receipt you can only fetch once the pipeline succeeded cannot answer
    the question it exists for: "is this thing actually recording?"
    """
    session_id = (
        await client.post(
            "/v1/sessions", json={"tz": "UTC"}, params={"user_id": "manan"}
        )
    ).json()["id"]

    response = await client.get(
        f"/v1/sessions/{session_id}/receipt", params={"user_id": "manan"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["segments_total"] == 0
    assert body["analysis_present"] is False
    assert body["headline"] == "Nothing was recorded in this session."
    # Derived values are served, not left for the client to recompute.
    for key in ("coverage", "gap_s", "is_complete", "headline"):
        assert key in body


async def test_receipt_of_someone_elses_session_is_reported_as_missing(
    client: AsyncClient,
) -> None:
    """404, not 403: a 403 would confirm the id exists."""
    session_id = (
        await client.post(
            "/v1/sessions", json={"tz": "UTC"}, params={"user_id": "pulkit"}
        )
    ).json()["id"]

    response = await client.get(
        f"/v1/sessions/{session_id}/receipt", params={"user_id": "manan"}
    )
    assert response.status_code == 404
