"""User accounts and registered devices.

Password hashing uses `hashlib.scrypt` from the standard library rather than
bcrypt: it is memory-hard, needs no compiler on Windows, and avoids adding a
native dependency to a project that has deliberately kept them few.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from enum import StrEnum

from pydantic import BaseModel, EmailStr, Field

from app.models.domain import StoredModel

#: scrypt cost parameters. n=2**14 keeps a verification around 50-100ms, which
#: is slow enough to make offline guessing expensive and fast enough that a
#: login does not feel broken.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Return `salt$hash`, both hex encoded."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of `password` against a stored `salt$hash`."""
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    # compare_digest rather than == so a wrong password cannot be narrowed
    # down by timing.
    return hmac.compare_digest(candidate, expected)


class User(StoredModel):
    email: str
    password_hash: str
    display_name: str = ""
    tz: str = "UTC"

    #: Local time for the daily briefing, "HH:MM" in the user's own timezone.
    briefing_time: str = "07:00"
    briefing_enabled: bool = True

    disabled: bool = False
    last_login_at: dt.datetime | None = None


class DevicePlatform(StrEnum):
    IOS = "ios"
    ANDROID = "android"
    WEB = "web"
    BAND = "band"


class Device(StoredModel):
    """A phone (or, later, a band) that belongs to a user.

    `push_token` is what notifications are sent to. A device with no token
    simply receives nothing - that is a normal state, not an error.
    """

    user_id: str
    platform: DevicePlatform
    name: str = ""
    push_token: str | None = None
    last_seen_at: dt.datetime | None = None
    #: Set for band devices: a long-lived secret scoped to ingest only.
    device_token_hash: str | None = None


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = ""
    tz: str = "UTC"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    email: str
    display_name: str
    tz: str


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    tz: str
    briefing_time: str
    briefing_enabled: bool

    @classmethod
    def from_domain(cls, user: User) -> UserResponse:
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            tz=user.tz,
            briefing_time=user.briefing_time,
            briefing_enabled=user.briefing_enabled,
        )
