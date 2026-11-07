"""Tests for `/api/v1/projects/{id}/silver-objects` (AGENTS.md §33.9)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _valid_payload(**overrides):
    body = {
        "tag": "finance",
        "curatedEntity": "gl_transaction",
        "sourceObjectKeys": ["sql_server.dbo.gl_transaction"],
        "writeBehavior": "append",
        "loadingBehavior": "FullLoad",
        "transformations": [
            {"name": "conform", "type": "conform_columns", "columns": "TransactionId:transaction_id"},
        ],
    }
    body.update(overrides)
    return body


def test_create_requires_tag_and_curated_entity(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post("/api/v1/projects/proj-a/silver-objects", json={"tag": "", "curatedEntity": "", "transformations": []})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "sttm_validation_error"


def test_create_and_get_roundtrip_computes_target_table(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post("/api/v1/projects/proj-a/silver-objects", json=_valid_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["targetTable"] == "curated.gl_transaction"
    assert body["objectKey"] == "gl_transaction"
    assert body["version"] == 1
    assert body["status"] == "valid"

    got = client.get("/api/v1/projects/proj-a/silver-objects/gl_transaction")
    assert got.status_code == 200
    assert got.json()["curatedEntity"] == "gl_transaction"


def test_create_honors_a_projects_custom_zone_prefix(client: TestClient, seed: dict[str, str]) -> None:
    """§25.3 Standards "Layer prefixes" — set via PUT /projects/{id}/settings, same document
    the frontend Standards page writes to."""
    settings = client.get("/api/v1/projects/proj-a/settings").json()
    settings["namingConvention"] = {**(settings.get("namingConvention") or {}), "layerPrefixes": {"silver": "cur"}}
    put = client.put("/api/v1/projects/proj-a/settings", json=settings)
    assert put.status_code == 200

    resp = client.post("/api/v1/projects/proj-a/silver-objects", json=_valid_payload())
    assert resp.status_code == 201, resp.text
    assert resp.json()["targetTable"] == "cur.gl_transaction"

    # An update recomputes target_table too, still honoring the project's custom prefix.
    upd = client.put(
        "/api/v1/projects/proj-a/silver-objects/gl_transaction",
        json={"expectedVersion": 1, "curatedEntity": "gl_transaction_v2"},
    )
    assert upd.status_code == 200
    assert upd.json()["targetTable"] == "cur.gl_transaction_v2"


def test_create_rejects_duplicate_curated_entity(client: TestClient, seed: dict[str, str]) -> None:
    client.post("/api/v1/projects/proj-a/silver-objects", json=_valid_payload())
    resp = client.post("/api/v1/projects/proj-a/silver-objects", json=_valid_payload(objectKey="gl_transaction_2"))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "entity_exists"


def test_list_filters_by_tag(client: TestClient, seed: dict[str, str]) -> None:
    client.post("/api/v1/projects/proj-a/silver-objects", json=_valid_payload())
    client.post("/api/v1/projects/proj-a/silver-objects", json=_valid_payload(curatedEntity="policy", tag="insurance"))

    finance = client.get("/api/v1/projects/proj-a/silver-objects", params={"tag": "finance"})
    assert finance.status_code == 200
    assert [o["curatedEntity"] for o in finance.json()] == ["gl_transaction"]

    every = client.get("/api/v1/projects/proj-a/silver-objects")
    assert len(every.json()) == 2


def test_update_requires_matching_expected_version(client: TestClient, seed: dict[str, str]) -> None:
    created = client.post("/api/v1/projects/proj-a/silver-objects", json=_valid_payload()).json()
    stale = client.put(
        "/api/v1/projects/proj-a/silver-objects/gl_transaction",
        json={"expectedVersion": created["version"] + 1, "notes": "x"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    ok = client.put(
        "/api/v1/projects/proj-a/silver-objects/gl_transaction",
        json={"expectedVersion": created["version"], "notes": "updated notes"},
    )
    assert ok.status_code == 200
    assert ok.json()["version"] == created["version"] + 1
    assert ok.json()["notes"] == "updated notes"


def test_delete_defaults_to_soft_disable(client: TestClient, seed: dict[str, str]) -> None:
    client.post("/api/v1/projects/proj-a/silver-objects", json=_valid_payload())
    resp = client.delete("/api/v1/projects/proj-a/silver-objects/gl_transaction")
    assert resp.status_code == 204
    got = client.get("/api/v1/projects/proj-a/silver-objects/gl_transaction")
    assert got.status_code == 200
    assert got.json()["enabled"] is False


def test_hard_delete_requires_team_admin_or_above(client: TestClient, seed: dict[str, str], as_user) -> None:
    client.post("/api/v1/projects/proj-a/silver-objects", json=_valid_payload())
    as_user("DEVELOPER")
    forbidden = client.delete("/api/v1/projects/proj-a/silver-objects/gl_transaction", params={"hard": "true"})
    assert forbidden.status_code == 403

    as_user("TEAM_ADMIN")
    ok = client.delete("/api/v1/projects/proj-a/silver-objects/gl_transaction", params={"hard": "true"})
    assert ok.status_code == 204
    gone = client.get("/api/v1/projects/proj-a/silver-objects/gl_transaction")
    assert gone.status_code == 404


def test_viewer_can_view_but_not_create(client: TestClient, seed: dict[str, str], as_user) -> None:
    as_user("VIEWER")
    listed = client.get("/api/v1/projects/proj-a/silver-objects")
    assert listed.status_code == 200

    forbidden = client.post("/api/v1/projects/proj-a/silver-objects", json=_valid_payload())
    assert forbidden.status_code == 403


def test_delta_load_requires_watermark_column(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/projects/proj-a/silver-objects",
        json=_valid_payload(loadingBehavior="DeltaLoad"),
    )
    assert resp.status_code == 422


def test_upsert_write_behavior_requires_business_keys(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/projects/proj-a/silver-objects",
        json=_valid_payload(writeBehavior="upsert"),
    )
    assert resp.status_code == 422


def test_dq_rule_column_not_in_transformation_output_warns_but_does_not_block(client: TestClient, seed: dict[str, str]) -> None:
    # STTM-08: a soft, non-blocking warning — never an error, never a 422.
    resp = client.post(
        "/api/v1/projects/proj-a/silver-objects",
        json=_valid_payload(
            transformations=[{"name": "conform", "type": "conform_columns", "columns": "TransactionId:transaction_id"}],
            dataQualityRules=[{"name": "not_null_amount", "column": "amount", "rule": "IS_NOT_NULL", "enforcement": "warn"}],
        ),
    )
    assert resp.status_code == 201, resp.text
    warnings = resp.json()["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["severity"] == "warning"
    assert "amount" in warnings[0]["message"]

    # Re-reading the object surfaces the same warning (derived at read time, not stored).
    got = client.get("/api/v1/projects/proj-a/silver-objects/gl_transaction")
    assert len(got.json()["warnings"]) == 1


def test_dq_rule_column_matching_transformation_output_has_no_warning(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/projects/proj-a/silver-objects",
        json=_valid_payload(
            transformations=[{"name": "conform", "type": "conform_columns", "columns": "TransactionId:transaction_id"}],
            dataQualityRules=[{"name": "not_null_txn", "column": "transaction_id", "rule": "IS_NOT_NULL", "enforcement": "warn"}],
        ),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["warnings"] == []


def test_join_transformation_now_allowed_on_silver(client: TestClient, seed: dict[str, str]) -> None:
    # §33.3 widening regression check at the API layer, not just the unit test.
    resp = client.post(
        "/api/v1/projects/proj-a/silver-objects",
        json=_valid_payload(
            transformations=[
                {"name": "join_orders", "type": "join", "joinEntity": "raw.orders", "joinCondition": "a.id = b.id"}
            ]
        ),
    )
    assert resp.status_code == 201, resp.text


def test_governance_fields_round_trip(client: TestClient, seed: dict[str, str]) -> None:
    """gaps.md DMT-08: the typed governance fields are stored, returned and partially updated."""
    resp = client.post(
        "/api/v1/projects/proj-a/silver-objects",
        json=_valid_payload(containsPii=True, retentionPeriod="7y", auditRequired=False),
    )
    assert resp.status_code == 201, resp.text
    obj = resp.json()
    assert (obj["containsPii"], obj["retentionPeriod"], obj["auditRequired"], obj["containsPhi"]) == (True, "7y", False, None)
    put = client.put(
        f"/api/v1/projects/proj-a/silver-objects/{obj['objectKey']}",
        json={"expectedVersion": obj["version"], "regulatoryTag": "GDPR"},
    )
    assert put.status_code == 200, put.text
    assert (put.json()["regulatoryTag"], put.json()["containsPii"]) == ("GDPR", True)
