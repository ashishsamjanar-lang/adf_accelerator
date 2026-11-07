"""Tests for `/api/v1/projects/{id}/onboarding/objects` (AGENTS.md §26.7).

Backend counterpart to `lib/adf/onboarding-store.ts` — the O1-phase Bronze
registry finally getting a real Postgres-backed table (previously
localStorage-only; see `models.OnboardingObject`'s docstring).
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _valid_payload(**overrides):
    body = {
        "source_system": "erp",
        "source_object": "customer",
        "source_type": "table",
        "target_type": "table",
        "target_file_format": "Parquet",
        "loading_behavior": "FullLoad",
    }
    body.update(overrides)
    return body


def test_create_requires_source_system_and_object(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post("/api/v1/projects/proj-a/onboarding/objects", json=_valid_payload(source_object=""))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "onboarding_source_object_required"


def test_create_and_get_roundtrip_defaults_object_key(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post("/api/v1/projects/proj-a/onboarding/objects", json=_valid_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["object_key"] == "erp.customer"
    assert body["version"] == 1
    assert body["status"] == "draft"

    got = client.get("/api/v1/projects/proj-a/onboarding/objects/erp.customer")
    assert got.status_code == 200
    assert got.json()["source_object"] == "customer"


def test_delta_load_requires_watermark_column(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/projects/proj-a/onboarding/objects",
        json=_valid_payload(loading_behavior="DeltaLoad"),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "onboarding_watermark_required"

    resp = client.post(
        "/api/v1/projects/proj-a/onboarding/objects",
        json=_valid_payload(loading_behavior="DeltaLoad", watermark_column="updated_at"),
    )
    assert resp.status_code == 201, resp.text


def test_duplicate_object_key_conflicts(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post("/api/v1/projects/proj-a/onboarding/objects", json=_valid_payload())
    assert resp.status_code == 201
    dupe = client.post("/api/v1/projects/proj-a/onboarding/objects", json=_valid_payload())
    assert dupe.status_code == 409
    assert dupe.json()["error"]["code"] == "object_exists"


def test_update_bumps_version(client: TestClient, seed: dict[str, str]) -> None:
    client.post("/api/v1/projects/proj-a/onboarding/objects", json=_valid_payload())
    upd = client.put(
        "/api/v1/projects/proj-a/onboarding/objects/erp.customer",
        json={"owner": "finance-team"},
    )
    assert upd.status_code == 200, upd.text
    body = upd.json()
    assert body["owner"] == "finance-team"
    assert body["version"] == 2


def test_soft_delete_disables_row(client: TestClient, seed: dict[str, str]) -> None:
    client.post("/api/v1/projects/proj-a/onboarding/objects", json=_valid_payload())
    resp = client.delete("/api/v1/projects/proj-a/onboarding/objects/erp.customer")
    assert resp.status_code == 204
    got = client.get("/api/v1/projects/proj-a/onboarding/objects/erp.customer")
    assert got.status_code == 200
    assert got.json()["enabled"] is False


def test_bulk_upsert_is_additive(client: TestClient, seed: dict[str, str]) -> None:
    client.post("/api/v1/projects/proj-a/onboarding/objects", json=_valid_payload())
    resp = client.post(
        "/api/v1/projects/proj-a/onboarding/objects/bulk",
        json={
            "objects": [
                _valid_payload(owner="updated-owner"),  # matches erp.customer -> update
                _valid_payload(source_system="erp", source_object="orders"),  # new
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"added": 1, "updated": 1}

    listed = client.get("/api/v1/projects/proj-a/onboarding/objects")
    assert listed.status_code == 200
    keys = {row["object_key"] for row in listed.json()}
    assert keys == {"erp.customer", "erp.orders"}


def test_viewer_cannot_edit(client: TestClient, as_user) -> None:
    as_user("VIEWER")
    resp = client.post("/api/v1/projects/proj-a/onboarding/objects", json=_valid_payload())
    assert resp.status_code == 403


def test_data_classification_roundtrips_and_is_validated(client: TestClient, seed: dict[str, str]) -> None:
    bad = client.post("/api/v1/projects/proj-a/onboarding/objects", json=_valid_payload(data_classification="secret"))
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "onboarding_data_classification_invalid"

    resp = client.post(
        "/api/v1/projects/proj-a/onboarding/objects", json=_valid_payload(data_classification="confidential")
    )
    assert resp.status_code == 201, resp.text
    assert client.get("/api/v1/projects/proj-a/onboarding/objects/erp.customer").json()["data_classification"] == (
        "confidential"
    )


def test_bulk_upsert_records_import_audit_row(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/projects/proj-a/onboarding/objects/bulk",
        json={"objects": [_valid_payload(), _valid_payload(source_object="orders")], "file_name": "erp.csv"},
    )
    assert resp.status_code == 200, resp.text
    client.post("/api/v1/projects/proj-a/onboarding/objects/bulk", json={"objects": [_valid_payload()]})

    imports = client.get("/api/v1/projects/proj-a/onboarding/imports")
    assert imports.status_code == 200
    rows = imports.json()
    assert len(rows) == 2
    by_name = {r["fileName"]: r for r in rows}
    first = by_name["erp.csv"]
    assert (first["rowTotal"], first["rowValid"], first["rowError"], first["added"], first["updated"]) == (2, 2, 0, 2, 0)
    assert len(first["fileHash"]) == 64 and first["committedAt"]
    assert by_name["(manual bulk upsert)"]["updated"] == 1


def test_rejected_bulk_upsert_records_no_import(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/projects/proj-a/onboarding/objects/bulk", json={"objects": [_valid_payload(source_object="")]}
    )
    assert resp.status_code == 422
    assert client.get("/api/v1/projects/proj-a/onboarding/imports").json() == []
