"""Audio ingest.

One endpoint, three clients: the phone mic, a file upload, and (phase 9) the
ESP32 band. Multipart rather than JSON so the band's firmware can stream a
file without base64 overhead.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status

from app.api.deps import CurrentUserId, SessionServiceDep
from app.core.errors import ValidationError
from app.models.dto import IngestAccepted
from app.models.places import CoarsePlace

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post(
    "/audio",
    response_model=IngestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload one audio segment (idempotent on segment_id)",
)
async def ingest_audio(
    service: SessionServiceDep,
    user_id: CurrentUserId,
    file: Annotated[UploadFile, File(description="Audio bytes for one segment")],
    segment_id: Annotated[str, Form(description="Client-generated UUIDv4")],
    session_id: Annotated[str, Form()],
    seq: Annotated[int, Form(description="Monotonic index within the session")],
    started_at: Annotated[
        datetime, Form(description="Segment start, ISO-8601 with UTC offset")
    ],
    duration_s: Annotated[float, Form()],
    source: Annotated[str, Form()] = "phone",
    # Coarse location, all optional. Absent whenever the user has not opted
    # in, which is the default. The client is expected to round before
    # sending; the server rounds again (see `models/places.py`) so the
    # promise does not depend on the client doing it.
    place_lat: Annotated[float | None, Form()] = None,
    place_lon: Annotated[float | None, Form()] = None,
    place_label: Annotated[str | None, Form()] = None,
    place_locality: Annotated[str | None, Form()] = None,
    place_region: Annotated[str | None, Form()] = None,
    place_country: Annotated[str | None, Form()] = None,
    place_accuracy_m: Annotated[float | None, Form()] = None,
) -> IngestAccepted:
    audio = await file.read()
    mime_type = file.content_type or ""
    if not mime_type:
        raise ValidationError("Upload is missing a Content-Type")

    segment, created = await service.ingest_segment(
        segment_id=segment_id,
        session_id=session_id,
        seq=seq,
        started_at=started_at,
        duration_s=duration_s,
        mime_type=mime_type,
        audio=audio,
        source=source,
        place=CoarsePlace.build(
            lat=place_lat,
            lon=place_lon,
            label=place_label,
            locality=place_locality,
            region=place_region,
            country=place_country,
            source="device",
            reported_accuracy_m=place_accuracy_m,
            captured_at=started_at,
        ),
        user_id=user_id,
    )
    return IngestAccepted(
        segment_id=segment.id,
        session_id=segment.session_id,
        status=segment.status,
        duplicate=not created,
    )
