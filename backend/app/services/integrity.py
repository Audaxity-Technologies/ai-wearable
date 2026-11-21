"""The capture-integrity receipt: what was recorded, and what was not.

## Why this exists

The complaint that makes people stop wearing a recording device is not a bad
summary. It is not trusting it. An Omi user reports the pendant "sometimes
shows as disconnected, so they miss critical data"; Limitless users found
recording had silently stopped after a phone restart. A system that captures
nothing while appearing to work is strictly worse than no system at all,
because by then the user has stopped taking their own notes.

Every other screen in this app answers "what did I say?". This one answers
**"should I believe the answer?"** - and it is the only screen whose job is to
report our own failures.

## The design rule

Unverifiable claims are not made. The receipt distinguishes three things that
a progress bar would flatten into one:

- **covered**   - audio we hold and transcribed
- **gap**       - wall-clock time inside the session that no segment covers.
                  The mic was muted, the app was killed, or the phone locked.
                  We do not know what was said, and we say so.
- **silent**    - audio we hold whose transcription came back empty. Captured
                  correctly; there was simply nothing to hear.

A gap and a silence look identical on a timeline and mean opposite things. One
is our failure, the other is a quiet room, and conflating them is how a
product ends up claiming coverage it does not have.

This is a pure function over data the session already holds, deliberately: it
has no repository, no clock and no I/O, so every branch is testable and the
receipt can never itself be the thing that fails.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field, computed_field

from app.models.analysis import ConversationAnalysis
from app.models.domain import Segment, Session
from app.models.status import SegmentStatus, SessionStatus

#: Gaps shorter than this are an artefact of how segments are cut, not a
#: capture failure. Chunk boundaries never align to the millisecond, and
#: reporting a 0.4s "gap" between consecutive segments would train the user
#: to ignore the whole screen.
GAP_TOLERANCE_S = 2.0

#: Below this fraction of wall-clock time covered, the session is described as
#: having lost material rather than merely having a gap. Chosen so that a
#: single short interruption does not trigger it but a dead recorder does.
COVERAGE_CONCERN = 0.9


class TimelineGap(BaseModel):
    """A stretch of the session no audio covers."""

    start_s: float = Field(description="Seconds from the session start")
    end_s: float
    at: dt.datetime = Field(description="Wall-clock time the gap began")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 2)


class SuppressedSpan(BaseModel):
    """Something heard but deliberately not used, and why."""

    quote: str
    reason: str


class CaptureReceipt(BaseModel):
    """What we captured for one session, and what we did not.

    Everything here is derived from stored records. Nothing is inferred.
    """

    session_id: str
    status: SessionStatus

    # --- segments ---
    segments_total: int = 0
    segments_transcribed: int = 0
    segments_failed: int = 0
    segments_pending: int = 0

    # --- time ---
    #: Wall clock from the first segment's start to the last segment's end.
    wall_clock_s: float = 0.0
    #: Audio we actually hold.
    captured_s: float = 0.0
    #: Of that, audio whose transcript came back empty.
    silent_s: float = 0.0
    gaps: list[TimelineGap] = Field(default_factory=list)

    # --- what we chose not to use ---
    suppressed: list[SuppressedSpan] = Field(default_factory=list)
    dropped_other: int = 0

    # --- analysis honesty ---
    analysis_present: bool = False
    analysis_degraded: bool = False
    analysis_degraded_reason: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def gap_s(self) -> float:
        return round(sum(gap.duration_s for gap in self.gaps), 2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def coverage(self) -> float:
        """Fraction of wall-clock time we hold audio for, 0.0-1.0.

        1.0 when there was no wall clock to cover (a session with a single
        segment, or none): claiming 0% coverage of nothing would read as a
        failure where there was no opportunity to fail.
        """
        if self.wall_clock_s <= 0:
            return 1.0
        return min(1.0, round(self.captured_s / self.wall_clock_s, 4))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_complete(self) -> bool:
        """Nothing failed, nothing is outstanding, and no audio is missing."""
        return (
            self.segments_failed == 0
            and self.segments_pending == 0
            and not self.gaps
            and not self.analysis_degraded
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def headline(self) -> str:
        """One sentence, in the user's terms, that never overstates.

        Ordered by what the user most needs to know, which is not the same as
        ordered by severity: an unfinished session is not a problem yet, but
        it is the thing that explains a missing answer.
        """
        if self.segments_total == 0:
            return "Nothing was recorded in this session."
        if self.segments_pending:
            return (
                f"Still processing {self.segments_pending} of "
                f"{self.segments_total} recordings."
            )
        if self.segments_failed:
            return (
                f"{self.segments_failed} of {self.segments_total} recordings "
                "could not be transcribed. What they contained is unknown."
            )
        if self.gaps and self.coverage < COVERAGE_CONCERN:
            return (
                f"{_duration(self.gap_s)} of this session was not recorded. "
                "Anything said then is missing."
            )
        if self.gaps:
            return (
                f"Recorded with a {_duration(self.gap_s)} break. "
                "Everything else was captured."
            )
        if self.analysis_degraded:
            return (
                "Audio was captured in full, but the analysis did not "
                "complete cleanly - items may be missing."
            )
        if self.silent_s and self.captured_s and self.silent_s >= self.captured_s:
            return "Recorded in full. No speech was detected in any of it."
        return f"Recorded in full - {_duration(self.captured_s)}, no gaps."


def _duration(seconds: float) -> str:
    """Human phrasing. Rounded honestly: never rounds a loss down to zero."""
    total = int(round(seconds))
    if seconds > 0 and total == 0:
        return "under a second"
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m" if secs == 0 else f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}m"


def build_receipt(
    *,
    session: Session,
    segments: list[Segment],
    analysis: ConversationAnalysis | None,
) -> CaptureReceipt:
    """Compute the receipt. Pure: no I/O, no clock, no hidden state."""
    receipt = CaptureReceipt(
        session_id=session.id,
        status=session.status,
        segments_total=len(segments),
        analysis_present=analysis is not None,
        analysis_degraded=bool(analysis and analysis.degraded),
        analysis_degraded_reason=analysis.degraded_reason if analysis else None,
    )

    if analysis is not None:
        for entry in analysis.dropped:
            if entry.reason.startswith("media"):
                receipt.suppressed.append(
                    SuppressedSpan(
                        quote=entry.source_quote or entry.title,
                        reason=entry.reason,
                    )
                )
            else:
                receipt.dropped_other += 1

    if not segments:
        return receipt

    ordered = sorted(segments, key=lambda item: (item.started_at, item.seq))

    for segment in ordered:
        if segment.status is SegmentStatus.TRANSCRIBED:
            receipt.segments_transcribed += 1
            receipt.captured_s += segment.duration_s
            if not (segment.text or "").strip():
                receipt.silent_s += segment.duration_s
        elif segment.status is SegmentStatus.FAILED:
            receipt.segments_failed += 1
            # Deliberately NOT counted as captured. We hold the audio but
            # cannot say what is in it, which is the same epistemic position
            # as a gap - and claiming coverage for it would be a lie of
            # exactly the kind this screen exists to prevent.
        else:
            receipt.segments_pending += 1
            receipt.captured_s += segment.duration_s

    first = ordered[0]
    last = ordered[-1]
    end = last.started_at + dt.timedelta(seconds=last.duration_s)
    receipt.wall_clock_s = round(
        max(0.0, (end - first.started_at).total_seconds()), 2
    )

    # Gaps: walk the segments in time order and record any stretch between the
    # end of one and the start of the next. Overlaps (a clock adjustment, or
    # two sources recording at once) are not gaps and must not produce
    # negative ones.
    cursor = first.started_at
    for segment in ordered:
        if (segment.started_at - cursor).total_seconds() > GAP_TOLERANCE_S:
            receipt.gaps.append(
                TimelineGap(
                    start_s=round(
                        (cursor - first.started_at).total_seconds(), 2
                    ),
                    end_s=round(
                        (segment.started_at - first.started_at).total_seconds(),
                        2,
                    ),
                    at=cursor,
                )
            )
        segment_end = segment.started_at + dt.timedelta(
            seconds=segment.duration_s
        )
        cursor = max(cursor, segment_end)

    receipt.captured_s = round(receipt.captured_s, 2)
    receipt.silent_s = round(receipt.silent_s, 2)
    return receipt
