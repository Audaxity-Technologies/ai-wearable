"""Tests for the utterance index (memory phase C).

Two classes of risk here. The index could be *wrong about time*, which would
make every phase D time filter quietly answer about the wrong moment. Or it
could **over-link** — attach a speech act to a span that did not carry it —
which would make "what did we decide" return things nobody decided.

The second one is not hypothetical: the first run against real recordings
tagged a four-character span `'Also'` as an assignment, because character
similarity scored "also" against the one-word window "so" at 0.67.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.models.analysis import (
    AnalysisItem,
    ConversationAnalysis,
    SpeechAct,
)
from app.models.domain import Segment, Session, TranscriptSpan
from app.models.places import CoarsePlace
from app.models.status import SegmentStatus, SessionStatus
from app.models.utterances import Utterance, UtteranceResponse
from app.repositories.json_repo import JsonRepository
from app.services.utterances import (
    MIN_SHARED_WORDS,
    UtteranceService,
    link_score,
)

START = dt.datetime(2026, 11, 21, 10, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def service(tmp_path) -> UtteranceService:
    return UtteranceService(
        utterances=JsonRepository(tmp_path / "utterances.json", Utterance)
    )


def _session(**kw) -> Session:
    return Session(
        **{
            "id": "sess-1",
            "user_id": "u1",
            "tz": "Asia/Kolkata",
            "started_at": START,
            "status": SessionStatus.READY,
            **kw,
        }
    )


def _segment(
    seq: int,
    *,
    offset_s: float,
    spans: list[tuple[float, float, str]],
    place: CoarsePlace | None = None,
    duration_s: float = 30.0,
) -> Segment:
    return Segment(
        id=f"seg-{seq}",
        session_id="sess-1",
        user_id="u1",
        seq=seq,
        started_at=START + dt.timedelta(seconds=offset_s),
        duration_s=duration_s,
        mime_type="audio/m4a",
        size_bytes=1024,
        blob_key=f"k{seq}",
        status=SegmentStatus.TRANSCRIBED,
        spans=[
            TranscriptSpan(start_s=a, end_s=b, text=t) for a, b, t in spans
        ],
        place=place,
    )


def _analysis(items: list[AnalysisItem]) -> ConversationAnalysis:
    return ConversationAnalysis(id="sess-1", session_id="sess-1", items=items)


def _item(
    quote: str,
    *,
    act: SpeechAct = SpeechAct.COMMITMENT,
    owner: str = "unknown",
    title: str = "something",
    project: str | None = None,
) -> AnalysisItem:
    return AnalysisItem(
        title=title, act=act, owner=owner, source_quote=quote, project=project
    )


# --- the thing this layer exists to add: absolute time ------------------


async def test_span_offsets_become_absolute_timestamps(service) -> None:
    """The whole point of phase C.

    A span offset is meaningless on its own - "that day" cannot be resolved
    against 13.0 seconds.
    """
    segment = _segment(
        0, offset_s=0, spans=[(0.0, 10.0, "first"), (13.0, 19.0, "second")]
    )
    built = await service.rebuild_for_session(_session(), [segment])

    assert built[0].started_at == START
    assert built[0].ended_at == START + dt.timedelta(seconds=10)
    assert built[1].started_at == START + dt.timedelta(seconds=13)
    assert built[1].offset_start_s == 13.0


async def test_the_second_segments_offsets_are_relative_to_its_own_start(
    service,
) -> None:
    """An offset is per segment, so a later segment must not restart the clock
    at the session's beginning."""
    segments = [
        _segment(0, offset_s=0, spans=[(0.0, 5.0, "early")]),
        _segment(1, offset_s=30, spans=[(2.0, 6.0, "late")]),
    ]
    built = await service.rebuild_for_session(_session(), segments)
    assert built[1].started_at == START + dt.timedelta(seconds=32)


