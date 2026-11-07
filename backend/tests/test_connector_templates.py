"""Downloadable-template + upload-parse endpoint tests (AGENTS.md §24.17, §26).

Mirrors `frontend/lib/adf/connections.test.ts`'s round-trip coverage: a
template downloaded from `app.connectors.templates` must parse back cleanly
through that same module's parser. No RBAC/DB involved — these endpoints only
read the static connector registry (same reasoning as `test_connectors.py`).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# --- Source objects (table / file onboarding) -----------------------------


def test_source_objects_template_downloads_as_csv():
    res = client.get("/api/v1/connectors/templates/source-objects")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    assert "customer" in res.text
    assert "catalog" in res.text  # the fuller 7-column shape, not the old 3-column one


def test_source_objects_template_round_trips_through_parse():
    template = client.get("/api/v1/connectors/templates/source-objects").text
    res = client.post(
        "/api/v1/connectors/templates/source-objects/parse",
        json={"content": template, "fileName": "source_objects_template.csv"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["warnings"] == []
    assert body["objects"] == [
        {
            "object": "customer",
            "catalog": None,
            "schema": "dbo",
            "qualifierOverride": None,
            "caseOverride": None,
            "watermarkColumn": None,
            "loadingBehavior": "FullLoad",
        },
        {
            "object": "ORDERS",
            "catalog": "MYDB",
            "schema": "SALES",
            "qualifierOverride": None,
            "caseOverride": None,
            "watermarkColumn": "MODIFIED_DATE",
            "loadingBehavior": "DeltaLoad",
        },
        {
            "object": "BusinessPartner",
            "catalog": None,
            "schema": None,
            "qualifierOverride": None,
            "caseOverride": None,
            "watermarkColumn": None,
            "loadingBehavior": None,
        },
    ]


def test_source_objects_parse_reports_duplicate_and_empty_rows():
    content = "object,watermark_column\ndbo.customer,\ndbo.customer,x\n,y"
    res = client.post(
        "/api/v1/connectors/templates/source-objects/parse",
        json={"content": content, "fileName": "objects.csv"},
    )
    body = res.json()
    assert len(body["objects"]) == 1
    assert any("Duplicate object" in w for w in body["warnings"])
    assert any("empty object name" in w for w in body["warnings"])


# --- Linked service / source system creation -------------------------------


def test_linked_service_template_downloads_as_csv():
    res = client.get("/api/v1/connectors/azure_sql_database/linked-service-template")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    assert "server" in res.text and "database" in res.text


def test_linked_service_template_unknown_connector_404s():
    res = client.get("/api/v1/connectors/not_a_real_connector/linked-service-template")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "connector_not_found"


def test_linked_service_template_round_trips_through_parse():
    template = client.get(
        "/api/v1/connectors/azure_sql_database/linked-service-template", params={"auth_type": "sql_authentication"}
    ).text
    res = client.post(
        "/api/v1/connectors/linked-service/parse",
        json={"content": template, "fileName": "linked_service_template.csv"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["warnings"] == []
    # The template now ships 2 sample rows — every row bulk-creates its own connection.
    assert len(body["values"]) == 2
    values = body["values"][0]
    assert values["name"] == "LS_azure_sql_database_source"
    assert values["connectorType"] == "azure_sql_database"
    assert values["authType"] == "sql_authentication"
    assert values["integrationRuntime"] == "AutoResolveIntegrationRuntime"
    assert values["keyVaultLinkedService"] == "LS_azure_key_vault"
    assert values["fields"]["server"] == "myserver.database.windows.net"
    assert "password" not in values["fields"]  # secret fields never carry a value
    assert body["values"][1]["name"] == "LS_azure_sql_database_source_2"


def test_linked_service_parse_bulk_creates_one_connection_per_row_and_warns_on_unknown_column():
    content = (
        "name,connector_type,auth_type,not_a_real_field\n"
        "LS_one,azure_sql_database,sql_authentication,whatever\n"
        "LS_two,azure_sql_database,sql_authentication,whatever\n"
    )
    res = client.post("/api/v1/connectors/linked-service/parse", json={"content": content, "fileName": "ls.csv"})
    body = res.json()
    assert [v["name"] for v in body["values"]] == ["LS_one", "LS_two"]
    assert "not_a_real_field" not in body["values"][0]["fields"]
    assert any("not_a_real_field" in w for w in body["warnings"])


def test_linked_service_parse_handles_json():
    payload = {
        "name": "LS_erp",
        "connector_type": "sql_server",
        "auth_type": "sql_authentication",
        "server": "erp-sql.internal",
    }
    res = client.post(
        "/api/v1/connectors/linked-service/parse",
        json={"content": __import__("json").dumps(payload), "fileName": "ls.json"},
    )
    body = res.json()
    assert len(body["values"]) == 1
    assert body["values"][0]["connectorType"] == "sql_server"
    assert body["values"][0]["fields"]["server"] == "erp-sql.internal"


def test_linked_service_parse_handles_json_array_bulk_create():
    payload = [
        {"name": "LS_one", "connector_type": "sql_server", "auth_type": "sql_authentication"},
        {"name": "LS_two", "connector_type": "sql_server", "auth_type": "sql_authentication"},
    ]
    res = client.post(
        "/api/v1/connectors/linked-service/parse",
        json={"content": __import__("json").dumps(payload), "fileName": "ls.json"},
    )
    body = res.json()
    assert [v["name"] for v in body["values"]] == ["LS_one", "LS_two"]


def test_linked_service_parse_no_data_row_reports_warning_and_no_values():
    res = client.post(
        "/api/v1/connectors/linked-service/parse", json={"content": "name,connector_type", "fileName": "ls.csv"}
    )
    body = res.json()
    assert body["values"] == []
    assert body["warnings"]
