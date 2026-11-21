"""Identifier helpers."""

from __future__ import annotations

import uuid

from app.core.errors import ValidationError


def new_id() -> str:
    return str(uuid.uuid4())


def validate_uuid(value: str, field: str) -> str:
    """Client-supplied ids must be UUIDs: they are our idempotency keys."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError(f"{field} must be a UUID, got {value!r}") from exc
    return value
