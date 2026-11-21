"""Retrieval and grounded answering."""

from __future__ import annotations

import json
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
from app.models.ask import SourceKind
from app.models.domain import Session
from app.models.memory import Memory, MemoryKind
from app.models.tasks import Task, TaskStatus
from app.repositories.json_repo import JsonRepository
from app.services.ask import NO_MATCH_ANSWER, AskService
from app.services.llm.fake_driver import FakeLLMDriver
from app.services.retrieval import RetrievalService, query_terms


@pytest.fixture
def repos(tmp_path):
    return {
        "sessions": JsonRepository(tmp_path / "s.json", Session),
        "analyses": JsonRepository(tmp_path / "a.json", ConversationAnalysis),
        "memories": JsonRepository(tmp_path / "m.json", Memory),
        "tasks": JsonRepository(tmp_path / "t.json", Task),
    }


@pytest.fixture
def retrieval(repos) -> RetrievalService:
    return RetrievalService(**repos)


def answer_payload(answer: str, sources: list[int], confident: bool = True) -> str:
    return json.dumps(
        {"answer": answer, "sources": sources, "confident": confident}
    )


async def seed(repos, *, day: int = 18, user_id: str = "manan") -> Session:
    """One conversation about the wearable prototype, with its derived data."""
    session = Session(
        user_id=user_id,
        tz="Asia/Kolkata",
        started_at=datetime(2026, 11, day, 11, 30, tzinfo=UTC),
        transcript=(
            "I'll test the microphone on the wearable prototype by Friday. "
            "The enclosure still needs ordering."
        ),
        title="Prototype sync",
    )
    await repos["sessions"].put(session)

    await repos["analyses"].put(
        ConversationAnalysis(
            id=session.id,
            session_id=session.id,
            title="Prototype sync",
            summary="Manan will test the microphone; the enclosure needs ordering.",
            participants=[Participant(name="Manan")],
            projects=["Wearable prototype"],
            items=[
                AnalysisItem(
                    title="Test the microphone",
                    act=SpeechAct.COMMITMENT,
                    owner="Manan",
                    source_quote=(
                        "I'll test the microphone on the wearable prototype "
                        "by Friday."
                    ),
                    project="Wearable prototype",
                    due=DueDate(
                        raw="by Friday",
                        date=date(2026, 11, 20),
                        resolution=DateResolution.CONFIDENT,
                    ),
                )
            ],
        )
    )

    await repos["memories"].put(
        Memory(
            user_id=user_id,
            kind=MemoryKind.PROJECT,
            key="project:wearable prototype",
            name="Wearable prototype",
            content="Wearable prototype is an active project.",
            source_session_ids=[session.id],
            mention_count=2,
            last_seen=session.started_at,
        )
    )

    await repos["tasks"].put(
        Task(
            user_id=user_id,
            title="Test the microphone",
            owner="Manan",
            act=SpeechAct.COMMITMENT,
            project="Wearable prototype",
            tz="Asia/Kolkata",
            due_date=date(2026, 11, 20),
            due_resolution=DateResolution.CONFIDENT,
            source_session_ids=[session.id],
            source_quotes=["I'll test the microphone..."],
        )
    )
    return session


# --- retrieval ------------------------------------------------------------


def test_query_terms_drops_stopwords() -> None:
    assert query_terms("What did we decide about the pitch?") == [
        "decide",
        "pitch",
    ]
    assert query_terms("what is the") == []


async def test_retrieval_finds_across_all_three_source_kinds(
    repos, retrieval: RetrievalService
) -> None:
    await seed(repos)
    result = await retrieval.retrieve("wearable prototype microphone")

    assert not result.is_empty
    kinds = {item.kind for item in result.items}
    assert kinds == {
        SourceKind.CONVERSATION,
        SourceKind.MEMORY,
        SourceKind.TASK,
    }


async def test_retrieval_labels_are_sequential(
    repos, retrieval: RetrievalService
) -> None:
    """Labels are what the model cites, so they must be 1..n with no gaps."""
    await seed(repos)
    result = await retrieval.retrieve("microphone")
    assert [item.label for item in result.items] == list(
        range(1, len(result.items) + 1)
    )


async def test_retrieval_is_empty_for_unknown_topics(
    repos, retrieval: RetrievalService
) -> None:
    await seed(repos)
    assert (await retrieval.retrieve("submarine maintenance")).is_empty


async def test_stopword_only_question_retrieves_nothing(
    repos, retrieval: RetrievalService
) -> None:
    await seed(repos)
    assert (await retrieval.retrieve("what is the")).is_empty


async def test_retrieval_is_scoped_per_user(
    repos, retrieval: RetrievalService
) -> None:
    await seed(repos, user_id="manan")
    assert not (
        await retrieval.retrieve("microphone", user_id="manan")
    ).is_empty
    assert (await retrieval.retrieve("microphone", user_id="pulkit")).is_empty


async def test_task_intent_surfaces_open_tasks(
    repos, retrieval: RetrievalService
) -> None:
    """"What is still pending?" names no task, but must return them."""
    await seed(repos)
    result = await retrieval.retrieve("what is still pending")

    assert any(item.kind is SourceKind.TASK for item in result.items)


