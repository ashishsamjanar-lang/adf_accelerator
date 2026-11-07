"""Template endpoints (§6, §14, §23.4b)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import repository
from app.api.deps import db_session
from app.api.schemas import TemplateOut
from app.errors import ApiError
from app.rbac import require_permission
from app.services import template_to_dict

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateOut], dependencies=[Depends(require_permission("template", "view"))])
def list_templates(db: Session = Depends(db_session)) -> list[dict]:
    return [template_to_dict(t) for t in repository.list_templates(db) if t.active]


@router.get(
    "/{template_id}", response_model=TemplateOut, dependencies=[Depends(require_permission("template", "view"))]
)
def get_template(template_id: str, db: Session = Depends(db_session)) -> dict:
    template = repository.get_template(db, template_id)
    if template is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "template_not_found", f"Template '{template_id}' does not exist.")
    return template_to_dict(template)
