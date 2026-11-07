"""§24.8 catalog, §24.9/§24.10 catalog changes, §26.6 onboarding Git drift —
`/projects/{id}/catalog*` and `/projects/{id}/onboarding/git-drift`. The index is
arranged directly (the worker's `catalog_refresh` job writes it in production)."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models

BASE = "/api/v1/projects/proj-a"
ROOT = "adf/artifacts"


def _entry(db: Session, path: str, doc: dict | str, *, artifact_type: str, name: str, refs=(), branch="dev") -> None:
    content = doc if isinstance(doc, str) else json.dumps(doc, indent=4) + "\n"
    db.add(models.GitCatalogEntry(
        project_id="proj-a", branch=branch, path=path, artifact_type=artifact_type, name=name,
        blob_sha=f"sha-{name}", content=content, references=[{"type": t, "name": n} for t, n in refs], parameters=[],
    ))


def _repo(db: Session) -> None:
    ls = {"name": "LS_sql", "properties": {"type": "AzureSqlDatabase"}}
    ds = {"name": "DS_orders", "properties": {"linkedServiceName": {"referenceName": "LS_sql", "type": "LinkedServiceReference"}}}
    _entry(db, f"{ROOT}/linkedService/LS_sql.json", ls, artifact_type="linked_service", name="LS_sql")
    _entry(db, f"{ROOT}/dataset/DS_orders.json", ds, artifact_type="dataset", name="DS_orders",
           refs=[("LinkedServiceReference", "LS_sql")])
    # Same file name as the seed pipeline's generated artifact (content "{}") -> tool-owned.
    _entry(db, f"{ROOT}/pipeline/pipeline-a.json", "{}", artifact_type="pipeline", name="pipeline-a")
    db.commit()


def test_refresh_enqueues_a_worker_job_and_never_stores_the_token(client: TestClient, seed, db: Session) -> None:
    res = client.post(f"{BASE}/catalog/refresh", json={"branch": "main", "gitToken": "ghp_secret"})
    assert res.status_code == 202, res.text
    job = db.scalars(select(models.Job).where(models.Job.type == "catalog_refresh")).one()
    assert job.payload["branch"] == "main" and job.payload["artifactRoot"] == "adf/artifacts/"
    assert job.payload["gitToken"] and "ghp_secret" not in job.payload["gitToken"]
    polled = client.get(f"{BASE}/jobs/{res.json()['id']}")
    assert polled.status_code == 200 and polled.json()["type"] == "catalog_refresh"


def test_list_resolves_ownership_used_by_and_reference_search(client: TestClient, seed, db: Session) -> None:
    _repo(db)
    out = client.get(f"{BASE}/catalog").json()
    assert out["branch"] == "dev" and out["branches"] == ["dev"]
    by_name = {e["name"]: e for e in out["entries"]}
    assert by_name["pipeline-a"]["ownership"]["state"] == "in_sync"
    assert by_name["pipeline-a"]["ownership"]["pipelineId"] == "pl-a"
    assert by_name["LS_sql"]["ownership"]["state"] == "foreign"
    assert by_name["LS_sql"]["usedByCount"] == 1
    # Search matches references, not just names.
    assert [e["name"] for e in client.get(f"{BASE}/catalog", params={"q": "ls_sql"}).json()["entries"]] == [
        "DS_orders", "LS_sql",
    ]
    assert [e["name"] for e in client.get(f"{BASE}/catalog", params={"type": "dataset"}).json()["entries"]] == ["DS_orders"]


def test_hand_edited_tool_file_is_drifted(client: TestClient, seed, db: Session) -> None:
    _entry(db, f"{ROOT}/pipeline/pipeline-a.json", {"name": "pipeline-a", "edited": True},
           artifact_type="pipeline", name="pipeline-a")
    db.commit()
    detail = client.get(f"{BASE}/catalog/artifact", params={"path": f"{ROOT}/pipeline/pipeline-a.json"}).json()
    assert detail["ownership"]["state"] == "drifted" and '"edited": true' in detail["content"]


def test_detail_and_impact_list_inbound_references(client: TestClient, seed, db: Session) -> None:
    _repo(db)
    path = f"{ROOT}/linkedService/LS_sql.json"
    detail = client.get(f"{BASE}/catalog/artifact", params={"path": path}).json()
    assert detail["usedBy"] == [{"path": f"{ROOT}/dataset/DS_orders.json", "type": "dataset", "name": "DS_orders"}]
    assert client.get(f"{BASE}/catalog/impact", params={"path": path}).json()["inbound"] == detail["usedBy"]
    assert client.get(f"{BASE}/catalog/artifact", params={"path": "nope.json"}).status_code == 404


def _change(client: TestClient, **body):
    return client.post(f"{BASE}/catalog/changes", json={"featureBranch": "feature/catalog", **body})


def test_update_validates_json_and_the_file_name_then_enqueues(client: TestClient, seed, db: Session) -> None:
    _repo(db)
    path = f"{ROOT}/linkedService/LS_sql.json"
    assert _change(client, mode="update", path=path, content="{nope").json()["error"]["code"] == "catalog_json_invalid"
    renamed = json.dumps({"name": "LS_other", "properties": {}})
    assert _change(client, mode="update", path=path, content=renamed).json()["error"]["code"] == "catalog_name_changed"
    assert _change(client, mode="update", path=path, content="{}", featureBranch="dev").json()["error"]["code"] == "protected_branch"

    edited = json.dumps({"name": "LS_sql", "properties": {"type": "AzureSqlDatabase", "description": "x"}}, indent=4)
    res = _change(client, mode="update", path=path, content=edited)
    assert res.status_code == 202, res.text
    change = db.get(models.GitCatalogChange, res.json()["id"])
    assert change.files == [{"path": path, "content": edited}] and change.base_blob_sha == "sha-LS_sql"
    job = db.scalars(select(models.Job).where(models.Job.type == "catalog_change")).one()
    assert job.payload["changeId"] == change.id
    assert [c["id"] for c in client.get(f"{BASE}/catalog/changes").json()] == [change.id]


def test_rename_guards_inbound_references_and_can_rewrite_them(client: TestClient, seed, db: Session) -> None:
    _repo(db)
    path = f"{ROOT}/linkedService/LS_sql.json"
    blocked = _change(client, mode="rename", path=path, newName="LS_warehouse")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "catalog_has_inbound_references"
    assert _change(client, mode="rename", path=path, newName="bad name").json()["error"]["code"] == "catalog_name_invalid"

    res = _change(client, mode="rename", path=path, newName="LS_warehouse", rewriteReferences=True)
    assert res.status_code == 202, res.text
    out = res.json()
    assert out["newPath"] == f"{ROOT}/linkedService/LS_warehouse.json"
    assert out["deletePaths"] == [path]
    change = db.get(models.GitCatalogChange, out["id"])
    files = {f["path"]: json.loads(f["content"]) for f in change.files}
    assert files[f"{ROOT}/linkedService/LS_warehouse.json"]["name"] == "LS_warehouse"
    assert files[f"{ROOT}/dataset/DS_orders.json"]["properties"]["linkedServiceName"]["referenceName"] == "LS_warehouse"
    # The rewritten file keeps the repo's 4-space indentation.
    assert '\n    "name"' in next(f["content"] for f in change.files if f["path"].endswith("DS_orders.json"))


def test_delete_requires_confirmation_when_referenced(client: TestClient, seed, db: Session) -> None:
    _repo(db)
    path = f"{ROOT}/linkedService/LS_sql.json"
    assert _change(client, mode="delete", path=path).status_code == 409
    res = _change(client, mode="delete", path=path, force=True)
    assert res.status_code == 202 and res.json()["deletePaths"] == [path] and res.json()["filePaths"] == []
    unreferenced = _change(client, mode="delete", path=f"{ROOT}/dataset/DS_orders.json")
    assert unreferenced.status_code == 202


def test_viewer_cannot_change_the_catalog(client: TestClient, seed, db: Session, as_user) -> None:
    _repo(db)
    as_user("VIEWER")
    assert client.get(f"{BASE}/catalog").status_code == 200
    assert _change(client, mode="delete", path=f"{ROOT}/dataset/DS_orders.json").status_code == 403


# --- §26.6 onboarding Git import + drift ---------------------------------------------

ROW = {"source_system": "erp", "source_type": "table", "target_type": "table",
       "target_file_format": "Parquet", "loading_behavior": "FullLoad"}
CSV_HEADER = "object_key,enabled,source_system,source_type,source_object,target_type,target_file_format,loading_behavior,notes"


def test_git_drift_reports_drifted_git_only_and_registry_only_rows(client: TestClient, seed, db: Session) -> None:
    rows = [{**ROW, "source_object": n} for n in ("customer", "orders", "invoices")]
    assert client.post(f"{BASE}/onboarding/objects/bulk", json={"objects": rows}).status_code == 200
    unavailable = client.get(f"{BASE}/onboarding/git-drift").json()
    assert unavailable["available"] is False and unavailable["indexed"] is False

    csv_text = "\r\n".join([
        CSV_HEADER,
        "erp.customer,TRUE,erp,table,customer,table,Parquet,FullLoad,",       # in sync
        "erp.orders,FALSE,erp,table,orders,table,Parquet,FullLoad,edited in git",  # drifted
        "erp.shipments,TRUE,erp,table,shipments,table,Parquet,FullLoad,",     # only in Git
    ]) + "\r\n"
    _entry(db, f"{ROOT}/metadata/objects.csv", csv_text, artifact_type="metadata", name="objects")
    db.commit()

    drift = client.get(f"{BASE}/onboarding/git-drift").json()
    assert drift["available"] is True and drift["inSync"] == 1
    by_key = {r["objectKey"]: r for r in drift["rows"]}
    assert by_key["erp.orders"]["status"] == "drifted"
    assert {f["field"] for f in by_key["erp.orders"]["fields"]} == {"enabled", "notes"}
    assert by_key["erp.shipments"]["status"] == "only_in_git"
    assert by_key["erp.shipments"]["gitRow"]["source_object"] == "shipments"
    assert by_key["erp.invoices"]["status"] == "only_in_registry"

    # Adopting Git's rows is a normal bulk upsert tagged git-import.
    adopt = [{**by_key[k]["gitRow"], "provenance": "git-import"} for k in ("erp.orders", "erp.shipments")]
    assert client.post(f"{BASE}/onboarding/objects/bulk", json={"objects": adopt}).status_code == 200
    objects = {o["object_key"]: o for o in client.get(f"{BASE}/onboarding/objects").json()}
    assert objects["erp.shipments"]["provenance"] == "git-import"
    assert objects["erp.orders"]["enabled"] is False and objects["erp.customer"]["provenance"] == "manual"
    after = client.get(f"{BASE}/onboarding/git-drift").json()
    assert [r["objectKey"] for r in after["rows"]] == ["erp.invoices"]


def test_provenance_is_validated(client: TestClient, seed) -> None:
    res = client.post(f"{BASE}/onboarding/objects", json={**ROW, "source_object": "x", "provenance": "carrier-pigeon"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "onboarding_provenance_invalid"