async def test_a_reversed_span_never_produces_a_negative_duration(
    service,
) -> None:
    """A provider can emit end < start on a mis-segmented chunk, and a
    negative duration would poison every time filter downstream."""
    segment = _segment(0, offset_s=0, spans=[(10.0, 4.0, "garbled")])
    built = await service.rebuild_for_session(_session(), [segment])
    assert built[0].duration_s >= 0
    assert built[0].ended_at >= built[0].started_at


async def test_segments_are_ordered_by_time_not_arrival(service) -> None:
    """Uploads arrive from a mobile outbox in any order."""
    segments = [
        _segment(2, offset_s=60, spans=[(0.0, 2.0, "third")]),
        _segment(0, offset_s=0, spans=[(0.0, 2.0, "first")]),
        _segment(1, offset_s=30, spans=[(0.0, 2.0, "second")]),
    ]
    built = await service.rebuild_for_session(_session(), segments)
    assert [u.text for u in built] == ["first", "second", "third"]
    assert [u.seq for u in built] == [0, 1, 2]


async def test_blank_spans_are_skipped(service) -> None:
    segment = _segment(
        0, offset_s=0, spans=[(0.0, 1.0, "   "), (1.0, 3.0, "real")]
    )
    built = await service.rebuild_for_session(_session(), [segment])
    assert [u.text for u in built] == ["real"]


async def test_place_is_copied_per_segment_and_never_inferred(
    service,
) -> None:
    """You walk from the office to a cafe mid-conversation, and losing a fix
    must leave a hole rather than borrow the previous segment's."""
    segments = [
        _segment(
            0,
            offset_s=0,
            spans=[(0.0, 2.0, "at the office")],
            place=CoarsePlace(lat=28.524, lon=77.185, label="Office"),
        ),
        _segment(1, offset_s=30, spans=[(0.0, 2.0, "no fix here")]),
    ]
    built = await service.rebuild_for_session(_session(), segments)
    assert built[0].place is not None and built[0].place.label == "Office"
    assert built[1].place is None


# --- the over-linking bug ----------------------------------------------


def test_a_short_span_does_not_carry_an_act() -> None:
    """The regression. `quote_similarity("Also", "So remind me")` is 0.67 -
    it compares "also" against the one-word window "so" - so the first run
    against real recordings tagged a four-character span as an assignment.
    """
    assert link_score("So remind me", "Also") == 0.0
    assert link_score("You can hire interns", "Hello") == 0.0


def test_an_exact_span_links_fully() -> None:
    assert link_score("So remind me", "So remind me") == 1.0


def test_a_quote_inside_a_longer_span_links() -> None:
    """The ordinary case: the extractor quotes part of a sentence."""
    assert (
        link_score(
            "I have to go somewhere tomorrow.",
            "Hello, I have to go somewhere tomorrow. Oh my friend's house",
        )
        == 1.0
    )


def test_a_quote_spanning_two_spans_links_both() -> None:
    quote = "day after tomorrow I have my exam"
    assert link_score(quote, "Also day after tomorrow") > 0.5
    assert link_score(quote, "I have my exam") > 0.5


def test_one_shared_word_is_not_enough() -> None:
    assert MIN_SHARED_WORDS == 2
    assert link_score("send the compliance forms", "forms") == 0.0


def test_empty_text_scores_zero() -> None:
    assert link_score("", "anything") == 0.0
    assert link_score("anything", "") == 0.0


