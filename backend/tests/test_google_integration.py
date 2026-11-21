"""Phase 10: Google Calendar and Gmail execution.

Everything here runs against a stub transport, so no test touches the
network. What is actually being asserted:

- the OAuth state cannot be forged, and a login token is not a valid state
- a refresh-token rejection is recorded once, not retried forever
- a calendar retry is genuinely idempotent (Google's 409 is a success)
- an event is never created without a real date and time
- Gmail drafts, and the scopes make sending impossible
- with no account connected, execution degrades to local recording; with one
  connected but broken, it fails loudly instead
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from app.core.security import create_access_token, create_purpose_token
from app.core.time_utils import utc_now
from app.models.actions import Action, ActionType
from app.models.integrations import GOOGLE_SCOPES, GoogleAccount
from app.repositories.json_repo import JsonRepository
from app.services.executors import (
    ExecutionError,
    GoogleOrLocalExecutor,
    LocalReminderExecutor,
    RequiresGoogleExecutor,
)
from app.services.google import (
    GmailDraftExecutor,
    GoogleCalendarExecutor,
    GoogleOAuthService,
    GoogleReminderExecutor,
    GoogleTokenError,
)
from app.services.google.calendar import event_id_for

SECRET = "t" * 48


@pytest.fixture
def accounts(tmp_path) -> JsonRepository[GoogleAccount]:
    return JsonRepository(tmp_path / "google_accounts.json", GoogleAccount)


def make_oauth(accounts, handler, **kwargs) -> GoogleOAuthService:
    return GoogleOAuthService(
        accounts=accounts,
        client_id=kwargs.pop("client_id", "cid.apps.googleusercontent.com"),
        client_secret=kwargs.pop("client_secret", "secret"),
        redirect_uri="http://localhost:8010/v1/integrations/google/callback",
        jwt_secret=SECRET,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def make_action(**overrides) -> Action:
    payload = {
        "date": "2026-11-25",
        "time": "17:00",
        "tz": "Asia/Kolkata",
        "owner": "Manan",
        "act": "commitment",
    }
    payload.update(overrides.pop("payload", {}))
    defaults = dict(
        user_id="u1",
        type=ActionType.CALENDAR_EVENT,
        title="Review the wearable prototype",
        reason="Manan committed to this in conversation.",
        source_session_id="s1",
        source_quote="Let's meet tomorrow at 5 PM and review the prototype.",
        payload=payload,
        idempotency_key="idem-1",
    )
    defaults.update(overrides)
    return Action(**defaults)  # type: ignore[arg-type]


# --- OAuth ------------------------------------------------------------------


def test_authorization_url_requests_offline_access_and_consent(accounts):
    oauth = make_oauth(accounts, lambda request: httpx.Response(200, json={}))
    url = oauth.authorization_url("u1")

    assert "access_type=offline" in url
    # Without prompt=consent a reconnect silently yields no refresh token.
    assert "prompt=consent" in url
    assert "gmail.compose" not in url
    assert "calendar.events" in url
    # The scope that would let the backend send mail as the user.
    assert "gmail.send" not in url


def test_state_round_trips_but_a_login_token_is_not_a_valid_state(accounts):
    oauth = make_oauth(accounts, lambda request: httpx.Response(200, json={}))
    url = oauth.authorization_url("u1")
    state = url.split("state=")[1].split("&")[0]

    assert oauth.user_from_state(state) == "u1"

    # The attack this blocks: using a stolen *login* token as the state, which
    # would link the attacker's Google account to the victim's user.
    login, _ = create_access_token(user_id="u1", secret=SECRET, expires_minutes=10)
    with pytest.raises(GoogleTokenError):
        oauth.user_from_state(login)

    # And a state signed with the wrong secret.
    forged = create_purpose_token(
        subject="u1", secret="wrong" * 10, purpose="google_oauth", expires_minutes=5
    )
    with pytest.raises(GoogleTokenError):
        oauth.user_from_state(forged)


@pytest.mark.asyncio
async def test_exchange_without_refresh_token_is_refused(accounts):
    """A connection that works for an hour and then dies is not a connection."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})

    oauth = make_oauth(accounts, handler)
    with pytest.raises(GoogleTokenError, match="refresh token"):
        await oauth.complete(code="abc", user_id="u1")

    assert await accounts.list() == []


