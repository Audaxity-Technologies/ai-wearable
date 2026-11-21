"""Push notifications via Expo's push service.

Expo's service is used rather than talking to FCM/APNs directly: it needs no
native credentials, works with Expo Go and dev builds alike, and is a single
HTTP endpoint. Swapping it for FCM later means replacing this one driver.

Delivery is best-effort by design. A notification that fails to send must
never fail the pipeline that produced it — the data is already safely stored,
and the user will see it next time they open the app.
"""

from __future__ import annotations

from enum import StrEnum

import httpx
from pydantic import BaseModel, Field

from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

#: Expo rejects a batch larger than this.
MAX_BATCH = 100


class NotificationKind(StrEnum):
    ACTIONS_READY = "actions_ready"
    WAKE_REQUEST = "wake_request"
    DAILY_BRIEFING = "daily_briefing"
    TASK_DUE = "task_due"


class PushMessage(BaseModel):
    to: list[str]
    title: str
    body: str
    data: dict[str, str] = Field(default_factory=dict)
    #: "default" plays the normal notification sound; "critical" is used for
    #: wake requests, which are meant to interrupt.
    sound: str = "default"
    priority: str = "high"
    channel_id: str = "default"


class PushResult(BaseModel):
    sent: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)
    #: Tokens Expo told us are dead. The caller should stop using them.
    invalid_tokens: list[str] = Field(default_factory=list)


class ExpoPushDriver:
    name = "expo"

    def __init__(self, *, timeout_s: float = 20.0) -> None:
        self._timeout_s = timeout_s

    async def send(self, message: PushMessage) -> PushResult:
        if not message.to:
            return PushResult()

        result = PushResult()
        for start in range(0, len(message.to), MAX_BATCH):
            batch = message.to[start : start + MAX_BATCH]
            payload = [
                {
                    "to": token,
                    "title": message.title,
                    "body": message.body,
                    "data": message.data,
                    "sound": message.sound,
                    "priority": message.priority,
                    "channelId": message.channel_id,
                }
                for token in batch
            ]
            await self._send_batch(payload, batch, result)
        return result

    async def _send_batch(
        self, payload: list[dict], tokens: list[str], result: PushResult
    ) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    EXPO_PUSH_URL,
                    json=payload,
                    headers={"accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            result.failed += len(tokens)
            result.errors.append(str(exc)[:200])
            log_event(logger, "push.transport_error", reason=str(exc)[:200])
            return

        if response.status_code != httpx.codes.OK:
            result.failed += len(tokens)
            result.errors.append(f"HTTP {response.status_code}")
            log_event(logger, "push.http_error", status=response.status_code)
            return

        for token, ticket in zip(
            tokens, response.json().get("data", []), strict=False
        ):
            if ticket.get("status") == "ok":
                result.sent += 1
                continue
            result.failed += 1
            detail = ticket.get("details", {}) or {}
            result.errors.append(str(ticket.get("message", "unknown"))[:200])
            if detail.get("error") == "DeviceNotRegistered":
                result.invalid_tokens.append(token)

    async def readiness(self) -> tuple[bool, str]:
        # Expo's endpoint needs no credentials, so there is nothing to check
        # beyond the driver being wired up.
        return True, "expo-push"


class NullPushDriver:
    """Used in tests and when notifications are switched off."""

    name = "none"

    def __init__(self) -> None:
        self.sent: list[PushMessage] = []

    async def send(self, message: PushMessage) -> PushResult:
        self.sent.append(message)
        return PushResult(sent=len(message.to))

    async def readiness(self) -> tuple[bool, str]:
        return True, "push disabled"