async def test_items_attach_to_the_spans_they_were_quoted_from(
    service,
) -> None:
    segment = _segment(
        0,
        offset_s=0,
        spans=[
            (0.0, 2.0, "Hello"),
            (2.0, 6.0, "I have my interview scheduled for tomorrow 6pm"),
            (6.0, 8.0, "Also"),
            (8.0, 10.0, "So remind me"),
        ],
    )
    analysis = _analysis(
        [
            _item(
                "I have my interview scheduled for tomorrow 6pm",
                act=SpeechAct.COMMITMENT,
                title="Interview",
            ),
            _item("So remind me", act=SpeechAct.ASSIGNMENT, title="Remind"),
        ]
    )
    built = await service.rebuild_for_session(_session(), [segment], analysis)
    by_text = {u.text: u for u in built}

    assert by_text["Hello"].act is None
    assert by_text["Also"].act is None, "short span must not carry an act"
    assert by_text[
        "I have my interview scheduled for tomorrow 6pm"
    ].act is SpeechAct.COMMITMENT
    assert by_text["So remind me"].act is SpeechAct.ASSIGNMENT
    assert by_text["So remind me"].item_title == "Remind"


async def test_the_first_item_keeps_a_contested_span(service) -> None:
    """Two items quoting one span: the earlier wins rather than being
    silently overwritten."""
    segment = _segment(0, offset_s=0, spans=[(0.0, 4.0, "send the forms")])
    analysis = _analysis(
        [
            _item("send the forms", act=SpeechAct.COMMITMENT, title="first"),
            _item("send the forms", act=SpeechAct.ASSIGNMENT, title="second"),
        ]
    )
    built = await service.rebuild_for_session(_session(), [segment], analysis)
    assert built[0].act is SpeechAct.COMMITMENT
    assert built[0].item_title == "first"


async def test_an_unmatchable_quote_falls_back_to_the_best_span(
    service,
) -> None:
    """A quote verified against the whole transcript can straddle a silence
    and match no single span well. Falling back keeps the item locatable
    rather than dropping it from the index.
    """
    segment = _segment(
        0,
        offset_s=0,
        spans=[(0.0, 2.0, "we need to finish the pitch"), (2.0, 4.0, "yes")],
    )
    analysis = _analysis([_item("finish the pitch by Friday")])
    built = await service.rebuild_for_session(_session(), [segment], analysis)
    assert built[0].act is SpeechAct.COMMITMENT
    assert built[1].act is None


async def test_no_analysis_means_no_acts_but_still_an_index(
    service,
) -> None:
    """Built even when extraction degraded: the spans and timestamps are
    still true, and an index of what was said is useful precisely then."""
    segment = _segment(0, offset_s=0, spans=[(0.0, 2.0, "something")])
    built = await service.rebuild_for_session(_session(), [segment], None)
    assert len(built) == 1
    assert built[0].act is None


async def test_speaker_is_never_inferred_from_an_items_owner(
    service,
) -> None:
    """`owner` is who took the work on, not who spoke.

    "You should send the forms" has owner Pulkit and was spoken by somebody
    else, so filling the speaker from the owner would attribute speech to
    whoever was named.
    """
    segment = _segment(0, offset_s=0, spans=[(0.0, 4.0, "You should send it")])
    analysis = _analysis(
        [_item("You should send it", act=SpeechAct.ASSIGNMENT, owner="Pulkit")]
    )
    built = await service.rebuild_for_session(_session(), [segment], analysis)
    assert built[0].item_owner == "Pulkit"
    assert built[0].speaker_person_id is None


# --- rebuild is idempotent ----------------------------------------------


async def test_rebuilding_replaces_rather_than_duplicates(service) -> None:
    segment = _segment(0, offset_s=0, spans=[(0.0, 2.0, "once")])
    await service.rebuild_for_session(_session(), [segment])
    await service.rebuild_for_session(_session(), [segment])
    assert len(await service.for_session("sess-1")) == 1


async def test_deleting_a_session_drops_its_rows(service) -> None:
    """A derived index that outlives its source would answer questions about
    audio the user believes they erased."""
    segment = _segment(0, offset_s=0, spans=[(0.0, 2.0, "gone soon")])
    await service.rebuild_for_session(_session(), [segment])
    assert await service.delete_for_session("sess-1") == 1
    assert await service.for_session("sess-1") == []


# --- querying ------------------------------------------------------------


