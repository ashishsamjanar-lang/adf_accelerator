"""Project + project-settings endpoints (§5, §14, §23.4)."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import ProjectCreateIn, ProjectOut
from app.db.base import new_id
from app.errors import ApiError
from app.rbac import require_permission, require_scope, scoped_team_id
from app.services import project_to_dict

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut], dependencies=[Depends(require_permission("project", "view"))])
def list_projects(
    team_id: str | None = None,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> list[dict]:
    # §27.4 scope: a non-admin's request narrows to (or is rejected outside)
    # their own team — see `scoped_team_id`. Previously `team_id=None` here
    # returned every team's projects to any authenticated caller.
    team_id = scoped_team_id(user, team_id)
    return [project_to_dict(db, p) for p in repository.list_projects(db, team_id)]


@router.post(
    "",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("project", "create"))],
)
def create_project(
    body: ProjectCreateIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    if repository.get_team(db, body.team_id) is None:
        raise ApiError(422, "team_not_found", f"Team '{body.team_id}' does not exist.")
    project = models.Project(
        id=new_id("proj"), team_id=body.team_id, name=body.name, description=body.description, created_by=user.id
    )
    db.add(project)
    db.add(models.ProjectSettings(project_id=project.id, document=_default_settings(project), updated_by=user.name))
    repository.record_audit(db, actor=user.name, action="project.created", target=body.name, team_id=body.team_id)
    db.flush()
    return project_to_dict(db, project, include_settings=True)


@router.get(
    "/{project_id}", response_model=ProjectOut, dependencies=[Depends(require_permission("project", "view"))]
)
def get_project(
    project_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "project_not_found", f"Project '{project_id}' does not exist.")
    require_scope(user, project.team_id)
    return project_to_dict(db, project, include_settings=True)


@router.get("/{project_id}/settings", dependencies=[Depends(require_permission("project", "view"))])
def get_settings(
    project_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict[str, Any]:
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "project_not_found", f"Project '{project_id}' does not exist.")
    require_scope(user, project.team_id)
    settings = repository.get_project_settings(db, project_id)
    return settings.document if settings else _default_settings(project)


@router.put("/{project_id}/settings", dependencies=[Depends(require_permission("project", "edit"))])
def put_settings(
    project_id: str,
    body: dict[str, Any],
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict[str, Any]:
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "project_not_found", f"Project '{project_id}' does not exist.")
    require_scope(user, project.team_id)
    _reject_secret_values(body)
    required_tags = body.get("requiredTags")
    if required_tags is not None and (
        not isinstance(required_tags, dict)
        or (required_tags.get("enforcement") or "off") not in ("off", "warn", "block")
        or not isinstance(required_tags.get("keys") or [], list)
    ):
        raise ApiError(422, "invalid_required_tags", 'requiredTags must be {"enforcement": "off"|"warn"|"block", "keys": [...]}.')
    settings = repository.get_project_settings(db, project_id)
    document = {**body, "projectId": project_id, "updatedBy": user.name}
    if settings is None:
        settings = models.ProjectSettings(project_id=project_id, document=document, updated_by=user.name)
        db.add(settings)
    else:
        settings.document = document
        settings.updated_by = user.name
    repository.record_audit(db, actor=user.name, action="project.settings_updated", target=project.name, team_id=project.team_id)
    db.flush()
    return settings.document


def _default_settings(project: models.Project) -> dict[str, Any]:
    return {
        "projectId": project.id,
        "name": project.name,
        "description": project.description,
        "teamId": project.team_id,
        "tags": [],
        "environmentVariables": [],
        "customMetadata": [],
    }


# Never let a raw secret value land in project_settings (§1.6, §23.5).
_SECRET_KEY_HINTS = ("password", "secret", "pat", "token", "accountkey", "connectionstring", "privatekey")


def _key_words(key: str) -> list[str]:
    # Split camelCase/snake_case/kebab-case into lowercase word tokens so a
    # short hint like "pat" only matches a standalone word (e.g. "gitPat"),
    # never a substring of an unrelated word (e.g. "pat" inside
    # "featureBranchPattern").
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(key))
    return [w for w in re.split(r"[^a-zA-Z0-9]+", spaced.lower()) if w]


def _key_matches_secret_hint(key: str) -> bool:
    words = _key_words(key)
    if any(hint in words for hint in _SECRET_KEY_HINTS):
        return True
    # A few hints are two-word compounds (accountKey, connectionString,
    # privateKey) that split into separate word tokens above.
    joined_pairs = {a + b for a, b in zip(words, words[1:])}
    return any(hint in joined_pairs for hint in _SECRET_KEY_HINTS)


def _reject_secret_values(node: Any, path: str = "") -> None:
    if isinstance(node, dict):
        # environmentVariables rows (§22.3.H) carry an explicit isSecret flag
        # instead of a telling key name — check `value` regardless of the
        # hint list below, or a plain custom key like "key"/"value" sails
        # through untouched.
        if node.get("isSecret") is True and isinstance(node.get("value"), str) and node["value"].strip():
            value = node["value"]
            if not (value.startswith(("@", "SecretRef:")) or ("/" not in value and " " not in value and len(value) < 128)):
                raise ApiError(
                    422,
                    "secret_value_rejected",
                    f"Field '{path}value' looks like a raw secret. Store only the Key Vault secret name.",
                )
        for key, value in node.items():
            if _key_matches_secret_hint(str(key)) and isinstance(value, str) and value.strip():
                # Allow references only: Key Vault secret name, @-expression, or SecretRef.
                if not (value.startswith(("@", "SecretRef:")) or "/" not in value and " " not in value and len(value) < 128):
                    raise ApiError(
                        422,
                        "secret_value_rejected",
                        f"Field '{path}{key}' looks like a raw secret. Store only the Key Vault secret name.",
                    )
            _reject_secret_values(value, f"{path}{key}.")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _reject_secret_values(item, f"{path}{i}.")
