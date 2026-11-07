"""Auth endpoints (§14, §23.4, §27.2/§27.6, password-mode extension)."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import update
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import AuthConfigOut, ChangePasswordIn, DevSwitchUserIn, LoginIn, UserOut
from app.config import settings
from app.db.base import as_aware_utc, new_id, utcnow
from app.errors import ApiError
from app.identity import SsoNotImplementedError, get_identity_provider
from app.security import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    hash_password,
    new_token,
    verify_password,
)
from app.services import user_to_dict

router = APIRouter(prefix="/auth", tags=["auth"])

# httpOnly session cookie is only safe to omit `Secure` on in local dev over
# plain HTTP (see `app_env`); a real deployment always sets it.
_COOKIE_SECURE = settings.app_env != "local"


@router.get("/config", response_model=AuthConfigOut)
def auth_config() -> dict:
    """Public, unauthenticated — the frontend uses this to decide whether to
    render the mock dev-switcher or a real email/password login screen."""
    # `idleTimeoutMinutes` drives the frontend's pre-expiry warning (§27 session UX) — only
    # meaningful when a real sliding session exists, so it's null in every other mode.
    idle = settings.auth_idle_timeout_minutes if settings.auth_mode == "password" else None
    return {"authMode": settings.auth_mode, "idleTimeoutMinutes": idle}


@router.get("/me", response_model=UserOut)
def me(user: models.User = Depends(current_user)) -> dict:
    return user_to_dict(user)


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, response: Response, db: Session = Depends(db_session)) -> dict:
    """AUTH_MODE=password only — 404 otherwise (mirrors `_require_mock_mode`,
    inverted: this endpoint is meaningless, not just hidden, outside password
    mode)."""
    _require_password_mode()
    user = repository.get_user_by_login(db, body.username or body.email)
    now = utcnow()
    if user is not None and user.locked_until and as_aware_utc(user.locked_until) > now:
        raise ApiError(
            423, "account_locked", f"Account is locked until {user.locked_until.isoformat()}."
        )
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        if user is not None and user.active:
            # A plain `user.failed_login_attempts += 1; db.commit()` is a
            # classic lost-update race: N concurrent requests (e.g. a
            # credential-stuffing burst) can all read the same starting count
            # before any of them commits, so several increments collapse into
            # one and the account may never reach the lockout threshold
            # (found via `tests/test_concurrency.py`'s 25-way concurrent
            # login test, 2026-09-13). An atomic `SET x = x + 1` pushes the
            # increment into the database's own row-level locking instead of
            # Python-side read-modify-write, so every attempt is counted
            # exactly once regardless of how many race in at once.
            new_count = db.execute(
                update(models.User)
                .where(models.User.id == user.id)
                .values(failed_login_attempts=models.User.failed_login_attempts + 1)
                .returning(models.User.failed_login_attempts)
            ).scalar_one()
            if new_count >= settings.auth_max_failed_logins:
                db.execute(
                    update(models.User)
                    .where(models.User.id == user.id)
                    .values(locked_until=now + timedelta(minutes=settings.auth_lockout_minutes))
                )
            # Raising below rolls the request back (see `db_session`'s
            # generator-cleanup semantics) — commit the failure count/lock
            # first, or every failed attempt would be silently un-recorded.
            db.commit()
        raise ApiError(401, "invalid_credentials", "Incorrect username or password.")

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_seen = now
    session = models.Session(
        id=new_id("sess"),
        user_id=user.id,
        issued_at=now,
        expires_at=now + timedelta(hours=settings.auth_absolute_timeout_hours),
        idle_expires_at=now + timedelta(minutes=settings.auth_idle_timeout_minutes),
        csrf_token=new_token(),
    )
    db.add(session)
    repository.record_audit(db, actor=user.name, action="auth.login", target=user.id, team_id=user.team_id)
    db.flush()
    _set_session_cookies(response, session)
    return user_to_dict(user)


@router.post("/logout", response_model=None, status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(db_session)) -> None:
    """Revokes the caller's session (password mode) and always clears the
    cookies — safe to call in any mode, including mock, where it's a no-op.

    Reads the cookie directly rather than depending on ``current_user`` so an
    already-expired/invalid session still gets its cookies cleared instead of
    401ing on the way out.
    """
    if settings.auth_mode == "password":
        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        if session_id and (session := repository.get_session(db, session_id)):
            repository.revoke_session(db, session)
            db.flush()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


@router.post("/change-password", response_model=UserOut)
def change_password(
    body: ChangePasswordIn,
    response: Response,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    _require_password_mode()
    if not verify_password(body.current_password, user.password_hash):
        raise ApiError(401, "invalid_credentials", "Current password is incorrect.")
    if len(body.new_password) < settings.auth_min_password_length:
        raise ApiError(
            422, "weak_password", f"New password must be at least {settings.auth_min_password_length} characters."
        )
    user.password_hash = hash_password(body.new_password)
    user.password_updated_at = utcnow()
    user.must_change_password = False
    repository.record_audit(db, actor=user.name, action="auth.password_changed", target=user.id, team_id=user.team_id)
    db.flush()
    return user_to_dict(user)


@router.get("/dev/users", response_model=list[UserOut])
def dev_list_users(db: Session = Depends(db_session)) -> list[dict]:
    """The dev user-switcher's picker list — one seeded user per role (§27.2).

    ``AUTH_MODE=mock`` only; a genuine security boundary, not a convenience
    flag (§27.9 assumption 3), so it 404s otherwise rather than reaching a
    real deployment.
    """
    _require_mock_mode()
    return [user_to_dict(u) for u in repository.list_users(db)]


@router.post("/dev/switch-user", response_model=UserOut)
def dev_switch_user(body: DevSwitchUserIn, db: Session = Depends(db_session)) -> dict:
    """Validate the requested identity for the dev user-switcher (closes §23.12 gap #16).

    There is no session store in mock mode (that's `password` mode's job),
    so this endpoint only validates the target user exists and is active;
    the frontend then sends its id back as ``X-Debug-User-Id`` on every
    subsequent request (see ``current_user`` in ``deps.py``), which is what
    actually changes who each request acts as. ``AUTH_MODE=mock`` only —
    404s otherwise.
    """
    _require_mock_mode()
    user = repository.get_user(db, body.user_id)
    if user is None or not user.active:
        raise ApiError(404, "user_not_found", f"No active mock user '{body.user_id}'.")
    return user_to_dict(user)


def _set_session_cookies(response: Response, session: models.Session) -> None:
    max_age = settings.auth_absolute_timeout_hours * 3600
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session.id,
        max_age=max_age,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    # Deliberately NOT httpOnly — the frontend reads this one and echoes it
    # back as `X-CSRF-Token` (double-submit CSRF pattern, §27.3).
    response.set_cookie(
        CSRF_COOKIE_NAME,
        session.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


# --- SSO (§27.2/§27.5) — scaffolding only, see app/identity/ -----------------
# Both routes exist so the redirect/callback URLs are fixed and the adapter
# contract is exercised end to end, but every provider raises
# SsoNotImplementedError, answered here as 501. No session is ever issued.


@router.get("/sso/{configuration_id}/login", response_model=None)
def sso_login(configuration_id: str, db: Session = Depends(db_session)) -> None:
    provider = get_identity_provider(_enabled_sso_configuration(db, configuration_id), settings.sso_callback_base_url)
    try:
        provider.begin_login()
    except SsoNotImplementedError as exc:
        raise ApiError(501, "sso_not_implemented", str(exc)) from exc


@router.api_route("/sso/{configuration_id}/callback", methods=["GET", "POST"], response_model=None)
def sso_callback(configuration_id: str, request: Request, db: Session = Depends(db_session)) -> None:
    provider = get_identity_provider(_enabled_sso_configuration(db, configuration_id), settings.sso_callback_base_url)
    try:
        provider.complete_login(dict(request.query_params), expected_state="", expected_nonce=None)
    except SsoNotImplementedError as exc:
        raise ApiError(501, "sso_not_implemented", str(exc)) from exc


def _enabled_sso_configuration(db: Session, configuration_id: str) -> models.SsoConfiguration:
    cfg = db.get(models.SsoConfiguration, configuration_id)
    if cfg is None or not cfg.enabled:
        raise ApiError(404, "sso_configuration_not_found", f"No enabled SSO configuration '{configuration_id}'.")
    return cfg


def _require_mock_mode() -> None:
    if settings.auth_mode != "mock":
        raise ApiError(404, "not_found", "Not found.")


def _require_password_mode() -> None:
    if settings.auth_mode != "password":
        raise ApiError(404, "not_found", "Not found.")
