"""Meta-tests for the eval set itself.

An eval set with bad labels is worse than none: it reports confident numbers
about nothing. These tests run without an LLM and check that every labelled
quote really appears in its transcript, and that the scorer actually catches
the errors it claims to catch.
"""

from __future__ import annotations

import json

import pytest

from app.core.text import verify_quote
from app.core.time_utils import session_now
from app.models.analysis import (
    AnalysisItem,
    ConversationAnalysis,
    DateResolution,
    DroppedItem,
    DueDate,
    SpeechAct,
)
from app.services.analysis import AnalysisService
from app.services.llm.fake_driver import FakeLLMDriver
from evals.runner import (
    _expected_acts,
    load_cases,
    score_case,
    session_from_case,
)

CASES = load_cases()
CASE_IDS = [case["id"] for case in CASES]


def test_cases_exist() -> None:
    assert len(CASES) >= 4


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_every_labelled_quote_appears_in_its_transcript(case: dict) -> None:
    """A label that cannot be quoted would score the extractor against fiction."""
    for expected in case["expected_items"]:
        assert verify_quote(expected["quote"], case["transcript"]), (
            f"{case['id']}: labelled quote not in transcript: "
            f"{expected['quote']!r}"
        )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_labels_are_well_formed(case: dict) -> None:
    for key in ("id", "description", "tz", "recorded_at", "transcript"):
        assert case.get(key), f"{case['id']}: missing {key}"

    for expected in case["expected_items"]:
        for act in _expected_acts(expected):
            SpeechAct(act)  # raises if the act is not a known value
        assert expected["owner"]
        if "due_resolution" in expected:
            DateResolution(expected["due_resolution"])


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_recorded_at_is_timezone_aware(case: dict) -> None:
    session = session_from_case(case)
    # Raises if the timestamp is naive or the zone is unknown.
    session_now(session.started_at, session.tz)


def _analysis_from(case: dict, items: list[AnalysisItem]) -> ConversationAnalysis:
    # A perfect extraction also performs the suppressions the case requires:
    # `must_suppress_as_media` asserts a span was dropped WITH a media reason,
    # so an analysis carrying no `dropped` entries cannot satisfy it.
    dropped = [
        DroppedItem(
            title=span[:60],
            reason="media: not part of the user's conversation",
            source_quote=span,
        )
        for span in case.get("must_suppress_as_media", [])
    ]
    return ConversationAnalysis(
        id=case["id"], session_id=case["id"], items=items, dropped=dropped
    )


def _perfect_items(case: dict) -> list[AnalysisItem]:
    items = []
    for expected in case["expected_items"]:
        due = DueDate(
            raw=expected.get("due_raw"),
            resolution=DateResolution(expected.get("due_resolution", "none")),
        )
        if expected.get("due_date"):
            from datetime import date

            due.date = date.fromisoformat(expected["due_date"])
        if expected.get("due_time"):
            from datetime import time

            hour, minute = expected["due_time"].split(":")
            due.time = time(int(hour), int(minute))
        items.append(
            AnalysisItem(
                title=expected["quote"][:40],
                act=SpeechAct(_expected_acts(expected)[0]),
                owner=expected["owner"],
                source_quote=expected["quote"],
                due=due,
            )
        )
    return items


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_scorer_passes_a_perfect_extraction(case: dict) -> None:
    """Guards against a scorer that can never pass."""
    score = score_case(case, _analysis_from(case, _perfect_items(case)))
    assert score.passed, [
        (item.quote, item.found, item.act_ok, item.owner_ok, item.date_ok)
        for item in score.items
    ]


def test_scorer_catches_a_wrong_owner() -> None:
    """The failure mode that matters most must not slip through."""
    case = next(c for c in CASES if c["id"] == "cofounder_sync")
    items = _perfect_items(case)
    commitment = next(i for i in items if i.act is SpeechAct.COMMITMENT)
    commitment.owner = "Pulkit"  # it was Manan who committed

    score = score_case(case, _analysis_from(case, items))
    assert not score.passed
    assert score.owner_correct < score.expected_count


def test_scorer_catches_a_flattened_speech_act() -> None:
    case = next(c for c in CASES if c["id"] == "speech_acts")
    items = _perfect_items(case)
    suggestion = next(i for i in items if i.act is SpeechAct.SUGGESTION)
    suggestion.act = SpeechAct.COMMITMENT  # the classic over-extraction

    score = score_case(case, _analysis_from(case, items))
    assert not score.passed
    assert score.act_correct < score.expected_count


def test_scorer_catches_an_invented_date() -> None:
    case = next(c for c in CASES if c["id"] == "ambiguous_dates")
    items = _perfect_items(case)
    vague = next(
        i for i in items if i.due.resolution is DateResolution.AMBIGUOUS
    )
    from datetime import date

    vague.due.date = date(2026, 11, 23)
    vague.due.resolution = DateResolution.CONFIDENT

    score = score_case(case, _analysis_from(case, items))
    assert not score.passed
    assert score.date_correct < score.expected_count


def test_scorer_catches_over_extraction() -> None:
    case = next(c for c in CASES if c["id"] == "small_talk")
    invented = AnalysisItem(
        title="Visit the coffee place",
        act=SpeechAct.COMMITMENT,
        owner="Arjun",
        source_quote="The coffee at the new place near the office is good.",
    )
    score = score_case(case, _analysis_from(case, [invented]))
    assert not score.passed
    assert score.spurious


def test_small_talk_expects_nothing() -> None:
    case = next(c for c in CASES if c["id"] == "small_talk")
    assert case["expected_items"] == []
    score = score_case(case, _analysis_from(case, []))
    assert score.passed


