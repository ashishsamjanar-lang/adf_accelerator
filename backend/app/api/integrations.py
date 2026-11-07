"""§26.5 Onboarding sync-to-Git, §29.1 layer-6 live ARM check, Purview lineage
push, and their shared per-project integration settings. Thin: validation and
enqueueing live in ``app/integration_services.py``; the worker does the calls."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import IntegrationIn, LiveCheckIn, OnboardingSyncIn, PurviewPushIn
from app.catalog_services import CATALOG_JOB_TYPES
from app.errors import ApiError
from app.integration_services import (
    INTEGRATION_JOB_TYPES,
    get_integration,
    integration_job_to_dict,
    integration_to_dict,
    list_onboarding_syncs,
    onboarding_sync_diff,
    onboarding_sync_to_dict,
    put_integration,
    request_live_check,
    request_onboarding_sync,
    request_purview_push,
)
from app.rbac import require_permission, require_scope

router = APIRouter(tags=["integrations"])


def _load_project(db: Session, project_id: str, user: models.User) -> models.Project:
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(404, "project_not_found", f"Project '{project_id}' does not exist.")
    require_scope(user, project.team_id)
    return project


# --- §26.5 onboarding sync-to-Git -------------------------------------------


@router.post(
    "/projects/{project_id}/onboarding/sync-to-git",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("git", "push"))],
)
def sync_to_git(
    project_id: str, body: OnboardingSyncIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    project = _load_project(db, project_id, user)
    sync = request_onboarding_sync(
        db,
        project,
        files=[f.model_dump() for f in body.files],
        object_keys=body.object_keys,
        provider=body.provider,
        repository_url=body.repository_url,
        feature_branch=body.feature_branch,
        base_branch=body.base_branch,
        commit_message=body.commit_message,
        pr_title=body.pr_title,
        pr_body=body.pr_body,
        reviewers=body.reviewers,
        git_token=body.git_token,
        actor=user.id,
    )
    db.commit()
    return onboarding_sync_to_dict(sync)


@router.get(
    "/projects/{project_id}/onboarding/syncs",
    dependencies=[Depends(require_permission("onboarding_object", "view"))],
)
def list_syncs(project_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> list[dict]:
    _load_project(db, project_id, user)
    return [onboarding_sync_to_dict(s) for s in list_onboarding_syncs(db, project_id)]


@router.get(
    "/projects/{project_id}/onboarding/sync-diff",
    dependencies=[Depends(require_permission("onboarding_object", "view"))],
)
def sync_diff(project_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> dict:
    _load_project(db, project_id, user)
    return onboarding_sync_diff(db, project_id)


# --- integration settings (§29.4 live-check settings + Purview) --------------


@router.get(
    "/projects/{project_id}/integrations/{kind}",
    dependencies=[Depends(require_permission("integration", "view"))],
)
def read_integration(
    project_id: str, kind: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    _load_project(db, project_id, user)
    return integration_to_dict(project_id, kind, get_integration(db, project_id, kind))


@router.put(
    "/projects/{project_id}/integrations/{kind}",
    dependencies=[Depends(require_permission("integration", "configure"))],
)
def update_integration(
    project_id: str,
    kind: str,
    body: IntegrationIn,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    project = _load_project(db, project_id, user)
    row = put_integration(db, project, kind, enabled=body.enabled, config=body.config, actor=user.id)
    db.commit()
    return integration_to_dict(project_id, kind, row)


# --- §29.1 layer 6 live check + Purview push --------------------------------


@router.post(
    "/projects/{project_id}/validate/live-check",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("live_check", "run"))],
)
def live_check(
    project_id: str, body: LiveCheckIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    project = _load_project(db, project_id, user)
    job = request_live_check(db, project, body.pipeline_id, actor=user.id)
    db.commit()
    return integration_job_to_dict(job)


@router.post(
    "/projects/{project_id}/lineage/purview-push",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("lineage", "push"))],
)
def purview_push(
    project_id: str, body: PurviewPushIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    project = _load_project(db, project_id, user)
    job = request_purview_push(db, project, body.lineage, actor=user.id)
    db.commit()
    return integration_job_to_dict(job)


@router.get(
    "/projects/{project_id}/jobs/{job_id}",
    dependencies=[Depends(require_permission("project", "view"))],
)
def read_job(
    project_id: str, job_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    """Poll a sync / live-check / Purview / catalog job. Scoped: the job must belong to
    this project, so a job id alone never leaks another team's result."""
    _load_project(db, project_id, user)
    job = db.get(models.Job, job_id)
    if job is None or job.type not in (*INTEGRATION_JOB_TYPES, *CATALOG_JOB_TYPES) or (job.payload or {}).get("projectId") != project_id:
        raise ApiError(404, "job_not_found", f"Job '{job_id}' not found in this project.")
    return integration_job_to_dict(job)
