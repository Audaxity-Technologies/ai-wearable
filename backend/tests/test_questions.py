"""Tests for the question parser (memory phase D).

The parser's job is to produce a *filter*, and the risk is not failing to
understand a question - that is reported honestly. The risk is resolving one
**wrongly**, because a wrong constraint returns real sentences from the wrong
person or the wrong moment, which reads as an answer rather than a mistake.

So most of these tests are about the three refusals: ask when ambiguous, admit
when not understood, and decline to search everything.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.models.analysis import SpeechAct
from app.models.domain import Segment, Session, TranscriptSpan
from app.models.people import Person
from app.models.places import CoarsePlace
from app.models.questions import ClarificationKind, QuestionKind
from app.models.status import SegmentStatus, SessionStatus
from app.models.utterances import Utterance
from app.repositories.json_repo import JsonRepository
from app.services.people import PersonService
from app.services.questions import QuestionParser
from app.services.utterances import UtteranceService

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 11, 25, 19, 0, tzinfo=IST)  # a Wednesday
USER = "u1"


@pytest.fixture
def people(tmp_path) -> PersonService:
    return PersonService(
        people=JsonRepository(tmp_path / "people.json", Person)
    )


@pytest.fixture
def utterances(tmp_path) -> UtteranceService:
    return UtteranceService(
        utterances=JsonRepository(tmp_path / "utterances.json", Utterance)
    )


@pytest.fixture
def parser(people, utterances) -> QuestionParser:
    return QuestionParser(people=people, utterances=utterances)


async def _seed(utterances: UtteranceService) -> None:
    """One conversation at the office on the 21st, one at a cafe on the 24th."""
    for day, place, text, act in (
        (21, "Office", "we decided to ship on Friday", SpeechAct.DECISION),
        (24, "Blue Tokai", "I will send the compliance forms", SpeechAct.COMMITMENT),
    ):
        session = Session(
            id=f"s{day}",
            user_id=USER,
            tz="Asia/Kolkata",
            started_at=dt.datetime(2026, 11, day, 10, 0, tzinfo=IST),
            status=SessionStatus.READY,
        )
        segment = Segment(
            id=f"g{day}",
            session_id=session.id,
            user_id=USER,
            seq=0,
            started_at=session.started_at,
            duration_s=30.0,
            mime_type="audio/m4a",
            size_bytes=10,
            blob_key=f"k{day}",
            status=SegmentStatus.TRANSCRIBED,
            spans=[TranscriptSpan(start_s=0.0, end_s=4.0, text=text)],
            place=CoarsePlace(lat=28.5, lon=77.1, label=place),
        )
        built = await utterances.rebuild_for_session(session, [segment])
        built[0].act = act
        await utterances._utterances.put(built[0])  # noqa: SLF001


async def _parse(parser: QuestionParser, text: str):
    return await parser.parse(text, user_id=USER, tz="Asia/Kolkata", now=NOW)


# --- classification -----------------------------------------------------


@pytest.mark.parametrize(
    "text,kind",
    [
        ("did I ever mention the deadline", QuestionKind.EXISTENCE),
        ("how many times did I promise that", QuestionKind.AGGREGATION),
        ("what did we decide yesterday", QuestionKind.TEMPORAL),
        ("what did we decide about the pitch", QuestionKind.POINT_LOOKUP),
        ("what happened", QuestionKind.UNKNOWN),
    ],
)
async def test_classification(parser, text: str, kind: QuestionKind) -> None:
    assert (await _parse(parser, text)).kind is kind


async def test_a_question_about_a_person_is_a_relationship(
    parser, people
) -> None:
    await people.resolve_or_create(USER, "Pulkit")
    resolved = await _parse(parser, "what has Pulkit committed to")
    assert resolved.kind is QuestionKind.RELATIONSHIP
    assert resolved.person_names == ["Pulkit"]


# --- act inference ------------------------------------------------------


@pytest.mark.parametrize(
    "text,act",
    [
        ("what did we decide", SpeechAct.DECISION),
        ("what did I promise", SpeechAct.COMMITMENT),
        ("what was I supposed to do", SpeechAct.ASSIGNMENT),
        ("what did anyone suggest", SpeechAct.SUGGESTION),
    ],
)
async def test_verbs_map_to_speech_acts(parser, text: str, act) -> None:
    """The product's core distinction, so the mapping is explicit."""
    assert act in (await _parse(parser, text)).acts


