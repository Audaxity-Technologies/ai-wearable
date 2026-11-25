"""Session and ingest service.

No FastAPI imports here by design: the pipeline, the tests and (later) the
device gateway all call these same functions.
"""

from __future__ import annotations

from datetime import datetime

from app.config import Settings
from app.core.errors import (
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from app.core.ids import validate_uuid
from app.core.logging import get_logger, log_event
from app.core.time_utils import to_utc, utc_now, validate_timezone
from app.models.analysis import ConversationAnalysis
from app.models.domain import (
    Job,
    Participation,
    ParticipationRole,
    Segment,
    Session,
)
from app.models.places import CoarsePlace, summarise_places
from app.models.status import SegmentStatus, SessionStatus, assert_transition
from app.repositories.base import BlobStore, Repository
from app.services.integrity import CaptureReceipt, build_receipt
from app.services.people import PersonService
from app.services.utterances import UtteranceService
from app.repositories.blob import blob_key, extension_for
from app.services.approval import ApprovalService
from app.services.memory import MemoryService
from app.services.pipeline import PipelineService
from app.services.tasks import TaskService

logger = get_logger(__name__)

#: Statuses from which a re-analysis is safe to start.
_REANALYSABLE = frozenset(
    {
        SessionStatus.READY,
        SessionStatus.ANALYZED,
        SessionStatus.ANALYZED_DEGRADED,
        SessionStatus.TRANSCRIBED,
        SessionStatus.FAILED,
    }
)


class SessionService:
    def __init__(
        self,
        *,
        sessions: Repository[Session],
        segments: Repository[Segment],
        jobs: Repository[Job],
        analyses: Repository[ConversationAnalysis],
        memory: MemoryService,
        tasks: TaskService,
        approval: ApprovalService,
        utterances: UtteranceService | None = None,
        people: PersonService | None = None,
        blobs: BlobStore,
        pipeline: PipelineService,
        settings: Settings,
    ) -> None:
        self._memory = memory
        self._task_service = tasks
        self._approval = approval
        self._utterances = utterances
        self._people = people
        self._sessions = sessions
        self._segments = segments
        self._jobs = jobs
        self._analyses = analyses
        self._blobs = blobs
        self._pipeline = pipeline
        self._settings = settings

    # --- sessions ---------------------------------------------------------

    async def open_session(
        self,
        *,
        tz: str,
        started_at: datetime | None = None,
        title: str | None = None,
        user_id: str = "dev-user",
    ) -> Session:
        session = Session(
            user_id=user_id,
            tz=validate_timezone(tz),
            started_at=to_utc(started_at) if started_at else utc_now(),
            title=title,
        )
        await self._sessions.put(session)
        log_event(
            logger,
            "session.opened",
            session_id=session.id,
            user_id=user_id,
            tz=session.tz,
        )
        return session

    async def get_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> Session:
        session = await self._sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"Session not found: {session_id}")
        if user_id is not None and session.user_id != user_id:
            # Reported as missing rather than forbidden: a 403 would confirm
            # that this id exists, which is itself a leak.
            raise NotFoundError(f"Session not found: {session_id}")
        return session

    async def list_sessions(
        self,
        *,
        user_id: str | None = None,
        status: SessionStatus | None = None,
        updated_since: datetime | None = None,
    ) -> list[Session]:
        filters: dict[str, object] = {}
        if user_id:
            filters["user_id"] = user_id
        if status:
            filters["status"] = status
        sessions = await self._sessions.list(**filters)
        if updated_since is not None:
            cutoff = to_utc(updated_since)
            sessions = [s for s in sessions if to_utc(s.updated_at) > cutoff]
        return sorted(sessions, key=lambda s: s.started_at, reverse=True)

    async def close_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> Session:
        session = await self.get_session(session_id, user_id=user_id)
        if session.status is not SessionStatus.OPEN:
            # Idempotent: closing twice is not an error, the client may retry.
            return session

        assert_transition(session.status, SessionStatus.QUEUED)
        session.status = SessionStatus.QUEUED
        session.closed_at = utc_now()
        # Summarise where this happened while the segments are to hand. The
        # per-segment records stay the truth; this is only the view.
        session.places = summarise_places(
            [
                (segment.place, segment.duration_s)
                for segment in await self.get_segments(session_id)
            ]
        )
        session.touch()
        await self._sessions.put(session)

        log_event(
            logger,
            "session.closed",
            session_id=session.id,
            segment_count=len(session.segment_ids),
        )

        if not session.segment_ids:
            # Nothing was recorded; there is no work for the pipeline.
            await self._fail_empty_session(session)
            return await self.get_session(session_id)

        await self._pipeline.enqueue_finalize(session.id)
        return session

    async def _fail_empty_session(self, session: Session) -> None:
        session.status = SessionStatus.FAILED
        session.failed_stage = "ingest"
        session.error = "Session was closed with no audio segments"
        session.touch()
        await self._sessions.put(session)

    async def get_segments(self, session_id: str) -> list[Segment]:
        segments = await self._segments.list(session_id=session_id)
        return sorted(segments, key=lambda item: item.seq)

    async def get_analysis(
        self, session_id: str, *, user_id: str | None = None
    ) -> ConversationAnalysis:
        # 404s if the session is unknown or belongs to someone else.
        await self.get_session(session_id, user_id=user_id)
        analysis = await self._analyses.get(session_id)
        if analysis is None:
            raise NotFoundError(
                f"No analysis for session {session_id}. It may still be "
                "processing."
            )
        return analysis

    # --- participants -----------------------------------------------------

    async def add_participant(
        self,
        session_id: str,
        name: str,
        *,
        role: ParticipationRole = ParticipationRole.SPOKE,
        user_id: str | None = None,
    ) -> Session:
        """Record that someone was in this conversation.

        Resolves the name through `PersonService`, so "Pull kit" lands on the
        existing Pulkit rather than creating a second person - the label
        benefits from phase B's alias matching for free.

        The session is also appended to that `Person`'s provenance, which is
        what makes the relationship accumulate: label three conversations
        with Pulkit and his row knows about all three, so the next question
        about him searches all three.
        """
        session = await self.get_session(session_id, user_id=user_id)
        if self._people is None:
            raise ValidationError("Person identity is not configured.")

        person = await self._people.resolve_or_create(
            session.user_id, name, session_id=session.id, at=session.started_at
        )
        if person is None:
            # Sentinels and pronouns are not people. Refusing loudly beats
            # creating a participant called "someone".
            raise ValidationError(
                f"{name!r} cannot be used as a person's name."
            )

        existing = {entry.person_id for entry in session.participants}
        if person.id not in existing:
            session.participants.append(
                Participation(
                    person_id=person.id,
                    name=person.canonical_name,
                    role=ParticipationRole.SELF if person.is_self else role,
                    source="user",
                )
            )
            session.touch()
            await self._sessions.put(session)
            log_event(
                logger,
                "session.participant_added",
                session_id=session.id,
                person_id=person.id,
                role=role.value,
            )

        # A task whose extracted owner matches a participant can now be
        # attributed with certainty: the name matched AND we know they were
        # there. This is the one place the label improves old rows.
        await self._attribute_tasks(session, person)
        return await self.get_session(session_id, user_id=user_id)

    async def remove_participant(
        self, session_id: str, person_id: str, *, user_id: str | None = None
    ) -> Session:
        """Unlabel. The `Person` and their other sessions are untouched."""
        session = await self.get_session(session_id, user_id=user_id)
        before = len(session.participants)
        session.participants = [
            entry for entry in session.participants
            if entry.person_id != person_id
        ]
        if len(session.participants) != before:
            session.touch()
            await self._sessions.put(session)
        return session

    async def sessions_with(
        self, *, user_id: str, person_id: str
    ) -> list[str]:
        """Ids of conversations this person took part in.

        What turns "what has Pulkit committed to" from a filter that resolves
        and cannot narrow into one that actually narrows.
        """
        sessions = await self._sessions.list(user_id=user_id)
        return [
            session.id
            for session in sessions
            if any(
                entry.person_id == person_id for entry in session.participants
            )
        ]

    async def _attribute_tasks(self, session: Session, person) -> None:
        """Fill in `owner_person_id` on this session's tasks where the owner
        string names this participant.

        Deliberately narrow: only when the extracted owner already matches
        the person's name or an alias. It never assigns a task to whoever
        happened to be present, because "someone in the room committed to
        this" is not the same as "this person did".
        """
        if self._task_service is None:
            return
        try:
            tasks = await self._task_service.list_tasks(
                user_id=session.user_id
            )
        except Exception:  # pragma: no cover - defensive
            return
        for task in tasks:
            if session.id not in task.source_session_ids:
                continue
            if task.owner_person_id is not None:
                continue
            if not person.matches(task.owner):
                continue
            await self._task_service.update(
                task.id, owner_person_id=person.id
            )

    async def get_receipt(
        self, session_id: str, *, user_id: str | None = None
    ) -> CaptureReceipt:
        """What was captured for this session, and what was not.

        Deliberately available at ANY status, including a session still
        processing or one that failed. A receipt you can only see once
        everything worked is useless for the one question it exists to answer.
        A missing analysis is reported as absent, not as an error.
        """
        session = await self.get_session(session_id, user_id=user_id)
        segments = await self.get_segments(session_id)
        analysis = await self._analyses.get(session_id)
        return build_receipt(
            session=session, segments=segments, analysis=analysis
        )

    async def reanalyze(
        self, session_id: str, *, user_id: str | None = None
    ) -> Session:
        """Re-run analysis on an existing transcript.

        The prompt-iteration loop: edit the prompt, replay a real conversation,
        compare. Costs one LLM call and no re-transcription.
        """
        session = await self.get_session(session_id, user_id=user_id)
        if not session.transcript:
            raise ConflictError(
                f"Session {session_id} has no transcript to analyse"
            )
        if session.status not in _REANALYSABLE:
            raise ConflictError(
                f"Session {session_id} is {session.status.value}; "
                "wait for the pipeline to finish before re-analysing"
            )

        # Rewind to TRANSCRIBED so the stage runs through its normal path.
        session.status = SessionStatus.TRANSCRIBED
        session.failed_stage = None
        session.error = None
        session.touch()
        await self._sessions.put(session)

        await self._pipeline.enqueue_analysis(session_id)
        log_event(logger, "session.reanalysis_requested", session_id=session_id)
        return session

    async def delete_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> None:
        """Cascade delete: audio blobs, segments, jobs, then the session.

        Privacy requirement, not a convenience. Audio goes first so a failure
        part-way through never leaves orphaned audio with no record pointing
        at it.
        """
        session = await self.get_session(session_id, user_id=user_id)
        segments = await self.get_segments(session_id)

        for segment in segments:
            await self._blobs.delete(segment.blob_key)
            await self._segments.delete(segment.id)
        for job in await self._jobs.list(session_id=session_id):
            await self._jobs.delete(job.id)
        await self._analyses.delete(session_id)
        # Memories supported only by this conversation go too; ones with other
        # sources survive with this session detached.
        # The utterance index is derived, but it is derived from audio the
        # user is erasing. An index that outlives its source would answer
        # questions about a recording they believe is gone.
        utterances_deleted = (
            await self._utterances.delete_for_session(session_id)
            if self._utterances is not None
            else 0
        )
        memories_deleted = await self._memory.delete_for_session(session_id)
        tasks_deleted = await self._task_service.delete_for_session(session_id)
        # Executed actions survive: they record something that already
        # happened, and erasing that would misrepresent history.
        actions_deleted = await self._approval.delete_for_session(session_id)
        await self._sessions.delete(session.id)

        log_event(
            logger,
            "session.deleted",
            session_id=session_id,
            segments_deleted=len(segments),
            memories_deleted=memories_deleted,
            tasks_deleted=tasks_deleted,
            actions_deleted=actions_deleted,
            utterances_deleted=utterances_deleted,
        )

    # --- ingest -----------------------------------------------------------

    async def ingest_segment(
        self,
        *,
        segment_id: str,
        session_id: str,
        seq: int,
        started_at: datetime,
        duration_s: float,
        mime_type: str,
        audio: bytes,
        source: str = "phone",
        place: CoarsePlace | None = None,
        user_id: str | None = None,
    ) -> tuple[Segment, bool]:
        """Store one audio segment and queue it for transcription.

        Returns `(segment, created)`. `created=False` means this segment_id was
        already ingested, so the call was a duplicate — the normal result of a
        client retry, and the reason this endpoint is safe to hammer.
        """
        validate_uuid(segment_id, "segment_id")
        # Ownership matters here as much as on reads: without it, anyone could
        # push audio into someone else's conversation.
        session = await self.get_session(session_id, user_id=user_id)

        if not session.is_open:
            raise ConflictError(
                f"Session {session_id} is {session.status.value}, "
                "it no longer accepts segments"
            )

        # Idempotency check before doing any expensive work.
        existing = await self._segments.get(segment_id)
        if existing is not None:
            if existing.session_id != session_id:
                raise ConflictError(
                    f"segment_id {segment_id} already belongs to another session"
                )
            log_event(
                logger,
                "ingest.duplicate",
                session_id=session_id,
                segment_id=segment_id,
            )
            return existing, False

        self._validate_upload(mime_type, audio, duration_s)
        extension = extension_for(mime_type)
        assert extension is not None  # guaranteed by _validate_upload

        key = blob_key(session_id, segment_id, extension)
        await self._blobs.put(key, audio)

        segment = Segment(
            id=segment_id,
            session_id=session_id,
            user_id=session.user_id,
            seq=seq,
            started_at=to_utc(started_at),
            duration_s=duration_s,
            mime_type=mime_type,
            size_bytes=len(audio),
            blob_key=key,
            source=source,
            status=SegmentStatus.UPLOADED,
            place=place,
        )
        stored, created = await self._segments.add_if_absent(segment)
        if not created:
            # Lost a race with a concurrent identical upload; the blob write
            # was idempotent, so simply report the duplicate.
            return stored, False

        if segment_id not in session.segment_ids:
            session.segment_ids.append(segment_id)
            session.touch()
            await self._sessions.put(session)

        await self._pipeline.enqueue_segment(segment)

        log_event(
            logger,
            "ingest.accepted",
            session_id=session_id,
            segment_id=segment_id,
            seq=seq,
            size_bytes=len(audio),
            mime_type=mime_type,
            source=source,
            # Whether, not where: a log of coarse points is still a log
            # of where conversations happened.
            has_place=place is not None,
        )
        return segment, True

    def _validate_upload(
        self, mime_type: str, audio: bytes, duration_s: float
    ) -> None:
        if extension_for(mime_type) is None:
            raise UnsupportedMediaTypeError(
                f"Unsupported audio type: {mime_type!r}"
            )
        if not audio:
            raise ValidationError("Audio payload is empty")
        if len(audio) > self._settings.max_segment_bytes:
            raise PayloadTooLargeError(
                f"Segment is {len(audio)} bytes, limit is "
                f"{self._settings.max_segment_bytes}"
            )
        if duration_s <= 0:
            raise ValidationError("duration_s must be greater than 0")
        if duration_s > self._settings.max_segment_duration_s:
            raise ValidationError(
                f"duration_s {duration_s} exceeds limit "
                f"{self._settings.max_segment_duration_s}"
            )
