"""STTM-Based Silver/Gold Object Registry endpoints (AGENTS.md §33.9).

`/projects/{id}/silver-objects...` and `/projects/{id}/mart-objects...` are
identical in shape (per §33.9's own "… identical shape, Gold" note) — built
once via `_register_layer_routes` and mounted twice rather than duplicated,
so the two paths can't silently drift apart.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import (
    RegistryObjectIn,
    RegistryObjectOut,
    RegistryObjectUpdateIn,
    SttmImportCommitIn,
    SttmImportOut,
)
from app.errors import ApiError
from app.onboarding.sttm_parser import (
    BundleEntryTooLargeError,
    InvalidWorkbookError,
    WorkbookTooLargeError,
    parse_workbook,
)
from app.rbac import require_permission, require_scope
from app.services import (
    commit_sttm_import,
    create_registry_object,
    create_sttm_import,
    delete_registry_object,
    project_layer_prefixes,
    registry_object_to_dict,
    sttm_import_to_dict,
    update_registry_object,
)

router = APIRouter(tags=["silver-gold-registry"])

_RESOURCE = {"silver": "silver_object", "mart": "mart_object"}
_PARSE_LAYER = {"silver": "silver", "mart": "gold"}  # medallion layer name the parser/validator expects


def _load_project(db: Session, project_id: str, user: models.User) -> models.Project:
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(404, "project_not_found", f"Project '{project_id}' does not exist.")
    require_scope(user, project.team_id)
    return project


def _load_object(db: Session, layer: str, project_id: str, object_key: str, user: models.User) -> Any:
    project = _load_project(db, project_id, user)
    obj = (
        repository.get_silver_object(db, project.id, object_key)
        if layer == "silver"
        else repository.get_mart_object(db, project.id, object_key)
    )
    if obj is None:
        raise ApiError(404, f"{layer}_object_not_found", f"{layer.capitalize()} object '{object_key}' does not exist.")
    return obj


def _register_layer_routes(layer: str, path_segment: str) -> None:
    resource = _RESOURCE[layer]

    @router.get(
        f"/projects/{{project_id}}/{path_segment}",
        response_model=list[RegistryObjectOut],
        dependencies=[Depends(require_permission(resource, "view"))],
        name=f"list_{layer}_objects",
    )
    def list_objects(
        project_id: str,
        tag: str | None = None,
        domain: str | None = None,
        status: str | None = None,
        source_system: str | None = None,
        q: str | None = None,
        db: Session = Depends(db_session),
        user: models.User = Depends(current_user),
    ) -> list[dict]:
        _load_project(db, project_id, user)
        rows = (
            repository.list_silver_objects(db, project_id, tag=tag, domain=domain, status=status, source_system=source_system, q=q)
            if layer == "silver"
            else repository.list_mart_objects(db, project_id, tag=tag, domain=domain, status=status, source_system=source_system, q=q)
        )
        return [registry_object_to_dict(db, r, layer=layer) for r in rows]

    @router.get(
        f"/projects/{{project_id}}/{path_segment}/{{object_key}}",
        response_model=RegistryObjectOut,
        dependencies=[Depends(require_permission(resource, "view"))],
        name=f"get_{layer}_object",
    )
    def get_object(
        project_id: str, object_key: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
    ) -> dict:
        return registry_object_to_dict(db, _load_object(db, layer, project_id, object_key, user), layer=layer)

    @router.post(
        f"/projects/{{project_id}}/{path_segment}",
        response_model=RegistryObjectOut,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_permission(resource, "edit"))],
        name=f"create_{layer}_object",
    )
    def create_object(
        project_id: str, body: RegistryObjectIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
    ) -> dict:
        _load_project(db, project_id, user)
        obj = create_registry_object(db, layer, project_id, body.model_dump(by_alias=True, exclude_none=False), actor=user.id)
        return registry_object_to_dict(db, obj, layer=layer)

    @router.put(
        f"/projects/{{project_id}}/{path_segment}/{{object_key}}",
        response_model=RegistryObjectOut,
        dependencies=[Depends(require_permission(resource, "edit"))],
        name=f"update_{layer}_object",
    )
    def update_object(
        project_id: str,
        object_key: str,
        body: RegistryObjectUpdateIn,
        db: Session = Depends(db_session),
        user: models.User = Depends(current_user),
    ) -> dict:
        obj = _load_object(db, layer, project_id, object_key, user)
        obj = update_registry_object(
            db, layer, obj, body.model_dump(by_alias=True, exclude_unset=True), actor=user.id
        )
        return registry_object_to_dict(db, obj, layer=layer)

    @router.delete(
        f"/projects/{{project_id}}/{path_segment}/{{object_key}}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        dependencies=[Depends(require_permission(resource, "delete"))],
        name=f"delete_{layer}_object",
    )
    def delete_object(
        project_id: str,
        object_key: str,
        hard: bool = False,
        db: Session = Depends(db_session),
        user: models.User = Depends(current_user),
    ) -> None:
        obj = _load_object(db, layer, project_id, object_key, user)
        if hard and user.role not in ("PLATFORM_ADMIN", "TEAM_ADMIN"):
            raise ApiError(403, "forbidden", "Hard delete requires TEAM_ADMIN or above.")
        delete_registry_object(db, layer, obj, hard=hard, actor=user.id)

    @router.post(
        f"/projects/{{project_id}}/{path_segment}/sttm-import",
        response_model=SttmImportOut,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_permission(resource, "edit"))],
        name=f"sttm_import_{layer}",
    )
    async def sttm_import(
        project_id: str,
        file: UploadFile = File(...),
        db: Session = Depends(db_session),
        user: models.User = Depends(current_user),
    ) -> dict:
        """Server-side authoritative re-parse (§33.9) — never trusts a
        client-submitted JSON payload for something this structurally
        validated. Does not persist registry rows; only the import + its
        per-sheet report, ready for `.../commit`."""
        _load_project(db, project_id, user)
        raw = await file.read()
        try:
            report = parse_workbook(
                raw, _PARSE_LAYER[layer], project_layer_prefixes(db, project_id), file_name=file.filename
            )
        except (WorkbookTooLargeError, BundleEntryTooLargeError) as exc:
            raise ApiError(422, "workbook_too_large", str(exc)) from exc
        except InvalidWorkbookError as exc:
            raise ApiError(422, "invalid_workbook", str(exc)) from exc
        imp = create_sttm_import(
            db,
            project_id=project_id,
            layer=layer,
            file_name=file.filename or "upload.xlsx",
            file_hash=report.file_hash,
            sheets=[s.to_dict() for s in report.sheets],
            actor=user.id,
        )
        return sttm_import_to_dict(db, imp)

    @router.post(
        f"/projects/{{project_id}}/{path_segment}/sttm-import/{{import_id}}/commit",
        dependencies=[Depends(require_permission(resource, "edit"))],
        name=f"sttm_import_commit_{layer}",
    )
    def sttm_import_commit(
        project_id: str,
        import_id: str,
        body: SttmImportCommitIn,
        db: Session = Depends(db_session),
        user: models.User = Depends(current_user),
    ) -> dict[str, Any]:
        _load_project(db, project_id, user)
        imp = repository.get_sttm_import(db, import_id)
        if imp is None or imp.project_id != project_id:
            raise ApiError(404, "sttm_import_not_found", f"Import '{import_id}' does not exist.")
        result = commit_sttm_import(
            db,
            imp,
            sheet_names=body.sheet_names,
            confirm_overwrite=body.confirm_overwrite,
            expected_versions=body.expected_versions,
            actor=user.id,
        )
        return {**result, "import": sttm_import_to_dict(db, imp)}


_register_layer_routes("silver", "silver-objects")
_register_layer_routes("mart", "mart-objects")


@router.get(
    "/projects/{project_id}/sttm-imports",
    response_model=list[SttmImportOut],
    dependencies=[Depends(require_permission("silver_object", "view"))],
)
def list_sttm_imports(
    project_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> list[dict]:
    _load_project(db, project_id, user)
    return [sttm_import_to_dict(db, i) for i in repository.list_sttm_imports(db, project_id)]