async def test_owing_spans_three_acts_rather_than_one(parser) -> None:
    """"What do I owe" is not a single act - it is commitment, assignment
    and requirement together."""
    resolved = await _parse(parser, "what do I owe")
    assert resolved.actionable_only
    assert resolved.acts == []


# --- self vs a named person ---------------------------------------------


async def test_first_person_means_the_account_holder(parser) -> None:
    resolved = await _parse(parser, "what did I promise")
    assert resolved.about_self
    assert resolved.person_ids == []


async def test_a_person_marked_as_self_also_sets_about_self(
    parser, people
) -> None:
    person = await people.resolve_or_create(USER, "Manan")
    assert person is not None
    await people.set_self(person.id, True)
    resolved = await _parse(parser, "what did Manan promise")
    assert resolved.about_self
    assert resolved.person_names == ["Manan"]


async def test_an_unknown_name_becomes_a_search_term_not_a_person(
    parser,
) -> None:
    """Honest: with nobody by that name recorded, there is no person to
    filter on, and inventing one would return nothing while looking like a
    resolved filter."""
    resolved = await _parse(parser, "what did Priya commit to")
    assert resolved.person_ids == []
    assert "priya" in resolved.terms


async def test_a_longer_name_wins_over_its_prefix(parser, people) -> None:
    await people.resolve_or_create(USER, "Pulkit Verma")
    resolved = await _parse(parser, "what did Pulkit Verma decide")
    assert resolved.person_names == ["Pulkit Verma"]
    assert len(resolved.person_ids) == 1


# --- the clarification branch -------------------------------------------


async def test_two_people_answering_to_one_name_asks_which(
    parser, people, tmp_path
) -> None:
    """Picking one would attribute speech to whoever sorted first."""
    repo = JsonRepository(tmp_path / "people.json", Person)
    for name in ("Pulkit Verma", "Pulkit Sharma"):
        await repo.put(
            Person(
                user_id=USER,
                canonical_name=name,
                aliases=["pulkit"],
                mention_count=3,
            )
        )
    service = PersonService(people=repo)
    local = QuestionParser(people=service, utterances=parser._utterances)  # noqa: SLF001

    resolved = await local.parse(
        "what did Pulkit decide", user_id=USER, tz="Asia/Kolkata", now=NOW
    )
    assert resolved.needs_clarification
    clarification = resolved.clarifications[0]
    assert clarification.kind is ClarificationKind.PERSON
    assert len(clarification.options) == 2
    assert "Which pulkit" in clarification.question


async def test_that_day_without_an_anchor_asks_which_day(parser) -> None:
    resolved = await _parse(parser, "what did we decide that day")
    assert resolved.needs_clarification
    assert resolved.clarifications[0].kind is ClarificationKind.TIME
    assert resolved.time_range is None


async def test_an_ambiguous_question_is_not_executed(parser, utterances) -> None:
    """The constraint is missing, so running the filter without it would
    quietly widen the search and present the result as understood."""
    await _seed(utterances)
    answer = await parser.answer(
        "what did we decide that day", user_id=USER, tz="Asia/Kolkata", now=NOW
    )
    assert answer.utterances == []
    assert answer.total == 0
    assert "which day" in answer.verdict.lower()


# --- place --------------------------------------------------------------


async def test_a_place_resolves_from_places_actually_recorded(
    parser, utterances
) -> None:
    await _seed(utterances)
    resolved = await _parse(parser, "what did we talk about at Blue Tokai")
    assert resolved.place == "Blue Tokai"


