"""Admin + environment endpoints (§11, §12, §23.12 gap 12, IMPLEMENTATION_PLAN.md A-4)."""
from __future__ import annotations

import hashlib
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import AuditOut, EnvironmentIn, EnvironmentOut, SessionOut, UserOut
from app.db.base import new_id, utcnow
from app.errors import ApiError
from app.rbac import require_permission, require_scope, scoped_team_id
from app.security import SESSION_COOKIE_NAME
from app.services import audit_to_dict, environment_to_dict, user_to_dict

router = APIRouter(prefix="/admin", tags=["admin"])
environments_router = APIRouter(prefix="/environments", tags=["environments"])


@router.get(
    "/users", response_model=list[UserOut], dependencies=[Depends(require_permission("admin", "manage_users"))]
)
def list_users(db: Session = Depends(db_session)) -> list[dict]:
    return [user_to_dict(u) for u in repository.list_users(db)]


@router.get("/audit", response_model=list[AuditOut])
def audit_log(
    db: Session = Depends(db_session),
    user: models.User = Depends(require_permission("admin", "view_audit")),
) -> list[dict]:
    """TEAM_ADMIN sees only their own team's rows; PLATFORM_ADMIN sees every team's
    (AGENTS.md §30.7 AQ1) — global actions (``team_id`` NULL, e.g. environment CRUD)
    are PLATFORM_ADMIN-only, the same visibility split as before this scope was added.
    """
    team_id = scoped_team_id(user, None)
    return [audit_to_dict(db, e) for e in repository.list_audit(db, team_id=team_id)]


def _session_handle(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]


def _active_sessions(db: Session, user: models.User) -> list[models.Session]:
    """§27 session admin: live (unrevoked, unexpired) sessions — TEAM_ADMIN sees only
    their own team's users, PLATFORM_ADMIN every team's. Always empty under
    ``AUTH_MODE=mock`` (no sessions are ever issued there)."""
    now = utcnow()
    return repository.list_active_sessions(db, now=now, team_id=scoped_team_id(user, None))


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    request: Request,
    db: Session = Depends(db_session),
    user: models.User = Depends(require_permission("admin", "manage_users")),
) -> list[dict]:
    own = request.cookies.get(SESSION_COOKIE_NAME)
    out: list[dict] = []
    for s in _active_sessions(db, user):
        owner = repository.get_user(db, s.user_id)
        out.append(
            {
                "handle": _session_handle(s.id),
                "userId": s.user_id,
                "userName": owner.name if owner else None,
                "userEmail": owner.email if owner else None,
                "teamId": owner.team_id if owner else None,
                "issuedAt": s.issued_at.isoformat() if s.issued_at else None,
                "idleExpiresAt": s.idle_expires_at.isoformat() if s.idle_expires_at else None,
                "expiresAt": s.expires_at.isoformat() if s.expires_at else None,
                "ip": s.ip,
                "userAgent": s.user_agent,
                "current": own is not None and own == s.id,
            }
        )
    return out


@router.delete("/sessions/{handle}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def revoke_session(
    handle: str,
    db: Session = Depends(db_session),
    user: models.User = Depends(require_permission("admin", "manage_users")),
) -> None:
    """Revokes one live session immediately (sets ``revoked_at``; the next request on it 401s)."""
    session = next((s for s in _active_sessions(db, user) if _session_handle(s.id) == handle), None)
    if session is None:
        raise ApiError(404, "session_not_found", "No active session matches that handle.")
    owner = repository.get_user(db, session.user_id)
    require_scope(user, owner.team_id if owner else None)
    session.revoked_at = utcnow()
    repository.record_audit(
        db, actor=user.id, action="session.revoked", target=owner.email if owner else session.user_id,
        team_id=owner.team_id if owner else None,
    )
    db.flush()


@environments_router.get(
    "", response_model=list[EnvironmentOut], dependencies=[Depends(require_permission("environment", "view"))]
)
def list_environments(db: Session = Depends(db_session)) -> list[dict]:
    """Environments are **connection configuration only** (§23.12 gap 12).

    Real per-DEV/QA/PROD storage/SQL/Key Vault/factory targets a project may
    reference — never a deployment status, never a secret value (§1.6/§20).
    Resolved from the ``environments`` table (IMPLEMENTATION_PLAN.md A-4);
    empty until an admin records the first one via POST below.
    """
    return [environment_to_dict(e) for e in repository.list_environments(db)]


@environments_router.post(
    "",
    response_model=EnvironmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("environment", "manage"))],
)
def create_environment(
    body: EnvironmentIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    if repository.get_environment(db, body.name):
        raise ApiError(409, "environment_exists", f"Environment '{body.name}' already exists.")
    env = models.Environment(
        id=new_id("env"),
        name=body.name,
        label=body.label or body.name,
        status=body.status,
        description=body.description,
        storage_account=body.storage_account,
        sql_server=body.sql_server,
        database=body.database,
        key_vault=body.key_vault,
        factory_name=body.factory_name,
        subscription_id=body.subscription_id,
        resource_group=body.resource_group,
        updated_by=user.name,
    )
    db.add(env)
    repository.record_audit(db, actor=user.name, action="environment.created", target=body.name)
    db.flush()
    return environment_to_dict(env)


@environments_router.put(
    "/{name}", response_model=EnvironmentOut, dependencies=[Depends(require_permission("environment", "manage"))]
)
def update_environment(
    name: str, body: EnvironmentIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    env = repository.get_environment(db, name)
    if env is None:
        raise ApiError(404, "environment_not_found", f"Environment '{name}' does not exist.")
    env.label = body.label or body.name
    env.status = body.status
    env.description = body.description
    env.storage_account = body.storage_account
    env.sql_server = body.sql_server
    env.database = body.database
    env.key_vault = body.key_vault
    env.factory_name = body.factory_name
    env.subscription_id = body.subscription_id
    env.resource_group = body.resource_group
    env.updated_by = user.name
    repository.record_audit(db, actor=user.name, action="environment.updated", target=name)
    db.flush()
    return environment_to_dict(env)


@environments_router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    # Explicit `response_model=None`: with `from __future__ import annotations`
    # (every module in this app), a `-> None` return annotation is the *string*
    # "None" by the time FastAPI inspects it, so it isn't recognized as "no
    # response body" and the 204 status assertion fails at import time unless
    # this is spelled out. (Pre-existing bug, surfaced by adding a second 204
    # route in `connections.py` — fixed there the same way.)
    response_model=None,
    dependencies=[Depends(require_permission("environment", "manage"))],
)
def delete_environment(name: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> None:
    env = repository.get_environment(db, name)
    if env is None:
        raise ApiError(404, "environment_not_found", f"Environment '{name}' does not exist.")
    db.delete(env)
    repository.record_audit(db, actor=user.name, action="environment.deleted", target=name)
    db.flush()
