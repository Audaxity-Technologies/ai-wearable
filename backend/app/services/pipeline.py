"""Pipeline orchestrator.

Phase 3 runs three stages: transcribe each segment, finalize the session
(concatenate segment transcripts in `seq` order), then analyze the transcript
into structured items. Memory (phase 4) and actions (phase 5) hook in after
analysis.

Analysis is its own job rather than part of finalization, so an LLM outage
retries independently and never costs us the transcript.

Design notes:
- The in-memory `asyncio.Queue` is only a scheduler. Every unit of work also
  has a persisted `Job` record, which is what makes the pipeline resumable:
  `requeue_unfinished` on startup picks up anything left RUNNING or PENDING by
  a crash.
- Finalization is event-driven. A segment finishing checks whether its session
  is closed and fully transcribed, so there is no polling loop.
- Session status is only ever changed through `assert_transition`, so an
  illegal move raises instead of corrupting state.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from app.core.errors import LLMError, TranscriptionError
from app.core.logging import get_logger, log_event
from app.core.time_utils import utc_now
from app.models.analysis import ConversationAnalysis
from app.models.domain import Job, Segment, Session
from app.models.status import (
    JobStatus,
    JobType,
    SegmentStatus,
    SessionStatus,
    assert_transition,
)
from app.repositories.base import BlobStore, Repository
from app.services.actions import ActionEngine
from app.services.analysis import AnalysisService
from app.services.memory import MemoryService
from app.services.tasks import TaskService
from app.services.utterances import UtteranceService
from app.services.transcription.base import TranscriptionDriver

logger = get_logger(__name__)

#: Segment states that need no further transcription work.
_TERMINAL_SEGMENT = frozenset({SegmentStatus.TRANSCRIBED, SegmentStatus.FAILED})


class PipelineService:
    def __init__(
        self,
        *,
        sessions: Repository[Session],
        segments: Repository[Segment],
        jobs: Repository[Job],
        analyses: Repository[ConversationAnalysis],
        blobs: BlobStore,
        driver: TranscriptionDriver,
        analysis: AnalysisService,
        memory: MemoryService,
        tasks: TaskService,
        utterances: UtteranceService | None = None,
        actions: ActionEngine,
        briefing=None,
        worker_count: int = 1,
        max_attempts: int = 3,
        retry_backoff_s: float = 2.0,
    ) -> None:
        self._retry_backoff_s = max(0.0, retry_backoff_s)
        self._sessions = sessions
        self._segments = segments
        self._jobs = jobs
        self._analyses = analyses
        self._blobs = blobs
        self._driver = driver
        self._analysis = analysis
        self._memory = memory
        self._tasks = tasks
        # Optional so existing call sites keep working; with no service
        # the index is simply not built, and `scripts/rebuild_utterances.py`
        # can fill it in later from stored segments.
        self._utterances = utterances
        self._actions = actions
        #: Optional: used to tell the user their conversation is ready.
        self._briefing = briefing
        self._worker_count = max(1, worker_count)
        self._max_attempts = max(1, max_attempts)

        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # --- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        if self._workers:
            return
        for index in range(self._worker_count):
            self._workers.append(
                asyncio.create_task(self._worker_loop(index), name=f"pipeline-{index}")
            )
        log_event(logger, "pipeline.started", workers=self._worker_count)

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        log_event(logger, "pipeline.stopped")

    async def requeue_unfinished(self) -> int:
        """Re-enqueue jobs left behind by a crash. Returns how many."""
        requeued = 0
        for job in await self._jobs.list():
            if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                job.status = JobStatus.PENDING
                job.touch()
                await self._jobs.put(job)
                self._queue.put_nowait(job.id)
                requeued += 1
        if requeued:
            log_event(logger, "pipeline.requeued", count=requeued)
        return requeued

    @property
    def pending_jobs(self) -> int:
        return self._queue.qsize()

    async def drain(self) -> None:
        """Wait until every queued job has been processed (used by tests)."""
        await self._queue.join()

    # --- enqueueing -------------------------------------------------------

    async def enqueue_segment(self, segment: Segment) -> Job:
        return await self._enqueue(
            Job(
                type=JobType.TRANSCRIBE_SEGMENT,
                session_id=segment.session_id,
                segment_id=segment.id,
            )
        )

    async def enqueue_finalize(self, session_id: str) -> Job:
        return await self._enqueue(
            Job(type=JobType.FINALIZE_SESSION, session_id=session_id)
        )

    async def enqueue_analysis(self, session_id: str) -> Job:
        return await self._enqueue(
            Job(type=JobType.ANALYZE_SESSION, session_id=session_id)
        )

    async def _enqueue(self, job: Job) -> Job:
        await self._jobs.put(job)
        self._queue.put_nowait(job.id)
        log_event(
            logger,
            "pipeline.enqueued",
            job_id=job.id,
            job_type=job.type.value,
            session_id=job.session_id,
            segment_id=job.segment_id,
        )
        return job

    # --- worker -----------------------------------------------------------

    async def _worker_loop(self, index: int) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._run_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - last-resort guard
                logger.exception(
                    "pipeline worker %s crashed on job %s", index, job_id
                )
            finally:
                # Exactly once per successful `get`, including on cancellation,
                # so `drain()` cannot hang or under-count.
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        job = await self._jobs.get(job_id)
        if job is None or job.status is JobStatus.DONE:
            return

        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.started_at = utc_now()
        job.touch()
        await self._jobs.put(job)

        try:
            if job.type is JobType.TRANSCRIBE_SEGMENT:
                await self._transcribe_segment(job)
            elif job.type is JobType.ANALYZE_SESSION:
                await self._analyze_session(job.session_id)
            else:
                await self._finalize_session(job.session_id)
        except (TranscriptionError, LLMError) as exc:
            await self._handle_failure(job, str(exc), retryable=exc.retryable)
            return
        except Exception as exc:  # unexpected: do not retry blindly
            await self._handle_failure(job, repr(exc), retryable=False)
            return

        job.status = JobStatus.DONE
        job.finished_at = utc_now()
        job.last_error = None
        job.touch()
        await self._jobs.put(job)

    async def _handle_failure(
        self, job: Job, message: str, *, retryable: bool
    ) -> None:
        can_retry = retryable and job.attempts < self._max_attempts
        job.last_error = message
        job.status = JobStatus.PENDING if can_retry else JobStatus.FAILED
        job.touch()
        if not can_retry:
            job.finished_at = utc_now()
        await self._jobs.put(job)

        log_event(
            logger,
            "pipeline.job_failed",
            job_id=job.id,
            job_type=job.type.value,
            session_id=job.session_id,
            segment_id=job.segment_id,
            attempts=job.attempts,
            will_retry=can_retry,
            reason=message[:200],
        )

        if can_retry:
            # Rate limits are the most common retryable failure, and retrying
            # instantly just burns the remaining attempts. With a single
            # worker this sleep also throttles the queue, which is what we
            # want while the provider is saying "slow down".
            backoff = self._retry_backoff_s * (2 ** (job.attempts - 1))
            if backoff > 0:
                log_event(
                    logger,
                    "pipeline.retry_backoff",
                    job_id=job.id,
                    seconds=round(backoff, 1),
                )
                await asyncio.sleep(backoff)
            if job.type is JobType.ANALYZE_SESSION:
                # The failed attempt left the session in ANALYZING, where the
                # stage's own guard clause would make the retry a silent no-op
                # and strand the session. Rewind so the retry can actually run.
                await self._rollback_analysis(job.session_id)
            self._queue.put_nowait(job.id)
            return

        if job.type is JobType.TRANSCRIBE_SEGMENT and job.segment_id:
            segment = await self._segments.get(job.segment_id)
            if segment is not None:
                segment.status = SegmentStatus.FAILED
                segment.error = message[:500]
                segment.touch()
                await self._segments.put(segment)
            await self._maybe_finalize(job.session_id)
        elif job.type is JobType.ANALYZE_SESSION:
            # The transcript is safe; only the analysis stage failed. Record it
            # as degraded rather than failing the whole session, so the user
            # keeps the conversation and can retry analysis later.
            await self._mark_analysis_degraded(job.session_id, message)
        else:
            await self._fail_session(job.session_id, "finalize", message)

    # --- stages -----------------------------------------------------------

    async def _transcribe_segment(self, job: Job) -> None:
        assert job.segment_id is not None
        segment = await self._segments.get(job.segment_id)
        if segment is None or segment.status is SegmentStatus.TRANSCRIBED:
            return

        segment.status = SegmentStatus.TRANSCRIBING
        segment.touch()
        await self._segments.put(segment)

        audio = await self._blobs.get(segment.blob_key)
        filename = segment.blob_key.rsplit("/", 1)[-1]
        result = await self._driver.transcribe(audio, filename=filename)

        segment.text = result.text
        segment.language = result.language
        segment.spans = result.spans
        if result.duration_s:
            segment.duration_s = result.duration_s
        segment.status = SegmentStatus.TRANSCRIBED
        segment.error = None
        segment.touch()
        await self._segments.put(segment)

        log_event(
            logger,
            "pipeline.segment_transcribed",
            session_id=segment.session_id,
            segment_id=segment.id,
            provider=result.provider,
            model=result.model,
            char_count=len(result.text),  # length only, never the text
        )

        await self._maybe_finalize(segment.session_id)

    async def _maybe_finalize(self, session_id: str) -> None:
        """Finalize if the session is closed and all segments are terminal."""
        session = await self._sessions.get(session_id)
        if session is None or session.status is not SessionStatus.QUEUED:
            return
        segments = await self._segments.list(session_id=session_id)
        if any(segment.status not in _TERMINAL_SEGMENT for segment in segments):
            return
        await self._finalize_session(session_id)

    async def _finalize_session(self, session_id: str) -> None:
        async with self._session_locks[session_id]:
            session = await self._sessions.get(session_id)
            if session is None or session.status is not SessionStatus.QUEUED:
                return

            segments = sorted(
                await self._segments.list(session_id=session_id),
                key=lambda item: item.seq,
            )
            if any(item.status not in _TERMINAL_SEGMENT for item in segments):
                # Still transcribing. A segment completion will re-trigger this.
                return

            transcribed = [
                item for item in segments if item.status is SegmentStatus.TRANSCRIBED
            ]
            if segments and not transcribed:
                await self._fail_session(
                    session_id, "transcription", "all segments failed to transcribe"
                )
                return

            assert_transition(session.status, SessionStatus.TRANSCRIBING)
            session.status = SessionStatus.TRANSCRIBING
            session.touch()
            await self._sessions.put(session)

            session.transcript = "\n".join(
                item.text.strip() for item in transcribed if item.text
            ).strip()
            session.duration_s = sum(item.duration_s for item in segments)
            session.language = next(
                (item.language for item in transcribed if item.language), None
            )

            assert_transition(session.status, SessionStatus.TRANSCRIBED)
            session.status = SessionStatus.TRANSCRIBED
            session.touch()
            await self._sessions.put(session)

            log_event(
                logger,
                "pipeline.session_transcribed",
                session_id=session_id,
                segment_count=len(segments),
                failed_segments=len(segments) - len(transcribed),
                duration_s=round(session.duration_s, 2),
            )

        # Outside the lock: analysis is a separate job so an LLM failure
        # retries on its own without re-running transcription.
        if session.transcript:
            await self.enqueue_analysis(session_id)
        else:
            await self._complete_session(session_id)

    async def _complete_session(self, session_id: str) -> None:
        """Move a session to READY from wherever it legitimately can."""
        session = await self._sessions.get(session_id)
        if session is None or session.status is SessionStatus.READY:
            return
        assert_transition(session.status, SessionStatus.READY)
        session.status = SessionStatus.READY
        session.touch()
        await self._sessions.put(session)

    async def _analyze_session(self, session_id: str) -> None:
        async with self._session_locks[session_id]:
            session = await self._sessions.get(session_id)
            if session is None or session.status is not SessionStatus.TRANSCRIBED:
                return

            assert_transition(session.status, SessionStatus.ANALYZING)
            session.status = SessionStatus.ANALYZING
            session.touch()
            await self._sessions.put(session)

        # The LLM call happens outside the lock: it is slow, and nothing else
        # touches this session while it sits in ANALYZING.
        analysis = await self._analysis.analyze(session)
        await self._analyses.put(analysis)

        async with self._session_locks[session_id]:
            session = await self._sessions.get(session_id)
            if session is None or session.status is not SessionStatus.ANALYZING:
                return
            target = (
                SessionStatus.ANALYZED_DEGRADED
                if analysis.degraded
                else SessionStatus.ANALYZED
            )
            assert_transition(session.status, target)
            session.status = target
            if analysis.degraded:
                session.failed_stage = "analysis"
                session.error = analysis.degraded_reason
            if analysis.title and not session.title:
                session.title = analysis.title
            session.touch()
            await self._sessions.put(session)

        log_event(
            logger,
            "pipeline.session_analyzed",
            session_id=session_id,
            degraded=analysis.degraded,
            items=len(analysis.items),
            dropped=len(analysis.dropped),
            actionable=len(analysis.actionable_items),
        )

        # Built even when the analysis degraded: the spans and their
        # timestamps are still true, and an index of what was said is useful
        # precisely when the extraction failed.
        await self._build_utterances(session_id, analysis)

        if not analysis.degraded:
            await self._write_memory(session_id, analysis)
            await self._propose_actions(session_id, analysis)

        await self._complete_session(session_id)

    async def _propose_actions(
        self, session_id: str, analysis: ConversationAnalysis
    ) -> None:
        """Tasks and proposed actions.

        Like the memory stage this runs inline: both are local and
        deterministic. The action engine only *proposes* - nothing here can
        execute anything.
        """
        session = await self._sessions.get(session_id)
        if session is None or session.status is not SessionStatus.MEMORY_WRITTEN:
            return

        try:
            tasks = await self._tasks.create_from_analysis(session, analysis)
            actions = await self._actions.propose_from_analysis(
                session, analysis, tasks
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("action stage failed for session %s", session_id)
            session.failed_stage = "actions"
            session.error = repr(exc)[:500]
            session.touch()
            await self._sessions.put(session)
            return

        async with self._session_locks[session_id]:
            session = await self._sessions.get(session_id)
            if session is None or session.status is not SessionStatus.MEMORY_WRITTEN:
                return
            assert_transition(session.status, SessionStatus.ACTIONS_PROPOSED)
            session.status = SessionStatus.ACTIONS_PROPOSED
            session.touch()
            await self._sessions.put(session)

        log_event(
            logger,
            "pipeline.actions_proposed",
            session_id=session_id,
            tasks=len(tasks),
            actions=len(actions),
        )

        # Best-effort: a failed notification must never fail a conversation
        # that has already been analysed and stored.
        if self._briefing is not None and actions:
            try:
                await self._briefing.notify_actions_ready(
                    session.user_id, len(actions)
                )
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "failed to notify about session %s", session_id
                )

    async def _build_utterances(
        self, session_id: str, analysis: ConversationAnalysis
    ) -> None:
        """Rebuild the utterance index for this session.

        Never fails the pipeline: this is a derived index that can be rebuilt
        from stored segments at any time, so losing it costs a script run.
        Failing a session over it would cost the recording.
        """
        if self._utterances is None:
            return
        try:
            session = await self._sessions.get(session_id)
            if session is None:
                return
            segments = await self._segments.list(session_id=session_id)
            await self._utterances.rebuild_for_session(
                session, segments, analysis
            )
        except Exception as exc:  # pragma: no cover - defensive
            log_event(
                logger,
                "pipeline.utterances_failed",
                session_id=session_id,
                error=str(exc),
            )

    async def _write_memory(
        self, session_id: str, analysis: ConversationAnalysis
    ) -> None:
        """Memory stage.

        Runs inline rather than as its own job: it is local, deterministic and
        fast, so there is no outage to retry around. A failure here must not
        cost the analysis, so it degrades to a logged warning.
        """
        session = await self._sessions.get(session_id)
        if session is None or session.status is not SessionStatus.ANALYZED:
            return

        try:
            result = await self._memory.write_from_analysis(session, analysis)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("memory stage failed for session %s", session_id)
            session.failed_stage = "memory"
            session.error = repr(exc)[:500]
            session.touch()
            await self._sessions.put(session)
            return

        async with self._session_locks[session_id]:
            session = await self._sessions.get(session_id)
            if session is None or session.status is not SessionStatus.ANALYZED:
                return
            assert_transition(session.status, SessionStatus.MEMORY_WRITTEN)
            session.status = SessionStatus.MEMORY_WRITTEN
            session.touch()
            await self._sessions.put(session)

        log_event(
            logger,
            "pipeline.memory_written",
            session_id=session_id,
            created=len(result.created),
            updated=len(result.updated),
            conflicted=len(result.conflicted),
        )

    async def _rollback_analysis(self, session_id: str) -> None:
        """Return a session from ANALYZING to TRANSCRIBED so a retry can run."""
        async with self._session_locks[session_id]:
            session = await self._sessions.get(session_id)
            if session is None or session.status is not SessionStatus.ANALYZING:
                return
            assert_transition(session.status, SessionStatus.TRANSCRIBED)
            session.status = SessionStatus.TRANSCRIBED
            session.touch()
            await self._sessions.put(session)

    async def _mark_analysis_degraded(self, session_id: str, reason: str) -> None:
        async with self._session_locks[session_id]:
            session = await self._sessions.get(session_id)
            if session is None or session.status is not SessionStatus.ANALYZING:
                return
            assert_transition(session.status, SessionStatus.ANALYZED_DEGRADED)
            session.status = SessionStatus.ANALYZED_DEGRADED
            session.failed_stage = "analysis"
            session.error = reason[:500]
            session.touch()
            await self._sessions.put(session)

        await self._analyses.put(
            ConversationAnalysis(
                id=session_id,
                session_id=session_id,
                degraded=True,
                degraded_reason=reason[:500],
            )
        )
        log_event(
            logger,
            "pipeline.analysis_degraded",
            session_id=session_id,
            reason=reason[:200],
        )
        await self._complete_session(session_id)

    async def _fail_session(
        self, session_id: str, stage: str, reason: str
    ) -> None:
        session = await self._sessions.get(session_id)
        if session is None or session.status is SessionStatus.READY:
            return
        session.status = SessionStatus.FAILED
        session.failed_stage = stage
        session.error = reason[:500]
        session.retry_count += 1
        session.touch()
        await self._sessions.put(session)
        log_event(
            logger,
            "pipeline.session_failed",
            session_id=session_id,
            stage=stage,
            reason=reason[:200],
        )