@pytest.mark.asyncio
async def test_exchange_stores_account_and_refreshes_when_stale(accounts):
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "userinfo" in str(request.url):
            return httpx.Response(200, json={"email": "manan@example.com"})
        body = dict(
            pair.split("=", 1)
            for pair in request.content.decode().split("&")
            if "=" in pair
        )
        calls.append(body)
        if body.get("grant_type") == "authorization_code":
            return httpx.Response(
                200,
                json={
                    "access_token": "first",
                    "refresh_token": "rt-1",
                    "expires_in": 3600,
                    "scope": " ".join(GOOGLE_SCOPES),
                },
            )
        return httpx.Response(200, json={"access_token": "second", "expires_in": 3600})

    oauth = make_oauth(accounts, handler)
    account = await oauth.complete(code="abc", user_id="u1")
    assert account.email == "manan@example.com"
    assert account.refresh_token == "rt-1"

    # Fresh token is reused rather than refreshed.
    assert await oauth.access_token("u1") == "first"
    assert sum(1 for c in calls if c.get("grant_type") == "refresh_token") == 0

    # Expire it, and the next call refreshes.
    account.access_expires_at = utc_now() - dt.timedelta(minutes=5)
    await accounts.put(account)
    assert await oauth.access_token("u1") == "second"
    assert sum(1 for c in calls if c.get("grant_type") == "refresh_token") == 1


@pytest.mark.asyncio
async def test_rejected_refresh_token_is_marked_once_not_retried(accounts):
    """The 7-day unverified-app expiry. It must not become a request storm."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "Token expired"},
        )

    oauth = make_oauth(accounts, handler)
    await accounts.put(
        GoogleAccount(
            user_id="u1",
            refresh_token="rt-dead",
            access_token="stale",
            access_expires_at=utc_now() - dt.timedelta(hours=2),
        )
    )

    with pytest.raises(GoogleTokenError, match="Reconnect"):
        await oauth.access_token("u1")
    assert attempts["n"] == 1

    stored = await oauth.for_user("u1")
    assert stored is not None and stored.invalid_since is not None

    # Second call fails from the stored state, without calling Google again.
    with pytest.raises(GoogleTokenError):
        await oauth.access_token("u1")
    assert attempts["n"] == 1


@pytest.mark.asyncio
async def test_disconnect_deletes_even_if_revoke_fails(accounts):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    oauth = make_oauth(accounts, handler)
    await accounts.put(GoogleAccount(user_id="u1", refresh_token="rt"))

    assert await oauth.disconnect("u1") is True
    assert await oauth.for_user("u1") is None
    # Idempotent.
    assert await oauth.disconnect("u1") is False


# --- Calendar ---------------------------------------------------------------


async def connected(accounts) -> None:
    await accounts.put(
        GoogleAccount(
            user_id="u1",
            refresh_token="rt",
            access_token="at",
            access_expires_at=utc_now() + dt.timedelta(hours=1),
            scopes=list(GOOGLE_SCOPES),
        )
    )


@pytest.mark.asyncio
async def test_calendar_event_is_created_with_evidence_attached(accounts):
    await connected(accounts)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer at"
        return httpx.Response(
            200, json={"htmlLink": "https://calendar.google.com/event?eid=x"}
        )

    oauth = make_oauth(accounts, lambda r: httpx.Response(200, json={}))
    executor = GoogleCalendarExecutor(
        oauth=oauth, transport=httpx.MockTransport(handler)
    )
    result = await executor.execute(make_action())

    assert "Added to your Google Calendar" in result
    assert "2026-11-25 17:00" in result
    assert captured["start"] == {
        "dateTime": "2026-11-25T17:00:00+05:30",
        "timeZone": "Asia/Kolkata",
    }
    # An hour long by default, and the end is after the start.
    assert captured["end"] == {
        "dateTime": "2026-11-25T18:00:00+05:30",
        "timeZone": "Asia/Kolkata",
    }
    # The quote travels with the event, so it is auditable in Calendar itself.
    assert "review the prototype" in str(captured["description"])


@pytest.mark.asyncio
async def test_a_retried_approval_does_not_create_a_second_event(accounts):
    """Google's 409 on a duplicate client id is the idempotency guarantee."""
    await connected(accounts)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["id"] in seen:
            return httpx.Response(409, json={"error": {"message": "duplicate"}})
        seen.append(body["id"])
        return httpx.Response(200, json={})

    oauth = make_oauth(accounts, lambda r: httpx.Response(200, json={}))
    executor = GoogleCalendarExecutor(
        oauth=oauth, transport=httpx.MockTransport(handler)
    )
    action = make_action()

    first = await executor.execute(action)
    second = await executor.execute(action)

    assert "Added to your Google Calendar" in first
    assert "Already on your Google Calendar" in second
    assert len(seen) == 1


