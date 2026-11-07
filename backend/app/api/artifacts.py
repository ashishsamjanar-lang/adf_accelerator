"""Artifact endpoints (§8, §14, §23.4)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import ArtifactDiffOut, ArtifactOut
from app.errors import ApiError
from app.rbac import require_permission, require_scope, scoped_team_id
from app.services import artifact_to_dict

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _scope_to_pipeline(db: Session, user: models.User, pipeline_id: str) -> None:
    pipeline = repository.get_pipeline(db, pipeline_id)
    if pipeline is not None:
        require_scope(user, pipeline.team_id)


@router.get("", response_model=list[ArtifactOut], dependencies=[Depends(require_permission("artifact", "view"))])
def list_artifacts(
    pipeline_id: str | None = None,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> list[dict]:
    team_id = None
    if pipeline_id:
        _scope_to_pipeline(db, user, pipeline_id)
    else:
        # No pipeline filter: previously returned every team's artifacts
        # (including generated ADF JSON content) to any authenticated caller.
        team_id = scoped_team_id(user, None)
    return [artifact_to_dict(a, include_content=False) for a in repository.list_artifacts(db, pipeline_id, team_id)]


@router.get(
    "/{artifact_id}", response_model=ArtifactOut, dependencies=[Depends(require_permission("artifact", "view"))]
)
def get_artifact(
    artifact_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    artifact = repository.get_artifact(db, artifact_id)
    if artifact is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "artifact_not_found", f"Artifact '{artifact_id}' does not exist.")
    _scope_to_pipeline(db, user, artifact.pipeline_id)
    return artifact_to_dict(artifact)


@router.get(
    "/{artifact_id}/diff",
    response_model=ArtifactDiffOut,
    dependencies=[Depends(require_permission("artifact", "view"))],
)
def artifact_diff(
    artifact_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    artifact = repository.get_artifact(db, artifact_id)
    if artifact is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "artifact_not_found", f"Artifact '{artifact_id}' does not exist.")
    _scope_to_pipeline(db, user, artifact.pipeline_id)
    # New file relative to the base branch: every non-empty line is an addition.
    additions = sum(1 for line in artifact.content.splitlines() if line.strip())
    return {"additions": additions, "deletions": 0}
