"""Bronze Source Onboarding Registry endpoints (AGENTS.md §26.7).

Server-backed replacement for the browser-only `lib/adf/onboarding-store.ts`
(localStorage). Field set matches `frontend/types/onboarding.ts`'s
`OnboardingObjectEntry` — the O1-phase subset of §26.2's full catalogue, not
the aspirational CSV import-wizard endpoints (§26.7's `/import`,
`/import/commit`, `/import-from-git`; `/mapping-profiles` landed 2026-09-26), which
stay out of scope here since CSV parsing already happens client-side
(`lib/adf/onboarding.ts`) and only the parsed rows need a place to land.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import (
    DqRuleCreateIn,
    DqRuleOut,
    OnboardingImportOut,
    OnboardingMappingProfileIn,
    OnboardingMappingProfileOut,
    OnboardingBulkUpsertIn,
    OnboardingObjectIn,
    OnboardingObjectOut,
    OnboardingObjectUpdateIn,
)
from app.errors import ApiError
from app.rbac import require_permission, require_scope
from app.services import (
    bulk_upsert_onboarding_objects,
    create_dq_rule,
    create_onboarding_object,
    delete_dq_rule,
    delete_mapping_profile,
    delete_onboarding_object,
    mapping_profile_to_dict,
    dq_rule_to_dict,
    onboarding_import_to_dict,
    onboarding_object_to_dict,
    update_onboarding_object,
    upsert_mapping_profile,
)

router = APIRouter(tags=["onboarding"])


def _load_project(db: Session, project_id: str, user: models.User) -> models.Project:
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(404, "project_not_found", f"Project '{project_id}' does not exist.")
    require_scope(user, project.team_id)
    return project


def _load_object(db: Session, project_id: str, object_key: str, user: models.User) -> models.OnboardingObject:
    _load_project(db, project_id, user)
    obj = repository.get_onboarding_object(db, project_id, object_key)
    if obj is None:
        raise ApiError(404, "onboarding_object_not_found", f"Onboarding object '{object_key}' does not exist.")
    return obj


@router.get(
    "/projects/{project_id}/onboarding/objects",
    response_model=list[OnboardingObjectOut],
    dependencies=[Depends(require_permission("onboarding_object", "view"))],
)
def list_objects(
    project_id: str,
    status: str | None = None,
    source_system: str | None = None,
    q: str | None = None,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> list[dict]:
    _load_project(db, project_id, user)
    rows = repository.list_onboarding_objects(db, project_id, status=status, source_system=source_system, q=q)
    return [onboarding_object_to_dict(r) for r in rows]


@router.get(
    "/projects/{project_id}/onboarding/objects/{object_key}",
    response_model=OnboardingObjectOut,
    dependencies=[Depends(require_permission("onboarding_object", "view"))],
)
def get_object(
    project_id: str, object_key: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    return onboarding_object_to_dict(_load_object(db, project_id, object_key, user))


@router.post(
    "/projects/{project_id}/onboarding/objects",
    response_model=OnboardingObjectOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("onboarding_object", "edit"))],
)
def create_object(
    project_id: str, body: OnboardingObjectIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    _load_project(db, project_id, user)
    obj = create_onboarding_object(db, project_id, body.model_dump(exclude_none=False), actor=user.id)
    return onboarding_object_to_dict(obj)


@router.put(
    "/projects/{project_id}/onboarding/objects/{object_key}",
    response_model=OnboardingObjectOut,
    dependencies=[Depends(require_permission("onboarding_object", "edit"))],
)
def update_object(
    project_id: str,
    object_key: str,
    body: OnboardingObjectUpdateIn,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    obj = _load_object(db, project_id, object_key, user)
    obj = update_onboarding_object(db, obj, body.model_dump(exclude_unset=True), actor=user.id)
    return onboarding_object_to_dict(obj)


@router.delete(
    "/projects/{project_id}/onboarding/objects/{object_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(require_permission("onboarding_object", "delete"))],
)
def delete_object(
    project_id: str,
    object_key: str,
    hard: bool = False,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> None:
    obj = _load_object(db, project_id, object_key, user)
    if hard and user.role not in ("PLATFORM_ADMIN", "TEAM_ADMIN"):
        raise ApiError(403, "forbidden", "Hard delete requires TEAM_ADMIN or above.")
    delete_onboarding_object(db, obj, hard=hard, actor=user.id)


@router.post(
    "/projects/{project_id}/onboarding/objects/bulk",
    dependencies=[Depends(require_permission("onboarding_object", "edit"))],
)
def bulk_upsert(
    project_id: str,
    body: OnboardingBulkUpsertIn,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict[str, int]:
    _load_project(db, project_id, user)
    rows = [o.model_dump(exclude_none=False) for o in body.objects]
    return bulk_upsert_onboarding_objects(
        db, project_id, rows, actor=user.id, file_name=body.file_name, mapping_profile_id=body.mapping_profile_id
    )


@router.get(
    "/projects/{project_id}/onboarding/imports",
    response_model=list[OnboardingImportOut],
    dependencies=[Depends(require_permission("onboarding_object", "view"))],
)
def list_imports(
    project_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> list[dict]:
    _load_project(db, project_id, user)
    return [onboarding_import_to_dict(db, i) for i in repository.list_onboarding_imports(db, project_id)]


# --- Saved mapping profiles (§26.3 Map step / §26.7 `/mapping-profiles`) ------


@router.get(
    "/projects/{project_id}/onboarding/mapping-profiles",
    response_model=list[OnboardingMappingProfileOut],
    dependencies=[Depends(require_permission("onboarding_object", "view"))],
)
def list_mapping_profiles(
    project_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> list[dict]:
    _load_project(db, project_id, user)
    return [mapping_profile_to_dict(p) for p in repository.list_mapping_profiles(db, project_id)]


@router.post(
    "/projects/{project_id}/onboarding/mapping-profiles",
    response_model=OnboardingMappingProfileOut,
    dependencies=[Depends(require_permission("onboarding_object", "edit"))],
)
def save_mapping_profile(
    project_id: str,
    body: OnboardingMappingProfileIn,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    """Upsert by (trimmed, case-insensitive) name — same contract as the old localStorage store."""
    project = _load_project(db, project_id, user)
    return mapping_profile_to_dict(upsert_mapping_profile(db, project, body.name, body.header_map, actor=user.id))


@router.delete(
    "/projects/{project_id}/onboarding/mapping-profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(require_permission("onboarding_object", "delete"))],
)
def remove_mapping_profile(
    project_id: str, profile_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> None:
    project = _load_project(db, project_id, user)
    profile = repository.get_mapping_profile(db, profile_id)
    if profile is None or profile.project_id != project_id:
        raise ApiError(404, "mapping_profile_not_found", f"Mapping profile '{profile_id}' does not exist.")
    delete_mapping_profile(db, profile, team_id=project.team_id, actor=user.id)


# --- Data Quality rules (§30.4/§30.5, gap AQ2's table-only slice) --------


def _load_dq_rule(db: Session, project_id: str, object_key: str, rule_id: str, user: models.User) -> models.DqRule:
    _load_object(db, project_id, object_key, user)
    rule = repository.get_dq_rule(db, rule_id)
    if rule is None or rule.project_id != project_id or rule.object_key != object_key:
        raise ApiError(404, "dq_rule_not_found", f"Data quality rule '{rule_id}' does not exist.")
    return rule


@router.get(
    "/projects/{project_id}/onboarding/objects/{object_key}/dq-rules",
    response_model=list[DqRuleOut],
    dependencies=[Depends(require_permission("dq_rule", "view"))],
)
def list_dq_rules(
    project_id: str, object_key: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> list[dict]:
    _load_object(db, project_id, object_key, user)
    return [dq_rule_to_dict(r) for r in repository.list_dq_rules(db, project_id, object_key)]


@router.post(
    "/projects/{project_id}/onboarding/objects/{object_key}/dq-rules",
    response_model=DqRuleOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("dq_rule", "create"))],
)
def create_rule(
    project_id: str,
    object_key: str,
    body: DqRuleCreateIn,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> dict:
    obj = _load_object(db, project_id, object_key, user)
    rule = create_dq_rule(
        db, obj, check_type=body.check_type, config=body.config, enforcement=body.enforcement, actor=user.id
    )
    return dq_rule_to_dict(rule)


@router.delete(
    "/projects/{project_id}/onboarding/objects/{object_key}/dq-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(require_permission("dq_rule", "delete"))],
)
def remove_rule(
    project_id: str,
    object_key: str,
    rule_id: str,
    db: Session = Depends(db_session),
    user: models.User = Depends(current_user),
) -> None:
    rule = _load_dq_rule(db, project_id, object_key, rule_id, user)
    delete_dq_rule(db, rule, actor=user.id)
