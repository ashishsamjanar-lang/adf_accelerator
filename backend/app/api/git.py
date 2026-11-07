"""Git push / PR endpoints (§8, §9, §14, §23.6).

The API only *validates and records* here; the actual provider calls run in the
worker (``git_push`` job) behind the provider adapter. Hard rules (feature
branch != protected base, repo configured, >=1 artifact) are enforced before
any job is enqueued.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import GitPushIn, GitPushOut, PullRequestIn
from app.errors import ApiError
from app.rbac import require_permission, require_scope
from app.services import push_request_to_dict, request_git_push

router = APIRouter(prefix="/git", tags=["git"])
# Alias used by the Artifacts plane's "Push to Git" action.
artifacts_router = APIRouter(prefix="/artifacts", tags=["git"])


def _do_push(db: Session, body: GitPushIn, user: models.User) -> dict:
    pipeline = repository.get_pipeline(db, body.pipeline_id)
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "pipeline_not_found", f"Pipeline '{body.pipeline_id}' does not exist.")
    require_scope(user, pipeline.team_id)  # §27.4: can't push another team's pipeline just by knowing its id
    push = request_git_push(
        db,
        pipeline,
        artifact_ids=body.artifact_ids,
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
    return push_request_to_dict(repository.get_push_request(db, push.id))


@router.post(
    "/push",
    response_model=GitPushOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("git", "push"))],
)
def git_push(body: GitPushIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> dict:
    return _do_push(db, body, user)


@artifacts_router.post(
    "/push-to-git",
    response_model=GitPushOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("git", "push"))],
)
def push_to_git(body: GitPushIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> dict:
    return _do_push(db, body, user)


@router.post(
    "/pull-requests",
    response_model=GitPushOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("git", "create_pr"))],
)
def create_pull_request(
    body: PullRequestIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    push = repository.get_push_request(db, body.push_request_id)
    if push is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "push_not_found", f"Push request '{body.push_request_id}' not found.")
    pipeline = repository.get_pipeline(db, push.pipeline_id)
    if pipeline is not None:
        require_scope(user, pipeline.team_id)
    if not push.commit_sha:
        raise ApiError(409, "push_not_ready", "A PR can be opened only after the artifacts are pushed.")
    if push.pull_request is not None:
        raise ApiError(409, "pr_exists", f"Pull request #{push.pull_request.number} already exists for this push.")
    from app.services import JOB_GIT_PUSH, enqueue_job

    enqueue_job(
        db,
        job_type=JOB_GIT_PUSH,
        pipeline_id=push.pipeline_id,
        payload={"pushRequestId": push.id, "prOnly": True, "prTitle": body.title, "prBody": body.body},
        actor=user.id,
    )
    db.commit()
    return push_request_to_dict(repository.get_push_request(db, push.id))


@router.get(
    "/push/{push_id}", response_model=GitPushOut, dependencies=[Depends(require_permission("artifact", "view"))]
)
def get_push(
    push_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    push = repository.get_push_request(db, push_id)
    if push is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "push_not_found", f"Push request '{push_id}' not found.")
    pipeline = repository.get_pipeline(db, push.pipeline_id)
    if pipeline is not None:
        require_scope(user, pipeline.team_id)
    return push_request_to_dict(push)
