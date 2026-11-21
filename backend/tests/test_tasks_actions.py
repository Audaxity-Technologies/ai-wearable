"""Task creation, action proposal, and the approval state machine."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.core.errors import ConflictError, InvalidTransitionError
from app.models.actions import (
    Action,
    ActionStatus,
    ActionType,
    assert_transition,
    can_transition,
)
from app.models.analysis import (
    OWNER_UNKNOWN,
    AnalysisItem,
    ConversationAnalysis,
    DateResolution,
    DueDate,
    SpeechAct,
)
from app.models.domain import Session
from app.models.tasks import Task, TaskStatus
from app.repositories.json_repo import JsonRepository
from app.services.actions import ActionEngine
from app.services.approval import ApprovalService
from app.services.executors import build_executors
from app.services.tasks import TaskService


@pytest.fixture
def tasks_repo(tmp_path) -> JsonRepository[Task]:
    return JsonRepository(tmp_path / "tasks.json", Task)


@pytest.fixture
def actions_repo(tmp_path) -> JsonRepository[Action]:
    return JsonRepository(tmp_path / "actions.json", Action)


@pytest.fixture
def task_service(tasks_repo) -> TaskService:
    return TaskService(tasks=tasks_repo)


@pytest.fixture
def engine(actions_repo) -> ActionEngine:
    return ActionEngine(actions=actions_repo)


@pytest.fixture
def approval(actions_repo, tasks_repo) -> ApprovalService:
    return ApprovalService(
        actions=actions_repo, executors=build_executors(tasks=tasks_repo)
    )


def make_session(day: int = 19) -> Session:
    return Session(
        user_id="manan",
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
    due_date: str | None = None,
    due_time: str | None = None,
    resolution: DateResolution = DateResolution.NONE,
    due_raw: str | None = None,
    confidence: float = 0.9,
) -> AnalysisItem:
    due = DueDate(raw=due_raw, resolution=resolution)
    if due_date:
        due.date = date.fromisoformat(due_date)
    if due_time:
        hour, minute = due_time.split(":")
        due.time = time(int(hour), int(minute))
    return AnalysisItem(
        title=title,
        act=act,
        owner=owner,
        source_quote=f"I'll handle {title.lower()}.",
        project=project,
        confidence=confidence,
        due=due,
    )


def analysis(session: Session, items: list[AnalysisItem], *, degraded=False):
    return ConversationAnalysis(
        id=session.id, session_id=session.id, items=items, degraded=degraded
    )


# --- tasks ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("act", "created"),
    [
        (SpeechAct.COMMITMENT, True),
        (SpeechAct.ASSIGNMENT, True),
        (SpeechAct.REQUIREMENT, True),
        (SpeechAct.DECISION, False),
        (SpeechAct.SUGGESTION, False),
        (SpeechAct.QUESTION, False),
        (SpeechAct.DISCUSSION, False),
    ],
)
async def test_only_actionable_acts_become_tasks(
    task_service: TaskService, act: SpeechAct, created: bool
) -> None:
    """Telling someone they owe work they never agreed to is the worst bug."""
    session = make_session()
    await task_service.create_from_analysis(
        session, analysis(session, [item("Send the deck", act=act)])
    )
    assert bool(await task_service.list_tasks()) is created


async def test_same_commitment_twice_is_one_task(
    task_service: TaskService,
) -> None:
    first, second = make_session(16), make_session(18)
    for session in (first, second):
        await task_service.create_from_analysis(
            session, analysis(session, [item("Prepare projections")])
        )

    tasks = await task_service.list_tasks()
    assert len(tasks) == 1
    assert set(tasks[0].source_session_ids) == {first.id, second.id}


async def test_a_later_conversation_pins_down_a_vague_deadline(
    task_service: TaskService,
) -> None:
    first, second = make_session(16), make_session(18)
    await task_service.create_from_analysis(
        first,
        analysis(
            first,
            [
                item(
                    "Prepare projections",
                    resolution=DateResolution.AMBIGUOUS,
                    due_raw="next week",
                )
            ],
        ),
    )
    await task_service.create_from_analysis(
        second,
        analysis(
            second,
            [
                item(
                    "Prepare projections",
                    due_date="2026-11-24",
                    resolution=DateResolution.CONFIDENT,
                )
            ],
        ),
    )

    task = (await task_service.list_tasks())[0]
    assert task.due_date == date(2026, 11, 24)
    assert task.due_resolution is DateResolution.CONFIDENT
    assert not task.needs_clarification


async def test_user_edits_are_not_overwritten_by_re_extraction(
    task_service: TaskService,
) -> None:
    session = make_session(16)
    await task_service.create_from_analysis(
        session, analysis(session, [item("Prepare projections", owner="unknown")])
    )
    task = (await task_service.list_tasks())[0]
    await task_service.update(task.id, owner="Pulkit")

    later = make_session(18)
    await task_service.create_from_analysis(
        later, analysis(later, [item("Prepare projections", owner="unknown")])
    )

    # The dedupe key includes the owner, so the edit creates a distinct task
    # rather than being silently reverted by a later extraction.
    edited = await task_service.get(task.id)
    assert edited.owner == "Pulkit"
    assert edited.edited is True


async def test_overdue_only_counts_confident_dates(
    task_service: TaskService,
) -> None:
    """An ambiguous 'next week' must never be reported as overdue."""
    session = make_session()
    yesterday = (datetime.now(UTC).date() - timedelta(days=2)).isoformat()
    await task_service.create_from_analysis(
        session,
        analysis(
            session,
            [
                item(
                    "Confident and late",
                    due_date=yesterday,
                    resolution=DateResolution.CONFIDENT,
                ),
                item(
                    "Vague and late",
                    due_date=yesterday,
                    resolution=DateResolution.AMBIGUOUS,
                    due_raw="next week",
                ),
            ],
        ),
    )

    overdue = await task_service.list_tasks(overdue_only=True)
    assert [t.title for t in overdue] == ["Confident and late"]


async def test_completing_a_task(task_service: TaskService) -> None:
    session = make_session()
    await task_service.create_from_analysis(
        session, analysis(session, [item("Prepare projections")])
    )
    task = (await task_service.list_tasks())[0]

    done = await task_service.update(task.id, status=TaskStatus.DONE)
    assert done.status is TaskStatus.DONE
    assert done.completed_at is not None
    assert not done.is_open
    assert await task_service.list_tasks(status=TaskStatus.PENDING) == []


# --- action proposal ------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {
                "due_date": "2026-11-20",
                "due_time": "17:00",
                "resolution": DateResolution.CONFIDENT,
            },
            ActionType.CALENDAR_EVENT,
        ),
        (
            {"due_date": "2026-11-20", "resolution": DateResolution.CONFIDENT},
            ActionType.REMINDER,
        ),
        (
            {"resolution": DateResolution.AMBIGUOUS, "due_raw": "next week"},
            ActionType.REMINDER,
        ),
        ({}, ActionType.TASK),
    ],
)
async def test_action_type_follows_what_was_established(
    engine: ActionEngine, kwargs: dict, expected: ActionType
) -> None:
    session = make_session()
    actions = await engine.propose_from_analysis(
        session, analysis(session, [item("Do the thing", **kwargs)]), []
    )
    assert actions[0].type is expected


async def test_proposed_actions_are_not_executed(
    engine: ActionEngine, approval: ApprovalService
) -> None:
    """The core guarantee: proposing must never execute."""
    session = make_session()
    await engine.propose_from_analysis(
        session, analysis(session, [item("Do the thing")]), []
    )

    actions = await approval.list_actions()
    assert all(a.status is ActionStatus.PROPOSED for a in actions)
    assert all(a.executed_at is None for a in actions)
    assert all(a.execution_result is None for a in actions)


async def test_every_action_carries_a_reason_and_its_quote(
    engine: ActionEngine,
) -> None:
    session = make_session()
    actions = await engine.propose_from_analysis(
        session,
        analysis(
            session,
            [
                item(
                    "Finish the pitch",
                    due_date="2026-11-20",
                    resolution=DateResolution.CONFIDENT,
                )
            ],
        ),
        [],
    )
    action = actions[0]
    assert action.reason
    assert "2026-11-20" in action.reason
    assert action.source_quote
    assert action.source_session_id == session.id


async def test_ambiguous_dates_are_flagged_not_guessed(
    engine: ActionEngine,
) -> None:
    session = make_session()
    actions = await engine.propose_from_analysis(
        session,
        analysis(
            session,
            [
                item(
                    "Contact V-NEST",
                    resolution=DateResolution.AMBIGUOUS,
                    due_raw="next week",
                )
            ],
        ),
        [],
    )
    action = actions[0]
    assert action.needs_clarification
    assert "date" not in action.payload
    assert "needs confirming" in action.reason


async def test_the_same_action_is_not_proposed_twice(
    engine: ActionEngine,
) -> None:
    first, second = make_session(16), make_session(18)
    for session in (first, second):
        await engine.propose_from_analysis(
            session, analysis(session, [item("Prepare projections")]), []
        )

    actions = await engine._actions.list()  # noqa: SLF001
    assert len(actions) == 1


async def test_a_rejected_action_is_not_proposed_again(
    engine: ActionEngine, approval: ApprovalService
) -> None:
    """Re-asking every time the topic comes up is nagging."""
    first, second = make_session(16), make_session(18)
    await engine.propose_from_analysis(
        first, analysis(first, [item("Prepare projections")]), []
    )
    action = (await approval.list_actions())[0]
    await approval.reject(action.id, reason="not needed")

    await engine.propose_from_analysis(
        second, analysis(second, [item("Prepare projections")]), []
    )
    assert len(await approval.list_actions()) == 1


async def test_degraded_analysis_proposes_nothing(
    engine: ActionEngine, task_service: TaskService
) -> None:
    session = make_session()
    payload = analysis(session, [item("Do the thing")], degraded=True)
    assert await task_service.create_from_analysis(session, payload) == []
    assert await engine.propose_from_analysis(session, payload, []) == []


# --- approval state machine ----------------------------------------------


def test_execution_is_only_reachable_from_approved() -> None:
    assert can_transition(ActionStatus.APPROVED, ActionStatus.EXECUTING)
    assert not can_transition(ActionStatus.PROPOSED, ActionStatus.EXECUTING)
    assert not can_transition(ActionStatus.PROPOSED, ActionStatus.EXECUTED)
    assert not can_transition(ActionStatus.REJECTED, ActionStatus.EXECUTING)
    with pytest.raises(InvalidTransitionError):
        assert_transition(ActionStatus.PROPOSED, ActionStatus.EXECUTED)


def test_terminal_statuses() -> None:
    assert not can_transition(ActionStatus.EXECUTED, ActionStatus.APPROVED)
    assert not can_transition(ActionStatus.REJECTED, ActionStatus.APPROVED)
    # A failed execution may be retried.
    assert can_transition(ActionStatus.FAILED, ActionStatus.APPROVED)


async def _propose(engine: ActionEngine, **kwargs) -> Action:
    session = make_session()
    actions = await engine.propose_from_analysis(
        session, analysis(session, [item("Do the thing", **kwargs)]), []
    )
    return actions[0]


async def test_approve_executes(
    engine: ActionEngine, approval: ApprovalService
) -> None:
    action = await _propose(
        engine, due_date="2026-11-20", resolution=DateResolution.CONFIDENT
    )
    approved = await approval.approve(action.id)

    assert approved.status is ActionStatus.EXECUTED
    assert approved.executed_at is not None
    assert approved.execution_result
    assert approved.idempotency_key
    events = [e.event for e in approved.history]
    assert events == ["approved", "executing", "executed"]


async def test_double_approve_executes_once(
    engine: ActionEngine, approval: ApprovalService
) -> None:
    """A double-tap must not create two calendar events."""
    action = await _propose(
        engine, due_date="2026-11-20", resolution=DateResolution.CONFIDENT
    )

    first = await approval.approve(action.id)
    second = await approval.approve(action.id)

    assert second.status is ActionStatus.EXECUTED
    assert second.executed_at == first.executed_at
    assert second.idempotency_key == first.idempotency_key
    assert [e.event for e in second.history].count("executed") == 1


async def test_concurrent_approvals_execute_once(
    engine: ActionEngine, approval: ApprovalService
) -> None:
    """Two requests racing must not both reach the executor."""
    action = await _propose(
        engine, due_date="2026-11-20", resolution=DateResolution.CONFIDENT
    )

    results = await asyncio.gather(
        approval.approve(action.id),
        approval.approve(action.id),
        approval.approve(action.id),
    )

    final = await approval.get(action.id)
    assert final.status is ActionStatus.EXECUTED
    assert [e.event for e in final.history].count("executed") == 1
    assert all(r.status is ActionStatus.EXECUTED for r in results)


async def test_rejected_action_cannot_be_approved(
    engine: ActionEngine, approval: ApprovalService
) -> None:
    action = await _propose(engine)
    await approval.reject(action.id, reason="no thanks")

    with pytest.raises(ConflictError):
        await approval.approve(action.id)

    stored = await approval.get(action.id)
    assert stored.status is ActionStatus.REJECTED
    assert stored.execution_result is None


async def test_reject_is_idempotent(
    engine: ActionEngine, approval: ApprovalService
) -> None:
    action = await _propose(engine)
    await approval.reject(action.id)
    again = await approval.reject(action.id)
    assert again.status is ActionStatus.REJECTED


async def test_edit_before_approving(
    engine: ActionEngine, approval: ApprovalService
) -> None:
    action = await _propose(
        engine, resolution=DateResolution.AMBIGUOUS, due_raw="next week"
    )
    assert action.needs_clarification

    edited = await approval.edit(
        action.id, title="Contact V-NEST team", date=date(2026, 11, 25)
    )

    assert edited.title == "Contact V-NEST team"
    assert edited.payload["date"] == "2026-11-25"
    assert edited.edited is True
    # The user supplied the date, so it no longer needs confirming.
    assert edited.needs_clarification is False


async def test_cannot_edit_after_approval(
    engine: ActionEngine, approval: ApprovalService
) -> None:
    action = await _propose(
        engine, due_date="2026-11-20", resolution=DateResolution.CONFIDENT
    )
    await approval.approve(action.id)

    with pytest.raises(ConflictError):
        await approval.edit(action.id, title="too late")


async def test_calendar_event_without_a_time_fails_loudly(
    engine: ActionEngine, approval: ApprovalService, actions_repo
) -> None:
    """Refuse to invent a slot rather than booking a guess."""
    action = await _propose(
        engine, due_date="2026-11-20", resolution=DateResolution.CONFIDENT
    )
    action.type = ActionType.CALENDAR_EVENT
    action.payload.pop("time", None)
    await actions_repo.put(action)

    result = await approval.approve(action.id)
    assert result.status is ActionStatus.FAILED
    assert "date and a time" in result.execution_result


async def test_unimplemented_action_type_fails_clearly(
    engine: ActionEngine, approval: ApprovalService, actions_repo
) -> None:
    action = await _propose(engine)
    action.type = ActionType.EMAIL_DRAFT
    await actions_repo.put(action)

    result = await approval.approve(action.id)
    assert result.status is ActionStatus.FAILED
    assert "No executor" in result.execution_result


async def test_failed_action_can_be_approved_again(
    engine: ActionEngine, approval: ApprovalService, actions_repo
) -> None:
    action = await _propose(engine)
    action.type = ActionType.EMAIL_DRAFT
    await actions_repo.put(action)
    assert (await approval.approve(action.id)).status is ActionStatus.FAILED

    # Fix the problem, then retry.
    stored = await approval.get(action.id)
    stored.type = ActionType.TASK
    await actions_repo.put(stored)

    assert (await approval.approve(action.id)).status is ActionStatus.EXECUTED


async def test_task_action_creates_a_task_on_execution(
    engine: ActionEngine, approval: ApprovalService, task_service: TaskService
) -> None:
    action = await _propose(engine)
    assert action.type is ActionType.TASK
    assert await task_service.list_tasks() == []

    executed = await approval.approve(action.id)

    assert executed.status is ActionStatus.EXECUTED
    tasks = await task_service.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].title == "Do the thing"


async def test_adding_a_date_upgrades_a_task_action_to_a_reminder(
    engine: ActionEngine, approval: ApprovalService, task_service: TaskService
) -> None:
    """Editing in a date must not be silently discarded at execution.

    A plain task action has no date, so the task executor would ignore one.
    Adding a date means "remind me then", and adding a time means "put it in
    the calendar".
    """
    action = await _propose(engine)
    assert action.type is ActionType.TASK

    edited = await approval.edit(action.id, date=date(2026, 12, 1))
    assert edited.type is ActionType.REMINDER

    executed = await approval.approve(action.id)
    assert executed.status is ActionStatus.EXECUTED
    assert "2026-12-01" in executed.execution_result


async def test_adding_a_time_upgrades_to_a_calendar_event(
    engine: ActionEngine, approval: ApprovalService
) -> None:
    action = await _propose(
        engine, due_date="2026-11-20", resolution=DateResolution.CONFIDENT
    )
    assert action.type is ActionType.REMINDER

    edited = await approval.edit(action.id, time=time(9, 30))
    assert edited.type is ActionType.CALENDAR_EVENT

    executed = await approval.approve(action.id)
    assert executed.status is ActionStatus.EXECUTED
    assert "09:30" in executed.execution_result


async def test_an_explicit_type_on_edit_wins(
    engine: ActionEngine, approval: ApprovalService
) -> None:
    action = await _propose(engine)
    edited = await approval.edit(
        action.id, date=date(2026, 12, 1), action_type=ActionType.TASK
    )
    assert edited.type is ActionType.TASK


async def test_a_task_action_with_a_date_creates_a_dated_task(
    engine: ActionEngine, approval: ApprovalService, task_service: TaskService
) -> None:
    action = await _propose(engine)
    await approval.edit(action.id, action_type=ActionType.TASK,
                        date=date(2026, 12, 1))
    await approval.approve(action.id)

    task = (await task_service.list_tasks())[0]
    assert task.due_date == date(2026, 12, 1)
    assert task.due_resolution is DateResolution.CONFIDENT


# --- owner resolution through Person (memory phase B) -------------------


def _people_service(tmp_path) -> "PersonService":
    from app.models.people import Person
    from app.services.people import PersonService

    return PersonService(
        people=JsonRepository(tmp_path / "people.json", Person)
    )


async def test_task_creation_resolves_the_owner_to_a_person(tmp_path) -> None:
    """Two spellings of one name must produce one person on the tasks.

    This is the bug phase B exists for: before it, "Pulkit" and Whisper's
    "Pull kit" were two owners in a product whose whole claim is who
    committed to what.
    """
    people = _people_service(tmp_path)
    repo = JsonRepository(tmp_path / "tasks.json", Task)
    service = TaskService(tasks=repo, people=people)

    first = make_session(19)
    await service.create_from_analysis(
        first,
        analysis(
            first,
            [item(title="Send the compliance forms", owner="Pulkit")],
        ),
    )
    second = make_session(20)
    await service.create_from_analysis(
        second,
        analysis(
            second,
            [item(title="Send the vendor contract", owner="Pull kit")],
        ),
    )

    stored = await repo.list()
    assert len(stored) == 2
    person_ids = {task.owner_person_id for task in stored}
    assert None not in person_ids
    assert len(person_ids) == 1, "two spellings produced two people"
    assert len(await people.list_people(first.user_id)) == 1


async def test_a_sentinel_owner_gets_no_person(tmp_path) -> None:
    """"unknown" must not become a Person called "unknown"."""
    people = _people_service(tmp_path)
    repo = JsonRepository(tmp_path / "tasks.json", Task)
    service = TaskService(tasks=repo, people=people)

    session = make_session()
    await service.create_from_analysis(
        session,
        analysis(
            session,
            [
                item(
                    title="Send the invoice",
                    owner=OWNER_UNKNOWN,
                    act=SpeechAct.REQUIREMENT,
                )
            ],
        ),
    )
    stored = await repo.list()
    assert stored[0].owner_person_id is None
    assert await people.list_people(session.user_id) == []


async def test_tasks_still_work_with_no_person_service(tmp_path) -> None:
    """`owner_person_id` stays None, which is the same state as an owner we
    could not attribute - not an error."""
    repo = JsonRepository(tmp_path / "tasks.json", Task)
    service = TaskService(tasks=repo)

    session = make_session()
    created = await service.create_from_analysis(
        session, analysis(session, [item(title="Call the vendor", owner="Pulkit")])
    )
    assert len(created) == 1
    assert created[0].owner_person_id is None
