"""POST/PUT /admin/users + reset-password (§11/§27 extension).

Uses the standard `client`/`as_user` mock-identity fixtures (RBAC role gate
is already covered generically in `test_rbac.py`'s matrix) — this file checks
what the endpoints actually do: temp-password issuance, scope enforcement for
TEAM_ADMIN, and the last-platform-admin guard.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.db.base import utcnow
from app.security import verify_password


def test_create_user_issues_a_one_time_temp_password(client: TestClient, seed: dict[str, str], db: Session) -> None:
    res = client.post(
        "/api/v1/admin/users",
        json={"name": "New Dev", "email": "newdev@test.local", "role": "DEVELOPER", "teamId": "team-a"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["mustChangePassword"] is True
    assert body["tempPassword"]

    row = db.get(models.User, body["id"])
    assert verify_password(body["tempPassword"], row.password_hash)
    assert row.invited_by == "u-admin"


def test_create_user_rejects_duplicate_email(client: TestClient, seed: dict[str, str]) -> None:
    client.post("/api/v1/admin/users", json={"name": "A", "email": "dup@test.local", "role": "VIEWER"})
    dup = client.post("/api/v1/admin/users", json={"name": "B", "email": "dup@test.local", "role": "VIEWER"})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "email_exists"


def test_team_admin_cannot_create_users(client: TestClient, as_user, seed: dict[str, str]) -> None:
    as_user("TEAM_ADMIN")
    res = client.post("/api/v1/admin/users", json={"name": "X", "email": "x@test.local", "role": "VIEWER"})
    assert res.status_code == 403


def test_team_admin_can_edit_own_team_member_but_not_grant_platform_admin(
    client: TestClient, as_user, seed: dict[str, str], db: Session
) -> None:
    db.add(models.User(id="u-member", name="Member", email="member@test.local", role="DEVELOPER", team_id="team-a"))
    db.commit()

    as_user("TEAM_ADMIN")
    ok = client.put("/api/v1/admin/users/u-member", json={"role": "VIEWER"})
    assert ok.status_code == 200
    assert ok.json()["role"] == "VIEWER"

    escalate = client.put("/api/v1/admin/users/u-member", json={"role": "PLATFORM_ADMIN"})
    assert escalate.status_code == 403


def test_team_admin_cannot_edit_a_member_of_another_team(
    client: TestClient, as_user, seed: dict[str, str], db: Session
) -> None:
    db.add(models.Team(id="team-b", name="Team B", description=""))
    db.add(models.User(id="u-other", name="Other", email="other@test.local", role="DEVELOPER", team_id="team-b"))
    db.commit()

    as_user("TEAM_ADMIN")
    res = client.put("/api/v1/admin/users/u-other", json={"active": False})
    assert res.status_code == 403


def test_cannot_demote_the_last_active_platform_admin(client: TestClient, as_user, seed: dict[str, str], db: Session) -> None:
    db.add(models.User(id="u-admin", name="Test Admin", email="admin@test.local", role="PLATFORM_ADMIN", team_id="team-a"))
    db.commit()

    as_user("PLATFORM_ADMIN")
    res = client.put("/api/v1/admin/users/u-admin", json={"active": False})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "last_platform_admin"


def test_reset_password_issues_new_temp_password_and_revokes_sessions(
    client: TestClient, seed: dict[str, str], db: Session
) -> None:
    db.add(models.User(id="u-member", name="Member", email="member@test.local", role="DEVELOPER", team_id="team-a"))
    db.add(
        models.Session(
            id="sess-1",
            user_id="u-member",
            issued_at=utcnow(),
            expires_at=utcnow(),
            idle_expires_at=utcnow(),
            csrf_token="x",
        )
    )
    db.commit()

    res = client.post("/api/v1/admin/users/u-member/reset-password")
    assert res.status_code == 200
    assert res.json()["tempPassword"]

    db.expire_all()
    assert db.get(models.Session, "sess-1").revoked_at is not None
