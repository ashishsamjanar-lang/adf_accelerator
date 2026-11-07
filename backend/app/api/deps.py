"""Request dependencies — auth/RBAC (AGENTS.md §11, §23.4, §27.2/§27.6).

``AUTH_MODE=mock`` resolves the caller to the seeded platform admin by
default, or to whichever seeded user ``X-Debug-User-Id`` names — the dev-only
per-request identity switch that closes §23.12 gap #16 (mock auth previously
had no way to act as anyone but the first admin). The header is honored only
in mock mode.

``AUTH_MODE=password`` (§27.2 extension) resolves the caller from the
``adf_session`` cookie against the ``sessions`` table (§27.3): idle/absolute
timeout, revocation, and — on any state-changing request — a CSRF header
check against the session's stored token (double-submit pattern, since a
cookie-based session is otherwise CSRF-exposed).

``AUTH_MODE=oidc``/``saml`` (real SSO, §27.2/§27.5) are not implemented yet —
fails closed with 501, same as before.

RBAC enforcement itself lives in ``app/rbac.py`` (``require_permission``), not
here — this module only resolves *who* is calling.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app import models, repository
from app.config import settings
from app.db.base import as_aware_utc, utcnow
from app.db.session import get_session
from app.errors import ApiError
from app.security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, UNSAFE_METHODS

MOCK_USER = models.User(
    id="user-mock-admin",
    name="Local Admin",
    email="admin@localhost",
    role="PLATFORM_ADMIN",
    team_id=None,
    active=True,
)


def db_session() -> Iterator[Session]:
    yield from get_session()


def current_user(
    request: Request,
    db: Session = Depends(db_session),
    x_debug_user_id: str | None = Header(default=None, alias="X-Debug-User-Id"),
) -> models.User:
    if settings.auth_mode == "mock":
        if x_debug_user_id:
            user = repository.get_user(db, x_debug_user_id)
            if user is None or not user.active:
                raise ApiError(
                    401,
                    "unknown_mock_user",
                    f"X-Debug-User-Id '{x_debug_user_id}' does not match an active seeded user.",
                )
            return user
        if admin := repository.first_admin(db):
            return admin
        users = repository.list_users(db)
        return users[0] if users else MOCK_USER

    if settings.auth_mode == "password":
        return _current_user_from_session(request, db)

    raise ApiError(
        501,
        "auth_not_implemented",
        f"AUTH_MODE={settings.auth_mode!r} is not implemented yet (SSO is scaffolded in app/identity/ only). "
        "Set AUTH_MODE=password.",
    )


def _current_user_from_session(request: Request, db: Session) -> models.User:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise ApiError(401, "unauthenticated", "No session. Log in via POST /auth/login.")

    session = repository.get_session(db, session_id)
    now = utcnow()
    if (
        session is None
        or session.revoked_at is not None
        or as_aware_utc(session.expires_at) <= now
        or as_aware_utc(session.idle_expires_at) <= now
    ):
        raise ApiError(401, "session_expired", "Session is expired or revoked. Log in again.")

    user = repository.get_user(db, session.user_id)
    if user is None or not user.active:
        raise ApiError(401, "unauthenticated", "Session's user is no longer active.")

    if request.method in UNSAFE_METHODS:
        header_token = request.headers.get(CSRF_HEADER_NAME)
        if not header_token or header_token != session.csrf_token:
            raise ApiError(403, "csrf_check_failed", "Missing or invalid CSRF token.")

    # Slide the idle window forward on every authenticated request.
    session.idle_expires_at = now + timedelta(minutes=settings.auth_idle_timeout_minutes)
    return user
