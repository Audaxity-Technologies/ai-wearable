"""Accounts, tokens, devices, wake requests and the briefing."""

from __future__ import annotations

import datetime as dt
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.container import build_container
from app.core.errors import ConflictError
from app.core.security import AuthError, create_access_token, decode_access_token
from app.main import create_app
from app.models.users import DevicePlatform, hash_password, verify_password
from app.services.llm.fake_driver import FakeLLMDriver
from app.services.notifications import NullPushDriver
from app.services.transcription.fake_driver import FakeTranscriptionDriver

CREDENTIALS = {"email": "Manan@Audaxity.com", "password": "a-good-password"}


@pytest.fixture
def auth_settings(tmp_path) -> Settings:
    return Settings(
        app_env="test",
        log_level="WARNING",
        data_dir=tmp_path / "data",
        transcription_provider="fake",
        llm_provider="fake",
        push_provider="none",
        jwt_secret="test-secret-at-least-32-characters-long",
        auth_required=True,
        pipeline_workers=1,
        job_retry_backoff_s=0.0,
    )


@pytest.fixture
async def auth_container(auth_settings):
    built = build_container(
        auth_settings, driver=FakeTranscriptionDriver(), llm=FakeLLMDriver()
    )
    await built.pipeline.start()
    try:
        yield built
    finally:
        await built.pipeline.stop()


