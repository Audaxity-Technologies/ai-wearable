"""Tests for bi-temporal memory (phase E).

The capability is "what did we think then" and "how many times has this
changed". The risk is a broken chain: if each new version invalidates the
wrong row, several versions read as current at once, as-of returns
overlapping answers, and the change count is silently wrong.

That is not hypothetical. The first implementation closed whichever row was
stored first, so four statements about one launch date produced three
"current" versions and a count of 1. It only shows up past two versions.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.models.analysis import (
    AnalysisItem,
    ConversationAnalysis,
    DateResolution,
    DueDate,
    SpeechAct,
)
from app.models.domain import Session
from app.models.memory import Memory
from app.models.status import SessionStatus
from app.repositories.json_repo import JsonRepository
from app.services.memory import MemoryService

IST = ZoneInfo("Asia/Kolkata")
KEY = "fact::ship the launch"
USER = "u1"


@pytest.fixture
def service(tmp_path) -> MemoryService:
    return MemoryService(
        memories=JsonRepository(tmp_path / "memories.json", Memory)
    )


def _session(month: int, day: int, index: int) -> Session:
    return Session(
        id=f"s{index}",
        user_id=USER,
        tz="Asia/Kolkata",
        started_at=dt.datetime(2026, month, day, 10, tzinfo=IST),
        status=SessionStatus.READY,
    )


def _analysis(session: Session, due_month: int) -> ConversationAnalysis:
    return ConversationAnalysis(
        id=session.id,
        session_id=session.id,
        items=[
            AnalysisItem(
                title="Ship the launch",
                act=SpeechAct.DECISION,
                owner="group",
                source_quote=f"we are launching in month {due_month}",
                due=DueDate(
                    raw=f"month {due_month}",
                    date=dt.date(2026, due_month, 1),
                    resolution=DateResolution.CONFIDENT,
                ),
            )
        ],
    )


async def _four_versions(service: MemoryService) -> None:
    """Heard in January, March, May and September; due April, June, August,
    October."""
    for index, (month, day, due) in enumerate(
        [(1, 10, 4), (3, 3, 6), (5, 20, 8), (9, 1, 10)]
    ):
        session = _session(month, day, index)
        await service.write_from_analysis(session, _analysis(session, due))


# --- the chain ----------------------------------------------------------


async def test_each_version_is_closed_by_the_next(service) -> None:
    """The regression. Closing whichever row was stored first leaves three
    versions reading as current."""
    await _four_versions(service)
    versions = await service.history(user_id=USER, key=KEY)

    assert len(versions) == 4
    current = [v for v in versions if v.is_current]
    assert len(current) == 1, "exactly one version may be current"
    assert current[0].attributes["due_date"] == "2026-10-01"

    # Each closed version ends when the next was heard.
    for earlier, later in zip(versions, versions[1:], strict=False):
        assert earlier.valid_to == later.heard_at
        assert earlier.superseded_by == later.id


async def test_change_count_counts_closed_intervals(service) -> None:
    """The sentence no to-do list can produce."""
    await _four_versions(service)
    assert await service.change_count(user_id=USER, key=KEY) == 3


async def test_a_fact_heard_once_has_not_changed(service) -> None:
    """Zero, not one: the first version is not a change."""
    session = _session(1, 10, 0)
    await service.write_from_analysis(session, _analysis(session, 4))
    assert await service.change_count(user_id=USER, key=KEY) == 0


async def test_history_is_ordered_by_when_it_was_heard(service) -> None:
    await _four_versions(service)
    heard = [v.heard_at for v in await service.history(user_id=USER, key=KEY)]
    assert heard == sorted(heard)


# --- as of --------------------------------------------------------------


@pytest.mark.parametrize(
    "month,expected",
    [(2, "2026-04-01"), (4, "2026-06-01"), (7, "2026-08-01"), (11, "2026-10-01")],
)
async def test_as_of_returns_the_version_believed_then(
    service, month: int, expected: str
) -> None:
    """The question `conflicts_with` could never answer."""
    await _four_versions(service)
    rows = await service.as_of(
        user_id=USER, when=dt.datetime(2026, month, 15, tzinfo=IST)
    )
    facts = [r for r in rows if r.key == KEY]
    assert len(facts) == 1, "exactly one version was believed at any moment"
    assert facts[0].attributes["due_date"] == expected


async def test_as_of_before_anything_was_heard_returns_nothing(
    service,
) -> None:
    await _four_versions(service)
    rows = await service.as_of(
        user_id=USER, when=dt.datetime(2025, 6, 1, tzinfo=IST)
    )
    assert [r for r in rows if r.key == KEY] == []


async def test_the_instant_of_a_change_reports_only_the_new_version(
    service,
) -> None:
    """Half-open intervals: otherwise the moment of a change reports both."""
    await _four_versions(service)
    versions = await service.history(user_id=USER, key=KEY)
    boundary = versions[0].valid_to
    assert boundary is not None

    assert not versions[0].was_valid_at(boundary)
    assert versions[1].was_valid_at(boundary)


# --- the two axes -------------------------------------------------------


async def test_observed_at_is_when_we_heard_it(service) -> None:
    session = _session(3, 3, 0)
    await service.write_from_analysis(session, _analysis(session, 6))
    version = (await service.history(user_id=USER, key=KEY))[0]
    assert version.observed_at == session.started_at


async def test_valid_from_mirrors_observed_at_rather_than_inventing_precision(
    service,
) -> None:
    """An admission, not a measurement: we learn when we were told, almost
    never when the fact became true. A due date in `attributes` is the
    fact's content and says nothing about when the fact itself held."""
    session = _session(3, 3, 0)
    await service.write_from_analysis(session, _analysis(session, 6))
    version = (await service.history(user_id=USER, key=KEY))[0]
    assert version.valid_from == version.observed_at
    assert version.attributes["due_date"] == "2026-06-01"


