"""`python -m app.manage create-platform-admin` (§27 extension — "platform
admin added from backend", never a public sign-up route). Runs directly
against the same in-memory SQLite database the `db` fixture (`tests/
conftest.py`) is bound to, via a monkeypatched `session_scope` — reusing that
fixture's own session/engine (not a freshly-imported one) matters here: this
test module has no `tests/__init__.py`, so importing `tests.conftest`
separately would create a second module object with its own, empty engine.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy.orm import Session

from app import manage, models
from app.security import verify_password


@pytest.fixture(autouse=True)
def _patch_session_scope(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    @contextmanager
    def _scope():
        yield db
        db.commit()

    monkeypatch.setattr(manage, "session_scope", _scope)


def test_create_platform_admin_creates_a_new_user(db: Session, capsys: pytest.CaptureFixture[str]) -> None:
    manage.create_platform_admin("admin@example.com", "Root Admin", None)
    out = capsys.readouterr().out
    assert "Temporary password" in out

    user = db.query(models.User).filter_by(email="admin@example.com").one()
    assert user.role == "PLATFORM_ADMIN"
    assert user.must_change_password is True
    assert user.active is True


def test_create_platform_admin_is_idempotent_and_promotes_existing_user(db: Session) -> None:
    db.add(
        models.User(
            id="u-existing", name="Someone", email="admin@example.com", role="DEVELOPER", active=False
        )
    )
    db.commit()

    manage.create_platform_admin("admin@example.com", "Root Admin", "a-fixed-password")

    db.expire_all()
    user = db.query(models.User).filter_by(email="admin@example.com").one()
    assert user.id == "u-existing"
    assert user.role == "PLATFORM_ADMIN"
    assert user.active is True
    assert verify_password("a-fixed-password", user.password_hash)
