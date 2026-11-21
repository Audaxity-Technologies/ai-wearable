"""Token issuing and verification.

Short, deliberate surface: issue a signed JWT for a user, and verify one.
The signing secret comes from configuration and must be set in production —
the app refuses to start with the development default when APP_ENV=prod.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import jwt

from app.core.errors import AudaxityError
from app.core.time_utils import utc_now

ALGORITHM = "HS256"


class AuthError(AudaxityError):
    status_code = 401
    code = "unauthorized"


def create_access_token(
    *, user_id: str, secret: str, expires_minutes: int
) -> tuple[str, int]:
    """Return `(token, expires_in_seconds)`."""
    expires_in = expires_minutes * 60
    now = utc_now()
    payload: dict[str, Any] = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM), expires_in


def decode_access_token(token: str, *, secret: str) -> str:
    """Return the user id in `token`, or raise AuthError.

    Every failure mode is reported the same way on purpose: distinguishing
    "expired" from "bad signature" tells an attacker which half of their guess
    was right.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Your session has expired. Sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid credentials.") from exc

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise AuthError("Invalid credentials.")
    return user_id


# --- short-lived, purpose-scoped tokens -------------------------------------
#
# An OAuth `state` parameter must not be usable as a login token, and a login
# token must not be usable as a `state`. Both are HS256 JWTs signed with the
# same secret, so the only thing keeping them apart is an explicit `purpose`
# claim that is checked on the way back in. Without it, a stolen login token
# would authorise linking an attacker's Google account to the victim's user.


def create_purpose_token(
    *,
    subject: str,
    secret: str,
    purpose: str,
    expires_minutes: int,
    nonce: str = "",
) -> str:
    now = utc_now()
    payload: dict[str, Any] = {
        "sub": subject,
        "purpose": purpose,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=expires_minutes)).timestamp()),
    }
    if nonce:
        payload["nonce"] = nonce
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_purpose_token(token: str, *, secret: str, purpose: str) -> str:
    """Return the subject, or raise AuthError.

    Rejects a token issued for any other purpose, including a plain login
    token, which carries no `purpose` claim at all.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise AuthError("That link has expired or is not valid.") from exc

    if payload.get("purpose") != purpose:
        raise AuthError("That link has expired or is not valid.")
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthError("That link has expired or is not valid.")
    return subject
