"""Connected third-party accounts.

One record per user per provider. The refresh token is the long-lived secret
here: it is what lets the backend write to someone's calendar without them
present, so it is treated like a password - never logged, never returned by
the API, and deleted outright on disconnect rather than flagged inactive.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.time_utils import utc_now
from app.models.domain import StoredModel


class IntegrationProvider(StrEnum):
    GOOGLE = "google"


#: What the backend asks Google for by default, and nothing more.
#:
#: `calendar.events` can create and read events but cannot delete a calendar
#: or read the user's settings.
#:
#: ## No Gmail scope. A correction, 2026-11-21.
#:
#: Phase 10 requested `gmail.compose` on the reasoning that it could draft but
#: not send, with `gmail.send` deliberately withheld. **That was wrong, and it
#: was stated as a guarantee in the code, the docs and the app's own UI.**
#:
#: Google's reference lists `gmail.compose` as a RESTRICTED scope described as
#: "Manage drafts and send emails", and both
#: `users.drafts.send` and `users.messages.send` accept it:
#:
#:     users.drafts.send   requires one of: mail.google.com | gmail.modify
#:                                        | gmail.compose
#:     users.messages.send requires one of: mail.google.com | gmail.modify
#:                                        | gmail.compose | gmail.send
#:
#: So a token holding only `gmail.compose` can send mail, and `gmail.send` is
#: the *narrower* scope of the two - Google classifies it as merely sensitive
#: while `compose` is restricted. The intended structural guarantee - "even a
#: fully compromised backend cannot send mail as the user" - is not available
#: from the Gmail API at all: there is no draft-only scope.
#:
#: Since the guarantee cannot be had, the scope is not requested. With no mail
#: scope the promise is trivially true, and `gmail.compose` would additionally
#: require restricted-scope verification plus a security assessment before
#: this could ever ship to anyone.
#:
#: Email drafting is therefore opt-in (`GOOGLE_ENABLE_GMAIL`), off by default,
#: and the UI says plainly what granting it means. See `services/google/gmail.py`.
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "email",
)

#: Added to the request only when `GOOGLE_ENABLE_GMAIL` is on. Named for what
#: it actually permits, so nobody adds it back believing it is draft-only.
GMAIL_COMPOSE_AND_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.compose"


def google_scopes(*, enable_gmail: bool = False) -> tuple[str, ...]:
    """The scopes to request. Gmail is never included by default."""
    if enable_gmail:
        return (*GOOGLE_SCOPES, GMAIL_COMPOSE_AND_SEND_SCOPE)
    return GOOGLE_SCOPES


class GoogleAccount(StoredModel):
    user_id: str
    provider: IntegrationProvider = IntegrationProvider.GOOGLE

    #: The account the tokens belong to, shown in the app so the user can see
    #: which Google account they connected.
    email: str = ""

    refresh_token: str
    access_token: str = ""
    #: When `access_token` stops working. Refreshed slightly early.
    access_expires_at: dt.datetime | None = None

    scopes: list[str] = Field(default_factory=lambda: list(GOOGLE_SCOPES))
    connected_at: dt.datetime = Field(default_factory=utc_now)

    #: Set when Google rejects the refresh token (revoked access, password
    #: change, or the 7-day expiry that unverified test apps get). Execution
    #: then falls back to local recording and the app can prompt a reconnect
    #: instead of failing silently.
    invalid_since: dt.datetime | None = None
    invalid_reason: str | None = None

    @property
    def is_usable(self) -> bool:
        return self.invalid_since is None and bool(self.refresh_token)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


class IntegrationStatus(BaseModel):
    """What the API reports. Deliberately carries no token material."""

    provider: IntegrationProvider = IntegrationProvider.GOOGLE
    connected: bool
    email: str = ""
    scopes: list[str] = Field(default_factory=list)
    connected_at: dt.datetime | None = None
    needs_reconnect: bool = False
    detail: str = ""
    can_create_calendar_events: bool = False
    can_draft_email: bool = False
    #: Whether the granted token is *capable* of sending mail - not whether
    #: this product ever does. True exactly when `gmail.compose` was granted,
    #: because that scope permits `drafts.send` and `messages.send`.
    #:
    #: Named for the capability rather than the intent on purpose: the
    #: previous field asserted "can_send_email: False" as a structural fact
    #: while the token could in fact send, which made the app's own UI
    #: misleading. A capability the user has granted must be reported as
    #: granted, whatever our code chooses to call.
    token_can_send_email: bool = False
