"""Tests for participant labelling.

The feature's value is that it is **certain**: a voiceprint is probabilistic
and an extracted owner is the model's reading of the words, while "Pulkit was
here" is a fact only the user has.

Its risk is overstating that. A label records presence, not who spoke which
sentence, and nothing here may quietly turn one into the other.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.container import Container
from app.core.time_utils import utc_now


async def _record(client: AsyncClient, user_id: str = "dev-user") -> str:
    params = {"user_id": user_id}
    session_id = (
        await client.post(
            "/v1/sessions", json={"tz": "Asia/Kolkata"}, params=params
        )
    ).json()["id"]
    await client.post(
        "/v1/ingest/audio",
        data={
            "segment_id": str(uuid.uuid4()),
            "session_id": session_id,
            "seq": "0",
            "started_at": utc_now().isoformat(),
            "duration_s": "20.0",
        },
        files={"file": ("seg0.wav", b"0" * 4096, "audio/wav")},
        params=params,
    )
    await client.post(f"/v1/sessions/{session_id}/close", params=params)
    return session_id


async def _add(
    client: AsyncClient, session_id: str, name: str, user: str = "dev-user"
):
    return await client.post(
        f"/v1/sessions/{session_id}/participants",
        params={"user_id": user},
        json={"name": name},
    )


# --- labelling ----------------------------------------------------------


async def test_labelling_a_past_conversation_creates_the_person(
    client: AsyncClient, container: Container
) -> None:
    """After the fact, which is the point."""
    session_id = await _record(client)
    response = await _add(client, session_id, "Pulkit")
    assert response.status_code == 200

    participants = response.json()["participants"]
    assert [entry["name"] for entry in participants] == ["Pulkit"]
    assert participants[0]["source"] == "user"
    assert participants[0]["role"] == "spoke"

    # The fake analysis also names owners, so the index holds people this
    # label did not create. Assert on the one that matters.
    person = await container.person_service.find("dev-user", "Pulkit")
    assert person is not None
    assert person.canonical_name == "Pulkit"


async def test_a_misheard_name_lands_on_the_same_person(
    client: AsyncClient, container: Container
) -> None:
    """The label gets phase B's alias matching for free."""
    first = await _record(client)
    second = await _record(client)
    await _add(client, first, "Pulkit")
    await _add(client, second, "Pull kit")

    pulkit = await container.person_service.find("dev-user", "Pulkit")
    pull_kit = await container.person_service.find("dev-user", "Pull kit")
    assert pulkit is not None and pull_kit is not None
    assert pulkit.id == pull_kit.id, "the mis-hearing split into two people"
    assert {first, second} <= set(pulkit.source_session_ids)


async def test_the_relationship_accumulates_across_conversations(
    client: AsyncClient, container: Container
) -> None:
    """Label three conversations and the person knows about all three, which
    is what makes the next question about them search all three."""
    ids = [await _record(client) for _ in range(3)]
    for session_id in ids:
        await _add(client, session_id, "Priya")

    person = await container.person_service.find("dev-user", "Priya")
    assert person is not None
    assert sorted(person.source_session_ids) == sorted(ids)

    found = await container.session_service.sessions_with(
        user_id="dev-user", person_id=person.id
    )
    assert sorted(found) == sorted(ids)


async def test_labelling_twice_does_not_duplicate(client: AsyncClient) -> None:
    session_id = await _record(client)
    await _add(client, session_id, "Pulkit")
    body = (await _add(client, session_id, "Pulkit")).json()
    assert len(body["participants"]) == 1


async def test_a_sentinel_cannot_be_a_participant(
    client: AsyncClient,
) -> None:
    """Refusing loudly beats creating a participant called "someone"."""
    session_id = await _record(client)
    for name in ("unknown", "someone", "he"):
        response = await _add(client, session_id, name)
        assert response.status_code == 422, name


async def test_removing_a_label_leaves_the_person_and_other_sessions(
    client: AsyncClient, container: Container
) -> None:
    first = await _record(client)
    second = await _record(client)
    await _add(client, first, "Pulkit")
    await _add(client, second, "Pulkit")
    person = await container.person_service.find("dev-user", "Pulkit")
    assert person is not None

    response = await client.delete(
        f"/v1/sessions/{first}/participants/{person.id}",
        params={"user_id": "dev-user"},
    )
    assert response.status_code == 200
    assert response.json()["participants"] == []

    # The person survives, and so does the other conversation's label.
    assert await container.person_service.find("dev-user", "Pulkit") is not None
    remaining = await container.session_service.sessions_with(
        user_id="dev-user", person_id=person.id
    )
    assert remaining == [second]


async def test_another_users_session_is_reported_as_missing(
    client: AsyncClient,
) -> None:
    session_id = await _record(client, user_id="pulkit")
    response = await _add(client, session_id, "Ravi", user="manan")
    assert response.status_code == 404


async def test_marking_yourself_uses_the_self_role(
    client: AsyncClient, container: Container
) -> None:
    """So the own/owed split does not re-derive it on every query."""
    session_id = await _record(client)
    me = await container.person_service.resolve_or_create("dev-user", "Manan")
    assert me is not None
    await container.person_service.set_self(me.id, True)

    body = (await _add(client, session_id, "Manan")).json()
    assert body["participants"][0]["role"] == "self"


# --- it must not overstate what a label means ---------------------------


async def test_a_label_does_not_assign_a_speaker_to_any_utterance(
    client: AsyncClient, container: Container
) -> None:
    """Presence is not authorship.

    A three-person conversation labelled with three people says all three
    were there, and nothing more.
    """
    session_id = await _record(client)
    await _add(client, session_id, "Pulkit")

    rows = await container.utterances_repo.list(session_id=session_id)
    assert rows
    assert all(row.speaker_person_id is None for row in rows)


async def test_a_label_does_not_assign_unowned_tasks(
    client: AsyncClient, container: Container
) -> None:
    """Someone in the room committing to something is not this person
    committing to it."""
    session_id = await _record(client)
    await _add(client, session_id, "Pulkit")

    tasks = await container.task_service.list_tasks(user_id="dev-user")
    for task in tasks:
        if task.owner.lower() in ("unknown", "group"):
            assert task.owner_person_id is None


def test_the_question_filter_says_presence_not_authorship() -> None:
    """The words the user reads must not overstate the evidence."""
    from app.models.questions import ResolvedQuestion

    resolved = ResolvedQuestion(text="q", person_names=["Pulkit"])
    described = resolved.describe()
    assert "in conversations with Pulkit" in described
    assert "by Pulkit" not in described


async def test_a_person_with_no_labelled_conversation_says_so(
    client: AsyncClient, container: Container
) -> None:
    """An honest empty that tells the user what to do about it, rather than
    silently searching everything."""
    await _record(client)
    await container.person_service.resolve_or_create("dev-user", "Pulkit")

    answer = await container.question_parser.answer(
        "what did Pulkit promise", user_id="dev-user", tz="Asia/Kolkata"
    )
    assert answer.total == 0
    assert "no conversation is labelled" in answer.verdict.lower()
    assert "say who was there" in answer.verdict.lower()