async def test_an_unrecorded_place_is_not_resolved(parser, utterances) -> None:
    """Accepting "at the Taj Mahal" and returning nothing would look like a
    retrieval failure rather than a place never recorded at."""
    await _seed(utterances)
    resolved = await _parse(parser, "what did we say at the Taj Mahal")
    assert resolved.place is None


# --- terms --------------------------------------------------------------


async def test_question_scaffolding_does_not_become_a_search_term(
    parser,
) -> None:
    """"How many times did I promise" must not filter text on "many"."""
    resolved = await _parse(parser, "how many times did I promise that")
    assert "many" not in resolved.terms
    assert "times" not in resolved.terms


async def test_a_resolved_time_phrase_is_not_also_a_term(parser) -> None:
    resolved = await _parse(parser, "what did we decide yesterday")
    assert "yesterday" not in resolved.terms


# --- answering ----------------------------------------------------------


async def test_a_resolved_question_returns_the_matching_span(
    parser, utterances
) -> None:
    await _seed(utterances)
    answer = await parser.answer(
        "what did we decide at the Office",
        user_id=USER,
        tz="Asia/Kolkata",
        now=NOW,
    )
    assert answer.total == 1
    assert "ship on Friday" in answer.utterances[0]["text"]
    assert "decision" in answer.resolved.describe()


async def test_a_time_filter_excludes_the_other_day(parser, utterances) -> None:
    """The query session-grained retrieval could never express."""
    await _seed(utterances)
    answer = await parser.answer(
        "what did I commit to yesterday",
        user_id=USER,
        tz="Asia/Kolkata",
        now=NOW,
    )
    assert answer.total == 1
    assert "compliance forms" in answer.utterances[0]["text"]


async def test_nothing_matching_says_so_rather_than_returning_empty(
    parser, utterances
) -> None:
    await _seed(utterances)
    answer = await parser.answer(
        "what did we decide about the budget",
        user_id=USER,
        tz="Asia/Kolkata",
        now=NOW,
    )
    assert answer.total == 0
    assert "nothing recorded matches" in answer.verdict.lower()
    # And it says which filter found nothing, so the user can tell a wrong
    # question from an empty history.
    assert "decision" in answer.verdict


async def test_an_unparseable_question_is_admitted(parser, utterances) -> None:
    await _seed(utterances)
    answer = await parser.answer(
        "what happened", user_id=USER, tz="Asia/Kolkata", now=NOW
    )
    assert answer.resolved.kind is QuestionKind.UNKNOWN
    assert "could not turn that into a search" in answer.verdict


async def test_another_users_utterances_are_never_returned(
    parser, utterances
) -> None:
    await _seed(utterances)
    answer = await parser.answer(
        "what did we decide at the Office",
        user_id="someone-else",
        tz="Asia/Kolkata",
        now=NOW,
    )
    assert answer.total == 0


async def test_several_acts_are_all_honoured(parser, utterances) -> None:
    await _seed(utterances)
    answer = await parser.answer(
        "what did we decide or promise",
        user_id=USER,
        tz="Asia/Kolkata",
        now=NOW,
    )
    assert {SpeechAct.DECISION, SpeechAct.COMMITMENT} <= set(
        answer.resolved.acts
    )
    assert answer.total == 2


# --- the filter is legible ----------------------------------------------


async def test_describe_states_the_filter_in_words(parser, utterances) -> None:
    """So an answer can show its own reasoning, and a wrong one is
    debuggable rather than merely plausible."""
    await _seed(utterances)
    resolved = await _parse(
        parser, "what did I decide at Blue Tokai yesterday about forms"
    )
    described = resolved.describe()
    assert "decision" in described
    assert "by you" in described
    assert "yesterday" in described
    assert "Blue Tokai" in described
    assert "forms" in described


async def test_an_empty_question_resolves_to_nothing(parser) -> None:
    resolved = await _parse(parser, "   ")
    assert resolved.kind is QuestionKind.UNKNOWN
    assert resolved.is_empty
