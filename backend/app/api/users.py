"""User onboarding + management (§11/§27 extension).

New accounts are always created by PLATFORM_ADMIN (`POST /admin/users`) —
there is no self-signup route anywhere in this app. A TEAM_ADMIN may edit an
*existing* member of their own team (role/active), never create an account,
grant PLATFORM_ADMIN, or move someone to a different team.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import ResetPasswordOut, UserCreateIn, UserCreateOut, UserOut, UserUpdateIn
from app.db.base import new_id
from app.errors import ApiError
from app.rbac import require_permission, require_scope
from app.config import settings
from app.security import USERNAME_RE, generate_temp_password, hash_password, normalize_username, username_from_email
from app.services import user_to_dict

router = APIRouter(prefix="/admin/users", tags=["admin"])

_ROLES = {"PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER", "VIEWER"}


@router.post(
    "",
    response_model=UserCreateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("user", "create"))],
)
def create_user(
    body: UserCreateIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    """PLATFORM_ADMIN only (see module docstring). The admin sets the username
    (or it is derived from the email) and either chooses the initial password or
    gets a generated one-time one to relay out of band — this app has no email
    sender. Either way the account must change it on first login."""
    if body.role not in _ROLES:
        raise ApiError(422, "invalid_role", f"Role must be one of {sorted(_ROLES)}.")
    if repository.get_user_by_email(db, body.email):
        raise ApiError(409, "email_exists", f"A user with email '{body.email}' already exists.")
    username = validate_new_username(db, body.username or username_from_email(body.email))
    if body.team_id and repository.get_team(db, body.team_id) is None:
        raise ApiError(422, "team_not_found", f"Team '{body.team_id}' does not exist.")
    if body.password is not None and len(body.password) < settings.auth_min_password_length:
        raise ApiError(
            422, "weak_password", f"Password must be at least {settings.auth_min_password_length} characters."
        )

    temp_password = None if body.password is not None else generate_temp_password()
    new_user = models.User(
        id=new_id("user"),
        name=body.name,
        email=body.email,
        username=username,
        role=body.role,
        team_id=body.team_id,
        active=True,
        password_hash=hash_password(body.password if body.password is not None else temp_password),
        must_change_password=True,
        invited_by=user.id,
    )
    db.add(new_user)
    repository.record_audit(db, actor=user.name, action="user.created", target=new_user.email, team_id=new_user.team_id)
    db.flush()
    return {**user_to_dict(new_user), "tempPassword": temp_password}


def validate_new_username(db: Session, raw: str) -> str:
    """Normalize + validate a username for a new account (also used by
    ``app/manage.py``). 422 on a bad shape, 409 when already taken."""
    username = normalize_username(raw)
    if not USERNAME_RE.match(username):
        raise ApiError(
            422,
            "invalid_username",
            "Username must be 2-64 characters: lowercase letters, digits, '.', '_' or '-', starting with a letter or digit.",
        )
    if repository.get_user_by_username(db, username):
        raise ApiError(409, "username_exists", f"A user with username '{username}' already exists.")
    return username


@router.put("/{user_id}", response_model=UserOut, dependencies=[Depends(require_permission("user", "edit"))])
def update_user(
    user_id: str,
    body: UserUpdateIn,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    target = repository.get_user(db, user_id)
    if target is None:
        raise ApiError(404, "user_not_found", f"User '{user_id}' does not exist.")
    require_scope(user, target.team_id)  # PLATFORM_ADMIN bypasses; TEAM_ADMIN must own this team

    if user.role != "PLATFORM_ADMIN":
        if body.team_id is not None and body.team_id != target.team_id:
            raise ApiError(403, "forbidden", "Only a platform admin can move a user between teams.")
        if body.role == "PLATFORM_ADMIN":
            raise ApiError(403, "forbidden", "Only a platform admin can grant the platform admin role.")

    if body.role is not None and body.role not in _ROLES:
        raise ApiError(422, "invalid_role", f"Role must be one of {sorted(_ROLES)}.")
    if body.team_id is not None and repository.get_team(db, body.team_id) is None:
        raise ApiError(422, "team_not_found", f"Team '{body.team_id}' does not exist.")

    demoting_or_deactivating = (body.role is not None and body.role != "PLATFORM_ADMIN") or body.active is False
    if target.role == "PLATFORM_ADMIN" and demoting_or_deactivating:
        _guard_last_platform_admin(db, target.id)

    if body.name is not None:
        target.name = body.name
    if body.role is not None:
        target.role = body.role
    if body.team_id is not None:
        target.team_id = body.team_id
    if body.active is not None:
        target.active = body.active

    repository.record_audit(db, actor=user.name, action="user.updated", target=target.email, team_id=target.team_id)
    db.flush()
    return user_to_dict(target)


@router.post(
    "/{user_id}/reset-password",
    response_model=ResetPasswordOut,
    dependencies=[Depends(require_permission("user", "edit"))],
)
def reset_password(
    user_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    target = repository.get_user(db, user_id)
    if target is None:
        raise ApiError(404, "user_not_found", f"User '{user_id}' does not exist.")
    require_scope(user, target.team_id)

    temp_password = generate_temp_password()
    target.password_hash = hash_password(temp_password)
    target.must_change_password = True
    target.failed_login_attempts = 0
    target.locked_until = None
    repository.revoke_user_sessions(db, target.id)
    repository.record_audit(db, actor=user.name, action="user.password_reset", target=target.email, team_id=target.team_id)
    db.flush()
    return {"tempPassword": temp_password}


def _guard_last_platform_admin(db: Session, excluding_user_id: str) -> None:
    if repository.count_active_platform_admins(db, exclude_user_id=excluding_user_id) == 0:
        raise ApiError(
            409,
            "last_platform_admin",
            "Cannot demote or deactivate the last active platform admin.",
        )