async def test_runner_works_end_to_end_with_the_fake_driver() -> None:
    """Exercises the harness wiring itself, without touching the network."""
    from evals.runner import run_case

    case = next(c for c in CASES if c["id"] == "cofounder_sync")
    payload = json.dumps(
        {
            "title": "t",
            "summary": "s",
            "participants": [{"name": "Manan", "speaking": True}],
            "projects": [],
            "topics": [],
            "items": [
                {
                    "title": "Work on the financial projections",
                    "act": "commitment",
                    "owner": "Manan",
                    "source_quote": "I'll work on the financial projections.",
                    "due_raw": None,
                    "project": None,
                    "confidence": 0.9,
                }
            ],
        }
    )
    service = AnalysisService(driver=FakeLLMDriver(responses=[payload]))
    score = await run_case(case, service)

    # One of four labelled items found, so the case must not pass.
    assert score.found_count == 1
    assert not score.passed
    matched = next(i for i in score.items if i.found)
    assert matched.owner_ok and matched.act_ok


def test_tolerated_spurious_is_accepted_but_still_reported() -> None:
    """A deliberate product decision should not leave the suite permanently red.

    A scorecard that always fails gets ignored, and then it stops catching
    real regressions. Accepted borderline extractions are listed explicitly in
    the case file and still printed in the report.
    """
    case = next(c for c in CASES if c["id"] == "small_talk")
    assert case.get("tolerated_spurious"), "the decision must be recorded"
    assert case.get("decision_note"), "and the reasoning with it"

    borderline = AnalysisItem(
        title="Take him there",
        act=SpeechAct.SUGGESTION,
        owner="Arjun",
        source_quote="My brother is visiting next month, so I might take him there.",
    )
    score = score_case(case, _analysis_from(case, [borderline]))

    assert score.passed, "an accepted extraction must not fail the case"
    assert score.tolerated == ["Take him there [suggestion]"]
    assert score.spurious == []


def test_other_over_extraction_still_fails_small_talk() -> None:
    """The tolerance is for one named item, not a blanket exemption."""
    case = next(c for c in CASES if c["id"] == "small_talk")
    invented = AnalysisItem(
        title="Watch the match",
        act=SpeechAct.COMMITMENT,
        owner="Neha",
        source_quote="Did you watch the match last night?",
    )
    score = score_case(case, _analysis_from(case, [invented]))

    assert not score.passed
    assert score.spurious


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_media_spans_appear_in_their_transcript(case: dict) -> None:
    """A suppression label that cannot be quoted asserts nothing."""
    for span in case.get("must_suppress_as_media", []):
        assert verify_quote(span, case["transcript"]), (
            f"{case['id']}: must_suppress_as_media span not in transcript: "
            f"{span!r}"
        )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_forbidden_spans_appear_in_their_transcript_ENFORCED(case: dict) -> None:
    """Same for must_not_extract: it must forbid something really present."""
    for span in case.get("must_not_extract_spans", []):
        assert verify_quote(span, case["transcript"]), (
            f"{case["id"]}: must_not_extract_spans span not in transcript: {span!r}"
        )


def test_scorer_catches_a_forbidden_extraction() -> None:
    """`must_not_extract` was documented but unimplemented until 2026-11-21.

    `remind_me.json` calls its forbidden list "the real assertion", and the
    scorer silently ignored it. This test is what stops that happening again.
    """
    case = next(c for c in CASES if c["id"] == "remind_me")
    items = _perfect_items(case)
    items.append(
        AnalysisItem(
            title="Remind me",
            act=SpeechAct.ASSIGNMENT,
            owner="unknown",
            source_quote="So remind me of that",
        )
    )
    score = score_case(case, _analysis_from(case, items))
    assert score.forbidden, "a forbidden extraction was not reported"
    assert not score.passed


def test_scorer_catches_a_forbidden_item_that_also_matched_a_label() -> None:
    """The matcher must not be able to consume a forbidden item.

    Checking `must_not_extract` only against *unmatched* items would let a
    forbidden fragment hide by resembling an expected quote.
    """
    case = next(c for c in CASES if c["id"] == "remind_me")
    items = _perfect_items(case)
    items[0] = items[0].model_copy(
        update={"source_quote": "interview scheduled for tomorrow 6pm"
                               " So remind me of that"}
    )
    score = score_case(case, _analysis_from(case, items))
    assert score.forbidden
    assert not score.passed


def test_scorer_catches_media_that_leaked_through() -> None:
    """Rule 13: a required suppression that did not happen must fail."""
    case = next(c for c in CASES if c["id"] == "media_bleed")
    analysis = ConversationAnalysis(
        id=case["id"],
        session_id=case["id"],
        items=_perfect_items(case),
        dropped=[],  # nothing suppressed
    )
    score = score_case(case, analysis)
    assert score.unsuppressed, "media leakage was not reported"
    assert not score.passed


def test_ignoring_media_does_not_count_as_suppressing_it() -> None:
    """Absence from `items` is luck; a media `DroppedItem` is a judgement.

    Dropping the span for an unrelated reason must not satisfy the
    assertion, or the eval would pass for a model that merely skipped that
    part of the transcript.
    """
    case = next(c for c in CASES if c["id"] == "media_bleed")
    analysis = ConversationAnalysis(
        id=case["id"],
        session_id=case["id"],
        items=_perfect_items(case),
        dropped=[
            DroppedItem(
                title="Bumrah",
                reason="duplicate of an earlier item",
                source_quote="bring Bumrah back before the break",
            )
        ],
    )
    score = score_case(case, analysis)
    assert "bring Bumrah back before the break" in score.unsuppressed
