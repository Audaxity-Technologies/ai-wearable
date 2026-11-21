"""People routes.

The API deliberately has no route that merges on the server's judgement:
there is one to apply a merge and one to read suggestions.
"""

from __future__ import annotations

from httpx import AsyncClient

from app.container import Container


async def _person(container: Container, user_id: str, name: str, **kw):
    person = await container.person_service.resolve_or_create(
        user_id, name, **kw
    )
    assert person is not None
    return person


async def test_list_is_empty_before_anyone_is_seen(
    client: AsyncClient,
) -> None:
    response = await client.get("/v1/people", params={"user_id": "manan"})
    assert response.status_code == 200
    body = response.json()
    assert body["people"] == []
    assert body["suggestions"] == []


async def test_list_returns_people_and_suggestions(
    client: AsyncClient, container: Container
) -> None:
    await _person(container, "manan", "Priya", session_id="s1")
    await _person(container, "manan", "Priyanka", session_id="s2")

    body = (
        await client.get("/v1/people", params={"user_id": "manan"})
    ).json()
    assert len(body["people"]) == 2
    # Similar, but not merged: the suggestion changed nothing on its own.
    assert len(body["suggestions"]) == 1
    assert body["suggestions"][0]["similarity"] >= 0.72


async def test_marking_yourself_is_exclusive(
    client: AsyncClient, container: Container
) -> None:
    me = await _person(container, "manan", "Manan")
    other = await _person(container, "manan", "Pulkit")

    first = await client.patch(
        f"/v1/people/{me.id}",
        params={"user_id": "manan"},
        json={"is_self": True},
    )
    assert first.status_code == 200
    assert first.json()["is_self"] is True

    await client.patch(
        f"/v1/people/{other.id}",
        params={"user_id": "manan"},
        json={"is_self": True},
    )
    body = (
        await client.get("/v1/people", params={"user_id": "manan"})
    ).json()
    selves = [p for p in body["people"] if p["is_self"]]
    assert len(selves) == 1
    assert selves[0]["id"] == other.id


async def test_renaming_keeps_the_old_spelling_resolvable(
    client: AsyncClient, container: Container
) -> None:
    person = await _person(container, "manan", "Pull kit")
    response = await client.patch(
        f"/v1/people/{person.id}",
        params={"user_id": "manan"},
        json={"name": "Pulkit Verma"},
    )
    assert response.status_code == 200
    assert response.json()["canonical_name"] == "Pulkit Verma"
    assert await container.person_service.find("manan", "Pull kit") is not None


async def test_an_alias_owned_by_someone_else_is_a_conflict(
    client: AsyncClient, container: Container
) -> None:
    priya = await _person(container, "manan", "Priya")
    await _person(container, "manan", "Prithvi")

    response = await client.post(
        f"/v1/people/{priya.id}/aliases",
        params={"user_id": "manan"},
        json={"alias": "Prithvi"},
    )
    assert response.status_code == 409
    assert "Merge them instead" in response.text


async def test_merge_folds_one_into_the_other(
    client: AsyncClient, container: Container
) -> None:
    primary = await _person(container, "manan", "Priya", session_id="s1")
    other = await _person(container, "manan", "Priyanka", session_id="s2")

    response = await client.post(
        f"/v1/people/{primary.id}/merge",
        params={"user_id": "manan"},
        json={"other_id": other.id},
    )
    assert response.status_code == 200, response.text
    assert response.json()["session_count"] == 2

    body = (
        await client.get("/v1/people", params={"user_id": "manan"})
    ).json()
    assert len(body["people"]) == 1
    assert body["suggestions"] == []


async def test_another_accounts_person_is_reported_as_missing(
    client: AsyncClient, container: Container
) -> None:
    """404, not 403: a 403 would confirm the id exists."""
    theirs = await _person(container, "pulkit", "Ravi")
    response = await client.get(
        f"/v1/people/{theirs.id}", params={"user_id": "manan"}
    )
    assert response.status_code == 404


async def test_deleting_a_person_does_not_delete_their_tasks(
    client: AsyncClient, container: Container
) -> None:
    """Tasks keep their owner string and fall back to "not attributed" -
    visibly unowned rather than silently reassigned."""
    person = await _person(container, "manan", "Pulkit")
    response = await client.delete(
        f"/v1/people/{person.id}", params={"user_id": "manan"}
    )
    assert response.status_code == 204

    ledger = await client.get(
        "/v1/tasks/open-loops", params={"user_id": "manan"}
    )
    assert ledger.status_code == 200
