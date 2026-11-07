"""Team endpoint tests (AGENTS.md §14, §23.12 gap #20)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_teams_empty(client: TestClient) -> None:
    res = client.get("/api/v1/teams")
    assert res.status_code == 200
    assert res.json() == []


def test_create_and_get_team(client: TestClient) -> None:
    created = client.post("/api/v1/teams", json={"name": "Data Platform", "description": "Owns the lake."})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Data Platform"
    assert body["memberCount"] == 0
    assert body["projectCount"] == 0

    fetched = client.get(f"/api/v1/teams/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_get_unknown_team_404s_with_envelope(client: TestClient) -> None:
    res = client.get("/api/v1/teams/does-not-exist")
    assert res.status_code == 404
    err = res.json()["error"]
    assert err["code"] == "team_not_found"
    assert err["correlation_id"]


def test_team_members(client: TestClient, seed: dict[str, str], db) -> None:
    from app import models

    db.add(models.User(id="u-x", name="Xavier", email="xavier@test.local", role="DEVELOPER", team_id="team-a"))
    db.commit()

    res = client.get(f"/api/v1/teams/{seed['team_id']}/members")
    assert res.status_code == 200
    assert [u["id"] for u in res.json()] == ["u-x"]


def test_team_members_unknown_team_404s(client: TestClient) -> None:
    assert client.get("/api/v1/teams/nope/members").status_code == 404
