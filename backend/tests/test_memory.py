"""Memory service: selectivity, dedup, conflicts, retrieval, deletion."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.models.analysis import (
    AnalysisItem,
    ConversationAnalysis,
    DateResolution,
    DueDate,
    Participant,
    SpeechAct,
)
from app.models.domain import Session
from app.models.memory import Memory, MemoryKind
from app.repositories.json_repo import JsonRepository
from app.services.memory import MemoryService


@pytest.fixture
def repo(tmp_path) -> JsonRepository[Memory]:
    return JsonRepository(tmp_path / "memories.json", Memory)


@pytest.fixture
def service(repo: JsonRepository[Memory]) -> MemoryService:
    return MemoryService(memories=repo)


def make_session(day: int = 19, user_id: str = "manan") -> Session:
    return Session(
        user_id=user_id,
        tz="Asia/Kolkata",
        started_at=datetime(2026, 11, day, 11, 30, tzinfo=UTC),
        transcript="...",
    )


def item(
    title: str,
    *,
    act: SpeechAct = SpeechAct.COMMITMENT,
    owner: str = "Manan",
    project: str | None = "Investor pitch",
    due: str | None = None,
    confidence: float = 0.9,
    quote: str | None = None,
) -> AnalysisItem:
    due_date = DueDate(resolution=DateResolution.NONE)
    if due:
        due_date = DueDate(
            raw=due, date=date.fromisoformat(due), resolution=DateResolution.CONFIDENT
        )
    return AnalysisItem(
        title=title,
        act=act,
        owner=owner,
        source_quote=quote or f"I'll handle {title.lower()}.",
        project=project,
        confidence=confidence,
        due=due_date,
    )


def analysis(
    session: Session,
    *,
    items: list[AnalysisItem] | None = None,
    participants: list[str] | None = None,
    projects: list[str] | None = None,
    degraded: bool = False,
) -> ConversationAnalysis:
    return ConversationAnalysis(
        id=session.id,
        session_id=session.id,
        items=items or [],
        participants=[Participant(name=n) for n in (participants or [])],
        projects=projects or [],
        degraded=degraded,
    )


# --- selectivity ---------------------------------------------------------


async def test_people_and_projects_are_remembered(
    service: MemoryService,
) -> None:
    session = make_session()
    await service.write_from_analysis(
        session,
        analysis(
            session,
            participants=["Pulkit", "Manan"],
            projects=["Investor pitch"],
        ),
    )

    people = await service.list_memories(kind=MemoryKind.PERSON)
    projects = await service.list_memories(kind=MemoryKind.PROJECT)
    assert {m.name for m in people} == {"Pulkit", "Manan"}
    assert [m.name for m in projects] == ["Investor pitch"]


@pytest.mark.parametrize(
    ("act", "remembered"),
    [
        (SpeechAct.COMMITMENT, True),
        (SpeechAct.ASSIGNMENT, True),
        (SpeechAct.REQUIREMENT, True),
        (SpeechAct.DECISION, True),
        (SpeechAct.SUGGESTION, False),
        (SpeechAct.QUESTION, False),
        (SpeechAct.DISCUSSION, False),
    ],
)
async def test_only_durable_acts_become_facts(
    service: MemoryService, act: SpeechAct, remembered: bool
) -> None:
    """A floated idea must not become a remembered fact."""
    session = make_session()
    await service.write_from_analysis(
        session, analysis(session, items=[item("Send the deck", act=act)])
    )

    facts = await service.list_memories(kind=MemoryKind.FACT)
    assert bool(facts) is remembered


async def test_low_confidence_items_are_skipped(
    service: MemoryService,
) -> None:
    session = make_session()
    await service.write_from_analysis(
        session,
        analysis(session, items=[item("Vague thing", confidence=0.2)]),
    )
    assert await service.list_memories(kind=MemoryKind.FACT) == []


async def test_degraded_analysis_writes_nothing(
    service: MemoryService,
) -> None:
    session = make_session()
    result = await service.write_from_analysis(
        session,
        analysis(session, participants=["Manan"], degraded=True),
    )
    assert result.total_written == 0
    assert await service.list_memories() == []


# --- dedup and cross-conversation ----------------------------------------


async def test_same_project_across_two_conversations_is_one_memory(
    service: MemoryService,
) -> None:
    """The point of phase 4: Monday and Wednesday join into one memory."""
    monday = make_session(day=16)
    wednesday = make_session(day=18)

    await service.write_from_analysis(
        monday, analysis(monday, projects=["Wearable prototype"])
    )
    await service.write_from_analysis(
        wednesday, analysis(wednesday, projects=["Wearable prototype"])
    )

    projects = await service.list_memories(kind=MemoryKind.PROJECT)
    assert len(projects) == 1

    memory = projects[0]
    assert memory.mention_count == 2
    assert memory.is_cross_conversation
    assert set(memory.source_session_ids) == {monday.id, wednesday.id}
    assert memory.first_seen == monday.started_at
    assert memory.last_seen == wednesday.started_at


async def test_confidence_grows_with_corroboration(
    service: MemoryService,
) -> None:
    first = make_session(day=16)
    second = make_session(day=17)
    for session in (first, second):
        await service.write_from_analysis(
            session, analysis(session, projects=["V-NEST incubation"])
        )

    memory = (await service.list_memories(kind=MemoryKind.PROJECT))[0]
    assert memory.confidence > 0.7
    assert memory.confidence <= 0.99


async def test_name_variants_resolve_to_one_person(
    service: MemoryService,
) -> None:
    """'Manan' and 'Manan Dudeja' are the same person, and the fuller name wins."""
    first = make_session(day=16)
    second = make_session(day=17)
    await service.write_from_analysis(
        first, analysis(first, participants=["Manan"])
    )
    await service.write_from_analysis(
        second, analysis(second, participants=["Manan Dudeja"])
    )

    people = await service.list_memories(kind=MemoryKind.PERSON)
    assert len(people) == 1
    assert people[0].name == "Manan Dudeja"
    assert people[0].mention_count == 2


async def test_different_people_stay_separate(service: MemoryService) -> None:
    session = make_session()
    await service.write_from_analysis(
        session, analysis(session, participants=["Manan", "Pulkit", "Neha"])
    )
    assert len(await service.list_memories(kind=MemoryKind.PERSON)) == 3


async def test_repeating_the_same_session_does_not_inflate_counts(
    service: MemoryService,
) -> None:
    """Re-analysis of one conversation must not look like corroboration."""
    session = make_session()
    payload = analysis(session, projects=["Investor pitch"])
    await service.write_from_analysis(session, payload)
    await service.write_from_analysis(session, payload)

    memory = (await service.list_memories(kind=MemoryKind.PROJECT))[0]
    assert memory.mention_count == 1
    assert memory.source_session_ids == [session.id]


async def test_memories_are_scoped_per_user(service: MemoryService) -> None:
    mine = make_session(user_id="manan")
    theirs = make_session(user_id="pulkit")
    await service.write_from_analysis(mine, analysis(mine, projects=["Pitch"]))
    await service.write_from_analysis(
        theirs, analysis(theirs, projects=["Pitch"])
    )

    assert len(await service.list_memories(user_id="manan")) == 1
    assert len(await service.list_memories(user_id="pulkit")) == 1
    assert len(await service.list_memories()) == 2


# --- conflicts ------------------------------------------------------------


async def test_changed_deadline_creates_a_conflict_not_an_overwrite(
    service: MemoryService,
) -> None:
    """Monday said Friday; Wednesday said the following Tuesday. Keep both."""
    monday = make_session(day=16)
    wednesday = make_session(day=18)

    await service.write_from_analysis(
        monday,
        analysis(monday, items=[item("Ship the prototype", due="2026-11-20")]),
    )
    await service.write_from_analysis(
        wednesday,
        analysis(
            wednesday, items=[item("Ship the prototype", due="2026-11-24")]
        ),
    )

    facts = await service.list_memories(kind=MemoryKind.FACT)
    assert len(facts) == 2, "the older deadline must survive"

    dates = {m.attributes["due_date"] for m in facts}
    assert dates == {"2026-11-20", "2026-11-24"}
    # The link is mutual, so either row surfaces the disagreement.
    assert all(m.has_conflict for m in facts)
    assert facts[0].id in facts[1].conflicts_with
    assert facts[1].id in facts[0].conflicts_with


async def test_changed_owner_creates_a_conflict(
    service: MemoryService,
) -> None:
    first = make_session(day=16)
    second = make_session(day=17)
    await service.write_from_analysis(
        first, analysis(first, items=[item("Prepare projections", owner="Manan")])
    )
    await service.write_from_analysis(
        second,
        analysis(second, items=[item("Prepare projections", owner="Pulkit")]),
    )

    facts = await service.list_memories(kind=MemoryKind.FACT)
    assert len(facts) == 2
    assert {m.attributes["owner"] for m in facts} == {"Manan", "Pulkit"}


async def test_learning_an_owner_is_not_a_conflict(
    service: MemoryService,
) -> None:
    """Going from 'unknown' to a name is new information, not a contradiction."""
    first = make_session(day=16)
    second = make_session(day=17)
    await service.write_from_analysis(
        first,
        analysis(first, items=[item("Book the venue", owner="unknown")]),
    )
    await service.write_from_analysis(
        second, analysis(second, items=[item("Book the venue", owner="Divya")])
    )

    facts = await service.list_memories(kind=MemoryKind.FACT)
    assert len(facts) == 1
    assert not facts[0].has_conflict


async def test_repeating_the_same_fact_is_not_a_conflict(
    service: MemoryService,
) -> None:
    first = make_session(day=16)
    second = make_session(day=17)
    for session in (first, second):
        await service.write_from_analysis(
            session,
            analysis(session, items=[item("Ship it", due="2026-11-20")]),
        )

    facts = await service.list_memories(kind=MemoryKind.FACT)
    assert len(facts) == 1
    assert facts[0].mention_count == 2
    assert not facts[0].has_conflict


async def test_conflicts_only_filter(service: MemoryService) -> None:
    first = make_session(day=16)
    second = make_session(day=17)
    await service.write_from_analysis(
        first,
        analysis(
            first,
            items=[item("Ship it", due="2026-11-20")],
            projects=["Investor pitch"],
        ),
    )
    await service.write_from_analysis(
        second, analysis(second, items=[item("Ship it", due="2026-11-24")])
    )

    conflicted = await service.list_memories(conflicts_only=True)
    assert len(conflicted) == 2
    assert all(m.kind is MemoryKind.FACT for m in conflicted)


# --- provenance -----------------------------------------------------------


async def test_every_fact_keeps_its_evidence(service: MemoryService) -> None:
    session = make_session()
    await service.write_from_analysis(
        session,
        analysis(
            session,
            items=[
                item(
                    "Prepare the projections",
                    quote="I'll work on the financial projections.",
                )
            ],
        ),
    )

    fact = (await service.list_memories(kind=MemoryKind.FACT))[0]
    assert fact.source_quotes == ["I'll work on the financial projections."]
    assert fact.source_session_ids == [session.id]


# --- retrieval ------------------------------------------------------------


async def test_search_finds_across_conversations(
    service: MemoryService,
) -> None:
    monday = make_session(day=16)
    wednesday = make_session(day=18)
    await service.write_from_analysis(
        monday,
        analysis(
            monday,
            projects=["Wearable prototype"],
            items=[
                item(
                    "Test the microphone",
                    project="Wearable prototype",
                    quote="We should test the microphone this week.",
                )
            ],
        ),
    )
    await service.write_from_analysis(
        wednesday,
        analysis(
            wednesday,
            items=[
                item(
                    "Order the enclosure",
                    project="Wearable prototype",
                    quote="I'll order the enclosure.",
                )
            ],
        ),
    )

    hits = await service.search("wearable prototype")
    assert hits
    names = {hit.memory.name for hit in hits}
    assert "Wearable prototype" in names
    assert hits[0].score > 0


async def test_search_matches_source_quotes(service: MemoryService) -> None:
    session = make_session()
    await service.write_from_analysis(
        session,
        analysis(
            session,
            items=[
                item(
                    "Follow up",
                    quote="I'll chase the incubation paperwork tomorrow.",
                )
            ],
        ),
    )

    hits = await service.search("incubation paperwork")
    assert hits
    assert "incubation" in hits[0].matched_terms


async def test_search_ignores_stopwords(service: MemoryService) -> None:
    session = make_session()
    await service.write_from_analysis(
        session, analysis(session, projects=["Investor pitch"])
    )

    assert await service.search("what is the") == []
    assert await service.search("what about the investor pitch")


async def test_search_is_scoped_per_user(service: MemoryService) -> None:
    mine = make_session(user_id="manan")
    theirs = make_session(user_id="pulkit")
    await service.write_from_analysis(mine, analysis(mine, projects=["Alpha"]))
    await service.write_from_analysis(
        theirs, analysis(theirs, projects=["Alpha"])
    )

    assert len(await service.search("alpha", user_id="manan")) == 1


async def test_list_by_project(service: MemoryService) -> None:
    session = make_session()
    await service.write_from_analysis(
        session,
        analysis(
            session,
            projects=["Investor pitch"],
            items=[item("Draft slides", project="Investor pitch")],
        ),
    )

    scoped = await service.list_memories(project="Investor pitch")
    assert len(scoped) == 2  # the project itself plus the fact


# --- deletion -------------------------------------------------------------


async def test_delete_unlinks_conflicts(service: MemoryService) -> None:
    first = make_session(day=16)
    second = make_session(day=17)
    await service.write_from_analysis(
        first, analysis(first, items=[item("Ship it", due="2026-11-20")])
    )
    await service.write_from_analysis(
        second, analysis(second, items=[item("Ship it", due="2026-11-24")])
    )

    facts = await service.list_memories(kind=MemoryKind.FACT)
    await service.delete(facts[0].id)

    remaining = await service.list_memories(kind=MemoryKind.FACT)
    assert len(remaining) == 1
    assert remaining[0].conflicts_with == [], "dangling conflict reference"


async def test_deleting_a_session_removes_only_its_own_memories(
    service: MemoryService,
) -> None:
    """A memory two conversations support must survive deleting one of them."""
    monday = make_session(day=16)
    wednesday = make_session(day=18)
    await service.write_from_analysis(
        monday,
        analysis(monday, projects=["Shared project"], participants=["Manan"]),
    )
    await service.write_from_analysis(
        wednesday,
        analysis(wednesday, projects=["Shared project", "Solo project"]),
    )

    removed = await service.delete_for_session(wednesday.id)
    assert removed == 1  # only "Solo project"

    remaining = {m.name for m in await service.list_memories()}
    assert remaining == {"Shared project", "Manan"}

    shared = next(
        m for m in await service.list_memories() if m.name == "Shared project"
    )
    assert shared.source_session_ids == [monday.id]
    assert shared.mention_count == 1


async def test_delete_missing_memory_404s(service: MemoryService) -> None:
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await service.delete("nope")


# --- supersedes: conflicts across differently-worded statements -----------


async def test_supersedes_links_a_differently_worded_change(
    service: MemoryService,
) -> None:
    """The case live testing exposed.

    Conversation 1: "Investor pitch ready" due Friday.
    Conversation 2: "Investor pitch deadline moved" due the 21st.

    Different titles, so the dedupe key never matches. The extractor's
    `supersedes` hint is what links them into a conflict instead of leaving
    two unrelated facts, one of them stale.
    """
    monday = make_session(day=16)
    wednesday = make_session(day=18)

    original = item(
        "Investor pitch ready",
        act=SpeechAct.REQUIREMENT,
        owner="unknown",
        due="2026-11-20",
        quote="The investor pitch also needs to be ready by Friday.",
    )
    await service.write_from_analysis(
        monday, analysis(monday, items=[original])
    )

    change = item(
        "Investor pitch deadline moved",
        act=SpeechAct.DECISION,
        owner="unknown",
        due="2026-11-21",
        quote="The investor pitch is now due the day after tomorrow, not Friday.",
    )
    change.supersedes = "investor pitch ready by Friday"
    await service.write_from_analysis(
        wednesday, analysis(wednesday, items=[change])
    )

    facts = await service.list_memories(kind=MemoryKind.FACT)
    assert len(facts) == 2, "the superseded deadline must survive"
    assert all(f.has_conflict for f in facts), "both sides must be linked"
    assert {f.attributes["due_date"] for f in facts} == {
        "2026-11-20",
        "2026-11-21",
    }

    conflicted = await service.list_memories(conflicts_only=True)
    assert len(conflicted) == 2


async def test_supersedes_without_a_match_just_stores_the_fact(
    service: MemoryService,
) -> None:
    """No plausible target means no conflict - never a guessed one."""
    session = make_session()
    change = item("Something changed", act=SpeechAct.DECISION)
    change.supersedes = "a commitment nobody ever made about submarines"
    await service.write_from_analysis(
        session, analysis(session, items=[change])
    )

    facts = await service.list_memories(kind=MemoryKind.FACT)
    assert len(facts) == 1
    assert not facts[0].has_conflict


async def test_supersedes_does_not_cross_projects(
    service: MemoryService,
) -> None:
    """A change to project A must not supersede a fact about project B."""
    first = make_session(day=16)
    second = make_session(day=17)
    await service.write_from_analysis(
        first,
        analysis(
            first,
            items=[
                item(
                    "Ship the enclosure",
                    project="Wearable prototype",
                    due="2026-11-20",
                )
            ],
        ),
    )

    change = item(
        "Deadline moved", project="Investor pitch", due="2026-11-24",
        act=SpeechAct.DECISION,
    )
    change.supersedes = "ship the enclosure by Friday"
    await service.write_from_analysis(
        second, analysis(second, items=[change])
    )

    facts = await service.list_memories(kind=MemoryKind.FACT)
    assert len(facts) == 2
    assert not any(f.has_conflict for f in facts)


async def test_no_supersedes_hint_leaves_behaviour_unchanged(
    service: MemoryService,
) -> None:
    session = make_session()
    await service.write_from_analysis(
        session, analysis(session, items=[item("Plain task")])
    )
    facts = await service.list_memories(kind=MemoryKind.FACT)
    assert len(facts) == 1
    assert not facts[0].has_conflict