async def test_rows_written_before_phase_e_still_read(service) -> None:
    """`observed_at` is None on older rows, so reads go through
    `heard_at`."""
    legacy = Memory(
        user_id=USER,
        kind="fact",
        key="fact::legacy",
        name="Legacy",
        content="written before phase E",
    )
    await service._memories.put(legacy)  # noqa: SLF001
    stored = await service.get(legacy.id)
    assert stored.observed_at is None
    assert stored.heard_at == stored.created_at
    assert stored.is_current
    assert stored.was_valid_at(stored.created_at)


async def test_a_backdated_conversation_cannot_invert_an_interval(
    service,
) -> None:
    """Recordings can be processed out of order; a negative interval would
    make every as-of query mishandle the row."""
    later = _session(9, 1, 0)
    await service.write_from_analysis(later, _analysis(later, 10))
    earlier = _session(1, 10, 1)
    await service.write_from_analysis(earlier, _analysis(earlier, 4))

    for version in await service.history(user_id=USER, key=KEY):
        if version.valid_to is not None:
            assert version.valid_to >= version.became_true_at


# --- what keeps moving --------------------------------------------------


async def test_changed_facts_surfaces_the_things_that_keep_moving(
    service,
) -> None:
    """What a weekly review is built on, and nothing else surfaces it."""
    await _four_versions(service)
    changes = await service.changed_facts(user_id=USER)
    assert changes
    assert changes[0] == (KEY, 3)


async def test_a_stable_fact_is_not_listed_as_changing(service) -> None:
    session = _session(1, 10, 0)
    await service.write_from_analysis(session, _analysis(session, 4))
    assert await service.changed_facts(user_id=USER) == []


# --- conflicts still work -----------------------------------------------


async def test_invalidation_does_not_replace_conflict_links(service) -> None:
    """Both survive: `conflicts_with` is mutual and drives the conflicts
    screen, while `superseded_by` is directional and makes the chain
    walkable."""
    await _four_versions(service)
    versions = await service.history(user_id=USER, key=KEY)
    assert versions[0].conflicts_with
    assert versions[0].superseded_by == versions[1].id


async def test_nothing_is_deleted_by_a_change(service) -> None:
    await _four_versions(service)
    rows = await service.list_memories(user_id=USER)
    assert len([r for r in rows if r.key == KEY]) == 4
