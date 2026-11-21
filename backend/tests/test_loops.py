"""Tests for the open-loops ledger.

The ledger's risk is not that it misses something - it is that it invents an
obligation. Most of these tests are about attribution refusing to guess.
"""

from __future__ import annotations

import datetime as dt

from app.models.analysis import (
    OWNER_GROUP,
    OWNER_UNKNOWN,
    DateResolution,
    SpeechAct,
)
from app.models.tasks import Task, TaskStatus
from app.services.loops import (
    QUIET_AFTER_DAYS,
    LoopSide,
    build_ledger,
    classify,
)

NOW = dt.datetime(2026, 11, 21, 18, 0, tzinfo=dt.UTC)
TODAY = NOW.date()


def _task(
    *,
    title: str = "Send the compliance forms",
    owner: str = OWNER_UNKNOWN,
    act: SpeechAct = SpeechAct.COMMITMENT,
    status: TaskStatus = TaskStatus.PENDING,
    days_ago: int = 1,
    last_seen_days_ago: int | None = None,
    sessions: int = 1,
    due_date: dt.date | None = None,
    due_resolution: DateResolution = DateResolution.NONE,
    quotes: list[str] | None = None,
) -> Task:
    created = NOW - dt.timedelta(days=days_ago)
    updated = NOW - dt.timedelta(
        days=last_seen_days_ago if last_seen_days_ago is not None else days_ago
    )
    return Task(
        user_id="u1",
        title=title,
        owner=owner,
        act=act,
        status=status,
        tz="Asia/Kolkata",
        created_at=created,
        updated_at=updated,
        due_date=due_date,
        due_resolution=due_resolution,
        source_session_ids=[f"s{i}" for i in range(sessions)],
        source_quotes=quotes if quotes is not None else ["I'll send them over"],
    )


def _build(tasks, self_names=("Manan",)):
    return build_ledger(tasks, self_names=self_names, today=TODAY, now=NOW)


# --- attribution --------------------------------------------------------


def test_unknown_owner_is_never_assigned_to_the_user() -> None:
    """The rule the whole screen depends on.

    Filing an unowned commitment under "you owe" would invent an obligation -
    the same error prompt rule 2 exists to prevent.
    """
    ledger = _build([_task(owner=OWNER_UNKNOWN)])
    assert ledger.owed_by_me == []
    assert ledger.owed_to_me == []
    assert len(ledger.unattributed) == 1
    assert ledger.unattributed[0].side is LoopSide.UNATTRIBUTED
    assert ledger.unattributed[0].counterparty is None


def test_the_users_own_name_lands_in_owed_by_me() -> None:
    ledger = _build([_task(owner="Manan")])
    assert len(ledger.owed_by_me) == 1
    assert ledger.owed_by_me[0].counterparty is None


def test_self_attribution_ignores_case_and_spacing() -> None:
    ledger = _build([_task(owner="  manan ")])
    assert len(ledger.owed_by_me) == 1


def test_first_person_owners_count_as_the_user() -> None:
    """An unnamed speaker can still yield a first-person owner."""
    ledger = _build([_task(owner="me")], self_names=())
    assert len(ledger.owed_by_me) == 1


def test_a_named_other_person_lands_in_owed_to_me_with_their_name() -> None:
    ledger = _build([_task(owner="Pulkit")])
    assert len(ledger.owed_to_me) == 1
    assert ledger.owed_to_me[0].counterparty == "Pulkit"


def test_group_owners_are_shared_not_mine() -> None:
    """"We need to send it" is not a promise the user made alone."""
    ledger = _build([_task(owner=OWNER_GROUP)])
    assert ledger.owed_by_me == []
    assert len(ledger.shared) == 1


def test_sentinels_win_over_a_name_collision() -> None:
    """A user called "Group" must not be mistaken for the group sentinel."""
    assert classify(OWNER_GROUP, ["Group"]) is LoopSide.SHARED
    assert classify("Group", ["Group"]) is LoopSide.MINE


def test_blank_owner_is_unattributed() -> None:
    assert classify("", ["Manan"]) is LoopSide.UNATTRIBUTED
    assert classify("   ", ["Manan"]) is LoopSide.UNATTRIBUTED


