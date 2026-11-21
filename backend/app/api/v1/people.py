"""People routes.

The shape of this API is the two-tier policy from `models/people.py`:
there is a route to **apply** a merge and a route to **see suggested** ones,
and nothing that merges on the server's own judgement. A wrong merge silently
reassigns someone's commitments and is close to impossible to spot afterwards;
an unmerged duplicate is visible and one tap to fix.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Response, status

from app.api.deps import CurrentUserId, PersonServiceDep
from app.models.people import PeopleList, PersonSummary

router = APIRouter(prefix="/people", tags=["people"])


@router.get(
    "",
    response_model=PeopleList,
    summary="Everyone seen in your conversations, plus possible duplicates",
)
async def list_people(
    service: PersonServiceDep, user_id: CurrentUserId
) -> PeopleList:
    people = await service.list_people(user_id)
    return PeopleList(
        people=[PersonSummary.from_domain(person) for person in people],
        suggestions=await service.suggestions(user_id),
    )


@router.get("/{person_id}", response_model=PersonSummary)
async def get_person(
    person_id: str, service: PersonServiceDep, user_id: CurrentUserId
) -> PersonSummary:
    return PersonSummary.from_domain(
        await service.get(person_id, user_id=user_id)
    )


@router.post(
    "/{person_id}/aliases",
    response_model=PersonSummary,
    summary="Teach another spelling for this person",
)
async def add_alias(
    person_id: str,
    service: PersonServiceDep,
    user_id: CurrentUserId,
    alias: Annotated[str, Body(embed=True)],
) -> PersonSummary:
    """409 when the alias already belongs to somebody else.

    Two people answering to one name would make the resolver depend on
    iteration order, which is how attribution silently becomes a coin flip.
    """
    return PersonSummary.from_domain(
        await service.add_alias(person_id, alias, user_id=user_id)
    )


@router.delete(
    "/{person_id}/aliases/{alias}",
    response_model=PersonSummary,
    summary="Forget a spelling",
)
async def remove_alias(
    person_id: str,
    alias: str,
    service: PersonServiceDep,
    user_id: CurrentUserId,
) -> PersonSummary:
    return PersonSummary.from_domain(
        await service.remove_alias(person_id, alias, user_id=user_id)
    )


@router.patch(
    "/{person_id}",
    response_model=PersonSummary,
    summary="Rename, or mark as yourself",
)
async def update_person(
    person_id: str,
    service: PersonServiceDep,
    user_id: CurrentUserId,
    name: Annotated[str | None, Body(embed=True)] = None,
    is_self: Annotated[bool | None, Body(embed=True)] = None,
) -> PersonSummary:
    """`is_self` is the single most useful thing the user can tell us.

    It decides the own/owed split in the open-loops ledger for every past and
    future task, and it is information only they have - so it is never
    inferred from a name.
    """
    person = await service.get(person_id, user_id=user_id)
    if name is not None:
        person = await service.rename(person_id, name, user_id=user_id)
    if is_self is not None:
        person = await service.set_self(person_id, is_self, user_id=user_id)
    return PersonSummary.from_domain(person)


@router.post(
    "/{person_id}/merge",
    response_model=PersonSummary,
    summary="Fold another person into this one",
)
async def merge_people(
    person_id: str,
    service: PersonServiceDep,
    user_id: CurrentUserId,
    other_id: Annotated[str, Body(embed=True)],
) -> PersonSummary:
    """Nothing is discarded: names become aliases, sessions and mentions are
    added, and the absorbed id keeps resolving through `merged_from`."""
    return PersonSummary.from_domain(
        await service.merge(person_id, other_id, user_id=user_id)
    )


@router.delete(
    "/{person_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a person",
)
async def delete_person(
    person_id: str, service: PersonServiceDep, user_id: CurrentUserId
) -> Response:
    """Deletes the identity, not the conversations.

    Tasks keep their `owner` string and simply stop resolving to a person,
    which puts them back in the ledger's "not attributed" bucket - visibly
    unowned rather than silently reassigned.
    """
    await service.delete(person_id, user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
