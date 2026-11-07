"""Functional tests for app/api/admin.py (§11, §12, §23.12 gap 12).

``test_rbac.py`` already checks the permission gate on ``/admin/users`` and
``/admin/audit`` (who may call it); this file checks what the endpoints
actually do once a call is allowed — user/audit listing content, and the full
CRUD + validation behavior of the environments sub-router, which had no
dedicated test at all before this file (only its ``environment.view``
permission was covered, via the RBAC matrix's blanket ``ALL_ROLES`` cases
elsewhere).
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.api.deps import current_user
from app.main import app


def test_list_users_returns_the_seeded_users(client: TestClient, as_user, persist_actor) -> None:
    admin = as_user("PLATFORM_ADMIN")
    persist_actor(admin)
    res = client.get("/api/v1/admin/users")
    assert res.status_code == 200
    emails = {u["email"] for u in res.json()}
    assert "admin@test.local" in emails


def test_audit_log_reflects_actions_taken_in_this_session(client: TestClient, as_user) -> None:
    as_user("PLATFORM_ADMIN")
    client.post(
        "/api/v1/environments",
        json={"name": "DEV", "label": "Development"},
    )
    res = client.get("/api/v1/admin/audit")
    assert res.status_code == 200
    actions = [e["action"] for e in res.json()]
    assert "environment.created" in actions


def test_audit_log_is_scoped_by_team_for_team_admin(client: TestClient, as_user, db: Session) -> None:
    """AGENTS.md §30.7 AQ1: a TEAM_ADMIN must see only their own team's audit
    rows; a PLATFORM_ADMIN sees every team's plus platform-global actions
    (e.g. environment CRUD, which has no owning team)."""
    db.add(models.Team(id="team-a", name="Team A", description="First team."))
    db.add(models.Team(id="team-b", name="Team B", description="Second team."))
    db.commit()

    as_user("PLATFORM_ADMIN")
    client.post("/api/v1/environments", json=_env_body())
    client.post("/api/v1/projects", json={"teamId": "team-a", "name": "Project A"})
    client.post("/api/v1/projects", json={"teamId": "team-b", "name": "Project B"})

    team_a_admin = models.User(
        id="u-ta-a", name="Team A Admin", email="ta-a@test.local", role="TEAM_ADMIN", team_id="team-a"
    )
    team_b_admin = models.User(
        id="u-ta-b", name="Team B Admin", email="ta-b@test.local", role="TEAM_ADMIN", team_id="team-b"
    )

    app.dependency_overrides[current_user] = lambda: team_a_admin
    actions_a = {(e["action"], e["target"]) for e in client.get("/api/v1/admin/audit").json()}
    app.dependency_overrides[current_user] = lambda: team_b_admin
    actions_b = {(e["action"], e["target"]) for e in client.get("/api/v1/admin/audit").json()}
    app.dependency_overrides.pop(current_user, None)

    assert ("project.created", "Project A") in actions_a
    assert ("project.created", "Project B") not in actions_a
    assert ("environment.created", "DEV") not in actions_a  # global action, platform-admin-only

    assert ("project.created", "Project B") in actions_b
    assert ("project.created", "Project A") not in actions_b

    as_user("PLATFORM_ADMIN")
    actions_all = {(e["action"], e["target"]) for e in client.get("/api/v1/admin/audit").json()}
    assert {("project.created", "Project A"), ("project.created", "Project B"), ("environment.created", "DEV")} <= actions_all


def _env_body(**overrides) -> dict:
    body = {
        "name": "DEV",
        "label": "Development",
        "status": "not_configured",
        "description": "Dev connection config.",
        "storageAccount": "stgdev",
        "sqlServer": "sql-dev.database.windows.net",
        "database": "adf_dev",
        "keyVault": "kv-dev",
        "factoryName": "adf-dev-factory",
        "subscriptionId": "sub-123",
        "resourceGroup": "rg-dev",
    }
    body.update(overrides)
    return body


def test_environments_start_empty(client: TestClient, as_user) -> None:
    as_user("PLATFORM_ADMIN")
    assert client.get("/api/v1/environments").json() == []


def test_create_environment_persists_the_full_configuration(client: TestClient, as_user) -> None:
    as_user("PLATFORM_ADMIN")
    res = client.post("/api/v1/environments", json=_env_body())
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "DEV"
    assert body["label"] == "Development"
    assert body["configuration"]["storageAccount"] == "stgdev"
    assert body["configuration"]["keyVault"] == "kv-dev"

    listed = client.get("/api/v1/environments").json()
    assert [e["name"] for e in listed] == ["DEV"]


def test_create_environment_rejects_a_duplicate_name(client: TestClient, as_user) -> None:
    as_user("PLATFORM_ADMIN")
    client.post("/api/v1/environments", json=_env_body())
    res = client.post("/api/v1/environments", json=_env_body())
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "environment_exists"


def test_create_environment_defaults_label_to_name_when_blank(client: TestClient, as_user) -> None:
    as_user("PLATFORM_ADMIN")
    res = client.post("/api/v1/environments", json=_env_body(name="QA", label=""))
    assert res.status_code == 201
    assert res.json()["label"] == "QA"


def test_update_environment_changes_fields(client: TestClient, as_user) -> None:
    as_user("PLATFORM_ADMIN")
    client.post("/api/v1/environments", json=_env_body())
    res = client.put("/api/v1/environments/DEV", json=_env_body(label="Development (updated)", status="active"))
    assert res.status_code == 200
    body = res.json()
    assert body["label"] == "Development (updated)"
    assert body["status"] == "active"


def test_update_environment_404s_for_an_unknown_name(client: TestClient, as_user) -> None:
    as_user("PLATFORM_ADMIN")
    res = client.put("/api/v1/environments/GHOST", json=_env_body(name="GHOST"))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "environment_not_found"


def test_delete_environment_removes_it(client: TestClient, as_user) -> None:
    as_user("PLATFORM_ADMIN")
    client.post("/api/v1/environments", json=_env_body())
    res = client.delete("/api/v1/environments/DEV")
    assert res.status_code == 204
    assert client.get("/api/v1/environments").json() == []


def test_delete_environment_404s_for_an_unknown_name(client: TestClient, as_user) -> None:
    as_user("PLATFORM_ADMIN")
    res = client.delete("/api/v1/environments/GHOST")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "environment_not_found"


def test_environment_manage_is_forbidden_for_a_developer(client: TestClient, as_user) -> None:
    as_user("DEVELOPER")
    res = client.post("/api/v1/environments", json=_env_body())
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"


def test_environment_view_is_allowed_for_a_viewer(client: TestClient, as_user) -> None:
    as_user("VIEWER")
    assert client.get("/api/v1/environments").status_code == 200
