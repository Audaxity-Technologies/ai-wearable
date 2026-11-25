"""Composition root.

Everything is wired once here and hung off `app.state`, so routes receive
services rather than constructing them. Tests build a container pointed at a
temp directory with the fake drivers.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.models.analysis import ConversationAnalysis
from app.models.domain import Job, Segment, Session
from app.models.actions import Action
from app.models.integrations import GoogleAccount
from app.models.memory import Memory
from app.models.tasks import Task
from app.models.users import Device, User
from app.models.people import Person
from app.models.utterances import Utterance
from app.models.voice import VoiceProfile
from app.repositories.blob import LocalBlobStore
from app.repositories.json_repo import JsonRepository
from app.services.actions import ActionEngine
from app.services.analysis import AnalysisService
from app.services.approval import ApprovalService
from app.services.auth import AuthService
from app.services.briefing import BriefingService
from app.services.ask import AskService
from app.services.llm.base import LLMDriver
from app.services.llm.factory import build_llm_driver
from app.services.executors import build_executors, build_google_executors
from app.services.google import GoogleOAuthService
from app.services.memory import MemoryService
from app.services.notifications import ExpoPushDriver, NullPushDriver
from app.services.retrieval import RetrievalService
from app.services.pipeline import PipelineService
from app.services.sessions import SessionService
from app.services.people import PersonService
from app.services.questions import QuestionParser
from app.services.tasks import TaskService
from app.services.utterances import UtteranceService
from app.services.voice.enrolment import VoiceService
from app.services.voice.factory import build_speaker_embedding_driver
from app.services.transcription.base import TranscriptionDriver
from app.services.transcription.factory import build_transcription_driver


@dataclass(slots=True)
class Container:
    settings: Settings
    sessions_repo: JsonRepository[Session]
    segments_repo: JsonRepository[Segment]
    jobs_repo: JsonRepository[Job]
    analyses_repo: JsonRepository[ConversationAnalysis]
    memories_repo: JsonRepository[Memory]
    tasks_repo: JsonRepository[Task]
    actions_repo: JsonRepository[Action]
    users_repo: JsonRepository[User]
    devices_repo: JsonRepository[Device]
    google_accounts_repo: JsonRepository[GoogleAccount]
    voice_profiles_repo: JsonRepository[VoiceProfile]
    people_repo: JsonRepository[Person]
    utterances_repo: JsonRepository[Utterance]
    blobs: LocalBlobStore
    driver: TranscriptionDriver
    llm: LLMDriver
    analysis_service: AnalysisService
    memory_service: MemoryService
    task_service: TaskService
    action_engine: ActionEngine
    approval_service: ApprovalService
    retrieval_service: RetrievalService
    ask_service: AskService
    auth_service: AuthService
    google_oauth: GoogleOAuthService
    voice_service: VoiceService
    person_service: PersonService
    utterance_service: UtteranceService
    question_parser: QuestionParser
    briefing_service: BriefingService
    push: ExpoPushDriver | NullPushDriver
    pipeline: PipelineService
    session_service: SessionService


def build_container(
    settings: Settings,
    *,
    driver: TranscriptionDriver | None = None,
    llm: LLMDriver | None = None,
) -> Container:
    settings.store_dir.mkdir(parents=True, exist_ok=True)

    sessions_repo: JsonRepository[Session] = JsonRepository(
        settings.store_dir / "sessions.json", Session
    )
    segments_repo: JsonRepository[Segment] = JsonRepository(
        settings.store_dir / "segments.json", Segment
    )
    jobs_repo: JsonRepository[Job] = JsonRepository(
        settings.store_dir / "jobs.json", Job
    )
    analyses_repo: JsonRepository[ConversationAnalysis] = JsonRepository(
        settings.store_dir / "analyses.json", ConversationAnalysis
    )
    memories_repo: JsonRepository[Memory] = JsonRepository(
        settings.store_dir / "memories.json", Memory
    )
    tasks_repo: JsonRepository[Task] = JsonRepository(
        settings.store_dir / "tasks.json", Task
    )
    actions_repo: JsonRepository[Action] = JsonRepository(
        settings.store_dir / "actions.json", Action
    )
    users_repo: JsonRepository[User] = JsonRepository(
        settings.store_dir / "users.json", User
    )
    devices_repo: JsonRepository[Device] = JsonRepository(
        settings.store_dir / "devices.json", Device
    )
    google_accounts_repo: JsonRepository[GoogleAccount] = JsonRepository(
        settings.store_dir / "google_accounts.json", GoogleAccount
    )
    voice_profiles_repo: JsonRepository[VoiceProfile] = JsonRepository(
        settings.store_dir / "voice_profiles.json", VoiceProfile
    )
    people_repo: JsonRepository[Person] = JsonRepository(
        settings.store_dir / "people.json", Person
    )
    utterances_repo: JsonRepository[Utterance] = JsonRepository(
        settings.store_dir / "utterances.json", Utterance
    )
    blobs = LocalBlobStore(settings.audio_dir)

    transcription_driver = driver or build_transcription_driver(settings)
    llm_driver = llm or build_llm_driver(settings)
    analysis_service = AnalysisService(driver=llm_driver)
    memory_service = MemoryService(memories=memories_repo)
    person_service = PersonService(people=people_repo)
    utterance_service = UtteranceService(utterances=utterances_repo)
    question_parser = QuestionParser(
        people=person_service,
        utterances=utterance_service,
        sessions=sessions_repo,
    )
    task_service = TaskService(tasks=tasks_repo, people=person_service)
    action_engine = ActionEngine(actions=actions_repo)
    voice_service = VoiceService(
        profiles=voice_profiles_repo,
        driver=build_speaker_embedding_driver(settings),
    )
    google_oauth = GoogleOAuthService(
        accounts=google_accounts_repo,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=settings.google_redirect_uri,
        jwt_secret=settings.jwt_secret,
        timeout_s=settings.google_timeout_s,
        enable_gmail=settings.google_enable_gmail,
    )
    # With no OAuth credentials the registry stays exactly as it was in
    # phase 5, so a deployment that never configures Google is unaffected by
    # phase 10 existing.
    executors = (
        build_google_executors(
            tasks=tasks_repo, oauth=google_oauth, llm=llm_driver
        )
        if google_oauth.configured
        else build_executors(tasks=tasks_repo)
    )
    approval_service = ApprovalService(
        actions=actions_repo, executors=executors
    )

    retrieval_service = RetrievalService(
        sessions=sessions_repo,
        analyses=analyses_repo,
        memories=memories_repo,
        tasks=tasks_repo,
    )
    ask_service = AskService(retrieval=retrieval_service, driver=llm_driver)
    auth_service = AuthService(
        users=users_repo, devices=devices_repo, settings=settings
    )
    push_driver = (
        ExpoPushDriver() if settings.push_provider == "expo" else NullPushDriver()
    )
    briefing_service = BriefingService(
        auth=auth_service,
        tasks=task_service,
        actions=approval_service,
        push=push_driver,
    )

    pipeline = PipelineService(
        sessions=sessions_repo,
        segments=segments_repo,
        jobs=jobs_repo,
        analyses=analyses_repo,
        blobs=blobs,
        driver=transcription_driver,
        analysis=analysis_service,
        memory=memory_service,
        tasks=task_service,
        utterances=utterance_service,
        actions=action_engine,
        briefing=briefing_service,
        worker_count=settings.pipeline_workers,
        max_attempts=settings.job_max_attempts,
        retry_backoff_s=settings.job_retry_backoff_s,
    )
    session_service = SessionService(
        sessions=sessions_repo,
        segments=segments_repo,
        jobs=jobs_repo,
        analyses=analyses_repo,
        memory=memory_service,
        tasks=task_service,
        approval=approval_service,
        utterances=utterance_service,
        people=person_service,
        blobs=blobs,
        pipeline=pipeline,
        settings=settings,
    )
    return Container(
        settings=settings,
        sessions_repo=sessions_repo,
        segments_repo=segments_repo,
        jobs_repo=jobs_repo,
        analyses_repo=analyses_repo,
        memories_repo=memories_repo,
        tasks_repo=tasks_repo,
        actions_repo=actions_repo,
        users_repo=users_repo,
        devices_repo=devices_repo,
        google_accounts_repo=google_accounts_repo,
        voice_profiles_repo=voice_profiles_repo,
        people_repo=people_repo,
        utterances_repo=utterances_repo,
        blobs=blobs,
        driver=transcription_driver,
        llm=llm_driver,
        analysis_service=analysis_service,
        memory_service=memory_service,
        task_service=task_service,
        action_engine=action_engine,
        approval_service=approval_service,
        retrieval_service=retrieval_service,
        ask_service=ask_service,
        auth_service=auth_service,
        google_oauth=google_oauth,
        voice_service=voice_service,
        person_service=person_service,
        utterance_service=utterance_service,
        question_parser=question_parser,
        briefing_service=briefing_service,
        push=push_driver,
        pipeline=pipeline,
        session_service=session_service,
    )