async def _populate(service) -> None:
    segments = [
        _segment(
            0,
            offset_s=0,
            spans=[(0.0, 2.0, "we decided to ship on Friday")],
            place=CoarsePlace(lat=28.524, lon=77.185, label="Office"),
        ),
        _segment(
            1,
            offset_s=3600,
            spans=[(0.0, 2.0, "I will send the compliance forms")],
            place=CoarsePlace(lat=28.600, lon=77.220, label="Blue Tokai"),
        ),
    ]
    analysis = _analysis(
        [
            _item("we decided to ship on Friday", act=SpeechAct.DECISION),
            _item(
                "I will send the compliance forms", act=SpeechAct.COMMITMENT
            ),
        ]
    )
    await service.rebuild_for_session(_session(), segments, analysis)


async def test_filtering_by_act(service) -> None:
    await _populate(service)
    rows, total = await service.query(
        user_id="u1", act=SpeechAct.DECISION
    )
    assert total == 1
    assert "ship on Friday" in rows[0].text


async def test_filtering_by_time_range(service) -> None:
    """"That day" becomes a range, which is what phase D will produce."""
    await _populate(service)
    rows, total = await service.query(
        user_id="u1",
        since=START + dt.timedelta(minutes=30),
        until=START + dt.timedelta(hours=2),
    )
    assert total == 1
    assert "compliance forms" in rows[0].text


async def test_filtering_by_place_uses_the_name(service) -> None:
    await _populate(service)
    rows, total = await service.query(user_id="u1", place="blue tokai")
    assert total == 1
    assert "compliance forms" in rows[0].text


async def test_filtering_by_text(service) -> None:
    await _populate(service)
    _, total = await service.query(user_id="u1", contains="compliance")
    assert total == 1


async def test_combining_filters_narrows(service) -> None:
    """The whole value over session-grained retrieval: act AND time AND
    place, at the grain of a sentence."""
    await _populate(service)
    _, total = await service.query(
        user_id="u1",
        act=SpeechAct.COMMITMENT,
        place="blue tokai",
        since=START,
    )
    assert total == 1

    _, none = await service.query(
        user_id="u1", act=SpeechAct.COMMITMENT, place="office"
    )
    assert none == 0


async def test_total_is_reported_separately_from_the_page(service) -> None:
    """"Nothing matched" and "a lot matched" must be distinguishable."""
    await _populate(service)
    rows, total = await service.query(user_id="u1", limit=1)
    assert len(rows) == 1
    assert total == 2


async def test_another_users_utterances_are_not_returned(service) -> None:
    await _populate(service)
    _, total = await service.query(user_id="someone-else")
    assert total == 0


async def test_actionable_only_excludes_discussion(service) -> None:
    segment = _segment(
        0,
        offset_s=0,
        spans=[(0.0, 2.0, "the coffee here is good"), (2.0, 4.0, "I will call them")],
    )
    analysis = _analysis(
        [
            _item("the coffee here is good", act=SpeechAct.DISCUSSION),
            _item("I will call them", act=SpeechAct.COMMITMENT),
        ]
    )
    await service.rebuild_for_session(_session(), [segment], analysis)
    rows, total = await service.query(user_id="u1", actionable_only=True)
    assert total == 1
    assert "call them" in rows[0].text


# --- the response shape --------------------------------------------------


def test_the_response_shows_a_place_name_never_coordinates() -> None:
    """Coordinates are an index, not an answer."""
    utterance = Utterance(
        user_id="u1",
        session_id="s",
        segment_id="g",
        seq=0,
        text="hello",
        started_at=START,
        ended_at=START + dt.timedelta(seconds=2),
        place=CoarsePlace(lat=28.524, lon=77.185, label="Blue Tokai"),
    )
    body = UtteranceResponse.from_domain(utterance).model_dump()
    assert body["place_name"] == "Blue Tokai"
    assert "28.524" not in str(body)
    assert "lat" not in body
