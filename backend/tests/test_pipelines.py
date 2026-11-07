"""Pipeline endpoint tests (AGENTS.md §7, §14, §16, §23.12 gap #20).

`generate`/`validate` hand off to a `Job` the (nonexistent, in this suite)
worker would normally process; `wait_for_job` is monkeypatched to return
immediately instead of blocking for its full timeout waiting on a job that
will never be claimed.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _no_worker_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.pipelines.wait_for_job", lambda *a, **k: None)


def test_create_pipeline_requires_active_template(client: TestClient, seed: dict[str, str]) -> None:
    res = client.post(
        "/api/v1/pipelines", json={"name": "p", "projectId": seed["project_id"], "templateId": "no-such-template"}
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "template_invalid"


def test_create_get_and_list_pipeline(client: TestClient, seed: dict[str, str], persist_actor) -> None:
    from tests.conftest import ROLE_USERS

    persist_actor(ROLE_USERS["PLATFORM_ADMIN"])
    created = client.post(
        "/api/v1/pipelines",
        json={"name": "orders-daily", "projectId": seed["project_id"], "templateId": seed["template_id"]},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["state"] == "DRAFT"
    # Tracking metadata (§23.5): who created it and when, resolved to a
    # human-readable name rather than the raw `User.id`.
    assert body["createdBy"] == "u-admin"
    assert body["createdByName"] == "Test Admin"
    assert body["createdAt"]
    assert body["updatedBy"] is None

    fetched = client.get(f"/api/v1/pipelines/{body['id']}")
    assert fetched.status_code == 200

    listed = client.get("/api/v1/pipelines", params={"project_id": seed["project_id"]})
    assert body["id"] in {p["id"] for p in listed.json()}


def test_pipeline_draft_round_trips_through_get_and_put(client: TestClient, seed: dict[str, str]) -> None:
    """The Build wizard's edit mode (append objects / rename master+child) hydrates from this —
    `spec` alone (the reduced LogicalPipelineSpec) can't round-trip back into a wizard draft."""
    created = client.post(
        "/api/v1/pipelines",
        json={
            "name": "orders-draft-roundtrip",
            "projectId": seed["project_id"],
            "templateId": seed["template_id"],
            "draft": {"sourceType": "sql_server", "curatedEntity": "orders"},
        },
    )
    assert created.status_code == 201
    pipeline_id = created.json()["id"]
    # `create_pipeline`/`update_pipeline` (app/services.py) also stamp name/projectId/templateId
    # onto the stored draft for self-consistency — assert the sent fields round-trip, not exact
    # equality, since that stamping is a deliberate, unrelated existing behavior.
    created_draft = created.json()["draft"]
    assert created_draft["sourceType"] == "sql_server"
    assert created_draft["curatedEntity"] == "orders"

    fetched = client.get(f"/api/v1/pipelines/{pipeline_id}")
    assert fetched.json()["draft"] == created_draft

    updated = client.put(
        f"/api/v1/pipelines/{pipeline_id}",
        json={"draft": {"sourceType": "sql_server", "curatedEntity": "orders_v2"}},
    )
    assert updated.status_code == 200
    assert updated.json()["draft"]["curatedEntity"] == "orders_v2"


def test_update_pipeline_rejects_a_stale_expected_updated_at(client: TestClient, seed: dict[str, str]) -> None:
    """§24.14 — a save built on a copy someone else has since changed 409s instead of overwriting."""
    url = f"/api/v1/pipelines/{seed['pipeline_id']}"
    loaded = client.get(url).json()["updatedAt"]

    first = client.put(url, json={"draft": {"curatedEntity": "a"}, "expectedUpdatedAt": loaded})
    assert first.status_code == 200
    stale = client.put(url, json={"draft": {"curatedEntity": "b"}, "expectedUpdatedAt": loaded})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"
    assert client.get(url).json()["draft"]["curatedEntity"] == "a"
    # Omitting it keeps last-write-wins.
    assert client.put(url, json={"draft": {"curatedEntity": "c"}}).status_code == 200


def test_update_pipeline_resets_state_to_draft(client: TestClient, seed: dict[str, str]) -> None:
    assert client.get(f"/api/v1/pipelines/{seed['pipeline_id']}").json()["state"] == "GENERATED"

    res = client.put(f"/api/v1/pipelines/{seed['pipeline_id']}", json={"name": "renamed-pipeline"})
    assert res.status_code == 200
    assert res.json()["name"] == "renamed-pipeline"
    assert res.json()["state"] == "DRAFT"


def test_update_pipeline_tracks_who_edited_it_and_audits_the_change(
    client: TestClient, seed: dict[str, str], persist_actor
) -> None:
    from tests.conftest import ROLE_USERS

    persist_actor(ROLE_USERS["PLATFORM_ADMIN"])
    res = client.put(f"/api/v1/pipelines/{seed['pipeline_id']}", json={"name": "renamed-pipeline"})
    assert res.status_code == 200
    body = res.json()
    assert body["updatedBy"] == "u-admin"
    assert body["updatedByName"] == "Test Admin"
    # `created_by` (the original creator) is untouched by an edit.
    assert body["createdBy"] == "u-admin"

    audit = client.get("/api/v1/admin/audit").json()
    entry = next(e for e in audit if e["action"] == "pipeline.updated")
    assert entry["target"] == "renamed-pipeline"
    assert entry["actorName"] == "Test Admin"


def test_generate_and_validate_enqueue_a_job(client: TestClient, seed: dict[str, str]) -> None:
    generated = client.post(f"/api/v1/pipelines/{seed['pipeline_id']}/generate")
    assert generated.status_code == 200

    validated = client.post(f"/api/v1/pipelines/{seed['pipeline_id']}/validate")
    assert validated.status_code == 200


def test_validation_result_pending_with_no_job_yet(client: TestClient, seed: dict[str, str]) -> None:
    res = client.get(f"/api/v1/pipelines/{seed['pipeline_id']}/validation")
    assert res.status_code == 200
    assert res.json()["state"] == "pending"
    assert res.json()["issues"] == []


def test_commit_pushes_using_project_git_defaults(client: TestClient, seed: dict[str, str]) -> None:
    res = client.post(f"/api/v1/pipelines/{seed['pipeline_id']}/commit")
    assert res.status_code == 200
    assert res.json()["state"] == "PUSHING"


def test_unknown_pipeline_404s(client: TestClient) -> None:
    assert client.get("/api/v1/pipelines/nope").status_code == 404
    assert client.post("/api/v1/pipelines/nope/generate").status_code == 404
