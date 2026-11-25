"""Utterance routes: memory phase C.

Deliberately a **filter**, not a search. Phase D builds the parser that
decides which filter a question implies; phase F adds embeddings as a ranker
inside a filtered set. Shipping the filter first is what keeps a wrong answer
visibly wrong rather than plausibly wrong.

`total` is returned alongside the page so a caller can tell "nothing matched"
from "a great deal matched" - which is the difference between answering
honestly and answering confidently.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUserId, SessionServiceDep, UtteranceServiceDep
from app.models.analysis import SpeechAct
from app.models.utterances import UtteranceList, UtteranceResponse

router = APIRouter(prefix="/utterances", tags=["utterances"])

MAX_LIMIT = 500


@router.get(
    "",
    response_model=UtteranceList,
    summary="Filter utterances by time, place, act or text",
)
async def list_utterances(
    service: UtteranceServiceDep,
    user_id: CurrentUserId,
    since: Annotated[dt.datetime | None, Query()] = None,
    until: Annotated[dt.datetime | None, Query()] = None,
    act: Annotated[SpeechAct | None, Query()] = None,
    actionable_only: Annotated[bool, Query()] = False,
    place: Annotated[str | None, Query()] = None,
    speaker_person_id: Annotated[str | None, Query()] = None,
    contains: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
) -> UtteranceList:
    """The shape phase D will drive.

    "What did we decide at the office last Tuesday" is
    `act=decision&place=office&since=...&until=...` - a filter over sentences,
    which is what the session-grained retrieval could never express.
    """
    rows, total = await service.query(
        user_id=user_id,
        since=since,
        until=until,
        act=act,
        actionable_only=actionable_only,
        place=place,
        speaker_person_id=speaker_person_id,
        contains=contains,
        limit=limit,
    )
    return UtteranceList(
        utterances=[UtteranceResponse.from_domain(row) for row in rows],
        total=total,
    )


@router.get(
    "/by-session/{session_id}",
    response_model=UtteranceList,
    summary="One conversation's utterances, in order",
)
async def utterances_for_session(
    session_id: str,
    service: UtteranceServiceDep,
    sessions: SessionServiceDep,
    user_id: CurrentUserId,
) -> UtteranceList:
    """Ownership is checked through the session, which 404s for someone
    else's id rather than confirming it exists."""
    await sessions.get_session(session_id, user_id=user_id)
    rows = await service.for_session(session_id)
    return UtteranceList(
        utterances=[UtteranceResponse.from_domain(row) for row in rows],
        total=len(rows),
    )