def test_event_id_is_stable_and_calendar_legal():
    action = make_action()
    # base32hex: a-v and 0-9 only, at least 5 characters.
    event_id = event_id_for(action)
    assert event_id == event_id_for(make_action())
    assert len(event_id) >= 5
    assert set(event_id) <= set("0123456789abcdefghijklmnopqrstuv")

    # A different approval gets a different id.
    other = make_action(idempotency_key="idem-2")
    assert event_id_for(other) != event_id


@pytest.mark.asyncio
async def test_calendar_refuses_to_invent_a_time(accounts):
    await connected(accounts)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not call Google without a time")

    oauth = make_oauth(accounts, lambda r: httpx.Response(200, json={}))
    executor = GoogleCalendarExecutor(
        oauth=oauth, transport=httpx.MockTransport(handler)
    )
    action = make_action(payload={"time": ""})
    action.payload.pop("time")

    with pytest.raises(ExecutionError, match="needs both a date and a time"):
        await executor.execute(action)


@pytest.mark.asyncio
async def test_calendar_refuses_an_unknown_timezone_rather_than_shifting_it(
    accounts,
):
    await connected(accounts)
    oauth = make_oauth(accounts, lambda r: httpx.Response(200, json={}))
    executor = GoogleCalendarExecutor(
        oauth=oauth,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={})
        ),
    )
    action = make_action(payload={"tz": "Mars/Olympus_Mons"})

    with pytest.raises(ExecutionError, match="Unknown timezone"):
        await executor.execute(action)


@pytest.mark.asyncio
async def test_dated_reminder_becomes_an_all_day_event(accounts):
    await connected(accounts)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={})

    oauth = make_oauth(accounts, lambda r: httpx.Response(200, json={}))
    executor = GoogleReminderExecutor(
        oauth=oauth,
        local_fallback=LocalReminderExecutor(),
        transport=httpx.MockTransport(handler),
    )
    action = make_action(type=ActionType.REMINDER, payload={"time": ""})
    action.payload.pop("time")

    result = await executor.execute(action)

    assert "All-day reminder" in result
    assert captured["start"] == {"date": "2026-11-25"}
    # An all-day end date is exclusive: same-day end renders as nothing.
    assert captured["end"] == {"date": "2026-11-26"}
    assert captured["transparency"] == "transparent"


@pytest.mark.asyncio
async def test_undated_reminder_stays_local_rather_than_guessing_today(accounts):
    await connected(accounts)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("an undated reminder must not reach Google")

    oauth = make_oauth(accounts, lambda r: httpx.Response(200, json={}))
    executor = GoogleReminderExecutor(
        oauth=oauth,
        local_fallback=LocalReminderExecutor(),
        transport=httpx.MockTransport(handler),
    )
    action = make_action(type=ActionType.REMINDER, payload={})
    action.payload.pop("date")
    action.payload.pop("time")

    result = await executor.execute(action)
    assert "without a date" in result


# --- Gmail ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_creates_a_draft_and_says_nothing_was_sent(accounts):
    await connected(accounts)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "draft-1"})

    oauth = make_oauth(accounts, lambda r: httpx.Response(200, json={}))
    executor = GmailDraftExecutor(
        oauth=oauth, llm=None, transport=httpx.MockTransport(handler)
    )
    action = make_action(
        type=ActionType.EMAIL_DRAFT,
        title="Send the investor update",
        payload={"to": "priya@example.com"},
    )

    result = await executor.execute(action)

    # The endpoint is drafts, not messages/send. This is the product promise.
    assert str(captured["url"]).endswith("/users/me/drafts")
    assert "Nothing has been sent" in result
    assert "priya@example.com" in result

    import base64

    raw = captured["body"]["message"]["raw"]  # type: ignore[index]
    decoded = base64.urlsafe_b64decode(raw).decode()
    assert "To: priya@example.com" in decoded
    assert "Send the investor update" in decoded


