"""Structured logging.

Privacy rule from the architecture doc: transcript text, audio bytes and
credentials are never logged. Log identifiers and stage transitions only.
Helpers here exist so call sites do not have to remember that.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_CONFIGURED = False

#: Field names that must never reach the logs.
REDACTED_KEYS = frozenset(
    {
        "text",
        "transcript",
        "transcript_text",
        "source_quote",
        "audio",
        "api_key",
        "groq_api_key",
        "token",
        "authorization",
        "password",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            payload.update(redact(extra))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def redact(context: dict[str, Any]) -> dict[str, Any]:
    """Drop values whose key suggests transcript content or a credential."""
    clean: dict[str, Any] = {}
    for key, value in context.items():
        if key.lower() in REDACTED_KEYS:
            clean[key] = "[redacted]"
        elif isinstance(value, dict):
            clean[key] = redact(value)
        else:
            clean[key] = value
    return clean


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, message: str, /, **context: Any) -> None:
    """Log an event with a redacted structured context."""
    logger.info(message, extra={"context": context})
