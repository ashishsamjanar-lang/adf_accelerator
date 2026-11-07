"""Saved-connection ("connection library") endpoints (§24.17, §14).

A connection bundles a linked service + its table/file onboarding list +
dataset shape into one reusable, project-scoped row — the metastore-backed
counterpart to the frontend's browser-only `lib/adf/connection-store.ts`
(kept there too, as an offline cache/fallback, same as every other resource).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import ConnectionCreateIn, ConnectionOut, ConnectionUpdateIn
from app.errors import ApiError
from app.rbac import require_permission, require_scope, scoped_team_id
from app.services import connection_to_dict, create_connection, delete_connection, update_connection

router = APIRouter(prefix="/connections", tags=["connections"])


def _load(db: Session, connection_id: str, user: models.User) -> models.ConnectionLibraryEntry:
    connection = repository.get_connection(db, connection_id)
    if connection is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "connection_not_found", f"Connection '{connection_id}' does not exist."
        )
    require_scope(user, connection.team_id)  # §27.4
    return connection


@router.get(
    "", response_model=list[ConnectionOut], dependencies=[Depends(require_permission("connection", "view"))]
)
def list_connections(
    project_id: str | None = None,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> list[dict]:
    team_id = None
    if project_id:
        project = repository.get_project(db, project_id)
        if project is not None:
            require_scope(user, project.team_id)
    else:
        # No project filter: narrow to the caller's own team (§27.4), same as
        # `GET /pipelines` / `GET /projects` with no filter.
        team_id = scoped_team_id(user, None)
    return [connection_to_dict(db, c) for c in repository.list_connections(db, project_id, team_id)]


@router.post(
    "",
    response_model=ConnectionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("connection", "create"))],
)
def create(
    body: ConnectionCreateIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    project = repository.get_project(db, body.project_id)
    if project is not None:
        require_scope(user, project.team_id)  # §27.4
    connection = create_connection(
        db,
        project_id=body.project_id,
        name=body.name,
        role=body.role,
        connector_type=body.connector_type,
        description=body.description,
        draft=body.draft,
        actor=user.id,
    )
    return connection_to_dict(db, connection)


@router.get(
    "/{connection_id}",
    response_model=ConnectionOut,
    dependencies=[Depends(require_permission("connection", "view"))],
)
def get(connection_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> dict:
    return connection_to_dict(db, _load(db, connection_id, user))


@router.put(
    "/{connection_id}",
    response_model=ConnectionOut,
    dependencies=[Depends(require_permission("connection", "edit"))],
)
def update(
    connection_id: str,
    body: ConnectionUpdateIn,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    connection = update_connection(
        db,
        _load(db, connection_id, user),
        name=body.name,
        role=body.role,
        connector_type=body.connector_type,
        description=body.description,
        draft=body.draft,
    )
    return connection_to_dict(db, connection)


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    # See the identical comment on `admin.py`'s `delete_environment` — required
    # for a 204 route to import cleanly under `from __future__ import annotations`.
    response_model=None,
    dependencies=[Depends(require_permission("connection", "delete"))],
)
def remove(connection_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> None:
    delete_connection(db, _load(db, connection_id, user), actor=user.id)
