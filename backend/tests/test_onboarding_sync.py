"""Tests for §26.5 Onboarding "Sync to Git" — `/projects/{id}/onboarding/sync-to-git`,
`/onboarding/syncs`, `/onboarding/sync-diff`."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models

BASE = "/api/v1/projects/proj-a/onboarding"
ROW = {
    "source_system": "erp", "source_type": "table", "target_type": "table",
    "target_file_format": "Parquet", "loading_behavior": "FullLoad",
}


def _seed_objects(client: TestClient, *names: str) -> list[str]:
    rows = [{**ROW, "source_object": n} for n in names]
    assert client.post(f"{BASE}/objects/bulk", json={"objects": rows}).status_code == 200
    return [o["object_key"] for o in client.get(f"{BASE}/objects").json()]


def _body(keys: list[str], **overrides) -> dict:
    return {
        "files": [
            {"name": "adf_control_table.sql", "content": "CREATE TABLE control.adf_control_table (...);"},
            {"name": "objects.csv", "content": "object_key\r\n"},
            {"name": "registry-lineage.json", "content": "{}"},
        ],
        "objectKeys": keys,
        "featureBranch": "feature/onboarding-sync",
        "gitToken": "ghp_not-stored",
        **overrides,
    }


def test_sync_enqueues_job_with_pinned_paths_and_encrypted_token(
    client: TestClient, seed: dict[str, str], db: Session
) -> None:
    keys = _seed_objects(client, "customer", "orders")
    res = client.post(f"{BASE}/sync-to-git", json=_body(keys))
    assert res.status_code == 202, res.text
    out = res.json()
    assert out["status"] == "pending"
    assert out["objectCount"] == 2
    assert out["baseBranch"] == "dev"  # project gitDefaults
    assert out["filePaths"] == [
        "adf/artifacts/metadata/adf_control_table.sql",
        "adf/artifacts/metadata/objects.csv",
        "adf/artifacts/metadata/registry-lineage.json",
    ]
    job = db.scalars(select(models.Job).where(models.Job.type == "onboarding_sync")).one()
    assert job.payload["syncId"] == out["id"]
    assert job.pipeline_id is None
    assert job.payload["gitToken"] and "ghp_not-stored" not in job.payload["gitToken"]
    assert "ghp_not-stored" not in str(client.get(f"{BASE}/syncs").json())


def test_sync_guards(client: TestClient, seed: dict[str, str]) -> None:
    assert client.post(f"{BASE}/sync-to-git", json=_body([])).json()["error"]["code"] == "no_objects"
    keys = _seed_objects(client, "customer")
    code = lambda **kw: client.post(f"{BASE}/sync-to-git", json=_body(keys, **kw)).json()["error"]["code"]  # noqa: E731
    assert code(featureBranch="dev") == "protected_branch"
    assert code(objectKeys=["someone-else"]) == "registry_changed"
    assert code(files=[{"name": "../evil.sh", "content": "x"}]) == "sync_file_not_allowed"
    assert code(files=[{"name": "objects.csv", "content": "x"}]) == "sync_file_missing"


def test_diff_against_last_successful_sync(client: TestClient, seed: dict[str, str], db: Session) -> None:
    keys = _seed_objects(client, "customer", "orders")
    first = client.get(f"{BASE}/sync-diff").json()
    assert first["baseSyncId"] is None and first["added"] == sorted(keys)

    sync_id = client.post(f"{BASE}/sync-to-git", json=_body(keys)).json()["id"]
    # Pending/failed syncs are never a diff base.
    assert client.get(f"{BASE}/sync-diff").json()["baseSyncId"] is None
    db.get(models.OnboardingSync, sync_id).status = "pr_created"
    db.commit()

    customer = next(k for k in keys if "customer" in k)
    orders = next(k for k in keys if "orders" in k)
    obj = client.get(f"{BASE}/objects").json()
    edited = next(o for o in obj if o["object_key"] == customer)
    assert client.put(f"{BASE}/objects/{customer}", json={**edited, "notes": "changed"}).status_code == 200
    client.delete(f"{BASE}/objects/{orders}?hard=true")
    _seed_objects(client, "invoice")

    diff = client.get(f"{BASE}/sync-diff").json()
    assert diff["baseSyncId"] == sync_id
    assert diff["removed"] == [orders]
    assert len(diff["added"]) == 1 and "invoice" in diff["added"][0]
    assert diff["changed"] == [{"objectKey": customer, "fields": ["notes"]}]


def test_viewer_cannot_sync_and_foreign_team_is_forbidden(
    client: TestClient, seed: dict[str, str], as_user, as_foreign_user
) -> None:
    assert client.get(f"{BASE}/syncs").status_code == 403  # as_foreign_user won the override


def test_viewer_can_read_but_not_sync(client: TestClient, seed: dict[str, str], as_user) -> None:
    keys = _seed_objects(client, "customer")
    as_user("VIEWER")
    assert client.get(f"{BASE}/sync-diff").status_code == 200
    assert client.post(f"{BASE}/sync-to-git", json=_body(keys)).status_code == 403
