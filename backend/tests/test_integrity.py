"""Tests for the capture-integrity receipt.

The receipt's whole value is that it does not overstate. These tests are
mostly about the ways it could lie: counting failed audio as captured,
reporting a chunk-boundary artefact as a loss, or flattening a gap and a
silence into the same number.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.models.analysis import ConversationAnalysis, DroppedItem
from app.models.domain import Segment, Session
from app.models.status import SegmentStatus, SessionStatus
from app.services.integrity import (
    COVERAGE_CONCERN,
    GAP_TOLERANCE_S,
    build_receipt,
)

START = dt.datetime(2026, 11, 21, 10, 0, 0, tzinfo=dt.UTC)


def _session(**kwargs) -> Session:
    defaults = {
        "user_id": "u1",
        "tz": "Asia/Kolkata",
        "started_at": START,
        "status": SessionStatus.READY,
    }
    return Session(**{**defaults, "id": "sess-1", **kwargs})


def _segment(
    seq: int,
    *,
    offset_s: float,
    duration_s: float = 30.0,
    status: SegmentStatus = SegmentStatus.TRANSCRIBED,
    text: str | None = "something was said",
) -> Segment:
    return Segment(
        session_id="sess-1",
        user_id="u1",
        seq=seq,
        started_at=START + dt.timedelta(seconds=offset_s),
        duration_s=duration_s,
        mime_type="audio/m4a",
        size_bytes=1024,
        blob_key=f"k{seq}",
        status=status,
        text=text,
    )


def _analysis(**kwargs) -> ConversationAnalysis:
    return ConversationAnalysis(
        id="sess-1", session_id="sess-1", **kwargs
    )


# --- the empty and trivial cases ---------------------------------------


def test_no_segments_reports_nothing_recorded() -> None:
    receipt = build_receipt(
        session=_session(), segments=[], analysis=None
    )
    assert receipt.segments_total == 0
    assert receipt.headline == "Nothing was recorded in this session."
    # No wall clock to cover: claiming 0% would read as a failure where there
    # was no opportunity to fail.
    assert receipt.coverage == 1.0


def test_contiguous_segments_report_full_coverage() -> None:
    segments = [
        _segment(0, offset_s=0),
        _segment(1, offset_s=30),
        _segment(2, offset_s=60),
    ]
    receipt = build_receipt(
        session=_session(), segments=segments, analysis=_analysis()
    )
    assert receipt.gaps == []
    assert receipt.coverage == 1.0
    assert receipt.is_complete
    assert receipt.captured_s == 90.0
    assert "no gaps" in receipt.headline


# --- gaps ---------------------------------------------------------------


def test_a_real_gap_is_reported_with_its_wall_clock_time() -> None:
    """The phone locked for two minutes in the middle of a conversation."""
    segments = [
        _segment(0, offset_s=0),
        _segment(1, offset_s=150),  # 120s gap after the first ends at 30s
    ]
    receipt = build_receipt(
        session=_session(), segments=segments, analysis=None
    )
    assert len(receipt.gaps) == 1
    gap = receipt.gaps[0]
    assert gap.start_s == 30.0
    assert gap.end_s == 150.0
    assert gap.duration_s == 120.0
    assert gap.at == START + dt.timedelta(seconds=30)
    assert not receipt.is_complete
    assert "not recorded" in receipt.headline


def test_sub_tolerance_boundaries_are_not_gaps() -> None:
    """Chunk boundaries never align exactly; reporting that trains distrust."""
    segments = [
        _segment(0, offset_s=0, duration_s=30.0),
        _segment(1, offset_s=30 + GAP_TOLERANCE_S - 0.1),
    ]
    receipt = build_receipt(
        session=_session(), segments=segments, analysis=None
    )
    assert receipt.gaps == []
    assert receipt.is_complete


def test_overlapping_segments_never_produce_a_negative_gap() -> None:
    """Two sources at once, or a clock adjustment, is not a loss."""
    segments = [
        _segment(0, offset_s=0, duration_s=60.0),
        _segment(1, offset_s=30, duration_s=60.0),
    ]
    receipt = build_receipt(
        session=_session(), segments=segments, analysis=None
    )
    assert receipt.gaps == []
    assert receipt.gap_s == 0.0
    # Captured audio legitimately exceeds wall clock when sources overlap;
    # coverage must still be clamped rather than reporting 133%.
    assert receipt.coverage == 1.0


def test_a_small_gap_reads_differently_from_a_large_one() -> None:
    """One brief interruption is not the same story as a dead recorder."""
    brief = build_receipt(
        session=_session(),
        segments=[
            _segment(0, offset_s=0, duration_s=300.0),
            _segment(1, offset_s=310, duration_s=300.0),
        ],
        analysis=None,
    )
    assert brief.coverage > COVERAGE_CONCERN
    assert "break" in brief.headline
    assert "not recorded" not in brief.headline

    mostly_lost = build_receipt(
        session=_session(),
        segments=[
            _segment(0, offset_s=0, duration_s=10.0),
            _segment(1, offset_s=600, duration_s=10.0),
        ],
        analysis=None,
    )
    assert mostly_lost.coverage < COVERAGE_CONCERN
    assert "not recorded" in mostly_lost.headline


# --- the distinction the screen exists for ------------------------------


def test_silence_is_not_a_gap() -> None:
    """Identical on a timeline, opposite in meaning.

    A gap is our failure. A silence is a quiet room, captured correctly.
    """
    segments = [
        _segment(0, offset_s=0, text="hello"),
        _segment(1, offset_s=30, text="   "),  # transcribed, nothing heard
    ]
    receipt = build_receipt(
        session=_session(), segments=segments, analysis=None
    )
    assert receipt.gaps == []
    assert receipt.silent_s == 30.0
    assert receipt.captured_s == 60.0
    assert receipt.coverage == 1.0
    assert receipt.is_complete


def test_fully_silent_session_says_so_without_claiming_failure() -> None:
    segments = [_segment(0, offset_s=0, text=""), _segment(1, offset_s=30, text="")]
    receipt = build_receipt(
        session=_session(), segments=segments, analysis=None
    )
    assert receipt.is_complete
    assert "No speech was detected" in receipt.headline


def test_failed_segments_are_not_counted_as_captured() -> None:
    """We hold the audio but cannot say what is in it.

    Counting it as coverage would be the exact lie this screen prevents.
    """
    segments = [
        _segment(0, offset_s=0),
        _segment(1, offset_s=30, status=SegmentStatus.FAILED, text=None),
    ]
    receipt = build_receipt(
        session=_session(), segments=segments, analysis=None
    )
    assert receipt.segments_failed == 1
    assert receipt.captured_s == 30.0
    assert receipt.coverage == 0.5
    assert not receipt.is_complete
    assert "could not be transcribed" in receipt.headline
    assert "unknown" in receipt.headline


def test_pending_segments_are_reported_before_failures() -> None:
    """An unfinished session is not a problem yet, but it explains a gap."""
    segments = [
        _segment(0, offset_s=0),
        _segment(1, offset_s=30, status=SegmentStatus.UPLOADED, text=None),
    ]
    receipt = build_receipt(
        session=_session(status=SessionStatus.TRANSCRIBING),
        segments=segments,
        analysis=None,
    )
    assert receipt.segments_pending == 1
    assert "Still processing" in receipt.headline
    assert not receipt.is_complete


# --- suppression and analysis honesty -----------------------------------


def test_media_suppression_is_surfaced_separately_from_other_drops() -> None:
    """Rule 13 suppressions are a product decision the user should see.

    A quote dropped because the model could not point at it is our bug; a
    quote dropped as a TV advert is a judgement we made on the user's behalf,
    and they are entitled to check it.
    """
    analysis = _analysis(
        dropped=[
            DroppedItem(
                title="Call now",
                reason="media: not part of the user's conversation",
                source_quote="Call now and get a second one absolutely free",
            ),
            DroppedItem(
                title="invented",
                reason="source_quote not found in transcript",
                source_quote="something never said",
            ),
        ]
    )
    receipt = build_receipt(
        session=_session(), segments=[_segment(0, offset_s=0)], analysis=analysis
    )
    assert len(receipt.suppressed) == 1
    assert receipt.suppressed[0].quote.startswith("Call now")
    assert receipt.dropped_other == 1


def test_degraded_analysis_is_reported_even_with_perfect_audio() -> None:
    analysis = _analysis(degraded=True, degraded_reason="schema repair failed")
    receipt = build_receipt(
        session=_session(), segments=[_segment(0, offset_s=0)], analysis=analysis
    )
    assert receipt.analysis_degraded
    assert not receipt.is_complete
    assert "did not complete cleanly" in receipt.headline


def test_missing_analysis_is_absence_not_failure() -> None:
    receipt = build_receipt(
        session=_session(status=SessionStatus.TRANSCRIBED),
        segments=[_segment(0, offset_s=0)],
        analysis=None,
    )
    assert receipt.analysis_present is False
    assert receipt.analysis_degraded is False
    assert receipt.is_complete


# --- phrasing -----------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0.4, "under a second"),
        (45, "45s"),
        (60, "1m"),
        (95, "1m 35s"),
        (3600, "1h"),
        (3900, "1h 5m"),
    ],
)
def test_durations_never_round_a_loss_down_to_zero(
    seconds: float, expected: str
) -> None:
    from app.services.integrity import _duration

    assert _duration(seconds) == expected


def test_segments_are_ordered_by_time_not_by_sequence() -> None:
    """Out-of-order arrival must not invent a gap.

    Segments are uploaded from a mobile outbox and can arrive in any order;
    the receipt is about the recording's timeline, not the upload's.
    """
    segments = [
        _segment(2, offset_s=60),
        _segment(0, offset_s=0),
        _segment(1, offset_s=30),
    ]
    receipt = build_receipt(
        session=_session(), segments=segments, analysis=None
    )
    assert receipt.gaps == []
    assert receipt.wall_clock_s == 90.0
