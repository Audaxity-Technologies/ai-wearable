"""Domain errors, mapped to HTTP status codes at the API boundary."""

from __future__ import annotations


class AudaxityError(Exception):
    """Base class for all expected domain failures."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AudaxityError):
    status_code = 404
    code = "not_found"


class ValidationError(AudaxityError):
    status_code = 422
    code = "validation_error"


class ConflictError(AudaxityError):
    """The request is valid but conflicts with current state."""

    status_code = 409
    code = "conflict"


class InvalidTransitionError(ConflictError):
    code = "invalid_transition"


class PayloadTooLargeError(AudaxityError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMediaTypeError(AudaxityError):
    status_code = 415
    code = "unsupported_media_type"


class TranscriptionError(AudaxityError):
    """A transcription driver failed. Retryable unless stated otherwise."""

    status_code = 502
    code = "transcription_failed"

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMError(AudaxityError):
    """An LLM driver failed. Retryable unless stated otherwise."""

    status_code = 502
    code = "llm_failed"

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class AnalysisError(AudaxityError):
    """The model responded, but the response could not be used."""

    status_code = 502
    code = "analysis_failed"


class ConfigurationError(AudaxityError):
    status_code = 500
    code = "configuration_error"