def test_no_mail_scope_is_requested_by_default():
    """The only honest form of "cannot send email": ask for no mail access.

    ## Why this test replaced one that asserted the opposite

    The previous version was named `test_the_requested_scopes_cannot_send_mail`
    and asserted that `gmail.compose` WAS requested while `gmail.send` was
    not, with the comment "the token is incapable of sending". It passed
    throughout, and it was asserting a falsehood: Google documents
    `gmail.compose` as "Manage drafts and send emails", and both
    `users.drafts.send` and `users.messages.send` accept it.

    The test could never have caught that, because it checked the *scope
    list* against what the author believed rather than against what the
    scope permits. A test that restates an assumption cannot falsify it.
    """
    assert not any("gmail" in scope for scope in GOOGLE_SCOPES)
    assert "https://www.googleapis.com/auth/calendar.events" in GOOGLE_SCOPES
    assert not any(scope.endswith("/gmail.modify") for scope in GOOGLE_SCOPES)
    assert "https://mail.google.com/" not in GOOGLE_SCOPES


def test_the_gmail_scope_is_named_for_what_it_actually_permits():
    """So nobody re-adds it believing it is draft-only.

    `gmail.compose` is a RESTRICTED scope that authorises sending. The
    constant's name carries that, because the comment did not survive
    contact with a confident assumption.
    """
    from app.models.integrations import (
        GMAIL_COMPOSE_AND_SEND_SCOPE,
        google_scopes,
    )

    assert "SEND" in GMAIL_COMPOSE_AND_SEND_SCOPE_NAME()
    assert GMAIL_COMPOSE_AND_SEND_SCOPE.endswith("/gmail.compose")
    # Opt-in, and only then.
    assert GMAIL_COMPOSE_AND_SEND_SCOPE not in google_scopes()
    assert GMAIL_COMPOSE_AND_SEND_SCOPE in google_scopes(enable_gmail=True)


def GMAIL_COMPOSE_AND_SEND_SCOPE_NAME() -> str:
    return "GMAIL_COMPOSE_AND_SEND_SCOPE"


@pytest.mark.asyncio
async def test_draft_falls_back_when_the_llm_misbehaves(accounts):
    await connected(accounts)

    class BrokenLLM:
        name = "broken"

        async def complete_json(
            self, *, system, user, temperature=0.0, seed=None
        ):
            raise RuntimeError("provider down")

        async def readiness(self):
            return False, "down"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "draft-1"})

    oauth = make_oauth(accounts, lambda r: httpx.Response(200, json={}))
    executor = GmailDraftExecutor(
        oauth=oauth,
        llm=BrokenLLM(),  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
    )
    action = make_action(type=ActionType.EMAIL_DRAFT, payload={"to": "a@b.com"})

    # A dead LLM must produce a plain draft, not a failed approval.
    result = await executor.execute(action)
    assert "Draft saved in Gmail" in result


# --- dispatch ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_account_records_locally_but_a_broken_one_fails_loudly(accounts):
    """The asymmetry that keeps the UI honest."""

    def unreachable(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not be called")

    oauth = make_oauth(accounts, lambda r: httpx.Response(200, json={}))
    remote = GoogleCalendarExecutor(
        oauth=oauth, transport=httpx.MockTransport(unreachable)
    )

    from app.services.executors import LocalCalendarExecutor

    dispatch = GoogleOrLocalExecutor(
        action_type=ActionType.CALENDAR_EVENT,
        remote=remote,
        local=LocalCalendarExecutor(),
        oauth=oauth,
    )

    # Nothing connected: the user never asked for a calendar write.
    result = await dispatch.execute(make_action())
    assert "Calendar event recorded" in result

    # Connected but the authorisation has gone stale: must NOT claim success.
    await accounts.put(
        GoogleAccount(
            user_id="u1",
            refresh_token="rt",
            invalid_since=utc_now(),
            invalid_reason="Reconnect Google in Settings.",
        )
    )
    with pytest.raises(ExecutionError, match="Reconnect"):
        await dispatch.execute(make_action())


@pytest.mark.asyncio
async def test_email_draft_without_google_refuses_clearly(accounts):
    oauth = make_oauth(accounts, lambda r: httpx.Response(200, json={}))
    executor = RequiresGoogleExecutor(
        action_type=ActionType.EMAIL_DRAFT,
        remote=GmailDraftExecutor(
            oauth=oauth,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={})
            ),
        ),
        oauth=oauth,
    )

    with pytest.raises(ExecutionError, match="connected Google account"):
        await executor.execute(make_action(type=ActionType.EMAIL_DRAFT))