async def test_completed_tasks_are_not_surfaced_by_intent(
    repos, retrieval: RetrievalService
) -> None:
    await seed(repos)
    task = (await repos["tasks"].list())[0]
    task.status = TaskStatus.DONE
    await repos["tasks"].put(task)

    result = await retrieval.retrieve("what is still pending")
    assert not any(item.kind is SourceKind.TASK for item in result.items)


async def test_conflicting_memories_rank_higher(
    repos, retrieval: RetrievalService
) -> None:
    """A contradiction is what someone asking about status needs to see."""
    await seed(repos)
    memory = (await repos["memories"].list())[0]
    plain_score = next(
        item.score
        for item in (await retrieval.retrieve("wearable prototype")).items
        if item.kind is SourceKind.MEMORY
    )

    memory.conflicts_with = ["other-id"]
    await repos["memories"].put(memory)
    conflicted = next(
        item
        for item in (await retrieval.retrieve("wearable prototype")).items
        if item.kind is SourceKind.MEMORY
    )

    assert conflicted.score > plain_score
    assert "CONFLICTS" in conflicted.content


# --- answering ------------------------------------------------------------


async def test_no_retrieval_means_no_llm_call(
    repos, retrieval: RetrievalService
) -> None:
    """The most important rule: never ask a model a question with no material."""
    await seed(repos)
    driver = FakeLLMDriver(responses=[answer_payload("invented", [1])])
    result = await AskService(retrieval=retrieval, driver=driver).ask(
        "what did we say about submarines"
    )

    assert driver.calls == [], "the model must not be consulted"
    assert result.answer == NO_MATCH_ANSWER
    assert result.grounded is False
    assert result.confident is False
    assert result.sources == []
    assert result.retrieved_count == 0


async def test_grounded_answer_resolves_citations(
    repos, retrieval: RetrievalService
) -> None:
    await seed(repos)
    driver = FakeLLMDriver(
        responses=[answer_payload("Manan is testing the microphone.", [1])]
    )
    result = await AskService(retrieval=retrieval, driver=driver).ask(
        "who is testing the microphone"
    )

    assert result.grounded is True
    assert result.confident is True
    assert result.answer == "Manan is testing the microphone."
    assert len(result.sources) == 1
    assert result.sources[0].label == 1
    assert result.sources[0].session_id
    assert result.dropped_citations == []


async def test_invented_citations_are_dropped(
    repos, retrieval: RetrievalService
) -> None:
    """A citation that does not resolve must never reach the app."""
    await seed(repos)
    driver = FakeLLMDriver(
        responses=[answer_payload("Something.", [1, 99, 1000])]
    )
    result = await AskService(retrieval=retrieval, driver=driver).ask(
        "microphone"
    )

    assert [s.label for s in result.sources] == [1]
    assert result.dropped_citations == [99, 1000]


async def test_an_answer_citing_nothing_is_not_grounded(
    repos, retrieval: RetrievalService
) -> None:
    await seed(repos)
    driver = FakeLLMDriver(responses=[answer_payload("I think so.", [])])
    result = await AskService(retrieval=retrieval, driver=driver).ask(
        "microphone"
    )

    assert result.grounded is False
    assert result.confident is False


async def test_model_uncertainty_is_preserved(
    repos, retrieval: RetrievalService
) -> None:
    await seed(repos)
    driver = FakeLLMDriver(
        responses=[answer_payload("Possibly Manan.", [1], confident=False)]
    )
    result = await AskService(retrieval=retrieval, driver=driver).ask(
        "microphone"
    )

    assert result.grounded is True
    assert result.confident is False


async def test_duplicate_citations_are_collapsed(
    repos, retrieval: RetrievalService
) -> None:
    await seed(repos)
    driver = FakeLLMDriver(responses=[answer_payload("Yes.", [1, 1, 1])])
    result = await AskService(retrieval=retrieval, driver=driver).ask(
        "microphone"
    )
    assert len(result.sources) == 1


async def test_llm_failure_still_returns_the_sources(
    repos, retrieval: RetrievalService
) -> None:
    """An outage must not look like 'you never said that'."""
    await seed(repos)
    driver = FakeLLMDriver(fail=True)
    result = await AskService(retrieval=retrieval, driver=driver).ask(
        "microphone"
    )

    assert result.degraded is True
    assert result.grounded is False
    assert result.sources, "matched sources are still useful to the user"
    assert "could not generate an answer" in result.answer


async def test_malformed_answer_degrades_gracefully(
    repos, retrieval: RetrievalService
) -> None:
    await seed(repos)
    driver = FakeLLMDriver(responses=["not json at all"])
    result = await AskService(retrieval=retrieval, driver=driver).ask(
        "microphone"
    )

    assert result.degraded is True
    assert result.degraded_reason
    assert result.sources


async def test_prompt_contains_the_sources_and_today(
    repos, retrieval: RetrievalService
) -> None:
    await seed(repos)
    driver = FakeLLMDriver(responses=[answer_payload("ok", [1])])
    await AskService(retrieval=retrieval, driver=driver).ask(
        "microphone", tz="Asia/Kolkata"
    )

    _system, user = driver.calls[0]
    assert "Asia/Kolkata" in user
    assert "[1]" in user
    assert "microphone" in user.lower()
