"""Connection library endpoint tests (AGENTS.md §24.17, §14).

The metastore-backed counterpart to the frontend's browser-only
`lib/adf/connection-store.ts` — a "connection" bundles a linked service, its
source-objects onboarding list, and a dataset shape into one reusable,
project-scoped row.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _draft() -> dict:
    return {
        "linkedService": {
            "name": "LS_erp_source",
            "connectorType": "azure_sql_database",
            "authType": "sql_authentication",
            "integrationRuntime": "AutoResolveIntegrationRuntime",
            "keyVaultLinkedService": "LS_azure_key_vault",
            "fields": {"server": "erp-sql.internal", "database": "erp"},
            "parameterize": [],
        },
        "objectMode": "form",
        "objects": [{"object": "dbo.customer", "loadingBehavior": "FullLoad"}],
        "dataset": {
            "name": "DS_erp_source",
            "linkedServiceName": "LS_erp_source",
            "schema": "dbo",
            "table": "customer",
            "folderPath": "",
            "fileName": "",
            "parameterized": True,
            "format": {
                "columnDelimiter": ",",
                "rowDelimiter": "\n",
                "quoteChar": '"',
                "escapeChar": "",
                "firstRowAsHeader": True,
                "encoding": "utf-8",
                "compression": "none",
            },
        },
    }


def test_create_get_and_list_connection(client: TestClient, seed: dict[str, str], persist_actor) -> None:
    from tests.conftest import ROLE_USERS

    persist_actor(ROLE_USERS["PLATFORM_ADMIN"])
    created = client.post(
        "/api/v1/connections",
        json={
            "projectId": seed["project_id"],
            "name": "ERP source",
            "role": "source",
            "connectorType": "azure_sql_database",
            "description": "Primary ERP database.",
            "draft": _draft(),
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "ERP source"
    assert body["projectId"] == seed["project_id"]
    assert body["draft"]["linkedService"]["fields"]["server"] == "erp-sql.internal"
    assert body["createdBy"] == "u-admin"
    assert body["createdByName"] == "Test Admin"

    fetched = client.get(f"/api/v1/connections/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "ERP source"

    listed = client.get("/api/v1/connections", params={"project_id": seed["project_id"]})
    assert body["id"] in {c["id"] for c in listed.json()}


def test_create_connection_requires_name(client: TestClient, seed: dict[str, str]) -> None:
    res = client.post(
        "/api/v1/connections",
        json={"projectId": seed["project_id"], "name": "   ", "connectorType": "azure_sql_database", "draft": {}},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "connection_name_required"


def test_create_connection_rejects_invalid_role(client: TestClient, seed: dict[str, str]) -> None:
    res = client.post(
        "/api/v1/connections",
        json={
            "projectId": seed["project_id"],
            "name": "x",
            "role": "not_a_role",
            "connectorType": "azure_sql_database",
            "draft": {},
        },
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "connection_role_invalid"


def test_create_connection_requires_existing_project(client: TestClient) -> None:
    res = client.post(
        "/api/v1/connections",
        json={"projectId": "no-such-project", "name": "x", "connectorType": "azure_sql_database", "draft": {}},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "project_not_found"


def test_update_connection_merges_fields(client: TestClient, seed: dict[str, str]) -> None:
    created = client.post(
        "/api/v1/connections",
        json={
            "projectId": seed["project_id"],
            "name": "ERP source",
            "connectorType": "azure_sql_database",
            "draft": _draft(),
        },
    ).json()

    updated_draft = _draft()
    updated_draft["objects"].append({"object": "dbo.orders", "loadingBehavior": "DeltaLoad", "watermarkColumn": "modified_date"})
    res = client.put(
        f"/api/v1/connections/{created['id']}",
        json={"name": "ERP source (renamed)", "draft": updated_draft},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "ERP source (renamed)"
    assert len(body["draft"]["objects"]) == 2
    # connectorType/role untouched by a partial update.
    assert body["connectorType"] == "azure_sql_database"


def test_delete_connection(client: TestClient, seed: dict[str, str]) -> None:
    created = client.post(
        "/api/v1/connections",
        json={"projectId": seed["project_id"], "name": "x", "connectorType": "sql_server", "draft": _draft()},
    ).json()

    res = client.delete(f"/api/v1/connections/{created['id']}")
    assert res.status_code == 204
    assert client.get(f"/api/v1/connections/{created['id']}").status_code == 404


def test_unknown_connection_404s(client: TestClient) -> None:
    assert client.get("/api/v1/connections/nope").status_code == 404
    assert client.put("/api/v1/connections/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/v1/connections/nope").status_code == 404


@pytest.mark.parametrize("role", ["VIEWER"])
def test_viewer_cannot_create_or_delete_connection(client: TestClient, as_user, seed: dict[str, str], role: str) -> None:
    as_user(role)
    created = client.post(
        "/api/v1/connections",
        json={"projectId": seed["project_id"], "name": "x", "connectorType": "sql_server", "draft": {}},
    )
    assert created.status_code == 403

    as_user("PLATFORM_ADMIN")
    seeded = client.post(
        "/api/v1/connections",
        json={"projectId": seed["project_id"], "name": "x", "connectorType": "sql_server", "draft": {}},
    ).json()

    as_user(role)
    assert client.delete(f"/api/v1/connections/{seeded['id']}").status_code == 403
    # VIEWER can still read.
    assert client.get(f"/api/v1/connections/{seeded['id']}").status_code == 200
