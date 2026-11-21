"""End-to-end pipeline: upload -> transcribe -> finalized transcript."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.container import Container
from app.core.time_utils import utc_now
from app.models.status import JobStatus, SegmentStatus, SessionStatus
from app.services.transcription.fake_driver import FakeTranscriptionDriver


async def upload(
    client: AsyncClient, session_id: str, seq: int, wav_bytes: bytes
) -> str:
    segment_id = str(uuid.uuid4())
    response = await client.post(
        "/v1/ingest/audio",
        data={
            "segment_id": segment_id,
            "session_id": session_id,
            "seq": str(seq),
            "started_at": utc_now().isoformat(),
            "duration_s": "1.0",
        },
        files={"file": (f"seg{seq}.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 202, response.text
    return segment_id


async def test_full_flow_produces_an_ordered_transcript(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    opened = await client.post("/v1/sessions", json={"tz": "Asia/Kolkata"})
    session_id = opened.json()["id"]

    # Upload out of order on purpose: the transcript must follow `seq`.
    await upload(client, session_id, 1, wav_bytes)
    await upload(client, session_id, 0, wav_bytes)

    await client.post(f"/v1/sessions/{session_id}/close")
    await container.pipeline.drain()

    summary = (await client.get(f"/v1/sessions/{session_id}")).json()
    assert summary["status"] == SessionStatus.READY
    assert summary["segment_count"] == 2
    assert summary["language"] == "en"

    transcript = (
        await client.get(f"/v1/sessions/{session_id}/transcript")
    ).json()
    assert [item["seq"] for item in transcript["segments"]] == [0, 1]
    assert all(
        item["status"] == SegmentStatus.TRANSCRIBED
        for item in transcript["segments"]
    )
    assert transcript["transcript"]
    assert len(transcript["transcript"].splitlines()) == 2
    assert transcript["segments"][0]["spans"], "timestamps must be preserved"


async def test_close_without_audio_fails_the_session(
    client: AsyncClient, container: Container
) -> None:
    opened = await client.post("/v1/sessions", json={"tz": "UTC"})
    session_id = opened.json()["id"]

    closed = await client.post(f"/v1/sessions/{session_id}/close")
    await container.pipeline.drain()

    assert closed.status_code == 200
    summary = (await client.get(f"/v1/sessions/{session_id}")).json()
    assert summary["status"] == SessionStatus.FAILED
    assert summary["failed_stage"] == "ingest"


async def test_close_is_idempotent(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    opened = await client.post("/v1/sessions", json={"tz": "UTC"})
    session_id = opened.json()["id"]
    await upload(client, session_id, 0, wav_bytes)

    await client.post(f"/v1/sessions/{session_id}/close")
    await container.pipeline.drain()
    second = await client.post(f"/v1/sessions/{session_id}/close")

    assert second.status_code == 200
    assert second.json()["status"] == SessionStatus.READY


async def test_retryable_failure_is_retried_then_gives_up(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """A failing segment must not hang the session: it fails, and so does the
    session, with the stage recorded."""
    driver = container.driver
    assert isinstance(driver, FakeTranscriptionDriver)
    driver._fail = True  # noqa: SLF001 - deliberate fault injection

    opened = await client.post("/v1/sessions", json={"tz": "UTC"})
    session_id = opened.json()["id"]
    await upload(client, session_id, 0, wav_bytes)
    await client.post(f"/v1/sessions/{session_id}/close")
    await container.pipeline.drain()

    assert len(driver.calls) == container.settings.job_max_attempts

    transcript = (
        await client.get(f"/v1/sessions/{session_id}/transcript")
    ).json()
    assert transcript["segments"][0]["status"] == SegmentStatus.FAILED

    summary = (await client.get(f"/v1/sessions/{session_id}")).json()
    assert summary["status"] == SessionStatus.FAILED
    assert summary["failed_stage"] == "transcription"

    jobs = await container.jobs_repo.list(session_id=session_id)
    assert any(job.status is JobStatus.FAILED for job in jobs)


async def test_non_retryable_failure_stops_immediately(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    driver = container.driver
    assert isinstance(driver, FakeTranscriptionDriver)
    driver._fail = True  # noqa: SLF001
    driver._retryable = False  # noqa: SLF001

    opened = await client.post("/v1/sessions", json={"tz": "UTC"})
    session_id = opened.json()["id"]
    await upload(client, session_id, 0, wav_bytes)
    await client.post(f"/v1/sessions/{session_id}/close")
    await container.pipeline.drain()

    assert len(driver.calls) == 1, "a non-retryable error must not be retried"


async def test_partial_failure_still_produces_a_transcript(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """One bad segment should not discard the rest of the conversation."""
    opened = await client.post("/v1/sessions", json={"tz": "UTC"})
    session_id = opened.json()["id"]
    good_id = await upload(client, session_id, 0, wav_bytes)
    bad_id = await upload(client, session_id, 1, wav_bytes)

    await container.pipeline.drain()

    # Mark one segment failed before finalization, as a dead driver would.
    bad = await container.segments_repo.get(bad_id)
    assert bad is not None
    bad.status = SegmentStatus.FAILED
    bad.error = "simulated"
    await container.segments_repo.put(bad)

    await client.post(f"/v1/sessions/{session_id}/close")
    await container.pipeline.drain()

    transcript = (
        await client.get(f"/v1/sessions/{session_id}/transcript")
    ).json()
    assert transcript["status"] == SessionStatus.READY
    assert transcript["transcript"]
    assert len(transcript["transcript"].splitlines()) == 1

    good = await container.segments_repo.get(good_id)
    assert good is not None and good.status is SegmentStatus.TRANSCRIBED


async def test_delete_session_removes_audio_and_records(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    opened = await client.post("/v1/sessions", json={"tz": "UTC"})
    session_id = opened.json()["id"]
    segment_id = await upload(client, session_id, 0, wav_bytes)
    await container.pipeline.drain()

    segment = await container.segments_repo.get(segment_id)
    assert segment is not None
    assert await container.blobs.exists(segment.blob_key)

    response = await client.delete(f"/v1/sessions/{session_id}")
    assert response.status_code == 204

    assert not await container.blobs.exists(segment.blob_key)
    assert await container.segments_repo.get(segment_id) is None
    assert (await client.get(f"/v1/sessions/{session_id}")).status_code == 404
    assert await container.jobs_repo.count(session_id=session_id) == 0
