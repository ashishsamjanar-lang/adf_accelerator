"""Tests for `/api/v1/projects/{id}/onboarding/objects/{key}/dq-rules` (AGENTS.md
§30.4/§30.5, gap AQ2's table-only slice).

Schema-only for now: a rule created here doesn't generate any pipeline
activity yet (§30.7 AQ2/AQ3 remain open) — this proves the CRUD surface and
RBAC/scope only.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _create_object(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/projects/proj-a/onboarding/objects",
        json={
            "source_system": "erp",
            "source_object": "customer",
            "source_type": "table",
            "target_type": "table",
            "target_file_format": "Parquet",
            "loading_behavior": "FullLoad",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["object_key"]


def test_create_list_and_delete_dq_rule(client: TestClient, seed: dict[str, str]) -> None:
    object_key = _create_object(client)

    created = client.post(
        f"/api/v1/projects/proj-a/onboarding/objects/{object_key}/dq-rules",
        json={"checkType": "not_null", "config": {"column": "customer_id"}, "enforcement": "block"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["checkType"] == "not_null"
    assert body["objectKey"] == object_key
    assert body["config"] == {"column": "customer_id"}
    assert body["enforcement"] == "block"
    assert body["createdBy"] == "u-admin"

    listed = client.get(f"/api/v1/projects/proj-a/onboarding/objects/{object_key}/dq-rules")
    assert listed.status_code == 200
    assert body["id"] in {r["id"] for r in listed.json()}

    deleted = client.delete(f"/api/v1/projects/proj-a/onboarding/objects/{object_key}/dq-rules/{body['id']}")
    assert deleted.status_code == 204
    listed_after = client.get(f"/api/v1/projects/proj-a/onboarding/objects/{object_key}/dq-rules").json()
    assert body["id"] not in {r["id"] for r in listed_after}


def test_create_rejects_unknown_check_type(client: TestClient, seed: dict[str, str]) -> None:
    object_key = _create_object(client)
    resp = client.post(
        f"/api/v1/projects/proj-a/onboarding/objects/{object_key}/dq-rules",
        json={"checkType": "not_a_real_check"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "dq_rule_check_type_invalid"


def test_create_rejects_unknown_enforcement(client: TestClient, seed: dict[str, str]) -> None:
    object_key = _create_object(client)
    resp = client.post(
        f"/api/v1/projects/proj-a/onboarding/objects/{object_key}/dq-rules",
        json={"checkType": "unique", "enforcement": "sometimes"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "dq_rule_enforcement_invalid"


def test_dq_rule_on_nonexistent_object_404s(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/projects/proj-a/onboarding/objects/does.notexist/dq-rules", json={"checkType": "unique"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "onboarding_object_not_found"


def test_unknown_rule_id_404s(client: TestClient, seed: dict[str, str]) -> None:
    object_key = _create_object(client)
    resp = client.delete(f"/api/v1/projects/proj-a/onboarding/objects/{object_key}/dq-rules/rule-nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "dq_rule_not_found"


@pytest.mark.parametrize("role", ["VIEWER"])
def test_viewer_cannot_create_or_delete_dq_rule(client: TestClient, as_user, seed: dict[str, str], role: str) -> None:
    object_key = _create_object(client)

    as_user(role)
    resp = client.post(
        f"/api/v1/projects/proj-a/onboarding/objects/{object_key}/dq-rules", json={"checkType": "unique"}
    )
    assert resp.status_code == 403

    as_user("PLATFORM_ADMIN")
    created = client.post(
        f"/api/v1/projects/proj-a/onboarding/objects/{object_key}/dq-rules", json={"checkType": "unique"}
    ).json()

    as_user(role)
    assert client.delete(f"/api/v1/projects/proj-a/onboarding/objects/{object_key}/dq-rules/{created['id']}").status_code == 403
    # VIEWER can still read.
    assert client.get(f"/api/v1/projects/proj-a/onboarding/objects/{object_key}/dq-rules").status_code == 200
