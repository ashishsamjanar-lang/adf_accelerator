"""AUTH_MODE=password: login/logout/change-password, session idle+absolute
expiry, CSRF, and lockout (§27.2 extension, §27.3). Uses `raw_client` so
`current_user` runs for real instead of the mock-identity override every
other test relies on.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.db.base import utcnow
from app.security import CSRF_COOKIE_NAME, hash_password


@pytest.fixture()
def password_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "password")


@pytest.fixture()
def pw_user(db: Session) -> models.User:
    user = models.User(
        id="u-pw",
        name="Pw User",
        email="pw@test.local",
        role="DEVELOPER",
        team_id="team-a",
        password_hash=hash_password("correct horse battery staple"),
    )
    db.add(user)
    db.commit()
    return user


def _csrf_header(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]}


def test_login_wrong_mode_404s(raw_client: TestClient) -> None:
    # AUTH_MODE is "mock" by default in this suite — login is meaningless there.
    res = raw_client.post("/api/v1/auth/login", json={"email": "x@test.local", "password": "x"})
    assert res.status_code == 404


def test_login_success_sets_cookies_and_me_works(raw_client: TestClient, password_mode, pw_user) -> None:
    res = raw_client.post(
        "/api/v1/auth/login", json={"email": "pw@test.local", "password": "correct horse battery staple"}
    )
    assert res.status_code == 200
    assert res.json()["email"] == "pw@test.local"
    assert "adf_session" in raw_client.cookies
    assert "adf_csrf" in raw_client.cookies

    me = raw_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "pw@test.local"


def test_login_wrong_password_401s_and_no_cookie(raw_client: TestClient, password_mode, pw_user) -> None:
    res = raw_client.post("/api/v1/auth/login", json={"email": "pw@test.local", "password": "nope"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_credentials"
    assert "adf_session" not in raw_client.cookies


def test_login_unknown_email_401s_same_as_wrong_password(raw_client: TestClient, password_mode) -> None:
    res = raw_client.post("/api/v1/auth/login", json={"email": "ghost@test.local", "password": "x"})
    assert res.status_code == 401


def test_account_locks_after_max_failed_attempts(raw_client: TestClient, password_mode, pw_user, db: Session) -> None:
    for _ in range(settings.auth_max_failed_logins):
        raw_client.post("/api/v1/auth/login", json={"email": "pw@test.local", "password": "wrong"})

    locked = raw_client.post(
        "/api/v1/auth/login", json={"email": "pw@test.local", "password": "correct horse battery staple"}
    )
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "account_locked"


def test_without_session_cookie_is_401(raw_client: TestClient, password_mode) -> None:
    res = raw_client.get("/api/v1/auth/me")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthenticated"


def test_mutating_request_without_csrf_header_is_403(raw_client: TestClient, password_mode, pw_user) -> None:
    raw_client.post("/api/v1/auth/login", json={"email": "pw@test.local", "password": "correct horse battery staple"})
    res = raw_client.post("/api/v1/teams", json={"name": "No CSRF"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "csrf_check_failed"


def test_mutating_request_with_csrf_header_succeeds_when_permitted(
    raw_client: TestClient, password_mode, db: Session
) -> None:
    admin = models.User(
        id="u-pw-admin",
        name="Pw Admin",
        email="pwadmin@test.local",
        role="PLATFORM_ADMIN",
        password_hash=hash_password("s3cret-enough"),
    )
    db.add(admin)
    db.commit()
    raw_client.post("/api/v1/auth/login", json={"email": "pwadmin@test.local", "password": "s3cret-enough"})
    res = raw_client.post("/api/v1/teams", json={"name": "With CSRF"}, headers=_csrf_header(raw_client))
    assert res.status_code == 201


def test_logout_revokes_session(raw_client: TestClient, password_mode, pw_user) -> None:
    raw_client.post("/api/v1/auth/login", json={"email": "pw@test.local", "password": "correct horse battery staple"})
    logout = raw_client.post("/api/v1/auth/logout", headers=_csrf_header(raw_client))
    assert logout.status_code == 204
    assert raw_client.get("/api/v1/auth/me").status_code == 401


def test_expired_idle_session_is_401(raw_client: TestClient, password_mode, pw_user, db: Session) -> None:
    raw_client.post("/api/v1/auth/login", json={"email": "pw@test.local", "password": "correct horse battery staple"})
    session_id = raw_client.cookies["adf_session"]
    session = db.get(models.Session, session_id)
    session.idle_expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    assert raw_client.get("/api/v1/auth/me").status_code == 401


def test_change_password_then_login_with_new_password(raw_client: TestClient, password_mode, pw_user) -> None:
    raw_client.post("/api/v1/auth/login", json={"email": "pw@test.local", "password": "correct horse battery staple"})
    changed = raw_client.post(
        "/api/v1/auth/change-password",
        json={"currentPassword": "correct horse battery staple", "newPassword": "a whole new passphrase"},
        headers=_csrf_header(raw_client),
    )
    assert changed.status_code == 200
    assert changed.json()["mustChangePassword"] is False

    raw_client.cookies.clear()
    relogin = raw_client.post(
        "/api/v1/auth/login", json={"email": "pw@test.local", "password": "a whole new passphrase"}
    )
    assert relogin.status_code == 200


def test_auth_config_exposes_idle_timeout_only_in_password_mode(raw_client: TestClient, monkeypatch) -> None:
    assert raw_client.get("/api/v1/auth/config").json()["idleTimeoutMinutes"] is None
    monkeypatch.setattr(settings, "auth_mode", "password")
    body = raw_client.get("/api/v1/auth/config").json()
    assert body["authMode"] == "password"
    assert body["idleTimeoutMinutes"] == settings.auth_idle_timeout_minutes
