"""Tests for per-project integrations (§29.1 layer-6 live check, Purview push)
and the project-scoped integration job poll endpoint."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models

P = "/api/v1/projects/proj-a"
LIVE = {
    "subscriptionId": "00000000-0000-0000-0000-000000000000",
    "resourceGroup": "rg-adf-sandbox",
    "location": "westeurope",
    "tenantId": "t", "clientId": "c", "clientSecretRef": "adf-live-check-sp",
}


def test_integration_defaults_then_roundtrip(client: TestClient, seed: dict[str, str]) -> None:
    empty = client.get(f"{P}/integrations/live_validation").json()
    assert empty["enabled"] is False and empty["config"]["resourceGroup"] == ""
    res = client.put(f"{P}/integrations/live_validation", json={"enabled": True, "config": LIVE})
    assert res.status_code == 200, res.text
    assert client.get(f"{P}/integrations/live_validation").json()["config"]["clientSecretRef"] == "adf-live-check-sp"


def test_integration_validation(client: TestClient, seed: dict[str, str]) -> None:
    def code(kind: str, enabled: bool, config: dict) -> str:
        return client.put(f"{P}/integrations/{kind}", json={"enabled": enabled, "config": config}).json()["error"]["code"]

    assert code("live_validation", True, {"resourceGroup": "rg"}) == "integration_incomplete"
    # A pasted client secret (contains `~`/`.`) is rejected — references only (rule 6).
    assert code("purview", False, {"tenantId": "t", "clientId": "c", "clientSecretRef": "Ab8Q~x.y"}) == "secret_reference_invalid"
    assert code("purview", False, {"clientSecretRef": "kv-ref"}) == "integration_auth_incomplete"
    assert code("purview", False, {"password": "x"}) == "integration_field_unknown"
    assert client.get(f"{P}/integrations/nope").json()["error"]["code"] == "integration_unknown"


def test_developer_cannot_configure(client: TestClient, seed: dict[str, str], as_user) -> None:
    as_user("DEVELOPER")
    assert client.get(f"{P}/integrations/purview").status_code == 200
    assert client.put(f"{P}/integrations/purview", json={"enabled": False, "config": {}}).status_code == 403


def test_live_check_requires_enabled_integration_then_enqueues(
    client: TestClient, seed: dict[str, str], db: Session
) -> None:
    body = {"pipelineId": "pl-a"}
    assert client.post(f"{P}/validate/live-check", json=body).json()["error"]["code"] == "integration_not_enabled"
    client.put(f"{P}/integrations/live_validation", json={"enabled": True, "config": LIVE})
    assert client.post(f"{P}/validate/live-check", json={"pipelineId": "nope"}).status_code == 404
    res = client.post(f"{P}/validate/live-check", json=body)
    assert res.status_code == 202, res.text
    job = res.json()
    assert job["type"] == "live_check" and job["status"] == "queued"
    assert db.get(models.Job, job["id"]).pipeline_id == "pl-a"
    assert client.get(f"{P}/jobs/{job['id']}").json()["status"] == "queued"


def test_purview_push_validates_lineage(client: TestClient, seed: dict[str, str]) -> None:
    lineage = {"jobs": [{"job": {"name": "bronze.x"}, "inputs": [], "outputs": []}]}
    assert client.post(f"{P}/lineage/purview-push", json={"lineage": lineage}).json()["error"]["code"] == "integration_not_enabled"
    client.put(f"{P}/integrations/purview", json={"enabled": True, "config": {"accountName": "contoso-purview"}})
    assert client.post(f"{P}/lineage/purview-push", json={"lineage": {"jobs": []}}).json()["error"]["code"] == "lineage_empty"
    res = client.post(f"{P}/lineage/purview-push", json={"lineage": lineage})
    assert res.status_code == 202 and res.json()["type"] == "purview_push"


def test_job_poll_is_project_scoped(client: TestClient, seed: dict[str, str], db: Session) -> None:
    db.add(models.Job(id="job-other", type="purview_push", status="queued", payload={"projectId": "proj-z"}))
    db.add(models.Job(id="job-gen", type="generate", status="queued", payload={"projectId": "proj-a"}))
    db.commit()
    assert client.get(f"{P}/jobs/job-other").status_code == 404
    assert client.get(f"{P}/jobs/job-gen").status_code == 404  # only integration job types are exposed here


def test_viewer_cannot_run_live_check(client: TestClient, seed: dict[str, str], as_user) -> None:
    as_user("VIEWER")
    assert client.post(f"{P}/validate/live-check", json={"pipelineId": "pl-a"}).status_code == 403
    assert client.post(f"{P}/lineage/purview-push", json={"lineage": {"jobs": [1]}}).status_code == 403
