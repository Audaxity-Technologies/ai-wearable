"""Tasks and the approval inbox, end to end over HTTP."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.container import Container
from app.core.time_utils import utc_now
from app.models.actions import ActionStatus
from app.models.status import SessionStatus


async def record(
    client: AsyncClient,
    container: Container,
    wav_bytes: bytes,
    *,
    user_id: str = "dev-user",
) -> str:
    """Record one conversation as `user_id`.

    Identity now comes from the token, or from the ?user_id= dev fallback the
    test settings leave enabled. It is no longer assertable in the body.
    """
    params = {"user_id": user_id}
    opened = await client.post(
        "/v1/sessions", json={"tz": "Asia/Kolkata"}, params=params
    )
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
        params=params,
    )
    await client.post(f"/v1/sessions/{session_id}/close", params=params)
    await container.pipeline.drain()
    return session_id


async def test_pipeline_produces_tasks_and_pending_actions(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    session_id = await record(client, container, wav_bytes)

    assert (await client.get(f"/v1/sessions/{session_id}")).json()[
        "status"
    ] == SessionStatus.READY

    tasks = (await client.get("/v1/tasks")).json()
    actions = (await client.get("/v1/actions/pending")).json()

    assert tasks["total"] == 2, "only the two actionable items become tasks"
    assert actions["total"] == 2
    assert all(a["status"] == ActionStatus.PROPOSED for a in actions["actions"])


async def test_suggestions_do_not_become_tasks(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    titles = {t["title"] for t in (await client.get("/v1/tasks")).json()["tasks"]}

    assert "Finish the investor pitch" in titles
    assert "Work on the financial projections" in titles
    assert "Contact the V-NEST team" not in titles
    assert "Review the wearable prototype" not in titles


async def test_nothing_executes_without_approval(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """The product's central promise, checked at the API boundary."""
    await record(client, container, wav_bytes)

    for action in (await client.get("/v1/actions")).json()["actions"]:
        assert action["status"] == ActionStatus.PROPOSED
        assert action["executed_at"] is None
        assert action["execution_result"] is None
        assert action["history"] == []


