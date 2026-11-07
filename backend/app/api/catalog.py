"""§24.8 Git-backed catalog + §24.9/§24.10 catalog changes + §26.6 onboarding
Git drift. Thin: logic lives in ``app/catalog_services.py``; the worker does
every provider call."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import CatalogChangeIn, CatalogRefreshIn
from app.catalog_services import (
    catalog_artifact,
    catalog_change_to_dict,
    catalog_impact,
    list_catalog,
    list_catalog_changes,
    onboarding_git_drift,
    request_catalog_change,
    request_catalog_refresh,
)
from app.errors import ApiError
from app.integration_services import integration_job_to_dict
from app.rbac import require_permission, require_scope

router = APIRouter(tags=["catalog"])


def _load_project(db: Session, project_id: str, user: models.User) -> models.Project:
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(404, "project_not_found", f"Project '{project_id}' does not exist.")
    require_scope(user, project.team_id)
    return project


@router.get("/projects/{project_id}/catalog", dependencies=[Depends(require_permission("artifact", "view"))])
def get_catalog(
    project_id: str,
    branch: str | None = None,
    type: str | None = Query(default=None),  # noqa: A002 - matches the §24.16 query param
    q: str | None = None,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    _load_project(db, project_id, user)
    return list_catalog(db, project_id, branch=branch, artifact_type=type, q=q)


@router.post(
    "/projects/{project_id}/catalog/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("artifact", "view"))],
)
def refresh_catalog(
    project_id: str, body: CatalogRefreshIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    project = _load_project(db, project_id, user)
    job = request_catalog_refresh(db, project, branch=body.branch, git_token=body.git_token, actor=user.id)
    db.commit()
    return integration_job_to_dict(job)


@router.get("/projects/{project_id}/catalog/artifact", dependencies=[Depends(require_permission("artifact", "view"))])
def get_catalog_artifact(
    project_id: str,
    path: str,
    branch: str | None = None,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    _load_project(db, project_id, user)
    return catalog_artifact(db, project_id, branch=branch, path=path)


@router.get("/projects/{project_id}/catalog/impact", dependencies=[Depends(require_permission("artifact", "view"))])
def get_catalog_impact(
    project_id: str,
    path: str,
    branch: str | None = None,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    _load_project(db, project_id, user)
    return catalog_impact(db, project_id, branch=branch, path=path)


@router.post(
    "/projects/{project_id}/catalog/changes",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("git", "push"))],
)
def create_catalog_change(
    project_id: str, body: CatalogChangeIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    project = _load_project(db, project_id, user)
    change = request_catalog_change(
        db,
        project,
        mode=body.mode,
        path=body.path,
        content=body.content,
        new_name=body.new_name,
        rewrite_references=body.rewrite_references,
        force=body.force,
        feature_branch=body.feature_branch,
        commit_message=body.commit_message,
        pr_title=body.pr_title,
        pr_body=body.pr_body,
        git_token=body.git_token,
        actor=user.id,
    )
    db.commit()
    return catalog_change_to_dict(change)


@router.get("/projects/{project_id}/catalog/changes", dependencies=[Depends(require_permission("artifact", "view"))])
def get_catalog_changes(
    project_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> list[dict]:
    _load_project(db, project_id, user)
    return [catalog_change_to_dict(c) for c in list_catalog_changes(db, project_id)]


@router.get(
    "/projects/{project_id}/onboarding/git-drift",
    dependencies=[Depends(require_permission("onboarding_object", "view"))],
)
def get_onboarding_git_drift(
    project_id: str,
    branch: str | None = None,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    _load_project(db, project_id, user)
    return onboarding_git_drift(db, project_id, branch=branch)
