"""Google OAuth: the only module in the codebase that handles tokens.

The flow is the standard authorization-code grant with `access_type=offline`,
which is what yields a refresh token. Things here that cost real time to
rediscover:

- **Google returns a refresh token only on the first consent.** A reconnect
  without `prompt=consent` silently produces an account record with no
  refresh token, which then breaks an hour later when the access token
  expires. So `prompt=consent` is always sent, and a response without a
  refresh token is refused outright rather than stored.
- **An unverified app's refresh tokens expire after 7 days.** That is Google
  policy for a client in "Testing" publishing status, not a bug to chase.
  When it happens the account is marked invalid and the user is asked to
  reconnect.
- **`state` is mandatory, not optional.** Without it the callback would
  accept an authorization code obtained in someone else's browser. It is a
  purpose-scoped token, so a login token cannot stand in for one.
"""

from __future__ import annotations

import datetime as dt
import secrets
from urllib.parse import urlencode

import httpx

from app.core.errors import AudaxityError
from app.core.logging import get_logger, log_event
from app.core.security import (
    AuthError,
    create_purpose_token,
    decode_purpose_token,
)
from app.core.time_utils import utc_now
from app.models.integrations import GoogleAccount, google_scopes
from app.repositories.base import Repository

logger = get_logger(__name__)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

STATE_PURPOSE = "google_oauth"

#: Refresh this early, so a long request never races the expiry.
REFRESH_MARGIN = dt.timedelta(seconds=120)


class GoogleTokenError(AudaxityError):
    """The user's Google authorisation is missing, expired or revoked."""

    status_code = 400
    code = "google_auth_failed"


class GoogleNotConfigured(AudaxityError):
    """No OAuth client credentials are set on this deployment."""

    status_code = 503
    code = "google_not_configured"


