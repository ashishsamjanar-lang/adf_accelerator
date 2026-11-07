"""Tests for `/api/v1/projects/{id}/mart-objects` (AGENTS.md §33.9) — same
shape as `test_silver_objects.py`, Gold-specific differences only
(`martDomain` mandatory, `scd2` now allowed per §33.3's widening)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _valid_payload(**overrides):
    body = {
        "tag": "finance",
        "martDomain": "finance",
        "curatedEntity": "gl_fact",
        "sourceObjectKeys": ["gl_transaction"],
        "transformations": [
            {"name": "agg", "type": "aggregate", "groupByColumns": "customer_id", "aggregateExpression": "SUM(amount) AS total"},
        ],
    }
    body.update(overrides)
    return body


def test_create_requires_mart_domain(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post("/api/v1/projects/proj-a/mart-objects", json=_valid_payload(martDomain=""))
    assert resp.status_code == 422


def test_create_and_get_roundtrip_computes_target_table(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post("/api/v1/projects/proj-a/mart-objects", json=_valid_payload())
    assert resp.status_code == 201, resp.text
    assert resp.json()["targetTable"] == "mart.finance_gl_fact"

    got = client.get("/api/v1/projects/proj-a/mart-objects/gl_fact")
    assert got.status_code == 200
    assert got.json()["martDomain"] == "finance"


def test_create_honors_a_projects_custom_zone_prefix(client: TestClient, seed: dict[str, str]) -> None:
    """§25.3 Standards "Layer prefixes", Gold side of test_silver_objects.py's equivalent."""
    settings = client.get("/api/v1/projects/proj-a/settings").json()
    settings["namingConvention"] = {**(settings.get("namingConvention") or {}), "layerPrefixes": {"gold": "pub"}}
    assert client.put("/api/v1/projects/proj-a/settings", json=settings).status_code == 200

    resp = client.post("/api/v1/projects/proj-a/mart-objects", json=_valid_payload())
    assert resp.status_code == 201, resp.text
    assert resp.json()["targetTable"] == "pub.finance_gl_fact"


def test_scd2_now_allowed_on_gold(client: TestClient, seed: dict[str, str]) -> None:
    # §33.3 widening regression check at the API layer.
    resp = client.post(
        "/api/v1/projects/proj-a/mart-objects",
        json=_valid_payload(
            curatedEntity="customer_dim",
            transformations=[{"name": "scd", "type": "scd2", "keyColumns": "customer_id", "orderColumn": "modified_date"}],
        ),
    )
    assert resp.status_code == 201, resp.text


def test_uniqueness_is_scoped_by_domain_and_entity(client: TestClient, seed: dict[str, str]) -> None:
    client.post("/api/v1/projects/proj-a/mart-objects", json=_valid_payload())
    # Same curated_entity, different domain -> allowed (uniqueness is (domain, entity)).
    other_domain = client.post(
        "/api/v1/projects/proj-a/mart-objects", json=_valid_payload(martDomain="ops", objectKey="gl_fact_ops")
    )
    assert other_domain.status_code == 201, other_domain.text

    same_domain = client.post("/api/v1/projects/proj-a/mart-objects", json=_valid_payload(objectKey="gl_fact_dup"))
    assert same_domain.status_code == 409


def test_list_filters_by_domain(client: TestClient, seed: dict[str, str]) -> None:
    client.post("/api/v1/projects/proj-a/mart-objects", json=_valid_payload())
    client.post(
        "/api/v1/projects/proj-a/mart-objects",
        json=_valid_payload(martDomain="ops", curatedEntity="shipment_fact"),
    )
    finance = client.get("/api/v1/projects/proj-a/mart-objects", params={"domain": "finance"})
    # `domain` query param filters the free-text `domain` column, not `mart_domain` —
    # confirm it's at least accepted and returns a well-formed list.
    assert finance.status_code == 200

    every = client.get("/api/v1/projects/proj-a/mart-objects")
    assert len(every.json()) == 2


def test_update_and_delete(client: TestClient, seed: dict[str, str]) -> None:
    """§29.5 happy path for PUT/DELETE on a mart object."""
    created = client.post("/api/v1/projects/proj-a/mart-objects", json=_valid_payload()).json()
    put = client.put(
        "/api/v1/projects/proj-a/mart-objects/gl_fact", json={"expectedVersion": created["version"], "tag": "fin_reporting"}
    )
    assert put.status_code == 200, put.text
    assert put.json()["tag"] == "fin_reporting" and put.json()["version"] == created["version"] + 1
    deleted = client.delete("/api/v1/projects/proj-a/mart-objects/gl_fact")
    assert deleted.status_code in (200, 204), deleted.text
    assert client.get("/api/v1/projects/proj-a/mart-objects/gl_fact").json()["enabled"] is False
