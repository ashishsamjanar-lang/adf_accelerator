"""Team endpoints (§14, §23.4)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import TeamCreateIn, TeamOut, UserOut
from app.db.base import new_id
from app.errors import ApiError
from app.rbac import require_permission
from app.services import team_to_dict, user_to_dict

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("", response_model=list[TeamOut], dependencies=[Depends(require_permission("team", "view"))])
def list_teams(db: Session = Depends(db_session)) -> list[dict]:
    return [team_to_dict(db, t) for t in repository.list_teams(db)]


@router.post(
    "",
    response_model=TeamOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("team", "create"))],
)
def create_team(
    body: TeamCreateIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    team = models.Team(id=new_id("team"), name=body.name, description=body.description)
    db.add(team)
    repository.record_audit(db, actor=user.name, action="team.created", target=body.name, team_id=team.id)
    db.flush()
    return team_to_dict(db, team)


@router.get("/{team_id}", response_model=TeamOut, dependencies=[Depends(require_permission("team", "view"))])
def get_team(team_id: str, db: Session = Depends(db_session)) -> dict:
    team = repository.get_team(db, team_id)
    if team is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "team_not_found", f"Team '{team_id}' does not exist.")
    return team_to_dict(db, team)


@router.get(
    "/{team_id}/members", response_model=list[UserOut], dependencies=[Depends(require_permission("team", "view"))]
)
def team_members(team_id: str, db: Session = Depends(db_session)) -> list[dict]:
    if repository.get_team(db, team_id) is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "team_not_found", f"Team '{team_id}' does not exist.")
    return [user_to_dict(u) for u in repository.list_team_members(db, team_id)]
