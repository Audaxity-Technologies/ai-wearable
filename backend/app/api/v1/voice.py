"""Owner voice enrolment routes.

Four routes, and the shape of them is the privacy promise:

- enrol **only the account holder** (there is no route to enrol anyone else,
  and no repository that could hold them)
- **read back** what is stored, which never includes the voiceprint itself
- **check** a clip, so the user can see for themselves whether it works
- **delete**, which really deletes

`POST /voice/enrol` takes the clips in one request rather than accumulating
them server-side. A half-finished enrolment sitting in storage is voice audio
kept for no operational reason; this way the clips exist only for the duration
of the request that turns them into a centroid.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile, status

from app.api.deps import CurrentUserId, VoiceServiceDep
from app.core.errors import ValidationError
from app.models.voice import SpeakerMatch, VoiceStatus
from app.services.voice.enrolment import EnrolmentClip

router = APIRouter(prefix="/voice", tags=["voice"])


@router.get(
    "/status",
    response_model=VoiceStatus,
    summary="Whether a voiceprint is enrolled, and whether it can be trusted",
)
async def voice_status(
    service: VoiceServiceDep, user_id: CurrentUserId
) -> VoiceStatus:
    return await service.status(user_id)


@router.post(
    "/enrol",
    response_model=VoiceStatus,
    status_code=status.HTTP_201_CREATED,
    summary="Enrol the account holder's voice from several short clips",
)
async def enrol(
    service: VoiceServiceDep,
    user_id: CurrentUserId,
    files: Annotated[
        list[UploadFile], File(description="Three or more short clips")
    ],
    durations: Annotated[
        str, Form(description="Comma-separated seconds, one per clip")
    ],
) -> VoiceStatus:
    """Replaces any existing voiceprint.

    Durations come from the client because the server does not decode audio
    to measure it. They are validated for shape and sign but are the client's
    claim: a lying client only shortchanges its own enrolment quality, so
    there is nothing here worth the cost of a decoder.
    """
    try:
        parsed = [float(value) for value in durations.split(",") if value.strip()]
    except ValueError as exc:
        raise ValidationError(
            "`durations` must be comma-separated numbers of seconds."
        ) from exc

    if len(parsed) != len(files):
        raise ValidationError(
            f"Got {len(files)} clips and {len(parsed)} durations; "
            "they must match one to one."
        )
    if any(value <= 0 for value in parsed):
        raise ValidationError("Every clip duration must be greater than zero.")

    clips: list[EnrolmentClip] = []
    for upload, duration in zip(files, parsed, strict=True):
        audio = await upload.read()
        if not audio:
            raise ValidationError("One of the clips was empty.")
        clips.append(
            EnrolmentClip(
                audio,
                mime_type=upload.content_type or "audio/m4a",
                duration_s=duration,
            )
        )

    return await service.enrol(user_id, clips)


@router.post(
    "/check",
    response_model=SpeakerMatch,
    summary="Does this clip match the enrolled voice?",
)
async def check(
    service: VoiceServiceDep,
    user_id: CurrentUserId,
    file: Annotated[UploadFile, File(description="One short clip")],
) -> SpeakerMatch:
    """Lets the user verify the feature on themselves.

    Returns a verdict rather than an error for every failure case - not
    enrolled, wrong model, undecodable audio - because "we do not know" is
    the honest answer and is what the rest of the system already expects.
    """
    audio = await file.read()
    return await service.verify(
        user_id, audio, mime_type=file.content_type or "audio/m4a"
    )


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete the enrolled voiceprint",
)
async def forget(
    service: VoiceServiceDep, user_id: CurrentUserId
) -> Response:
    """Idempotent, so the app can offer the control unconditionally."""
    await service.forget(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
