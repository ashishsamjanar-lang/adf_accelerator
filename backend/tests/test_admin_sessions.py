"""Tests for `/api/v1/admin/sessions` — §27 session admin (list + revoke)."""
from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.api.admin import _session_handle
from app.db.base import utcnow


def _arrange(db: Session) -> None:
    now = utcnow()
    db.add(models.Team(id="team-a", name="Team A"))
    db.add(models.Team(id="team-b", name="Team B"))
    db.add(models.User(id="u-a", name="Alice", email="a@test.local", role="DEVELOPER", team_id="team-a"))
    db.add(models.User(id="u-b", name="Bob", email="b@test.local", role="DEVELOPER", team_id="team-b"))

    def session(sid: str, user_id: str, **kw) -> models.Session:
        return models.Session(
            id=sid, user_id=user_id, issued_at=now, csrf_token="c",
            expires_at=kw.get("expires_at", now + timedelta(hours=8)),
            idle_expires_at=kw.get("idle_expires_at", now + timedelta(minutes=30)),
            revoked_at=kw.get("revoked_at"),
        )

    db.add_all(
        [
            session("sess-a", "u-a"),
            session("sess-b", "u-b"),
            session("sess-revoked", "u-a", revoked_at=now),
            session("sess-idle", "u-a", idle_expires_at=now - timedelta(minutes=1)),
        ]
    )
    db.commit()


def test_platform_admin_lists_only_live_sessions_without_raw_ids(client: TestClient, db: Session) -> None:
    _arrange(db)
    rows = client.get("/api/v1/admin/sessions").json()
    assert sorted(r["userEmail"] for r in rows) == ["a@test.local", "b@test.local"]
    assert all("id" not in r and r["handle"] not in {"sess-a", "sess-b"} for r in rows)


def test_team_admin_sees_own_team_only_and_cannot_revoke_other_team(client: TestClient, db: Session, as_user) -> None:
    _arrange(db)
    as_user("TEAM_ADMIN")  # team-a
    rows = client.get("/api/v1/admin/sessions").json()
    assert [r["userEmail"] for r in rows] == ["a@test.local"]
    assert client.delete(f"/api/v1/admin/sessions/{_session_handle('sess-b')}").status_code == 404


def test_revoke_marks_session_revoked(client: TestClient, db: Session) -> None:
    _arrange(db)
    assert client.delete(f"/api/v1/admin/sessions/{_session_handle('sess-a')}").status_code == 204
    db.expire_all()
    assert db.get(models.Session, "sess-a").revoked_at is not None
    assert [r["userEmail"] for r in client.get("/api/v1/admin/sessions").json()] == ["b@test.local"]


def test_developer_forbidden(client: TestClient, db: Session, as_user) -> None:
    as_user("DEVELOPER")
    assert client.get("/api/v1/admin/sessions").status_code == 403
