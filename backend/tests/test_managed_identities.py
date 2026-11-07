"""Managed identities endpoint tests (AGENTS.md §32.6, §32.8, gap #21 phase MI3).

An admin-curated pick-list of user-assigned managed identities (UAMI) —
TEAM_ADMIN+ only to create/edit/delete, any role may view, and every route is
team-scoped (§27.4) so one team can never see or touch another team's rows.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


@pytest.fixture()
def team_b(db: Session, seed: dict[str, str]) -> str:
    """A second team ("team-b"), alongside `seed`'s "team-a" — `as_foreign_user`
    (conftest.py) is a DEVELOPER on this team."""
    db.add(models.Team(id="team-b", name="Team B", description="Second seed team."))
    db.commit()
    return "team-b"


def test_create_get_and_list_managed_identity(client: TestClient, seed: dict[str, str]) -> None:
    created = client.post(
        "/api/v1/managed-identities",
        json={
            "name": "shared-uami",
            "resourceId": "/subscriptions/x/resourceGroups/y/providers/Microsoft.ManagedIdentity/userAssignedIdentities/uami1",
            "clientId": "11111111-1111-1111-1111-111111111111",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "shared-uami"
    assert body["teamId"] == seed["team_id"]
    assert body["resourceId"].endswith("uami1")
    assert body["createdBy"] == "u-admin"

    fetched = client.get(f"/api/v1/managed-identities/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "shared-uami"

    listed = client.get("/api/v1/managed-identities")
    assert body["id"] in {i["id"] for i in listed.json()}


def test_create_requires_name_and_resource_id(client: TestClient) -> None:
    res = client.post("/api/v1/managed-identities", json={"name": "  ", "resourceId": "/subscriptions/x"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "managed_identity_name_required"

    res = client.post("/api/v1/managed-identities", json={"name": "x", "resourceId": "  "})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "managed_identity_resource_id_required"


def test_update_merges_fields(client: TestClient) -> None:
    created = client.post(
        "/api/v1/managed-identities", json={"name": "uami-a", "resourceId": "/subscriptions/x/.../uami-a"}
    ).json()
    res = client.put(f"/api/v1/managed-identities/{created['id']}", json={"name": "uami-a-renamed"})
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "uami-a-renamed"
    assert body["resourceId"] == "/subscriptions/x/.../uami-a"  # untouched by a partial update


def test_delete_managed_identity(client: TestClient) -> None:
    created = client.post(
        "/api/v1/managed-identities", json={"name": "uami-del", "resourceId": "/subscriptions/x/.../uami-del"}
    ).json()
    res = client.delete(f"/api/v1/managed-identities/{created['id']}")
    assert res.status_code == 204
    assert client.get(f"/api/v1/managed-identities/{created['id']}").status_code == 404


def test_unknown_managed_identity_404s(client: TestClient) -> None:
    assert client.get("/api/v1/managed-identities/nope").status_code == 404
    assert client.put("/api/v1/managed-identities/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/v1/managed-identities/nope").status_code == 404


@pytest.mark.parametrize("role", ["DEVELOPER", "VIEWER"])
def test_only_team_admin_or_above_can_create_edit_delete(client: TestClient, as_user, role: str) -> None:
    as_user(role)
    created = client.post("/api/v1/managed-identities", json={"name": "x", "resourceId": "/subscriptions/x"})
    assert created.status_code == 403

    as_user("PLATFORM_ADMIN")
    seeded = client.post("/api/v1/managed-identities", json={"name": "x", "resourceId": "/subscriptions/x"}).json()

    as_user(role)
    assert client.put(f"/api/v1/managed-identities/{seeded['id']}", json={"name": "y"}).status_code == 403
    assert client.delete(f"/api/v1/managed-identities/{seeded['id']}").status_code == 403
    # Every role may still read.
    assert client.get(f"/api/v1/managed-identities/{seeded['id']}").status_code == 200


def test_team_admin_can_create_for_own_team(client: TestClient, as_user) -> None:
    as_user("TEAM_ADMIN")
    res = client.post("/api/v1/managed-identities", json={"name": "ta-uami", "resourceId": "/subscriptions/x"})
    assert res.status_code == 201
    assert res.json()["teamId"] == "team-a"


def test_cross_team_access_is_denied(client: TestClient, as_user, team_b: str) -> None:
    seeded = client.post("/api/v1/managed-identities", json={"name": "x", "resourceId": "/subscriptions/x"}).json()

    # Switch identity mid-test to a team-b TEAM_ADMIN. Not `as_foreign_user` (conftest.py,
    # a team-b DEVELOPER) — that fixture applies its override during fixture setup, before
    # this body's first (team-a) request above would run, and DEVELOPER can't create a
    # managed identity at all (§32.8 is TEAM_ADMIN+ regardless of team), which this test
    # also wants to exercise. Switching inline here keeps "create as team-a, then act as
    # team-b" in the order the test actually needs.
    from app.api.deps import current_user
    from app.main import app

    foreign_admin = models.User(
        id="u-foreign-admin", name="Foreign Admin", email="foreign-admin@test.local", role="TEAM_ADMIN", team_id="team-b"
    )
    app.dependency_overrides[current_user] = lambda: foreign_admin
    assert client.get(f"/api/v1/managed-identities/{seeded['id']}").status_code == 403
    assert client.put(f"/api/v1/managed-identities/{seeded['id']}", json={"name": "y"}).status_code == 403
    assert client.delete(f"/api/v1/managed-identities/{seeded['id']}").status_code == 403
    # A team-b TEAM_ADMIN can create their own team's identity...
    created = client.post("/api/v1/managed-identities", json={"name": "foreign-uami", "resourceId": "/subscriptions/x"})
    assert created.status_code == 201
    assert created.json()["teamId"] == "team-b"
    # ...and an unfiltered list only ever returns their own team's rows.
    listed = client.get("/api/v1/managed-identities").json()
    assert all(i["teamId"] == "team-b" for i in listed)
    assert seeded["id"] not in {i["id"] for i in listed}
