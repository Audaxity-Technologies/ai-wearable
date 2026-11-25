"""Phase D routes: question in, filter and evidence out.

Two routes on purpose. `/questions/parse` returns only the plan, so the
filter a question produced can be inspected without running it - which is how
you debug a wrong answer, and how the retrieval eval set (phase G) will assert
on resolution separately from results.

This is deliberately **not** `/ask`. Ask composes prose with citations; this
returns the resolved filter and the raw spans. Keeping them apart is what
stops a generated paragraph being mistaken for evidence.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Query

from app.api.deps import AuthServiceDep, CurrentUserId, QuestionParserDep
from app.models.questions import QuestionAnswer, ResolvedQuestion

router = APIRouter(prefix="/questions", tags=["questions"])


async def _timezone(auth: AuthServiceDep, user_id: str) -> str:
    """The user's own zone, which decides what "yesterday" means.

    Resolving a day boundary in UTC would answer about the wrong day for
    anyone east of Greenwich for part of every evening.
    """
    try:
        return (await auth.get_user(user_id)).tz
    except Exception:
        return "UTC"


@router.post(
    "/parse",
    response_model=ResolvedQuestion,
    summary="Show the filter a question resolves to, without running it",
)
async def parse_question(
    parser: QuestionParserDep,
    auth: AuthServiceDep,
    user_id: CurrentUserId,
    text: Annotated[str, Body(embed=True)],
) -> ResolvedQuestion:
    return await parser.parse(
        text, user_id=user_id, tz=await _timezone(auth, user_id)
    )


@router.post(
    "",
    response_model=QuestionAnswer,
    summary="Resolve a question and return the matching utterances",
)
async def ask_question(
    parser: QuestionParserDep,
    auth: AuthServiceDep,
    user_id: CurrentUserId,
    text: Annotated[str, Body(embed=True)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> QuestionAnswer:
    """Returns a verdict in every case, including the refusals.

    An empty `utterances` list is never silently empty: `verdict` says
    whether the question needs clarifying, could not be understood, would
    have searched everything, or simply matched nothing.
    """
    return await parser.answer(
        text,
        user_id=user_id,
        tz=await _timezone(auth, user_id),
        limit=limit,
    )
