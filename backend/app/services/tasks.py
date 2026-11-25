"""Task management.

Tasks come from actionable speech acts only. A task re-discussed in a later
conversation updates the existing row rather than creating a second one, which
is what stops a weekly standup from producing five copies of the same
commitment.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.core.text import normalise
from app.core.time_utils import utc_now
from app.models.analysis import (
    ACTIONABLE_ACTS,
    AnalysisItem,
    ConversationAnalysis,
    DateResolution,
)
from app.models.domain import Session
from app.models.tasks import Task, TaskStatus
from app.services.loops import OpenLoopLedger, build_ledger
from app.services.people import PersonService
from app.repositories.base import Repository

logger = get_logger(__name__)

#: Items below this confidence are not turned into tasks.
MIN_TASK_CONFIDENCE = 0.4


def task_dedupe_key(title: str, owner: str, project: str | None) -> str:
    return f"{normalise(project or '')}|{normalise(title)}|{normalise(owner)}"


class TaskService:
    def __init__(
        self,
        *,
        tasks: Repository[Task],
        people: PersonService | None = None,
    ) -> None:
        self._tasks = tasks
        # Optional so tests and older call sites keep working: with no
        # person service, `owner_person_id` simply stays None, which is
        # the same state as an unattributable owner.
        self._people = people

    # --- creation from analysis -------------------------------------------

    async def create_from_analysis(
        self, session: Session, analysis: ConversationAnalysis
    ) -> list[Task]:
        """Create or update tasks for this conversation's actionable items."""
        if analysis.degraded:
            return []

        existing = await self._tasks.list(user_id=session.user_id)
        by_key = {task.dedupe_key: task for task in existing if task.dedupe_key}
        touched: list[Task] = []

        for item in analysis.items:
            if item.act not in ACTIONABLE_ACTS:
                continue  # suggestions and questions are never tasks
            if item.confidence < MIN_TASK_CONFIDENCE:
                continue

            key = task_dedupe_key(item.title, item.owner, item.project)
            current = by_key.get(key)

            person_id = await self._resolve_owner(session, item)

            if current is None:
                task = self._build(item, session, key)
                task.owner_person_id = person_id
                await self._tasks.put(task)
                by_key[key] = task
                touched.append(task)
                continue

            merged = self._merge(current, item, session)
            if person_id and current.owner_person_id != person_id:
                # A later conversation named someone we could not identify
                # the first time. Filling it in is the point of the alias
                # learning in PersonService.
                current.owner_person_id = person_id
                merged = True
            if merged:
                await self._tasks.put(current)
                touched.append(current)

        if touched:
            log_event(
                logger,
                "tasks.written",
                session_id=session.id,
                count=len(touched),
            )
        return touched

    async def _resolve_owner(
        self, session: Session, item: AnalysisItem
    ) -> str | None:
        """The `Person` this item's owner denotes, if any.

        Never raises: identity resolution failing must not stop a commitment
        being recorded. A task with no person attached is still a task.
        """
        if self._people is None:
            return None
        try:
            person = await self._people.resolve_or_create(
                session.user_id, item.owner, session_id=session.id
            )
        except Exception:  # pragma: no cover - defensive
            return None
        return person.id if person else None

    @staticmethod
    def _build(item: AnalysisItem, session: Session, key: str) -> Task:
        return Task(
            user_id=session.user_id,
            title=item.title,
            owner=item.owner,
            act=item.act,
            project=item.project,
            tz=session.tz,
            due_date=item.due.date,
            due_time=item.due.time,
            due_raw=item.due.raw,
            due_resolution=item.due.resolution,
            source_session_ids=[session.id],
            source_quotes=[item.source_quote],
            dedupe_key=key,
        )

    @staticmethod
    def _merge(task: Task, item: AnalysisItem, session: Session) -> bool:
        """Fold a re-mention into an existing task. True if anything changed."""
        changed = False

        if session.id not in task.source_session_ids:
            task.source_session_ids.append(session.id)
            changed = True
        if item.source_quote not in task.source_quotes:
            task.source_quotes.append(item.source_quote)
            changed = True

        # A later conversation that pins down a previously vague deadline is
        # an improvement; one that contradicts a confident date is handled as
        # a memory conflict, not by silently moving the task.
        if (
            item.due.resolution is DateResolution.CONFIDENT
            and task.due_resolution is not DateResolution.CONFIDENT
        ):
            task.due_date = item.due.date
            task.due_time = item.due.time
            task.due_raw = item.due.raw
            task.due_resolution = item.due.resolution
            changed = True

        # Never overwrite a user's edit with a fresh extraction.
        if not task.edited and task.owner == "unknown" and item.owner != "unknown":
            task.owner = item.owner
            changed = True

        if changed:
            task.touch()
        return changed

    # --- reading ----------------------------------------------------------

    async def get(self, task_id: str) -> Task:
        task = await self._tasks.get(task_id)
        if task is None:
            raise NotFoundError(f"Task not found: {task_id}")
        return task

    async def list_tasks(
        self,
        *,
        user_id: str | None = None,
        status: TaskStatus | None = None,
        owner: str | None = None,
        project: str | None = None,
        overdue_only: bool = False,
        needs_clarification_only: bool = False,
    ) -> list[Task]:
        filters: dict[str, object] = {}
        if user_id:
            filters["user_id"] = user_id
        if status:
            filters["status"] = status
        tasks = await self._tasks.list(**filters)

        if owner:
            wanted = normalise(owner)
            tasks = [t for t in tasks if normalise(t.owner) == wanted]
        if project:
            wanted = normalise(project)
            tasks = [t for t in tasks if normalise(t.project or "") == wanted]
        if overdue_only:
            tasks = [t for t in tasks if t.is_overdue(self._today(t))]
        if needs_clarification_only:
            tasks = [t for t in tasks if t.needs_clarification]

        # Dated work first, soonest first; undated work after it.
        return sorted(
            tasks,
            key=lambda t: (t.due_date is None, t.due_date or dt.date.max, t.title),
        )

    async def open_loops(
        self,
        *,
        user_id: str,
        self_names: Sequence[str] = (),
        tz: str = "UTC",
    ) -> OpenLoopLedger:
        """What the user owes, and what the user is owed.

        `tz` decides what "today" and "days open" mean. Judging either in UTC
        would report a task as overdue during the user's own evening.
        """
        tasks = await self._tasks.list(user_id=user_id, status=TaskStatus.PENDING)
        try:
            now = utc_now().astimezone(ZoneInfo(tz))
        except Exception:
            now = utc_now()

        people: dict[str, object] = {}
        if self._people is not None:
            for person in await self._people.list_people(user_id):
                people[person.id] = person
                # Merged-away ids still resolve, so a task written before a
                # merge is not orphaned into `unattributed` by it.
                for absorbed in person.merged_from:
                    people[absorbed] = person
            if not self_names:
                self_names = await self._people.self_names(user_id)

        return build_ledger(
            tasks,
            self_names=self_names,
            people=people,  # type: ignore[arg-type]
            today=now.date(),
            now=now,
        )

    @staticmethod
    def _today(task: Task) -> dt.date:
        """Today in the task's own timezone."""
        try:
            return utc_now().astimezone(ZoneInfo(task.tz)).date()
        except Exception:  # pragma: no cover - defensive, tz validated on write
            return utc_now().date()

    def is_overdue(self, task: Task) -> bool:
        return task.is_overdue(self._today(task))

    # --- mutation ---------------------------------------------------------

    async def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        owner: str | None = None,
        status: TaskStatus | None = None,
        due_date: dt.date | None = None,
        due_time: dt.time | None = None,
        project: str | None = None,
        owner_person_id: str | None = None,
        clear_due: bool = False,
    ) -> Task:
        task = await self.get(task_id)

        if owner_person_id is not None:
            # Set by participant labelling, which is certain evidence:
            # the extracted owner matched this person AND the user told
            # us they were there.
            task.owner_person_id = owner_person_id

        if title is not None:
            if not title.strip():
                raise ValidationError("title must not be blank")
            task.title = title.strip()
        if owner is not None:
            task.owner = owner.strip() or "unknown"
        if project is not None:
            task.project = project.strip() or None

        if clear_due:
            task.due_date = None
            task.due_time = None
            task.due_raw = None
            task.due_resolution = DateResolution.NONE
        elif due_date is not None or due_time is not None:
            if due_date is not None:
                task.due_date = due_date
            if due_time is not None:
                task.due_time = due_time
            # A date the user set themselves is confident by definition, and
            # clears any "needs clarification" flag.
            task.due_resolution = DateResolution.CONFIDENT
            task.due_raw = None

        if status is not None and status is not task.status:
            task.status = status
            task.completed_at = (
                utc_now() if status is TaskStatus.DONE else None
            )

        # Marks this task as user-owned so re-extraction stops overwriting it.
        task.edited = True
        task.touch()
        await self._tasks.put(task)
        log_event(logger, "task.updated", task_id=task_id, status=task.status.value)
        return task

    async def delete_for_session(self, session_id: str) -> int:
        """Remove this session's contribution to tasks.

        Mirrors memory: a task only this conversation produced is deleted; one
        several conversations support survives with this session detached.
        """
        removed = 0
        for task in await self._tasks.list():
            if session_id not in task.source_session_ids:
                continue
            if len(task.source_session_ids) == 1:
                await self._tasks.delete(task.id)
                removed += 1
                continue
            task.source_session_ids = [
                value for value in task.source_session_ids if value != session_id
            ]
            task.touch()
            await self._tasks.put(task)
        return removed
