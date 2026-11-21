"""Connecting a Google account.

The flow, and why it is shaped like this on a phone:

1. The app calls `POST /v1/integrations/google/connect` with its bearer
   token, and gets back a URL.
2. The app opens that URL in a **system browser**, not a webview. Google
   blocks OAuth in embedded webviews (`disallowed_useragent`), and it is
   right to: a webview lets the host app read what you type into it.
3. Google redirects to `/callback` with `code` and `state`. The `state`
   proves which user began the flow, which is why the callback needs no
   bearer token of its own — and why it must not trust anything else in the
   request.
4. The callback returns a small HTML page telling the user to go back to the
   app. It is HTML and not JSON because a human is looking at it.

`GET /status` is what the Settings screen renders. It never returns token
material, only whether a connection exists and what it can do.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.api.deps import ContainerDep, CurrentUserId
from app.models.integrations import IntegrationStatus

router = APIRouter(prefix="/integrations", tags=["integrations"])

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"


class ConnectResponse(BaseModel):
    authorization_url: str
    #: Said plainly because it is a real constraint, not advice.
    note: str = (
        "Open this in the device browser, not an in-app webview — Google "
        "refuses OAuth in embedded webviews."
    )


@router.get("/google/status", response_model=IntegrationStatus)
async def google_status(
    container: ContainerDep, user_id: CurrentUserId
) -> IntegrationStatus:
    oauth = container.google_oauth
    if not oauth.configured:
        return IntegrationStatus(
            connected=False,
            detail=(
                "This server has no Google credentials configured, so "
                "approved actions are recorded locally. That is a server "
                "setting, not something you can change here."
            ),
        )

    account = await oauth.for_user(user_id)
    if account is None:
        return IntegrationStatus(
            connected=False,
            detail=(
                "Not connected. Approved reminders and events are recorded "
                "in Audaxity only."
            ),
        )

    needs_reconnect = not account.is_usable
    return IntegrationStatus(
        connected=not needs_reconnect,
        email=account.email,
        scopes=account.scopes,
        connected_at=account.connected_at,
        needs_reconnect=needs_reconnect,
        detail=(
            account.invalid_reason
            or "Connected. Approved events go to your Google Calendar."
        )
        if needs_reconnect
        else "Connected. Approved events go to your Google Calendar.",
        can_create_calendar_events=(
            not needs_reconnect and account.has_scope(CALENDAR_SCOPE)
        ),
        can_draft_email=(
            not needs_reconnect and account.has_scope(COMPOSE_SCOPE)
        ),
        # Reported as GRANTED when it is granted. `gmail.compose` permits
        # `drafts.send` and `messages.send`, so a connected Gmail scope means
        # the token can send - whatever this code chooses to call. Claiming
        # otherwise is what made the previous version of this field, and the
        # app's own UI, misleading.
        token_can_send_email=(
            not needs_reconnect and account.has_scope(COMPOSE_SCOPE)
        ),
    )


@router.post("/google/connect", response_model=ConnectResponse)
async def google_connect(
    container: ContainerDep, user_id: CurrentUserId
) -> ConnectResponse:
    url = container.google_oauth.authorization_url(user_id)
    return ConnectResponse(authorization_url=url)


@router.get("/google/callback", response_class=HTMLResponse)
async def google_callback(
    container: ContainerDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Google sends the user's browser here. No bearer token is present."""
    if error:
        # The commonest value is access_denied, i.e. the user pressed cancel.
        return HTMLResponse(
            _page(
                "Not connected",
                "Google reported: " + error,
                "Nothing has changed. You can close this tab.",
            ),
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            _page(
                "Something is missing",
                "That link did not carry the information Google should have "
                "sent back.",
                "Start the connection again from the app.",
            ),
            status_code=400,
        )

    oauth = container.google_oauth
    try:
        user_id = oauth.user_from_state(state)
        account = await oauth.complete(code=code, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - this page must never 500
        message = getattr(exc, "message", None) or str(exc)
        return HTMLResponse(
            _page("Could not finish connecting", message, "You can close this tab."),
            status_code=400,
        )

    return HTMLResponse(
        _page(
            "Google connected",
            f"Audaxity can now add approved events to {account.email or 'your calendar'} "
            "and write drafts.",
            "Audaxity does not send email. Go back to the app.",
        )
    )


@router.delete("/google", status_code=204)
async def google_disconnect(
    container: ContainerDep, user_id: CurrentUserId
) -> Response:
    """Revoke and forget. Safe to call when nothing is connected."""
    await container.google_oauth.disconnect(user_id)
    return Response(status_code=204)


def _page(title: str, body: str, footer: str) -> str:
    """A deliberately plain page. A human on a phone is reading it."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.5 -apple-system, system-ui, sans-serif;
         margin: 0; display: grid; place-items: center; min-height: 100vh;
         padding: 24px; }}
  main {{ max-width: 32rem; }}
  h1 {{ font-size: 1.35rem; margin: 0 0 .75rem; }}
  p {{ margin: 0 0 .75rem; }}
  .dim {{ opacity: .7; font-size: .9rem; }}
</style></head>
<body><main>
  <h1>{title}</h1>
  <p>{body}</p>
  <p class="dim">{footer}</p>
</main></body></html>"""