class GoogleOAuthService:
    def __init__(
        self,
        *,
        accounts: Repository[GoogleAccount],
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        jwt_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 30.0,
        enable_gmail: bool = False,
    ) -> None:
        self._accounts = accounts
        # Default: Calendar only. `gmail.compose` is restricted AND permits
        # sending, so it is never requested unless explicitly enabled.
        self._scopes = google_scopes(enable_gmail=enable_gmail)
        self._enable_gmail = enable_gmail
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._jwt_secret = jwt_secret
        self._transport = transport
        self._timeout_s = timeout_s

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    @property
    def scopes(self) -> tuple[str, ...]:
        return self._scopes

    @property
    def gmail_enabled(self) -> bool:
        return self._enable_gmail

    def _require_configured(self) -> None:
        if not self.configured:
            raise GoogleNotConfigured(
                "Google is not configured on this server. Set "
                "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to put approved "
                "events on a real calendar."
            )

    # --- the consent round trip ------------------------------------------

    def authorization_url(self, user_id: str) -> str:
        """Where to send the user's browser to grant access."""
        self._require_configured()
        state = create_purpose_token(
            subject=user_id,
            secret=self._jwt_secret,
            purpose=STATE_PURPOSE,
            expires_minutes=15,
            nonce=secrets.token_urlsafe(8),
        )
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._scopes),
            "access_type": "offline",
            # Without this, a reconnect returns no refresh token at all.
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{AUTH_ENDPOINT}?{urlencode(params)}"

    def user_from_state(self, state: str) -> str:
        """Verify the callback's `state` and return the user it names."""
        try:
            return decode_purpose_token(
                state, secret=self._jwt_secret, purpose=STATE_PURPOSE
            )
        except AuthError as exc:
            raise GoogleTokenError(
                "This Google sign-in link has expired or is not valid. "
                "Start the connection again from the app."
            ) from exc

    async def complete(self, *, code: str, user_id: str) -> GoogleAccount:
        """Exchange an authorization code and store the account."""
        self._require_configured()
        payload = await self._token_request(
            {
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
            }
        )

        refresh_token = str(payload.get("refresh_token") or "")
        if not refresh_token:
            # Without one the connection works for an hour and then breaks
            # with no unattended way to recover. Refuse it now, loudly.
            raise GoogleTokenError(
                "Google did not return a refresh token, so this connection "
                "would stop working within the hour. Remove Audaxity from "
                "your Google account permissions, then connect again."
            )

        access_token = str(payload.get("access_token") or "")
        granted = str(payload.get("scope") or "").split()

        account = await self.for_user(user_id)
        if account is None:
            account = GoogleAccount(user_id=user_id, refresh_token=refresh_token)
        account.refresh_token = refresh_token
        account.access_token = access_token
        account.access_expires_at = self._expiry(payload.get("expires_in"))
        account.scopes = granted or list(GOOGLE_SCOPES)
        account.invalid_since = None
        account.invalid_reason = None
        account.connected_at = utc_now()
        account.email = await self._fetch_email(access_token) or account.email
        account.touch()
        await self._accounts.put(account)

        log_event(
            logger,
            "google.connected",
            user_id=user_id,
            scope_count=len(account.scopes),
        )
        return account

    # --- using the connection --------------------------------------------

    async def for_user(self, user_id: str) -> GoogleAccount | None:
        matches = await self._accounts.list(user_id=user_id)
        return matches[0] if matches else None

    async def access_token(self, user_id: str) -> str:
        """A usable access token, refreshing first if it is near expiry."""
        account = await self.for_user(user_id)
        if account is None:
            raise GoogleTokenError(
                "No Google account is connected. Connect one in Settings to "
                "put approved events on your real calendar."
            )
        if not account.is_usable:
            raise GoogleTokenError(
                account.invalid_reason
                or "Your Google connection needs setting up again."
            )

        fresh_enough = (
            account.access_token
            and account.access_expires_at is not None
            and account.access_expires_at - REFRESH_MARGIN > utc_now()
        )
        if fresh_enough:
            return account.access_token
        return await self._refresh(account)

    async def _refresh(self, account: GoogleAccount) -> str:
        self._require_configured()
        try:
            payload = await self._token_request(
                {
                    "refresh_token": account.refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "refresh_token",
                }
            )
        except GoogleTokenError as exc:
            # A rejected refresh token is permanent. Mark it, so later
            # approvals fail fast with a reconnect prompt instead of calling
            # Google again every time.
            account.invalid_since = utc_now()
            account.invalid_reason = (
                "Google rejected the stored authorisation. This is expected "
                "after 7 days while the app is unverified. Reconnect Google "
                "in Settings."
            )
            account.access_token = ""
            account.touch()
            await self._accounts.put(account)
            log_event(logger, "google.refresh_rejected", user_id=account.user_id)
            raise GoogleTokenError(account.invalid_reason) from exc

        account.access_token = str(payload.get("access_token") or "")
        account.access_expires_at = self._expiry(payload.get("expires_in"))
        account.touch()
        await self._accounts.put(account)
        return account.access_token

    async def disconnect(self, user_id: str) -> bool:
        """Revoke at Google and delete our record. Idempotent."""
        account = await self.for_user(user_id)
        if account is None:
            return False
        token = account.refresh_token
        await self._accounts.delete(account.id)
        log_event(logger, "google.disconnected", user_id=user_id)

        # Deleting our copy is what actually matters. Revoking is courtesy,
        # and must not fail the disconnect when Google is unreachable.
        if token:
            try:
                async with self._client() as client:
                    await client.post(REVOKE_ENDPOINT, data={"token": token})
            except httpx.HTTPError:
                logger.warning("token revoke failed; local record was removed")
        return True

    # --- plumbing ---------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport)

    async def _token_request(self, data: dict[str, str]) -> dict[str, object]:
        try:
            async with self._client() as client:
                response = await client.post(TOKEN_ENDPOINT, data=data)
        except httpx.HTTPError as exc:
            raise GoogleTokenError(f"Could not reach Google: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise GoogleTokenError(
                f"Google rejected the request: {describe_error(response)}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise GoogleTokenError("Google returned an unexpected response.")
        return body

    async def _fetch_email(self, access_token: str) -> str:
        """Best effort: the address is for display only."""
        if not access_token:
            return ""
        try:
            async with self._client() as client:
                response = await client.get(
                    USERINFO_ENDPOINT,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if response.status_code == httpx.codes.OK:
                value = response.json().get("email")
                return str(value) if value else ""
        except (httpx.HTTPError, ValueError):
            pass
        return ""

    @staticmethod
    def _expiry(expires_in: object) -> dt.datetime | None:
        try:
            seconds = int(expires_in)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return utc_now() + dt.timedelta(seconds=seconds)


def describe_error(response: httpx.Response) -> str:
    """A short reason from a Google error body, carrying no token material."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):  # the API-style error envelope
            message = error.get("message")
            if message:
                return f"HTTP {response.status_code}: {message}"
        description = body.get("error_description")
        if description:
            return f"HTTP {response.status_code}: {description}"
        if isinstance(error, str):
            return f"HTTP {response.status_code}: {error}"
    return f"HTTP {response.status_code}"
