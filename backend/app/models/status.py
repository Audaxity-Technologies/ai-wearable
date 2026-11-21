"""Session and job status machines.

Transitions are declared, not implied, so an illegal move fails loudly instead
of leaving a session in a state the pipeline cannot interpret. Statuses after
`TRANSCRIBED` are declared now but only reached in phase 3+.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.errors import InvalidTransitionError


class SessionStatus(StrEnum):
    OPEN = "open"  # accepting segments
    QUEUED = "queued"  # closed, waiting for the pipeline
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    ANALYZING = "analyzing"  # phase 3
    ANALYZED = "analyzed"  # phase 3
    ANALYZED_DEGRADED = "analyzed_degraded"  # phase 3: schema repair failed
    MEMORY_WRITTEN = "memory_written"  # phase 4
    ACTIONS_PROPOSED = "actions_proposed"  # phase 5
    READY = "ready"
    FAILED = "failed"


#: Allowed forward moves. FAILED is reachable from any non-terminal state and is
#: handled separately so every stage can bail out.
_SESSION_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.OPEN: frozenset({SessionStatus.QUEUED}),
    SessionStatus.QUEUED: frozenset({SessionStatus.TRANSCRIBING}),
    SessionStatus.TRANSCRIBING: frozenset({SessionStatus.TRANSCRIBED}),
    # Phase 2 stops at TRANSCRIBED; READY is permitted so the pipeline has a
    # terminal state before the analysis stages exist.
    SessionStatus.TRANSCRIBED: frozenset(
        {SessionStatus.ANALYZING, SessionStatus.READY}
    ),
    SessionStatus.ANALYZING: frozenset(
        {
            SessionStatus.ANALYZED,
            SessionStatus.ANALYZED_DEGRADED,
            # Explicit rollback: a retryable analysis failure returns here so
            # the retry re-enters the stage instead of no-opping.
            SessionStatus.TRANSCRIBED,
        }
    ),
    # READY is permitted until phase 4 inserts the memory stage.
    SessionStatus.ANALYZED: frozenset(
        {SessionStatus.MEMORY_WRITTEN, SessionStatus.READY}
    ),
    SessionStatus.ANALYZED_DEGRADED: frozenset({SessionStatus.READY}),
    # READY is permitted until phase 5 inserts the action stage.
    SessionStatus.MEMORY_WRITTEN: frozenset(
        {SessionStatus.ACTIONS_PROPOSED, SessionStatus.READY}
    ),
    SessionStatus.ACTIONS_PROPOSED: frozenset({SessionStatus.READY}),
    SessionStatus.READY: frozenset(),
    SessionStatus.FAILED: frozenset({SessionStatus.QUEUED}),  # manual retry
}

TERMINAL_SESSION_STATUSES = frozenset({SessionStatus.READY})


def can_transition(current: SessionStatus, target: SessionStatus) -> bool:
    if target is SessionStatus.FAILED:
        return current not in TERMINAL_SESSION_STATUSES
    return target in _SESSION_TRANSITIONS[current]


def assert_transition(current: SessionStatus, target: SessionStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(
            f"Cannot move session from {current.value!r} to {target.value!r}"
        )


class SegmentStatus(StrEnum):
    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    FAILED = "failed"


class JobType(StrEnum):
    TRANSCRIBE_SEGMENT = "transcribe_segment"
    FINALIZE_SESSION = "finalize_session"
    ANALYZE_SESSION = "analyze_session"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
