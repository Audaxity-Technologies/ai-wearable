"""The analysis stage inside the pipeline, end to end over HTTP."""

from __future__ import annotations

import json
import uuid

from httpx import AsyncClient

from app.container import Container
from app.core.time_utils import utc_now
from app.models.status import JobStatus, JobType, SessionStatus
from app.services.llm.fake_driver import DEFAULT_ANALYSIS


async def record_and_close(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> str:
    opened = await client.post("/v1/sessions", json={"tz": "Asia/Kolkata"})
    session_id = opened.json()["id"]
    await client.post(
        "/v1/ingest/audio",
        data={
            "segment_id": str(uuid.uuid4()),
            "session_id": session_id,
            "seq": "0",
            "started_at": utc_now().isoformat(),
            "duration_s": "20.0",
        },
        files={"file": ("seg0.wav", wav_bytes, "audio/wav")},
    )
    await client.post(f"/v1/sessions/{session_id}/close")
    await container.pipeline.drain()
    return session_id


async def test_session_reaches_ready_with_an_analysis(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    session_id = await record_and_close(client, container, wav_bytes)

    summary = (await client.get(f"/v1/sessions/{session_id}")).json()
    assert summary["status"] == SessionStatus.READY
    # The analysis supplies a title when the user did not set one.
    assert summary["title"] == "Investor pitch and prototype review"

    analysis = (
        await client.get(f"/v1/sessions/{session_id}/analysis")
    ).json()
    assert analysis["degraded"] is False
    assert len(analysis["items"]) == 4
    assert analysis["actionable_count"] == 2
    assert analysis["needs_clarification_count"] >= 1
    assert analysis["provider"] == "fake"


async def test_every_item_carries_its_evidence(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """The app shows 'why did you create this?' from source_quote."""
    session_id = await record_and_close(client, container, wav_bytes)
    analysis = (await client.get(f"/v1/sessions/{session_id}/analysis")).json()

    transcript = (
        await client.get(f"/v1/sessions/{session_id}/transcript")
    ).json()["transcript"]

    for item in analysis["items"]:
        assert item["source_quote"].strip()
        assert item["source_quote"].lower()[:20] in transcript.lower()


async def test_analysis_runs_as_its_own_job(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    session_id = await record_and_close(client, container, wav_bytes)
    jobs = await container.jobs_repo.list(session_id=session_id)

    analyze_jobs = [j for j in jobs if j.type is JobType.ANALYZE_SESSION]
    assert len(analyze_jobs) == 1
    assert analyze_jobs[0].status is JobStatus.DONE


async def test_llm_failure_keeps_the_transcript(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """An LLM outage must degrade analysis, never lose the conversation."""
    container.llm._fail = True  # noqa: SLF001 - deliberate fault injection

    session_id = await record_and_close(client, container, wav_bytes)

    summary = (await client.get(f"/v1/sessions/{session_id}")).json()
    assert summary["status"] == SessionStatus.READY

    transcript = (
        await client.get(f"/v1/sessions/{session_id}/transcript")
    ).json()
    assert transcript["transcript"], "transcript must survive an LLM failure"

    analysis = (await client.get(f"/v1/sessions/{session_id}/analysis")).json()
    assert analysis["degraded"] is True
    assert analysis["degraded_reason"]


async def test_llm_failure_is_retried(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    container.llm._fail = True  # noqa: SLF001
    session_id = await record_and_close(client, container, wav_bytes)

    jobs = await container.jobs_repo.list(session_id=session_id)
    analyze = next(j for j in jobs if j.type is JobType.ANALYZE_SESSION)
    assert analyze.attempts == container.settings.job_max_attempts
    assert analyze.status is JobStatus.FAILED


async def test_reanalyze_reruns_without_retranscribing(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """The prompt-iteration loop: replay a real conversation cheaply."""
    session_id = await record_and_close(client, container, wav_bytes)
    transcription_calls = len(container.driver.calls)

    # Change what the model returns, as editing a prompt would.
    revised = json.loads(json.dumps(DEFAULT_ANALYSIS))
    revised["title"] = "Revised title"
    revised["items"] = revised["items"][:1]
    container.llm._responses = [json.dumps(revised)]  # noqa: SLF001

    response = await client.post(f"/v1/sessions/{session_id}/analyze")
    assert response.status_code == 202
    await container.pipeline.drain()

    analysis = (await client.get(f"/v1/sessions/{session_id}/analysis")).json()
    assert analysis["title"] == "Revised title"
    assert len(analysis["items"]) == 1

    assert len(container.driver.calls) == transcription_calls, (
        "re-analysis must not re-transcribe audio"
    )
    assert (await client.get(f"/v1/sessions/{session_id}")).json()[
        "status"
    ] == SessionStatus.READY


async def test_analysis_404_before_it_exists(client: AsyncClient) -> None:
    opened = await client.post("/v1/sessions", json={"tz": "UTC"})
    response = await client.get(f"/v1/sessions/{opened.json()['id']}/analysis")
    assert response.status_code == 404


async def test_reanalyze_rejects_a_session_with_no_transcript(
    client: AsyncClient,
) -> None:
    opened = await client.post("/v1/sessions", json={"tz": "UTC"})
    response = await client.post(f"/v1/sessions/{opened.json()['id']}/analyze")
    assert response.status_code == 409


async def test_delete_removes_the_analysis(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    session_id = await record_and_close(client, container, wav_bytes)
    assert await container.analyses_repo.get(session_id) is not None

    await client.delete(f"/v1/sessions/{session_id}")
    assert await container.analyses_repo.get(session_id) is None


async def test_health_reports_the_llm(client: AsyncClient) -> None:
    body = (await client.get("/v1/health")).json()
    assert body["llm_provider"] == "fake"
    assert body["llm_ready"] is True