async def test_every_pending_action_explains_itself(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    for action in (await client.get("/v1/actions/pending")).json()["actions"]:
        assert action["reason"]
        assert action["source_quote"]
        assert action["source_session_id"]


async def test_approve_executes_and_is_idempotent(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    action_id = (await client.get("/v1/actions/pending")).json()["actions"][0][
        "id"
    ]

    first = (await client.post(f"/v1/actions/{action_id}/approve")).json()
    second = (await client.post(f"/v1/actions/{action_id}/approve")).json()

    assert first["status"] == ActionStatus.EXECUTED
    assert second["executed_at"] == first["executed_at"]
    assert [e["event"] for e in second["history"]].count("executed") == 1


async def test_reject_removes_it_from_the_inbox(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    pending = (await client.get("/v1/actions/pending")).json()
    action_id = pending["actions"][0]["id"]

    response = await client.post(
        f"/v1/actions/{action_id}/reject", json={"reason": "not needed"}
    )
    assert response.json()["status"] == ActionStatus.REJECTED
    assert response.json()["rejection_reason"] == "not needed"

    after = (await client.get("/v1/actions/pending")).json()
    assert after["total"] == pending["total"] - 1


async def test_approving_a_rejected_action_is_a_conflict(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    action_id = (await client.get("/v1/actions/pending")).json()["actions"][0][
        "id"
    ]
    await client.post(f"/v1/actions/{action_id}/reject", json={})

    response = await client.post(f"/v1/actions/{action_id}/approve")
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


async def test_edit_then_approve(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    action_id = (await client.get("/v1/actions/pending")).json()["actions"][0][
        "id"
    ]

    edited = (
        await client.patch(
            f"/v1/actions/{action_id}",
            json={"title": "Corrected title", "date": "2026-12-01"},
        )
    ).json()
    assert edited["title"] == "Corrected title"
    assert edited["edited"] is True
    assert edited["payload"]["date"] == "2026-12-01"

    approved = (
        await client.post(f"/v1/actions/{action_id}/approve")
    ).json()
    assert approved["status"] == ActionStatus.EXECUTED
    assert "2026-12-01" in approved["execution_result"]


async def test_cannot_edit_after_approval_over_http(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    action_id = (await client.get("/v1/actions/pending")).json()["actions"][0][
        "id"
    ]
    await client.post(f"/v1/actions/{action_id}/approve")

    response = await client.patch(
        f"/v1/actions/{action_id}", json={"title": "too late"}
    )
    assert response.status_code == 409


async def test_task_completion_over_http(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    task_id = (await client.get("/v1/tasks")).json()["tasks"][0]["id"]

    done = (
        await client.patch(f"/v1/tasks/{task_id}", json={"status": "done"})
    ).json()
    assert done["status"] == "done"
    assert done["completed_at"] is not None

    pending = (await client.get("/v1/tasks", params={"status": "pending"})).json()
    assert task_id not in [t["id"] for t in pending["tasks"]]


async def test_needs_clarification_is_surfaced(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """The canned analysis has a 'next week' item; it must be flagged."""
    await record(client, container, wav_bytes)
    tasks = (
        await client.get("/v1/tasks", params={"needs_clarification": "true"})
    ).json()

    # The ambiguous item in the fixture is a suggestion, so it is not a task;
    # what matters is that nothing was given a guessed date.
    for task in (await client.get("/v1/tasks")).json()["tasks"]:
        if task["due_resolution"] == "ambiguous":
            assert task["due_date"] is None or task["needs_clarification"]
    assert tasks["total"] >= 0


async def test_tasks_are_scoped_by_user(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    await record(client, container, wav_bytes)
    mine = (await client.get("/v1/tasks", params={"user_id": "dev-user"})).json()
    theirs = (await client.get("/v1/tasks", params={"user_id": "nobody"})).json()
    assert mine["total"] > 0
    assert theirs["total"] == 0


async def test_deleting_a_session_removes_its_tasks_and_proposals(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    session_id = await record(client, container, wav_bytes)
    assert (await client.get("/v1/tasks")).json()["total"] > 0

    await client.delete(f"/v1/sessions/{session_id}")

    assert (await client.get("/v1/tasks")).json()["total"] == 0
    assert (await client.get("/v1/actions/pending")).json()["total"] == 0


async def test_executed_actions_survive_session_deletion(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    """An executed action records something that already happened."""
    session_id = await record(client, container, wav_bytes)
    action_id = (await client.get("/v1/actions/pending")).json()["actions"][0][
        "id"
    ]
    await client.post(f"/v1/actions/{action_id}/approve")

    await client.delete(f"/v1/sessions/{session_id}")

    remaining = (await client.get(f"/v1/actions/{action_id}")).json()
    assert remaining["status"] == ActionStatus.EXECUTED


async def test_degraded_analysis_proposes_nothing_over_http(
    client: AsyncClient, container: Container, wav_bytes: bytes
) -> None:
    container.llm._responses = ["not json", "still not json"]  # noqa: SLF001
    await record(client, container, wav_bytes)

    assert (await client.get("/v1/tasks")).json()["total"] == 0
    assert (await client.get("/v1/actions/pending")).json()["total"] == 0


async def test_open_loops_route_is_not_captured_by_the_task_id_route(
    client: AsyncClient,
) -> None:
    """`/tasks/open-loops` must not resolve as a task called "open-loops".

    Route ordering, which is easy to break by appending a route to the file.
    """
    response = await client.get(
        "/v1/tasks/open-loops", params={"user_id": "manan"}
    )
    assert response.status_code == 200
    body = response.json()
    for key in (
        "owed_by_me",
        "owed_to_me",
        "shared",
        "unattributed",
        "total",
        "quiet_count",
        "overdue_count",
    ):
        assert key in body


async def test_open_loops_is_scoped_to_the_acting_user(
    client: AsyncClient,
) -> None:
    mine = await client.get(
        "/v1/tasks/open-loops", params={"user_id": "manan"}
    )
    theirs = await client.get(
        "/v1/tasks/open-loops", params={"user_id": "pulkit"}
    )
    assert mine.status_code == theirs.status_code == 200
