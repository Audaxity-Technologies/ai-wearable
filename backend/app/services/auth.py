"""Accounts, sessions and device registration."""

from __future__ import annotations

from app.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger, log_event
from app.core.security import AuthError, create_access_token, decode_access_token
from app.core.time_utils import utc_now, validate_timezone
from app.models.users import (
    Device,
    DevicePlatform,
    TokenResponse,
    User,
    hash_password,
    verify_password,
)
from app.repositories.base import Repository

logger = get_logger(__name__)


def normalise_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(
        self,
        *,
        users: Repository[User],
        devices: Repository[Device],
        settings: Settings,
    ) -> None:
        self._users = users
        self._devices = devices
        self._settings = settings

    # --- accounts ---------------------------------------------------------

    async def register(
        self, *, email: str, password: str, display_name: str = "", tz: str = "UTC"
    ) -> User:
        address = normalise_email(email)
        if await self._find_by_email(address) is not None:
            # Deliberately explicit: this endpoint is not an oracle worth
            # protecting, and a vague error here just confuses a real user.
            raise ConflictError("An account with that email already exists.")

        user = User(
            email=address,
            password_hash=hash_password(password),
            display_name=display_name.strip() or address.split("@")[0],
            tz=validate_timezone(tz),
        )
        await self._users.put(user)
        log_event(logger, "auth.registered", user_id=user.id)
        return user

    async def login(self, *, email: str, password: str) -> TokenResponse:
        user = await self._find_by_email(normalise_email(email))

        # Verify even when the user is missing, so a response time cannot be
        # used to work out which emails have accounts.
        stored = user.password_hash if user else _DUMMY_HASH
        ok = verify_password(password, stored)

        if user is None or not ok or user.disabled:
            log_event(logger, "auth.login_failed", known_user=user is not None)
            raise AuthError("Incorrect email or password.")

        user.last_login_at = utc_now()
        user.touch()
        await self._users.put(user)

        token, expires_in = create_access_token(
            user_id=user.id,
            secret=self._settings.jwt_secret,
            expires_minutes=self._settings.jwt_expires_minutes,
        )
        log_event(logger, "auth.login", user_id=user.id)
        return TokenResponse(
            access_token=token,
            expires_in=expires_in,
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            tz=user.tz,
        )

    async def user_from_token(self, token: str) -> User:
        user_id = decode_access_token(token, secret=self._settings.jwt_secret)
        user = await self._users.get(user_id)
        if user is None or user.disabled:
            raise AuthError("Invalid credentials.")
        return user

    async def get_user(self, user_id: str) -> User:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError(f"User not found: {user_id}")
        return user

    async def update_preferences(
        self,
        user_id: str,
        *,
        tz: str | None = None,
        briefing_time: str | None = None,
        briefing_enabled: bool | None = None,
        display_name: str | None = None,
    ) -> User:
        user = await self.get_user(user_id)
        if tz is not None:
            user.tz = validate_timezone(tz)
        if briefing_time is not None:
            user.briefing_time = _validate_hhmm(briefing_time)
        if briefing_enabled is not None:
            user.briefing_enabled = briefing_enabled
        if display_name is not None:
            user.display_name = display_name.strip() or user.display_name
        user.touch()
        await self._users.put(user)
        return user

    async def _find_by_email(self, email: str) -> User | None:
        matches = await self._users.list(email=email)
        return matches[0] if matches else None

    # --- devices ----------------------------------------------------------

    async def register_device(
        self,
        *,
        user_id: str,
        platform: DevicePlatform,
        name: str = "",
        push_token: str | None = None,
        device_id: str | None = None,
    ) -> Device:
        """Register or update a device.

        Keyed on the client-supplied `device_id` so reinstalling the app
        updates one row instead of accumulating stale push targets.
        """
        existing = await self._devices.get(device_id) if device_id else None

        if existing is not None and existing.user_id != user_id:
            raise ConflictError("That device is registered to another account.")

        # A push token can only belong to one device; if it moved, clear it
        # from the old one so notifications do not go to the wrong place.
        if push_token:
            for other in await self._devices.list(push_token=push_token):
                if other.id != (device_id or ""):
                    other.push_token = None
                    other.touch()
                    await self._devices.put(other)

        device = existing or Device(
            id=device_id or Device().id, user_id=user_id, platform=platform
        )
        device.platform = platform
        device.name = name.strip() or device.name
        device.push_token = push_token or device.push_token
        device.last_seen_at = utc_now()
        device.touch()
        await self._devices.put(device)

        log_event(
            logger,
            "auth.device_registered",
            user_id=user_id,
            device_id=device.id,
            platform=platform.value,
            has_push_token=bool(device.push_token),
        )
        return device

    async def devices_for(self, user_id: str) -> list[Device]:
        return await self._devices.list(user_id=user_id)

    async def push_tokens_for(self, user_id: str) -> list[str]:
        return [
            device.push_token
            for device in await self.devices_for(user_id)
            if device.push_token
        ]

    async def forget_device(self, user_id: str, device_id: str) -> None:
        device = await self._devices.get(device_id)
        if device is None or device.user_id != user_id:
            raise NotFoundError(f"Device not found: {device_id}")
        await self._devices.delete(device_id)


def _validate_hhmm(value: str) -> str:
    from app.core.errors import ValidationError

    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValidationError("briefing_time must look like HH:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValidationError("briefing_time must look like HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValidationError("briefing_time must be a real time of day")
    return f"{hour:02d}:{minute:02d}"


#: A real hash of a throwaway password, used to keep login timing uniform for
#: unknown accounts.
_DUMMY_HASH = hash_password("audaxity-timing-equaliser")