def test_no_known_self_names_means_nothing_is_assigned_to_the_user() -> None:
    """Dev mode with no account record: honest, not broken."""
    ledger = _build([_task(owner="Pulkit"), _task(owner=OWNER_UNKNOWN)], self_names=())
    assert ledger.owed_by_me == []
    assert len(ledger.owed_to_me) == 1
    assert len(ledger.unattributed) == 1


# --- only open loops ----------------------------------------------------


def test_done_and_cancelled_tasks_are_not_loops() -> None:
    ledger = _build(
        [
            _task(owner="Pulkit", status=TaskStatus.DONE),
            _task(owner="Pulkit", status=TaskStatus.CANCELLED),
            _task(owner="Pulkit", status=TaskStatus.PENDING),
        ]
    )
    assert ledger.total == 1


# --- gone quiet ---------------------------------------------------------


def test_an_old_single_mention_has_gone_quiet() -> None:
    """The actual product sentence: said once, weeks ago, nothing since."""
    ledger = _build(
        [_task(owner="Pulkit", days_ago=QUIET_AFTER_DAYS + 7, sessions=1)]
    )
    loop = ledger.owed_to_me[0]
    assert loop.gone_quiet
    assert loop.raised_again is False
    assert loop.days_open == QUIET_AFTER_DAYS + 7
    assert ledger.quiet_count == 1


def test_an_old_loop_raised_again_has_not_gone_quiet() -> None:
    """Re-mentioned in a later conversation: being discussed, not forgotten."""
    ledger = _build(
        [_task(owner="Pulkit", days_ago=QUIET_AFTER_DAYS + 7, sessions=3)]
    )
    loop = ledger.owed_to_me[0]
    assert loop.raised_again
    assert loop.gone_quiet is False
    assert loop.mention_sessions == 3
    assert ledger.quiet_count == 0


def test_a_recent_loop_is_not_quiet_however_few_mentions() -> None:
    ledger = _build([_task(owner="Pulkit", days_ago=2, sessions=1)])
    assert ledger.owed_to_me[0].gone_quiet is False


def test_days_open_is_never_negative() -> None:
    """Defensive: a clock skew must not produce a negative age."""
    ledger = _build([_task(owner="Pulkit", days_ago=-3)])
    assert ledger.owed_to_me[0].days_open == 0


# --- overdue ------------------------------------------------------------


def test_overdue_uses_the_task_rule_not_a_bare_date_comparison() -> None:
    """An ambiguous date must never be reported as overdue."""
    confident = _task(
        owner="Pulkit",
        due_date=TODAY - dt.timedelta(days=2),
        due_resolution=DateResolution.CONFIDENT,
    )
    ambiguous = _task(
        owner="Pulkit",
        due_date=TODAY - dt.timedelta(days=2),
        due_resolution=DateResolution.AMBIGUOUS,
    )
    ledger = _build([confident, ambiguous])
    overdue = [loop for loop in ledger.owed_to_me if loop.is_overdue]
    assert len(overdue) == 1
    assert ledger.overdue_count == 1


# --- ordering -----------------------------------------------------------


def test_overdue_sorts_above_quiet_which_sorts_above_fresh() -> None:
    fresh = _task(title="fresh", owner="Pulkit", days_ago=1)
    quiet = _task(title="quiet", owner="Pulkit", days_ago=QUIET_AFTER_DAYS + 1)
    overdue = _task(
        title="overdue",
        owner="Pulkit",
        days_ago=3,
        due_date=TODAY - dt.timedelta(days=1),
        due_resolution=DateResolution.CONFIDENT,
    )
    ledger = _build([fresh, quiet, overdue])
    assert [loop.title for loop in ledger.owed_to_me] == [
        "overdue",
        "quiet",
        "fresh",
    ]


def test_an_undated_old_promise_outranks_a_dated_future_one() -> None:
    """Deliberate: the user can see tomorrow coming.

    What they forgot is the three-week-old promise with no date attached.
    """
    old_undated = _task(title="old", owner="Pulkit", days_ago=25)
    soon_dated = _task(
        title="soon",
        owner="Pulkit",
        days_ago=1,
        due_date=TODAY + dt.timedelta(days=1),
        due_resolution=DateResolution.CONFIDENT,
    )
    ledger = _build([old_undated, soon_dated])
    assert ledger.owed_to_me[0].title == "old"


