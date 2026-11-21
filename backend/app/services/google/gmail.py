"""Gmail execution - drafting only, and OFF by default.

## A correction that removed a guarantee, 2026-11-21

This file previously claimed the executor "cannot send email, by
construction", because the OAuth client requested `gmail.compose` rather than
`gmail.send`. **That was false.** Google's reference describes
`gmail.compose` as "Manage drafts and send emails", and both send methods
accept it:

    users.drafts.send    one of: mail.google.com | gmail.modify | gmail.compose
    users.messages.send  one of: mail.google.com | gmail.modify | gmail.compose
                                 | gmail.send

`gmail.send` is in fact the NARROWER scope - sensitive rather than restricted.
The asymmetry was real and the reasoning was sound; the scope was simply not
the one that implemented it, and there is no draft-only Gmail scope to switch
to.

So the guarantee is now structural in a different way: **the scope is not
requested at all** unless `GOOGLE_ENABLE_GMAIL` is set. With it off, this
executor is never built, Audaxity holds no access to the user's mail, and
"cannot send" is true because there is no token that could.

With it on, the honest statement is weaker and the UI makes it: the token
*can* send, this code never calls send, and that is a property of this code
rather than of the permission. Anyone turning it on is accepting that
difference - plus restricted-scope verification and a security assessment
before shipping to anybody else.

What this executor does, and only this: POST to `/users/me/drafts`. It never
calls `drafts.send` or `messages.send`.

Drafts have no server-side idempotency key, unlike calendar events. The
approval service's per-action lock and status machine already guarantee a
single execution per approval; the residual risk is a retry after a failure
that actually succeeded, which would leave two drafts. Two drafts is a
visible, harmless outcome, so this is not worth a side table to prevent.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage

import httpx

from app.core.logging import get_logger, log_event
from app.models.actions import Action, ActionType
from app.services.executors import ExecutionError
from app.services.google.oauth import (
    GoogleOAuthService,
    GoogleTokenError,
    describe_error,
)
from app.services.llm.base import LLMDriver

logger = get_logger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"

DRAFT_SYSTEM_PROMPT = """\
You draft a short, plain email on behalf of the person who recorded a
conversation. You are writing as them, to the person named.

Return ONLY a JSON object: {"subject": "...", "body": "..."}

Rules:
- Base it strictly on the quote and context given. Invent no facts, no
  numbers, no dates and no names that are not provided.
- Four sentences at most. Plain, direct, unfussy. No marketing voice.
- No greeting beyond "Hi <name>," and no signature block - the user's Gmail
  signature is appended automatically.
- If the context is too thin to write honestly, say so in the body in one
  sentence rather than padding it with invented detail.
"""

DRAFT_USER_TEMPLATE = """\
What needs saying: {title}

Why it came up: {reason}

Said in the conversation: "{quote}"

Project: {project}
Recipient: {recipient}

Return the JSON object now.
"""


class GmailDraftExecutor:
    """Creates a Gmail draft. Never sends."""

    action_type = ActionType.EMAIL_DRAFT

    def __init__(
        self,
        *,
        oauth: GoogleOAuthService,
        llm: LLMDriver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._oauth = oauth
        self._llm = llm
        self._transport = transport
        self._timeout_s = timeout_s

    async def execute(self, action: Action) -> str:
        recipient = (action.payload.get("to") or "").strip()

        try:
            token = await self._oauth.access_token(action.user_id)
        except GoogleTokenError as exc:
            raise ExecutionError(exc.message) from exc

        subject, body = await self._compose(action, recipient)
        raw = self._encode(
            to=recipient, subject=subject, body=body
        )

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{GMAIL_API}/users/me/drafts",
                    json={"message": {"raw": raw}},
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            raise ExecutionError(f"Could not reach Gmail: {exc}") from exc

        if response.status_code not in (httpx.codes.OK, httpx.codes.CREATED):
            raise ExecutionError(
                f"Gmail refused the draft: {describe_error(response)}"
            )

        log_event(
            logger,
            "google.gmail.drafted",
            action_id=action.id,
            user_id=action.user_id,
            has_recipient=bool(recipient),
        )

        addressed = f" to {recipient}" if recipient else " (no recipient set)"
        return (
            f"Draft saved in Gmail{addressed}: {subject!r}. "
            "Nothing has been sent — open Gmail to review and send it."
        )

    # --- composing --------------------------------------------------------

    async def _compose(self, action: Action, recipient: str) -> tuple[str, str]:
        """Subject and body, from the LLM when available."""
        fallback = self._fallback(action, recipient)
        if self._llm is None:
            return fallback

        try:
            response = await self._llm.complete_json(
                system=DRAFT_SYSTEM_PROMPT,
                user=DRAFT_USER_TEMPLATE.format(
                    title=action.title,
                    reason=action.reason,
                    quote=action.source_quote,
                    project=action.project or "none given",
                    recipient=recipient or "not specified",
                ),
                temperature=0.2,
            )
        except Exception as exc:  # noqa: BLE001 - a draft is not worth failing
            logger.warning("draft generation failed, using fallback: %r", exc)
            return fallback

        import json

        try:
            parsed = json.loads(response.text)
            subject = str(parsed["subject"]).strip()
            body = str(parsed["body"]).strip()
        except (ValueError, KeyError, TypeError):
            logger.warning("draft response was not usable JSON; using fallback")
            return fallback

        if not subject or not body:
            return fallback
        return subject, body

    @staticmethod
    def _fallback(action: Action, recipient: str) -> tuple[str, str]:
        """A usable draft with no LLM involved.

        Deliberately quotes rather than paraphrases: a template that invents
        prose on the user's behalf is worse than one that is plainly a stub.
        """
        name = recipient.split("@")[0] if recipient else "there"
        body = (
            f"Hi {name},\n\n"
            f"{action.title}.\n\n"
            f"This came out of a conversation where it was put as: "
            f"“{action.source_quote.strip()}”\n"
        )
        return action.title, body

    @staticmethod
    def _encode(*, to: str, subject: str, body: str) -> str:
        """RFC 2822 message, base64url as the Gmail API expects.

        `EmailMessage` handles the header encoding, which matters the moment a
        subject contains a non-ASCII character - hand-rolled formatting
        produces a draft Gmail renders as mojibake.
        """
        message = EmailMessage()
        if to:
            message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
