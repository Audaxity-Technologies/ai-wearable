"""Voice enrolment routes."""

from __future__ import annotations

from httpx import AsyncClient


async def test_status_before_enrolment_offers_enrolment(
    client: AsyncClient,
) -> None:
    response = await client.get("/v1/voice/status", params={"user_id": "manan"})
    assert response.status_code == 200
    body = response.json()
    assert body["enrolled"] is False
    assert body["others_enrolled"] == 0
    # No real model is wired up yet, and the app must be able to say so.
    assert body["verification_trustworthy"] is False


async def test_enrol_then_status_then_delete(client: AsyncClient) -> None:
    files = [
        ("files", (f"clip{i}.m4a", b"OWNER:" + bytes([i]), "audio/m4a"))
        for i in range(3)
    ]
    response = await client.post(
        "/v1/voice/enrol",
        params={"user_id": "manan"},
        files=files,
        data={"durations": "6,6,6"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["clip_count"] == 3

    status = await client.get("/v1/voice/status", params={"user_id": "manan"})
    assert status.json()["enrolled"] is True

    gone = await client.delete("/v1/voice", params={"user_id": "manan"})
    assert gone.status_code == 204
    after = await client.get("/v1/voice/status", params={"user_id": "manan"})
    assert after.json()["enrolled"] is False


async def test_enrol_rejects_mismatched_durations(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/voice/enrol",
        params={"user_id": "manan"},
        files=[("files", ("a.m4a", b"OWNER:a", "audio/m4a"))],
        data={"durations": "6,6,6"},
    )
    assert response.status_code == 422
    assert "one to one" in response.text


async def test_enrol_rejects_too_few_clips(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/voice/enrol",
        params={"user_id": "manan"},
        files=[("files", ("a.m4a", b"OWNER:a", "audio/m4a"))],
        data={"durations": "20"},
    )
    assert response.status_code == 422
    assert "at least" in response.text


async def test_check_returns_a_verdict_rather_than_an_error_when_unenrolled(
    client: AsyncClient,
) -> None:
    """"We do not know" is the honest answer, and is not an error."""
    response = await client.post(
        "/v1/voice/check",
        params={"user_id": "nobody-enrolled"},
        files={"file": ("clip.m4a", b"OWNER:x", "audio/m4a")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "not_enrolled"


async def test_a_voiceprint_is_never_returned_by_the_api(
    client: AsyncClient,
) -> None:
    """Status and check carry no biometric material."""
    files = [
        ("files", (f"clip{i}.m4a", b"OWNER:" + bytes([i]), "audio/m4a"))
        for i in range(3)
    ]
    await client.post(
        "/v1/voice/enrol",
        params={"user_id": "manan"},
        files=files,
        data={"durations": "6,6,6"},
    )
    status = await client.get("/v1/voice/status", params={"user_id": "manan"})
    check = await client.post(
        "/v1/voice/check",
        params={"user_id": "manan"},
        files={"file": ("clip.m4a", b"OWNER:x", "audio/m4a")},
    )
    for body in (status.json(), check.json()):
        assert "centroid" not in body
        assert "embedding" not in body