# --- evidence -----------------------------------------------------------


def test_the_quote_travels_with_the_loop() -> None:
    """Provenance: the user must be able to see why we believe this."""
    ledger = _build(
        [_task(owner="Pulkit", quotes=["I'll send the forms by Monday"])]
    )
    assert ledger.owed_to_me[0].quote == "I'll send the forms by Monday"


def test_a_loop_with_no_quote_is_still_reported() -> None:
    ledger = _build([_task(owner="Pulkit", quotes=[])])
    assert ledger.owed_to_me[0].quote is None


def test_act_is_preserved_so_the_client_can_distinguish_promise_from_demand(
) -> None:
    """"I'll do it" and "you should do it" are different obligations."""
    ledger = _build(
        [
            _task(owner="Pulkit", act=SpeechAct.COMMITMENT),
            _task(owner="Pulkit", act=SpeechAct.ASSIGNMENT, title="other"),
        ]
    )
    assert {loop.act for loop in ledger.owed_to_me} == {
        SpeechAct.COMMITMENT,
        SpeechAct.ASSIGNMENT,
    }


def test_empty_input_produces_an_empty_ledger() -> None:
    ledger = _build([])
    assert ledger.total == 0
    assert ledger.quiet_count == 0
    assert ledger.overdue_count == 0


# --- identity beats string matching (memory phase B) --------------------


class _FakePerson:
    """The slice of `Person` the ledger consumes."""

    def __init__(self, person_id: str, name: str, *, is_self: bool = False):
        self.id = person_id
        self.canonical_name = name
        self.is_self = is_self


def test_a_resolved_self_person_beats_the_display_name() -> None:
    """One tap - "this is me" - attributes a task whose owner string would
    never have matched a display name."""
    task = _task(owner="PK")
    task.owner_person_id = "p1"
    ledger = build_ledger(
        [task],
        self_names=("Manan",),  # would not have matched "PK"
        people={"p1": _FakePerson("p1", "Manan Dudeja", is_self=True)},
        today=TODAY,
        now=NOW,
    )
    assert len(ledger.owed_by_me) == 1
    assert ledger.unattributed == []


def test_a_resolved_other_person_shows_their_canonical_name() -> None:
    """Whisper's spelling must not be what the user reads.

    The owner string here is the mis-heard "Pull kit"; the ledger shows the
    person's real name.
    """
    task = _task(owner="Pull kit")
    task.owner_person_id = "p2"
    ledger = build_ledger(
        [task],
        people={"p2": _FakePerson("p2", "Pulkit")},
        today=TODAY,
        now=NOW,
    )
    assert ledger.owed_to_me[0].counterparty == "Pulkit"


def test_two_spellings_of_one_person_are_one_counterparty() -> None:
    """The bug phase B exists for: before this, these were two people."""
    first = _task(owner="Pulkit", title="send forms")
    second = _task(owner="Pull kit", title="send contract")
    first.owner_person_id = second.owner_person_id = "p2"
    ledger = build_ledger(
        [first, second],
        people={"p2": _FakePerson("p2", "Pulkit")},
        today=TODAY,
        now=NOW,
    )
    assert {loop.counterparty for loop in ledger.owed_to_me} == {"Pulkit"}
    assert len(ledger.owed_to_me) == 2


def test_an_unresolvable_person_id_falls_back_to_the_string_rules() -> None:
    """A dangling id must not silently become "not me".

    It means we lost the identity, not that the owner changed.
    """
    task = _task(owner="Manan")
    task.owner_person_id = "gone"
    ledger = build_ledger(
        [task], self_names=("Manan",), people={}, today=TODAY, now=NOW
    )
    assert len(ledger.owed_by_me) == 1


def test_identity_does_not_rescue_a_sentinel_owner() -> None:
    """"unknown" has no person, and must stay unattributed."""
    ledger = build_ledger(
        [_task(owner=OWNER_UNKNOWN)],
        self_names=("Manan",),
        people={"p1": _FakePerson("p1", "Manan", is_self=True)},
        today=TODAY,
        now=NOW,
    )
    assert len(ledger.unattributed) == 1
    assert ledger.owed_by_me == []
