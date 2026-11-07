"""Pipeline endpoints (§7, §14, §16, §23.4)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import PipelineCreateIn, PipelineOut, PipelineUpdateIn, ValidationResultOut
from app.errors import ApiError
from app.rbac import require_permission, require_scope, scoped_team_id
from app.services import (
    latest_validation_result,
    pipeline_to_dict,
    request_generate,
    request_git_push,
    request_validate,
    wait_for_job,
)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


def _load(db: Session, pipeline_id: str, user: models.User) -> models.PipelineDefinition:
    pipeline = repository.get_pipeline(db, pipeline_id)
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "pipeline_not_found", f"Pipeline '{pipeline_id}' does not exist.")
    require_scope(user, pipeline.team_id)  # §27.4: caller's role has to be right AND the pipeline theirs
    return pipeline


@router.get("", response_model=list[PipelineOut], dependencies=[Depends(require_permission("pipeline", "view"))])
def list_pipelines(
    project_id: str | None = None,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> list[dict]:
    team_id = None
    if project_id:
        # A project_id filter for a project outside the caller's team is a
        # scope violation, not "no results" — surface it the same way a
        # direct GET /projects/{id} would.
        project = repository.get_project(db, project_id)
        if project is not None:
            require_scope(user, project.team_id)
    else:
        # No project filter: previously returned every team's pipelines to
        # any authenticated caller. Narrow to the caller's own team (admins
        # still see everything) instead of leaking the whole org.
        team_id = scoped_team_id(user, None)
    return [pipeline_to_dict(db, p) for p in repository.list_pipelines(db, project_id, team_id)]


@router.post(
    "",
    response_model=PipelineOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("pipeline", "create"))],
)
def create_pipeline(
    body: PipelineCreateIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    from app.services import create_pipeline as _create

    # §27.4: a DEVELOPER/TEAM_ADMIN could otherwise create a pipeline inside
    # any other team's project just by knowing its id.
    project = repository.get_project(db, body.project_id)
    if project is not None:
        require_scope(user, project.team_id)
    pipeline = _create(
        db,
        name=body.name,
        project_id=body.project_id,
        template_id=body.template_id,
        draft=body.draft,
        actor=user.id,
    )
    return pipeline_to_dict(db, pipeline)


@router.get(
    "/{pipeline_id}", response_model=PipelineOut, dependencies=[Depends(require_permission("pipeline", "view"))]
)
def get_pipeline(
    pipeline_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    return pipeline_to_dict(db, _load(db, pipeline_id, user))


@router.put(
    "/{pipeline_id}", response_model=PipelineOut, dependencies=[Depends(require_permission("pipeline", "edit"))]
)
def update_pipeline(
    pipeline_id: str,
    body: PipelineUpdateIn,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    from app.services import update_pipeline as _update

    pipeline = _update(
        db,
        _load(db, pipeline_id, user),
        name=body.name,
        template_id=body.template_id,
        draft=body.draft,
        actor=user.id,
        expected_updated_at=body.expected_updated_at,
    )
    return pipeline_to_dict(db, pipeline)


@router.post(
    "/{pipeline_id}/generate",
    response_model=PipelineOut,
    dependencies=[Depends(require_permission("pipeline", "generate"))],
)
def generate(pipeline_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> dict:
    pipeline = _load(db, pipeline_id, user)
    job = request_generate(db, pipeline, actor=user.id)
    db.commit()
    wait_for_job(db, job.id)
    db.expire_all()
    return pipeline_to_dict(db, _load(db, pipeline_id, user))


@router.post(
    "/{pipeline_id}/validate",
    response_model=PipelineOut,
    dependencies=[Depends(require_permission("pipeline", "validate"))],
)
def validate(pipeline_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> dict:
    pipeline = _load(db, pipeline_id, user)
    job = request_validate(db, pipeline, actor=user.id)
    db.commit()
    wait_for_job(db, job.id)
    db.expire_all()
    return pipeline_to_dict(db, _load(db, pipeline_id, user))


@router.get(
    "/{pipeline_id}/validation",
    response_model=ValidationResultOut,
    dependencies=[Depends(require_permission("pipeline", "view"))],
)
def validation_result(
    pipeline_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    return latest_validation_result(db, _load(db, pipeline_id, user))


@router.post(
    "/{pipeline_id}/commit", response_model=PipelineOut, dependencies=[Depends(require_permission("git", "push"))]
)
def commit(pipeline_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> dict:
    """Push the current artifact set to a feature branch + open a PR (§8, §23.6).

    Feature branch and Git target come from the project's Git defaults; the
    dedicated ``POST /git/push`` endpoint accepts explicit overrides.
    """
    pipeline = _load(db, pipeline_id, user)
    settings = repository.get_project_settings(db, pipeline.project_id)
    git_defaults = ((settings.document if settings else {}) or {}).get("gitDefaults") or {}
    pattern = git_defaults.get("featureBranchPattern") or "feature/{pipeline-name}-{timestamp}"
    feature_branch = (
        pattern.replace("{pipeline-name}", _slug(pipeline.name))
        .replace("{pipeline}", _slug(pipeline.name))
        .replace("{timestamp}", datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    )
    request_git_push(
        db,
        pipeline,
        artifact_ids=[],
        provider=None,
        repository_url=None,
        feature_branch=feature_branch,
        base_branch=git_defaults.get("baseBranch") or "dev",
        commit_message=None,
        pr_title=None,
        pr_body=None,
        reviewers=[],
        actor=user.id,
    )
    db.commit()
    db.expire_all()
    return pipeline_to_dict(db, _load(db, pipeline_id, user))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