@pytest.fixture
async def auth_client(auth_container):
    app = create_app(auth_container.settings, container=auth_container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def register_and_login(client: AsyncClient) -> str:
    await client.post("/v1/auth/register", json=CREDENTIALS)
    response = await client.post("/v1/auth/login", json=CREDENTIALS)
    return response.json()["access_token"]


# --- password hashing -----------------------------------------------------


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("hunter2")
    second = hash_password("hunter2")

    assert first != second, "the same password must not produce the same hash"
    assert "hunter2" not in first
    assert verify_password("hunter2", first)
    assert verify_password("hunter2", second)
    assert not verify_password("hunter3", first)


def test_malformed_hash_is_rejected_not_crashed() -> None:
    assert not verify_password("anything", "not-a-real-hash")
    assert not verify_password("anything", "")


# --- tokens ---------------------------------------------------------------


def test_token_roundtrip() -> None:
    token, expires_in = create_access_token(
        user_id="u1", secret="a-test-secret-of-sufficient-length", expires_minutes=60
    )
    assert expires_in == 3600
    assert decode_access_token(token, secret="a-test-secret-of-sufficient-length") == "u1"


def test_token_signed_with_another_secret_is_rejected() -> None:
    token, _ = create_access_token(
        user_id="u1", secret="a-test-secret-of-sufficient-length", expires_minutes=60
    )
    with pytest.raises(AuthError):
        decode_access_token(token, secret="a-different-secret-of-sufficient-len")


def test_expired_token_is_rejected() -> None:
    token, _ = create_access_token(
        user_id="u1", secret="a-test-secret-of-sufficient-length", expires_minutes=-1
    )
    with pytest.raises(AuthError):
        decode_access_token(token, secret="a-test-secret-of-sufficient-length")


def test_garbage_token_is_rejected() -> None:
    with pytest.raises(AuthError):
        decode_access_token("not.a.token", secret="a-test-secret-of-sufficient-length")


# --- registration and login ----------------------------------------------


async def test_register_then_login(auth_client: AsyncClient) -> None:
    registered = await auth_client.post("/v1/auth/register", json=CREDENTIALS)
    assert registered.status_code == 201
    # Email is normalised, so case cannot create a second account.
    assert registered.json()["email"] == "manan@audaxity.com"

    login = await auth_client.post(
        "/v1/auth/login",
        json={"email": "MANAN@audaxity.com", "password": CREDENTIALS["password"]},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


async def test_duplicate_registration_is_rejected(auth_client: AsyncClient) -> None:
    await auth_client.post("/v1/auth/register", json=CREDENTIALS)
    again = await auth_client.post("/v1/auth/register", json=CREDENTIALS)
    assert again.status_code == 409


async def test_short_password_is_rejected(auth_client: AsyncClient) -> None:
    response = await auth_client.post(
        "/v1/auth/register", json={"email": "a@b.com", "password": "short"}
    )
    assert response.status_code == 422


async def test_wrong_password_is_rejected(auth_client: AsyncClient) -> None:
    await auth_client.post("/v1/auth/register", json=CREDENTIALS)
    response = await auth_client.post(
        "/v1/auth/login",
        json={"email": CREDENTIALS["email"], "password": "wrong-password"},
    )
    assert response.status_code == 401


async def test_unknown_email_and_wrong_password_look_the_same(
    auth_client: AsyncClient,
) -> None:
    """A login endpoint must not reveal which emails have accounts."""
    await auth_client.post("/v1/auth/register", json=CREDENTIALS)

    unknown = await auth_client.post(
        "/v1/auth/login",
        json={"email": "nobody@audaxity.com", "password": "a-good-password"},
    )
    wrong = await auth_client.post(
        "/v1/auth/login",
        json={"email": CREDENTIALS["email"], "password": "wrong-password"},
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


# --- protected endpoints --------------------------------------------------


async def test_endpoints_require_a_token_when_auth_is_on(
    auth_client: AsyncClient,
) -> None:
    assert (await auth_client.get("/v1/sessions")).status_code == 401
    assert (await auth_client.get("/v1/tasks")).status_code == 401
    assert (await auth_client.get("/v1/actions/pending")).status_code == 401


async def test_user_id_query_param_cannot_bypass_auth(
    auth_client: AsyncClient,
) -> None:
    """The dev fallback must not be reachable when auth is required."""
    response = await auth_client.get("/v1/sessions", params={"user_id": "someone"})
    assert response.status_code == 401


async def test_token_grants_access(auth_client: AsyncClient) -> None:
    token = await register_and_login(auth_client)
    response = await auth_client.get(
        "/v1/sessions", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


async def test_data_is_scoped_to_the_signed_in_user(
    auth_client: AsyncClient,
) -> None:
    """One account must never see another's conversations."""
    mine = await register_and_login(auth_client)
    await auth_client.post(
        "/v1/auth/register",
        json={"email": "other@audaxity.com", "password": "another-password"},
    )
    theirs = (
        await auth_client.post(
            "/v1/auth/login",
            json={"email": "other@audaxity.com", "password": "another-password"},
        )
    ).json()["access_token"]

    await auth_client.post(
        "/v1/sessions",
        json={"tz": "Asia/Kolkata"},
        headers={"Authorization": f"Bearer {mine}"},
    )

    assert (
        len(
            (
                await auth_client.get(
                    "/v1/sessions", headers={"Authorization": f"Bearer {mine}"}
                )
            ).json()["sessions"]
        )
        == 1
    )
    assert (
        (
            await auth_client.get(
                "/v1/sessions", headers={"Authorization": f"Bearer {theirs}"}
            )
        ).json()["sessions"]
        == []
    )


async def test_me_returns_the_signed_in_account(auth_client: AsyncClient) -> None:
    token = await register_and_login(auth_client)
    body = (
        await auth_client.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
    ).json()
    assert body["email"] == "manan@audaxity.com"
    assert body["briefing_enabled"] is True


# --- devices --------------------------------------------------------------


async def test_register_device_and_push_token(auth_client: AsyncClient) -> None:
    token = await register_and_login(auth_client)
    headers = {"Authorization": f"Bearer {token}"}

    response = await auth_client.post(
        "/v1/devices",
        json={
            "device_id": "device-1",
            "platform": "android",
            "name": "OPPO",
            "push_token": "ExponentPushToken[abc]",
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["has_push_token"] is True

    devices = (await auth_client.get("/v1/devices", headers=headers)).json()
    assert len(devices) == 1


async def test_re_registering_a_device_updates_it(auth_client: AsyncClient) -> None:
    """Reinstalling the app must not accumulate stale push targets."""
    token = await register_and_login(auth_client)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "device_id": "device-1",
        "platform": "android",
        "push_token": "ExponentPushToken[abc]",
    }
    await auth_client.post("/v1/devices", json=payload, headers=headers)
    await auth_client.post(
        "/v1/devices",
        json={**payload, "push_token": "ExponentPushToken[xyz]"},
        headers=headers,
    )

    devices = (await auth_client.get("/v1/devices", headers=headers)).json()
    assert len(devices) == 1


async def test_a_push_token_moving_devices_is_cleared_from_the_old_one(
    auth_client: AsyncClient, auth_container
) -> None:
    token = await register_and_login(auth_client)
    headers = {"Authorization": f"Bearer {token}"}
    for device_id in ("device-1", "device-2"):
        await auth_client.post(
            "/v1/devices",
            json={
                "device_id": device_id,
                "platform": "android",
                "push_token": "ExponentPushToken[shared]",
            },
            headers=headers,
        )

    with_token = [
        d for d in await auth_container.devices_repo.list() if d.push_token
    ]
    assert len(with_token) == 1, "a token must target exactly one device"


# --- wake requests --------------------------------------------------------


async def test_wake_sends_a_notification_but_does_not_record(
    auth_client: AsyncClient, auth_container
) -> None:
    """The product's rule: a remote trigger asks, it never starts the mic."""
    token = await register_and_login(auth_client)
    headers = {"Authorization": f"Bearer {token}"}
    await auth_client.post(
        "/v1/devices",
        json={
            "device_id": "d1",
            "platform": "android",
            "push_token": "ExponentPushToken[abc]",
        },
        headers=headers,
    )

    response = await auth_client.post(
        "/v1/wake", json={"reason": "Standup starting"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["sent"] == 1

    push = auth_container.push
    assert isinstance(push, NullPushDriver)
    message = push.sent[-1]
    assert message.data["kind"] == "wake_request"
    assert message.data["arm_recording"] == "true"
    assert "Standup starting" in message.body

    # Crucially: no session was opened. Recording starts only when the user
    # taps the notification and presses record.
    sessions = (await auth_client.get("/v1/sessions", headers=headers)).json()
    assert sessions["sessions"] == []


async def test_wake_without_a_device_reports_honestly(
    auth_client: AsyncClient,
) -> None:
    token = await register_and_login(auth_client)
    body = (
        await auth_client.post(
            "/v1/wake", json={}, headers={"Authorization": f"Bearer {token}"}
        )
    ).json()
    assert body["sent"] == 0
    assert "No device" in body["detail"]


# --- briefing -------------------------------------------------------------


async def test_briefing_is_empty_with_nothing_recorded(
    auth_client: AsyncClient,
) -> None:
    token = await register_and_login(auth_client)
    body = (
        await auth_client.get(
            "/v1/briefing", headers={"Authorization": f"Bearer {token}"}
        )
    ).json()
    assert body["is_empty"] is True
    assert body["headline"] == "Nothing on your plate"
    assert "Good" in body["greeting"]


async def test_briefing_counts_what_is_waiting(
    auth_client: AsyncClient, auth_container, wav_bytes: bytes
) -> None:
    import uuid

    from app.core.time_utils import utc_now

    token = await register_and_login(auth_client)
    headers = {"Authorization": f"Bearer {token}"}

    opened = await auth_client.post(
        "/v1/sessions", json={"tz": "Asia/Kolkata"}, headers=headers
    )
    session_id = opened.json()["id"]
    await auth_client.post(
        "/v1/ingest/audio",
        data={
            "segment_id": str(uuid.uuid4()),
            "session_id": session_id,
            "seq": "0",
            "started_at": utc_now().isoformat(),
            "duration_s": "20.0",
        },
        files={"file": ("a.wav", wav_bytes, "audio/wav")},
        headers=headers,
    )
    await auth_client.post(f"/v1/sessions/{session_id}/close", headers=headers)
    await auth_container.pipeline.drain()

    body = (await auth_client.get("/v1/briefing", headers=headers)).json()
    assert body["is_empty"] is False
    assert body["pending_approvals"] > 0
    assert body["headline"]


async def test_briefing_fires_inside_its_window_only(auth_container) -> None:
    service = auth_container.briefing_service
    user = await auth_container.auth_service.register(
        email="t@a.com", password="a-good-password", tz="Asia/Kolkata"
    )
    await auth_container.auth_service.update_preferences(
        user.id, briefing_time="07:00"
    )
    user = await auth_container.auth_service.get_user(user.id)
    ist = ZoneInfo("Asia/Kolkata")

    def at(hour: int, minute: int) -> datetime:
        return datetime(2026, 11, 20, hour, minute, tzinfo=ist).astimezone(UTC)

    assert await service.due_for_briefing(user, now=at(7, 0))
    assert await service.due_for_briefing(user, now=at(7, 4))
    assert not await service.due_for_briefing(user, now=at(7, 6))
    assert not await service.due_for_briefing(user, now=at(6, 59))
    assert not await service.due_for_briefing(user, now=at(13, 0))


async def test_briefing_respects_the_users_timezone(auth_container) -> None:
    """07:00 means 07:00 where the user is, not on the server."""
    service = auth_container.briefing_service
    user = await auth_container.auth_service.register(
        email="tz@a.com", password="a-good-password", tz="Asia/Kolkata"
    )
    user = await auth_container.auth_service.update_preferences(
        user.id, briefing_time="07:00"
    )

    # 01:30 UTC is 07:00 in Kolkata.
    utc_moment = datetime(2026, 11, 20, 1, 30, tzinfo=UTC)
    assert await service.due_for_briefing(user, now=utc_moment)


async def test_disabled_briefing_never_fires(auth_container) -> None:
    service = auth_container.briefing_service
    user = await auth_container.auth_service.register(
        email="off@a.com", password="a-good-password", tz="UTC"
    )
    user = await auth_container.auth_service.update_preferences(
        user.id, briefing_time="07:00", briefing_enabled=False
    )
    moment = datetime(2026, 11, 20, 7, 0, tzinfo=UTC)
    assert not await service.due_for_briefing(user, now=moment)


async def test_invalid_briefing_time_is_rejected(auth_client: AsyncClient) -> None:
    token = await register_and_login(auth_client)
    response = await auth_client.patch(
        "/v1/auth/me",
        json={"briefing_time": "25:99"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


# --- production configuration guards --------------------------------------


def test_prod_refuses_the_development_secret(monkeypatch) -> None:
    """A deployment must not silently run with a known signing key."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", "dev-secret-change-me")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        get_settings()
    get_settings.cache_clear()


def test_prod_refuses_a_short_secret(monkeypatch) -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="at least 32"):
        get_settings()
    get_settings.cache_clear()


def test_prod_refuses_disabled_auth(monkeypatch) -> None:
    """The dev fallback must be unreachable in production."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", "a" * 48)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    with pytest.raises(RuntimeError, match="AUTH_REQUIRED"):
        get_settings()
    get_settings.cache_clear()
