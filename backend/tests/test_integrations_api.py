"""The /v1/integrations endpoints.

The callback is the interesting one. It is the only route in the application
that is reached without a bearer token - Google's redirect carries no
Authorization header - so everything it trusts has to come out of the signed
`state`. These tests pin that down, and pin down that the status endpoint
never leaks token material.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.container import build_container
from app.core.security import create_access_token, create_purpose_token
from app.core.time_utils import utc_now
from app.main import create_app
from app.models.integrations import (
    GMAIL_COMPOSE_AND_SEND_SCOPE,
    GOOGLE_SCOPES,
    GoogleAccount,
)

SECRET = "integration-test-secret-at-least-32-chars"


@pytest.fixture
async def google_client(tmp_path):
    """An app with Google credentials configured."""
    settings = Settings(
        app_env="test",
        log_level="WARNING",
        data_dir=tmp_path / "data",
        transcription_provider="fake",
        llm_provider="fake",
        auth_required=False,
        push_provider="none",
        jwt_secret=SECRET,
        google_client_id="cid.apps.googleusercontent.com",
        google_client_secret="shh",
    )
    container = build_container(settings)
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http, container


@pytest.mark.asyncio
async def test_status_reports_not_configured_without_credentials(client):
    response = await client.get("/v1/integrations/google/status?user_id=u1")
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    assert "no Google credentials" in body["detail"]
    # Stated even when unconfigured: this product cannot send mail.
    assert body["token_can_send_email"] is False


@pytest.mark.asyncio
async def test_connect_returns_a_google_url_with_the_right_scopes(google_client):
    http, _ = google_client
    response = await http.post("/v1/integrations/google/connect?user_id=u1")
    assert response.status_code == 200

    url = response.json()["authorization_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "calendar.events" in url
    # Calendar only by default: `gmail.compose` permits sending, so it is
    # never requested unless GOOGLE_ENABLE_GMAIL is set.
    assert "gmail" not in url
    assert "calendar.events" in url
    assert "gmail.send" not in url
    # The app must be told this cannot run in a webview.
    assert "webview" in response.json()["note"]


@pytest.mark.asyncio
async def test_status_never_returns_token_material(google_client):
    http, container = google_client
    await container.google_accounts_repo.put(
        GoogleAccount(
            user_id="u1",
            email="manan@example.com",
            refresh_token="SUPER-SECRET-REFRESH",
            access_token="SUPER-SECRET-ACCESS",
            access_expires_at=utc_now() + dt.timedelta(hours=1),
            # Granted WITH the Gmail scope, which is the only case where
            # drafting is available - and therefore the case where the
            # status must admit the token can also send.
            scopes=[*GOOGLE_SCOPES, GMAIL_COMPOSE_AND_SEND_SCOPE],
        )
    )

    response = await http.get("/v1/integrations/google/status?user_id=u1")
    body = response.text

    assert response.status_code == 200
    assert "SUPER-SECRET-REFRESH" not in body
    assert "SUPER-SECRET-ACCESS" not in body

    parsed = response.json()
    assert parsed["connected"] is True
    assert parsed["email"] == "manan@example.com"
    assert parsed["can_create_calendar_events"] is True
    assert parsed["can_draft_email"] is True
    # The honest pair: granting `gmail.compose` grants BOTH, because that one
    # scope authorises drafts.send and messages.send. Reporting
    # "cannot send" here is what made the app's UI misleading.
    assert parsed["token_can_send_email"] is True


@pytest.mark.asyncio
async def test_stale_account_asks_for_a_reconnect(google_client):
    http, container = google_client
    await container.google_accounts_repo.put(
        GoogleAccount(
            user_id="u1",
            refresh_token="rt",
            invalid_since=utc_now(),
            invalid_reason="Google rejected the stored authorisation.",
        )
    )

    body = (await http.get("/v1/integrations/google/status?user_id=u1")).json()
    assert body["connected"] is False
    assert body["needs_reconnect"] is True
    assert body["can_create_calendar_events"] is False


@pytest.mark.asyncio
async def test_callback_rejects_a_forged_state(google_client):
    http, container = google_client

    # Signed with the wrong secret.
    forged = create_purpose_token(
        subject="victim", secret="a-different-secret-but-still-long-enough-to-sign",
        purpose="google_oauth", expires_minutes=5,
    )
    response = await http.get(
        f"/v1/integrations/google/callback?code=abc&state={forged}"
    )
    assert response.status_code == 400
    assert await container.google_oauth.for_user("victim") is None

    # A valid *login* token is not a valid state either.
    login, _ = create_access_token(
        user_id="victim", secret=SECRET, expires_minutes=30
    )
    response = await http.get(
        f"/v1/integrations/google/callback?code=abc&state={login}"
    )
    assert response.status_code == 400
    assert await container.google_oauth.for_user("victim") is None


@pytest.mark.asyncio
async def test_callback_handles_a_cancelled_consent_without_a_500(google_client):
    http, _ = google_client
    response = await http.get(
        "/v1/integrations/google/callback?error=access_denied"
    )
    assert response.status_code == 400
    assert "access_denied" in response.text
    # A human is reading this, so it is a page, not a JSON error envelope.
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_callback_with_no_code_is_a_page_not_a_crash(google_client):
    http, _ = google_client
    response = await http.get("/v1/integrations/google/callback")
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_disconnect_is_idempotent(google_client):
    http, container = google_client
    await container.google_accounts_repo.put(
        GoogleAccount(user_id="u1", refresh_token="rt")
    )

    assert (await http.delete("/v1/integrations/google?user_id=u1")).status_code == 204
    assert await container.google_oauth.for_user("u1") is None
    # Again, with nothing connected.
    assert (await http.delete("/v1/integrations/google?user_id=u1")).status_code == 204


@pytest.mark.asyncio
async def test_one_user_cannot_see_anothers_connection(google_client):
    http, container = google_client
    await container.google_accounts_repo.put(
        GoogleAccount(
            user_id="u1", refresh_token="rt", email="manan@example.com"
        )
    )

    body = (await http.get("/v1/integrations/google/status?user_id=u2")).json()
    assert body["connected"] is False
    assert body["email"] == ""
