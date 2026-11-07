"""Connector catalog API tests (AGENTS.md §18, §23.11)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.connectors import ALL_CONNECTORS, LLM_CONNECTORS
from app.connectors.schemas import registry_fingerprint
from app.main import app

client = TestClient(app)

EXPECTED_CONNECTOR_COUNT = 100
EXPECTED_LLM = {
    "azure_openai", "openai", "anthropic", "aws_bedrock", "google_vertex_ai", "cohere",
    "mistral_ai", "databricks_model_serving", "hugging_face_inference", "azure_ai_foundry",
}


def test_registry_covers_full_source_target_union():
    assert len(ALL_CONNECTORS) == EXPECTED_CONNECTOR_COUNT
    assert set(LLM_CONNECTORS) == EXPECTED_LLM


def test_list_connectors_endpoint():
    res = client.get("/api/v1/connectors")
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == EXPECTED_CONNECTOR_COUNT
    assert body["fingerprint"] == registry_fingerprint()
    ids = {c["id"] for c in body["connectors"]}
    assert "azure_openai" in ids and "sql_server" in ids
    grouped = {c for g in body["groups"] for c in g["connectors"]}
    assert grouped == set(ALL_CONNECTORS)


def test_get_connector_detail_shape():
    res = client.get("/api/v1/connectors/azure_openai")
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "llm"
    assert body["adfType"] == "RestService"
    assert body["category"] == "AI"
    field_keys = {f["key"] for f in body["fields"]}
    assert {"url", "apiKey", "deployment", "apiVersion"} <= field_keys
    api_key_field = next(f for f in body["fields"] if f["key"] == "apiKey")
    assert api_key_field["isSecret"] is True
    assert any(a["id"] == body["defaultAuth"] for a in body["authOptions"])


def test_get_connector_detail_relational():
    body = client.get("/api/v1/connectors/sql_server").json()
    assert body["kind"] == "database"
    assert body["datasetType"] == "SqlServerTable"


def test_unknown_connector_returns_error_envelope():
    res = client.get("/api/v1/connectors/not_a_real_connector")
    assert res.status_code == 404
    err = res.json()["error"]
    assert err["code"] == "connector_not_found"
    assert err["correlation_id"]


def test_groups_endpoint():
    res = client.get("/api/v1/connectors/groups")
    assert res.status_code == 200
    categories = [g["category"] for g in res.json()]
    assert "AI" in categories and "Database" in categories


def test_health_still_ok():
    assert client.get("/health").json()["status"] == "ok"
