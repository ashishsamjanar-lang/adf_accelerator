"""Managed identities ("credentials" pick-list) endpoints (§32.6, §32.8, §14).

An admin-curated list of user-assigned managed identities (UAMI) a builder
picks from — instead of typing a raw ARM resource ID freehand — once §32.9
phase MI3's Linked Service designer picker lands. TEAM_ADMIN+ only to
create/edit/delete (infrastructure trust configuration); any role may view.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import ManagedIdentityCreateIn, ManagedIdentityOut, ManagedIdentityUpdateIn
from app.errors import ApiError
from app.rbac import require_permission, require_scope, scoped_team_id
from app.services import create_managed_identity, delete_managed_identity, managed_identity_to_dict, update_managed_identity

router = APIRouter(prefix="/managed-identities", tags=["managed-identities"])


def _load(db: Session, identity_id: str, user: models.User) -> models.ManagedIdentity:
    identity = repository.get_managed_identity(db, identity_id)
    if identity is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "managed_identity_not_found", f"Managed identity '{identity_id}' does not exist."
        )
    require_scope(user, identity.team_id)  # §27.4
    return identity


@router.get(
    "", response_model=list[ManagedIdentityOut], dependencies=[Depends(require_permission("managed_identity", "view"))]
)
def list_managed_identities(
    team_id: str | None = None,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> list[dict]:
    scoped = scoped_team_id(user, team_id)
    return [managed_identity_to_dict(db, i) for i in repository.list_managed_identities(db, scoped)]


@router.post(
    "",
    response_model=ManagedIdentityOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("managed_identity", "create"))],
)
def create(
    body: ManagedIdentityCreateIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    # A non-PLATFORM_ADMIN may only ever register on their own team, regardless of what
    # `teamId` they send — same "role check + scope check, not either alone" pattern as
    # every other write route (§27.4).
    team_id = body.team_id or user.team_id
    require_scope(user, team_id)
    if not team_id:
        raise ApiError(422, "managed_identity_team_required", "A team is required.")
    identity = create_managed_identity(
        db, team_id=team_id, name=body.name, resource_id=body.resource_id, client_id=body.client_id, actor=user.id
    )
    return managed_identity_to_dict(db, identity)


@router.get(
    "/{identity_id}",
    response_model=ManagedIdentityOut,
    dependencies=[Depends(require_permission("managed_identity", "view"))],
)
def get(identity_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> dict:
    return managed_identity_to_dict(db, _load(db, identity_id, user))


@router.put(
    "/{identity_id}",
    response_model=ManagedIdentityOut,
    dependencies=[Depends(require_permission("managed_identity", "edit"))],
)
def update(
    identity_id: str,
    body: ManagedIdentityUpdateIn,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    identity = update_managed_identity(
        db, _load(db, identity_id, user), name=body.name, resource_id=body.resource_id, client_id=body.client_id
    )
    return managed_identity_to_dict(db, identity)


@router.delete(
    "/{identity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(require_permission("managed_identity", "delete"))],
)
def remove(identity_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> None:
    delete_managed_identity(db, _load(db, identity_id, user), actor=user.id)
