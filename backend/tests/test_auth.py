"""Mock-identity + dev user-switcher tests (AGENTS.md §27.2/§27.6, §23.12 gap #16).

Unlike the other router tests, these exercise ``current_user`` itself — the
real dependency, with the real ``X-Debug-User-Id`` header handling — so they
use the ``raw_client`` fixture (DB overridden, identity resolution untouched).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings


def _seed_users(db):
    from app import models

    db.add(models.User(id="u-1", name="Admin One", email="admin1@test.local", role="PLATFORM_ADMIN", team_id=None))
    db.add(models.User(id="u-2", name="Dev Two", email="dev2@test.local", role="DEVELOPER", team_id=None))
    db.add(models.User(id="u-3", name="Inactive Three", email="inactive3@test.local", role="VIEWER", active=False, team_id=None))
    db.commit()


def test_me_defaults_to_first_admin_with_no_header(raw_client: TestClient, db) -> None:
    _seed_users(db)
    res = raw_client.get("/api/v1/auth/me")
    assert res.status_code == 200
    assert res.json()["id"] == "u-1"


def test_x_debug_user_id_switches_identity(raw_client: TestClient, db) -> None:
    _seed_users(db)
    res = raw_client.get("/api/v1/auth/me", headers={"X-Debug-User-Id": "u-2"})
    assert res.status_code == 200
    assert res.json()["id"] == "u-2"
    assert res.json()["role"] == "DEVELOPER"


def test_x_debug_user_id_rejects_unknown_user(raw_client: TestClient, db) -> None:
    _seed_users(db)
    res = raw_client.get("/api/v1/auth/me", headers={"X-Debug-User-Id": "does-not-exist"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unknown_mock_user"


def test_x_debug_user_id_rejects_inactive_user(raw_client: TestClient, db) -> None:
    _seed_users(db)
    res = raw_client.get("/api/v1/auth/me", headers={"X-Debug-User-Id": "u-3"})
    assert res.status_code == 401


def test_dev_switch_user_validates_target(raw_client: TestClient, db) -> None:
    _seed_users(db)
    ok = raw_client.post("/api/v1/auth/dev/switch-user", json={"userId": "u-2"})
    assert ok.status_code == 200
    assert ok.json()["id"] == "u-2"

    missing = raw_client.post("/api/v1/auth/dev/switch-user", json={"userId": "nope"})
    assert missing.status_code == 404


def test_dev_list_users(raw_client: TestClient, db) -> None:
    _seed_users(db)
    res = raw_client.get("/api/v1/auth/dev/users")
    assert res.status_code == 200
    assert {u["id"] for u in res.json()} == {"u-1", "u-2", "u-3"}


def test_dev_endpoints_404_outside_mock_mode(raw_client: TestClient, db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "entra")
    assert raw_client.get("/api/v1/auth/dev/users").status_code == 404
    assert raw_client.post("/api/v1/auth/dev/switch-user", json={"userId": "u-1"}).status_code == 404
