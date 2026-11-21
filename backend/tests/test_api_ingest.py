"""Ingest endpoint: validation and idempotency."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.core.time_utils import utc_now


async def open_session(client: AsyncClient, tz: str = "Asia/Kolkata") -> str:
    response = await client.post("/v1/sessions", json={"tz": tz})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload_form(session_id: str, segment_id: str, seq: int = 0) -> dict:
    return {
        "segment_id": segment_id,
        "session_id": session_id,
        "seq": str(seq),
        "started_at": utc_now().isoformat(),
        "duration_s": "1.0",
        "source": "phone",
    }


async def test_upload_accepted(client: AsyncClient, wav_bytes: bytes) -> None:
    session_id = await open_session(client)
    segment_id = str(uuid.uuid4())

    response = await client.post(
        "/v1/ingest/audio",
        data=upload_form(session_id, segment_id),
        files={"file": ("seg0.wav", wav_bytes, "audio/wav")},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["segment_id"] == segment_id
    assert body["duplicate"] is False


async def test_duplicate_upload_is_idempotent(
    client: AsyncClient, wav_bytes: bytes
) -> None:
    """A client retry must not create a second segment or a second job."""
    session_id = await open_session(client)
    segment_id = str(uuid.uuid4())
    form = upload_form(session_id, segment_id)

    first = await client.post(
        "/v1/ingest/audio",
        data=form,
        files={"file": ("seg0.wav", wav_bytes, "audio/wav")},
    )
    second = await client.post(
        "/v1/ingest/audio",
        data=form,
        files={"file": ("seg0.wav", wav_bytes, "audio/wav")},
    )

    assert first.json()["duplicate"] is False
    assert second.status_code == 202
    assert second.json()["duplicate"] is True

    session = await client.get(f"/v1/sessions/{session_id}")
    assert session.json()["segment_count"] == 1


async def test_unsupported_media_type(
    client: AsyncClient, wav_bytes: bytes
) -> None:
    session_id = await open_session(client)
    response = await client.post(
        "/v1/ingest/audio",
        data=upload_form(session_id, str(uuid.uuid4())),
        files={"file": ("notes.pdf", wav_bytes, "application/pdf")},
    )
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_media_type"


async def test_oversized_segment_rejected(
    client: AsyncClient, container, wav_bytes: bytes
) -> None:
    container.settings.max_segment_bytes = 100
    session_id = await open_session(client)

    response = await client.post(
        "/v1/ingest/audio",
        data=upload_form(session_id, str(uuid.uuid4())),
        files={"file": ("seg0.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


async def test_segment_id_must_be_a_uuid(
    client: AsyncClient, wav_bytes: bytes
) -> None:
    session_id = await open_session(client)
    response = await client.post(
        "/v1/ingest/audio",
        data=upload_form(session_id, "segment-1"),
        files={"file": ("seg0.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_unknown_session_is_404(
    client: AsyncClient, wav_bytes: bytes
) -> None:
    response = await client.post(
        "/v1/ingest/audio",
        data=upload_form(str(uuid.uuid4()), str(uuid.uuid4())),
        files={"file": ("seg0.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 404


async def test_closed_session_rejects_new_segments(
    client: AsyncClient, container, wav_bytes: bytes
) -> None:
    session_id = await open_session(client)
    await client.post(
        "/v1/ingest/audio",
        data=upload_form(session_id, str(uuid.uuid4())),
        files={"file": ("seg0.wav", wav_bytes, "audio/wav")},
    )
    await client.post(f"/v1/sessions/{session_id}/close")
    await container.pipeline.drain()

    response = await client.post(
        "/v1/ingest/audio",
        data=upload_form(session_id, str(uuid.uuid4()), seq=1),
        files={"file": ("seg1.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


async def test_invalid_timezone_rejected(client: AsyncClient) -> None:
    response = await client.post("/v1/sessions", json={"tz": "Mars/Base"})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_ingest_stores_a_coarse_place_and_rounds_it(
    client: AsyncClient, container: Container
) -> None:
    """A client that sends full precision must not get it stored.

    This is the test that makes the retention promise real: it does not rely
    on the mobile app rounding correctly.
    """
    session_id = (
        await client.post(
            "/v1/sessions", json={"tz": "UTC"}, params={"user_id": "manan"}
        )
    ).json()["id"]

    segment_id = str(uuid.uuid4())
    response = await client.post(
        "/v1/ingest/audio",
        params={"user_id": "manan"},
        files={"file": ("a.m4a", b"0" * 2048, "audio/m4a")},
        data={
            "segment_id": segment_id,
            "session_id": session_id,
            "seq": "0",
            "started_at": utc_now().isoformat(),
            "duration_s": "30",
            "place_lat": "28.52451937",
            "place_lon": "77.18554812",
            "place_label": "Blue Tokai",
            "place_accuracy_m": "18",
        },
    )
    assert response.status_code == 202, response.text

    segment = await container.segments_repo.get(segment_id)
    assert segment is not None and segment.place is not None
    assert segment.place.lat == 28.525
    assert segment.place.lon == 77.186
    assert segment.place.label == "Blue Tokai"
    assert segment.place.reported_accuracy_m == 18.0


async def test_ingest_without_place_fields_stores_no_place(
    client: AsyncClient, container: Container
) -> None:
    """Opt-in: every segment recorded before the user turns this on has
    `place = None`, and nothing infers one."""
    session_id = (
        await client.post(
            "/v1/sessions", json={"tz": "UTC"}, params={"user_id": "manan"}
        )
    ).json()["id"]

    segment_id = str(uuid.uuid4())
    await client.post(
        "/v1/ingest/audio",
        params={"user_id": "manan"},
        files={"file": ("a.m4a", b"0" * 2048, "audio/m4a")},
        data={
            "segment_id": segment_id,
            "session_id": session_id,
            "seq": "0",
            "started_at": utc_now().isoformat(),
            "duration_s": "30",
        },
    )
    segment = await container.segments_repo.get(segment_id)
    assert segment is not None
    assert segment.place is None


async def test_a_place_is_never_inherited_from_a_previous_segment(
    client: AsyncClient, container: Container
) -> None:
    """Losing a GPS fix mid-conversation must leave a hole, not a guess.

    Place is the one axis of the index that cannot be re-derived later, so a
    borrowed value would corrupt it permanently.
    """
    session_id = (
        await client.post(
            "/v1/sessions", json={"tz": "UTC"}, params={"user_id": "manan"}
        )
    ).json()["id"]

    ids = [str(uuid.uuid4()) for _ in range(2)]
    for index, segment_id in enumerate(ids):
        data = {
            "segment_id": segment_id,
            "session_id": session_id,
            "seq": str(index),
            "started_at": utc_now().isoformat(),
            "duration_s": "30",
        }
        if index == 0:
            data |= {"place_lat": "28.524", "place_lon": "77.185"}
        await client.post(
            "/v1/ingest/audio",
            params={"user_id": "manan"},
            files={"file": ("a.m4a", b"0" * 2048, "audio/m4a")},
            data=data,
        )

    first = await container.segments_repo.get(ids[0])
    second = await container.segments_repo.get(ids[1])
    assert first is not None and first.place is not None
    assert second is not None and second.place is None


async def test_closing_a_session_summarises_its_places(
    client: AsyncClient, container: Container
) -> None:
    session_id = (
        await client.post(
            "/v1/sessions", json={"tz": "UTC"}, params={"user_id": "manan"}
        )
    ).json()["id"]

    places = [
        {"place_lat": "28.524", "place_lon": "77.185", "place_label": "Office"},
        {"place_lat": "28.600", "place_lon": "77.220", "place_label": "Cafe"},
        {"place_lat": "28.600", "place_lon": "77.220", "place_label": "Cafe"},
    ]
    for index, extra in enumerate(places):
        await client.post(
            "/v1/ingest/audio",
            params={"user_id": "manan"},
            files={"file": ("a.m4a", b"0" * 2048, "audio/m4a")},
            data={
                "segment_id": str(uuid.uuid4()),
                "session_id": session_id,
                "seq": str(index),
                "started_at": utc_now().isoformat(),
                "duration_s": "30",
            }
            | extra,
        )

    closed = await client.post(
        f"/v1/sessions/{session_id}/close", params={"user_id": "manan"}
    )
    assert closed.status_code == 200, closed.text
    summary = closed.json()["places"]
    # Busiest first: the cafe has two segments.
    assert [entry["place"]["label"] for entry in summary] == ["Cafe", "Office"]
    assert summary[0]["segment_count"] == 2


async def test_coordinates_are_not_written_to_the_ingest_log(
    client: AsyncClient, container: Container, caplog
) -> None:
    """Log "whether", never "where".

    A log of coarse points is still a log of where conversations happened,
    and logs travel further than the database does.
    """
    session_id = (
        await client.post(
            "/v1/sessions", json={"tz": "UTC"}, params={"user_id": "manan"}
        )
    ).json()["id"]

    with caplog.at_level("INFO"):
        await client.post(
            "/v1/ingest/audio",
            params={"user_id": "manan"},
            files={"file": ("a.m4a", b"0" * 2048, "audio/m4a")},
            data={
                "segment_id": str(uuid.uuid4()),
                "session_id": session_id,
                "seq": "0",
                "started_at": utc_now().isoformat(),
                "duration_s": "30",
                "place_lat": "28.524",
                "place_lon": "77.185",
                "place_label": "Blue Tokai",
            },
        )

    # Inspect the structured context, not `caplog.text`: the formatted
    # message is only the event name, so a text search would pass even if
    # coordinates were being logged under a key.
    contexts = [
        record.__dict__.get("context", {})
        for record in caplog.records
        if record.name == "app.services.sessions"
    ]
    ingest = next(
        context
        for record, context in zip(
            [r for r in caplog.records if r.name == "app.services.sessions"],
            contexts,
            strict=True,
        )
        if record.getMessage() == "ingest.accepted"
    )
    assert ingest["has_place"] is True
    flattened = repr(contexts)
    assert "28.524" not in flattened
    assert "77.185" not in flattened
    assert "Blue Tokai" not in flattened
