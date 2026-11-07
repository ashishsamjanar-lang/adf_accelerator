"""Usernames for AUTH_MODE=password (migration 0021), admin-chosen initial
passwords, and the SSO scaffold (app/identity/) answering 501."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import manage, models
from app.config import settings
from app.identity import OidcIdentityProvider, SamlIdentityProvider, SsoNotImplementedError, get_identity_provider
from app.security import hash_password, username_from_email, verify_password


@pytest.fixture()
def password_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "password")


@pytest.fixture()
def named_user(db: Session) -> models.User:
    user = models.User(
        id="u-named",
        name="Named User",
        email="named@test.local",
        username="named.user",
        role="DEVELOPER",
        team_id="team-a",
        password_hash=hash_password("a strong passphrase"),
    )
    db.add(user)
    db.commit()
    return user


# --- login -----------------------------------------------------------------


def test_login_with_username(raw_client: TestClient, password_mode, named_user) -> None:
    res = raw_client.post("/api/v1/auth/login", json={"username": "named.user", "password": "a strong passphrase"})
    assert res.status_code == 200
    assert res.json()["username"] == "named.user"


def test_login_username_is_case_insensitive(raw_client: TestClient, password_mode, named_user) -> None:
    res = raw_client.post("/api/v1/auth/login", json={"username": " Named.User ", "password": "a strong passphrase"})
    assert res.status_code == 200


def test_login_field_also_accepts_an_email(raw_client: TestClient, password_mode, named_user) -> None:
    res = raw_client.post(
        "/api/v1/auth/login", json={"username": "named@test.local", "password": "a strong passphrase"}
    )
    assert res.status_code == 200


def test_login_wrong_password_says_username_or_password(raw_client: TestClient, password_mode, named_user) -> None:
    res = raw_client.post("/api/v1/auth/login", json={"username": "named.user", "password": "nope"})
    assert res.status_code == 401
    assert res.json()["error"]["message"] == "Incorrect username or password."


# --- admin onboarding --------------------------------------------------------


def test_admin_creates_user_with_username_and_chosen_password(
    client: TestClient, seed: dict[str, str], db: Session
) -> None:
    res = client.post(
        "/api/v1/admin/users",
        json={
            "name": "Chosen",
            "email": "chosen@test.local",
            "username": "Chosen_1",
            "password": "initial-pass-123",
            "role": "VIEWER",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["username"] == "chosen_1"
    assert body["tempPassword"] is None
    assert body["mustChangePassword"] is True
    row = db.get(models.User, body["id"])
    assert verify_password("initial-pass-123", row.password_hash)


def test_admin_create_derives_username_from_email_when_omitted(client: TestClient, seed: dict[str, str]) -> None:
    res = client.post("/api/v1/admin/users", json={"name": "D", "email": "Derived.Name@test.local", "role": "VIEWER"})
    assert res.status_code == 201
    assert res.json()["username"] == "derived.name"
    assert res.json()["tempPassword"]


def test_admin_create_rejects_duplicate_username(client: TestClient, seed: dict[str, str]) -> None:
    client.post("/api/v1/admin/users", json={"name": "A", "email": "a1@test.local", "username": "dupe", "role": "VIEWER"})
    dup = client.post(
        "/api/v1/admin/users", json={"name": "B", "email": "b1@test.local", "username": "dupe", "role": "VIEWER"}
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "username_exists"


@pytest.mark.parametrize("bad", ["a", "has space", "x@y", "-leading"])
def test_admin_create_rejects_invalid_username(client: TestClient, seed: dict[str, str], bad: str) -> None:
    res = client.post("/api/v1/admin/users", json={"name": "B", "email": "bad@test.local", "username": bad, "role": "VIEWER"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_username"


def test_admin_create_rejects_short_chosen_password(client: TestClient, seed: dict[str, str]) -> None:
    res = client.post(
        "/api/v1/admin/users",
        json={"name": "S", "email": "short@test.local", "username": "short", "password": "abc", "role": "VIEWER"},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "weak_password"


def test_username_from_email() -> None:
    assert username_from_email("Jane.Doe+adf@contoso.com") == "jane.doe.adf"


# --- bootstrap CLI -----------------------------------------------------------


@pytest.fixture()
def cli_db(db: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    from contextlib import contextmanager

    @contextmanager
    def _scope():
        yield db
        db.commit()

    monkeypatch.setattr(manage, "session_scope", _scope)
    return db


def test_create_platform_admin_sets_username(cli_db: Session) -> None:
    manage.create_platform_admin("root@example.com", "Root", "a-fixed-password", "root")
    user = cli_db.query(models.User).filter_by(email="root@example.com").one()
    assert user.username == "root"


def test_create_platform_admin_defaults_username_from_email(cli_db: Session) -> None:
    manage.create_platform_admin("ops.admin@example.com", "Ops", "a-fixed-password")
    user = cli_db.query(models.User).filter_by(email="ops.admin@example.com").one()
    assert user.username == "ops.admin"


# --- SSO scaffold -------------------------------------------------------------


def _sso_config(db: Session, protocol: str, *, enabled: bool = True) -> models.SsoConfiguration:
    cfg = models.SsoConfiguration(id=f"sso-{protocol}", name=f"IdP {protocol}", protocol=protocol, enabled=enabled)
    db.add(cfg)
    db.commit()
    return cfg


@pytest.mark.parametrize("protocol", ["oidc", "saml"])
def test_sso_routes_answer_501_not_implemented(raw_client: TestClient, db: Session, protocol: str) -> None:
    _sso_config(db, protocol)
    for res in (
        raw_client.get(f"/api/v1/auth/sso/sso-{protocol}/login"),
        raw_client.get(f"/api/v1/auth/sso/sso-{protocol}/callback"),
    ):
        assert res.status_code == 501
        assert res.json()["error"]["code"] == "sso_not_implemented"


def test_sso_route_404s_for_a_disabled_configuration(raw_client: TestClient, db: Session) -> None:
    _sso_config(db, "oidc", enabled=False)
    assert raw_client.get("/api/v1/auth/sso/sso-oidc/login").status_code == 404


def test_identity_provider_factory_picks_by_protocol(db: Session) -> None:
    oidc = get_identity_provider(_sso_config(db, "oidc"), "https://app.example.com/")
    saml = get_identity_provider(_sso_config(db, "saml"), "https://app.example.com")
    assert isinstance(oidc, OidcIdentityProvider)
    assert isinstance(saml, SamlIdentityProvider)
    assert oidc.redirect_uri == "https://app.example.com/api/v1/auth/sso/sso-oidc/callback"
    with pytest.raises(SsoNotImplementedError):
        oidc.begin_login()
