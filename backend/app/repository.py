"""SQLAlchemy query helpers (thin data-access layer).

Routes/services call these; they never hand-write ``select()`` inline. Nothing
here commits — the request-scoped session (``get_session``) owns the
transaction boundary.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app import models
from app.db.base import utcnow


# --- Teams / users -----------------------------------------------------


def list_teams(db: Session) -> list[models.Team]:
    return list(db.scalars(select(models.Team).order_by(models.Team.name)))


def get_team(db: Session, team_id: str) -> models.Team | None:
    return db.get(models.Team, team_id)


def team_member_count(db: Session, team_id: str) -> int:
    return db.scalar(select(func.count()).select_from(models.User).where(models.User.team_id == team_id)) or 0


def team_project_count(db: Session, team_id: str) -> int:
    return db.scalar(select(func.count()).select_from(models.Project).where(models.Project.team_id == team_id)) or 0


def list_team_members(db: Session, team_id: str) -> list[models.User]:
    return list(db.scalars(select(models.User).where(models.User.team_id == team_id).order_by(models.User.name)))


def list_users(db: Session) -> list[models.User]:
    return list(db.scalars(select(models.User).order_by(models.User.name)))


def get_user(db: Session, user_id: str) -> models.User | None:
    return db.get(models.User, user_id)


def first_admin(db: Session) -> models.User | None:
    return db.scalars(
        select(models.User).where(models.User.role == "PLATFORM_ADMIN").order_by(models.User.name).limit(1)
    ).first()


def get_user_by_email(db: Session, email: str) -> models.User | None:
    return db.scalar(select(models.User).where(models.User.email == email))


def get_user_by_username(db: Session, username: str) -> models.User | None:
    return db.scalar(select(models.User).where(models.User.username == username.strip().lower()))


def get_user_by_login(db: Session, login: str) -> models.User | None:
    """One login field, two lookups: anything with an "@" is an email (usernames
    can never contain one — app/security.py USERNAME_RE), everything else a username."""
    login = login.strip()
    return get_user_by_email(db, login) if "@" in login else get_user_by_username(db, login)


def count_active_platform_admins(db: Session, *, exclude_user_id: str | None = None) -> int:
    stmt = select(func.count()).select_from(models.User).where(
        models.User.role == "PLATFORM_ADMIN", models.User.active.is_(True)
    )
    if exclude_user_id:
        stmt = stmt.where(models.User.id != exclude_user_id)
    return db.scalar(stmt) or 0


# --- Sessions (AUTH_MODE=password, §27.3/§27.7) -----------------------


def get_session(db: Session, session_id: str) -> models.Session | None:
    return db.get(models.Session, session_id)


def revoke_session(db: Session, session: models.Session) -> None:
    session.revoked_at = utcnow()


def revoke_user_sessions(db: Session, user_id: str, *, except_session_id: str | None = None) -> None:
    stmt = select(models.Session).where(models.Session.user_id == user_id, models.Session.revoked_at.is_(None))
    if except_session_id:
        stmt = stmt.where(models.Session.id != except_session_id)
    now = utcnow()
    for session in db.scalars(stmt):
        session.revoked_at = now


# --- Projects --------------------------------------------------------


def list_projects(db: Session, team_id: str | None = None) -> list[models.Project]:
    stmt = select(models.Project).order_by(models.Project.name)
    if team_id:
        stmt = stmt.where(models.Project.team_id == team_id)
    return list(db.scalars(stmt))


def get_project(db: Session, project_id: str) -> models.Project | None:
    return db.get(models.Project, project_id)


def project_pipeline_count(db: Session, project_id: str) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(models.PipelineDefinition).where(
                models.PipelineDefinition.project_id == project_id
            )
        )
        or 0
    )


def get_project_settings(db: Session, project_id: str) -> models.ProjectSettings | None:
    return db.get(models.ProjectSettings, project_id)


# --- Templates ------------------------------------------------------


def list_templates(db: Session) -> list[models.PipelineTemplate]:
    return list(db.scalars(select(models.PipelineTemplate).order_by(models.PipelineTemplate.name)))


def get_template(db: Session, template_id: str) -> models.PipelineTemplate | None:
    return db.get(models.PipelineTemplate, template_id)


# --- Pipelines -----------------------------------------------------


def list_pipelines(
    db: Session, project_id: str | None = None, team_id: str | None = None
) -> list[models.PipelineDefinition]:
    stmt = select(models.PipelineDefinition).order_by(models.PipelineDefinition.updated_at.desc())
    if project_id:
        stmt = stmt.where(models.PipelineDefinition.project_id == project_id)
    # `team_id` scopes an unfiltered ("every project") list to one team —
    # see `app.rbac.scoped_team_id` (§27.4); PipelineDefinition denormalizes
    # team_id so this needs no join.
    if team_id:
        stmt = stmt.where(models.PipelineDefinition.team_id == team_id)
    return list(db.scalars(stmt))


def get_pipeline(db: Session, pipeline_id: str) -> models.PipelineDefinition | None:
    return db.scalars(
        select(models.PipelineDefinition)
        .where(models.PipelineDefinition.id == pipeline_id)
        .options(selectinload(models.PipelineDefinition.versions))
    ).first()


def latest_version(db: Session, pipeline_id: str) -> models.PipelineVersion | None:
    return db.scalars(
        select(models.PipelineVersion)
        .where(models.PipelineVersion.pipeline_id == pipeline_id)
        .order_by(models.PipelineVersion.created_at.desc())
        .limit(1)
    ).first()


# --- Artifacts ----------------------------------------------------


def list_artifacts(
    db: Session, pipeline_id: str | None = None, team_id: str | None = None
) -> list[models.Artifact]:
    stmt = select(models.Artifact).order_by(models.Artifact.file_name)
    if pipeline_id:
        stmt = stmt.where(models.Artifact.pipeline_id == pipeline_id)
    # `team_id` scopes an unfiltered list to one team (§27.4, see
    # `app.rbac.scoped_team_id`); Artifact has no team_id of its own, so join
    # through its pipeline, which does.
    if team_id:
        stmt = stmt.join(
            models.PipelineDefinition, models.PipelineDefinition.id == models.Artifact.pipeline_id
        ).where(models.PipelineDefinition.team_id == team_id)
    return list(db.scalars(stmt))


def get_artifact(db: Session, artifact_id: str) -> models.Artifact | None:
    return db.get(models.Artifact, artifact_id)


def artifacts_for_current_version(db: Session, pipeline: models.PipelineDefinition) -> list[models.Artifact]:
    version = pipeline.latest_artifact_version
    stmt = select(models.Artifact).where(models.Artifact.pipeline_id == pipeline.id)
    if version:
        stmt = stmt.where(models.Artifact.version == version)
    return list(db.scalars(stmt.order_by(models.Artifact.file_name)))


# --- Git ------------------------------------------------------


def get_push_request(db: Session, push_id: str) -> models.GitPushRequest | None:
    return db.scalars(
        select(models.GitPushRequest)
        .where(models.GitPushRequest.id == push_id)
        .options(selectinload(models.GitPushRequest.pull_request))
    ).first()


# --- Environments -----------------------------------------------------


def list_environments(db: Session) -> list[models.Environment]:
    return list(db.scalars(select(models.Environment).order_by(models.Environment.name)))


def get_environment(db: Session, name: str) -> models.Environment | None:
    return db.scalars(select(models.Environment).where(models.Environment.name == name)).first()


# --- Connection library ------------------------------------------------


def list_connections(
    db: Session, project_id: str | None = None, team_id: str | None = None
) -> list[models.ConnectionLibraryEntry]:
    stmt = select(models.ConnectionLibraryEntry).order_by(models.ConnectionLibraryEntry.updated_at.desc())
    if project_id:
        stmt = stmt.where(models.ConnectionLibraryEntry.project_id == project_id)
    if team_id:
        stmt = stmt.where(models.ConnectionLibraryEntry.team_id == team_id)
    return list(db.scalars(stmt))


def get_connection(db: Session, connection_id: str) -> models.ConnectionLibraryEntry | None:
    return db.get(models.ConnectionLibraryEntry, connection_id)


# --- Managed identities (AGENTS.md §32.6) --------------------------------


def list_managed_identities(db: Session, team_id: str | None = None) -> list[models.ManagedIdentity]:
    stmt = select(models.ManagedIdentity).order_by(models.ManagedIdentity.name)
    if team_id:
        stmt = stmt.where(models.ManagedIdentity.team_id == team_id)
    return list(db.scalars(stmt))


def get_managed_identity(db: Session, identity_id: str) -> models.ManagedIdentity | None:
    return db.get(models.ManagedIdentity, identity_id)


# --- Bronze Source Onboarding Registry (AGENTS.md §26) ------------------


def list_onboarding_objects(
    db: Session,
    project_id: str,
    *,
    status: str | None = None,
    source_system: str | None = None,
    q: str | None = None,
) -> list[models.OnboardingObject]:
    stmt = select(models.OnboardingObject).where(models.OnboardingObject.project_id == project_id)
    if status:
        stmt = stmt.where(models.OnboardingObject.status == status)
    if source_system:
        stmt = stmt.where(models.OnboardingObject.source_system == source_system)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(models.OnboardingObject.object_key).like(like)
            | func.lower(models.OnboardingObject.source_object).like(like)
        )
    return list(db.scalars(stmt.order_by(models.OnboardingObject.object_key)))


def get_onboarding_object(db: Session, project_id: str, object_key: str) -> models.OnboardingObject | None:
    return db.get(models.OnboardingObject, {"object_key": object_key, "project_id": project_id})


# --- Data Quality rules (AGENTS.md §30.4/§30.5) --------------------------


def list_dq_rules(db: Session, project_id: str, object_key: str) -> list[models.DqRule]:
    stmt = (
        select(models.DqRule)
        .where(models.DqRule.project_id == project_id, models.DqRule.object_key == object_key)
        .order_by(models.DqRule.created_at)
    )
    return list(db.scalars(stmt))


def get_dq_rule(db: Session, rule_id: str) -> models.DqRule | None:
    return db.get(models.DqRule, rule_id)


# --- STTM Silver/Gold Object Registry (AGENTS.md §33) -------------------


def _apply_registry_filters(stmt, model, *, tag: str | None, domain: str | None, status_: str | None, q: str | None):
    if tag:
        stmt = stmt.where(model.tag == tag)
    if domain:
        stmt = stmt.where(model.domain == domain)
    if status_:
        stmt = stmt.where(model.status == status_)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(model.curated_entity).like(like) | func.lower(model.object_key).like(like)
        )
    return stmt


def list_silver_objects(
    db: Session,
    project_id: str,
    *,
    tag: str | None = None,
    domain: str | None = None,
    status: str | None = None,
    source_system: str | None = None,
    q: str | None = None,
) -> list[models.SilverObject]:
    stmt = select(models.SilverObject).where(models.SilverObject.project_id == project_id)
    stmt = _apply_registry_filters(stmt, models.SilverObject, tag=tag, domain=domain, status_=status, q=q)
    rows = list(db.scalars(stmt.order_by(models.SilverObject.curated_entity)))
    if source_system:
        # `source_object_keys` is a JSON array of bronze onboarding object keys
        # (still localStorage-only, §26 — this app has no backend row to join
        # against), so this is a best-effort substring match against the keys
        # themselves rather than a real source-system lookup.
        needle = source_system.lower()
        rows = [r for r in rows if any(needle in (k or "").lower() for k in (r.source_object_keys or []))]
    return rows


def get_silver_object(db: Session, project_id: str, object_key: str) -> models.SilverObject | None:
    return db.get(models.SilverObject, {"object_key": object_key, "project_id": project_id})


def get_silver_object_by_entity(db: Session, project_id: str, curated_entity: str) -> models.SilverObject | None:
    return db.scalars(
        select(models.SilverObject).where(
            models.SilverObject.project_id == project_id, models.SilverObject.curated_entity == curated_entity
        )
    ).first()


def list_mart_objects(
    db: Session,
    project_id: str,
    *,
    tag: str | None = None,
    domain: str | None = None,
    status: str | None = None,
    source_system: str | None = None,
    q: str | None = None,
) -> list[models.MartObject]:
    stmt = select(models.MartObject).where(models.MartObject.project_id == project_id)
    stmt = _apply_registry_filters(stmt, models.MartObject, tag=tag, domain=domain, status_=status, q=q)
    rows = list(db.scalars(stmt.order_by(models.MartObject.curated_entity)))
    if source_system:
        needle = source_system.lower()
        rows = [r for r in rows if any(needle in (k or "").lower() for k in (r.source_object_keys or []))]
    return rows


def get_mart_object(db: Session, project_id: str, object_key: str) -> models.MartObject | None:
    return db.get(models.MartObject, {"object_key": object_key, "project_id": project_id})


def get_mart_object_by_entity(
    db: Session, project_id: str, mart_domain: str, curated_entity: str
) -> models.MartObject | None:
    return db.scalars(
        select(models.MartObject).where(
            models.MartObject.project_id == project_id,
            models.MartObject.mart_domain == mart_domain,
            models.MartObject.curated_entity == curated_entity,
        )
    ).first()


def list_onboarding_imports(db: Session, project_id: str) -> list[models.OnboardingImport]:
    return list(
        db.scalars(
            select(models.OnboardingImport)
            .where(models.OnboardingImport.project_id == project_id)
            .order_by(models.OnboardingImport.created_at.desc())
        )
    )


def list_mapping_profiles(db: Session, project_id: str) -> list[models.OnboardingMappingProfile]:
    return list(
        db.scalars(
            select(models.OnboardingMappingProfile)
            .where(models.OnboardingMappingProfile.project_id == project_id)
            .order_by(models.OnboardingMappingProfile.name)
        )
    )


def get_mapping_profile(db: Session, profile_id: str) -> models.OnboardingMappingProfile | None:
    return db.get(models.OnboardingMappingProfile, profile_id)


def list_active_sessions(db: Session, *, now: datetime, team_id: str | None = None) -> list[models.Session]:
    query = (
        select(models.Session)
        .join(models.User, models.User.id == models.Session.user_id)
        .where(
            models.Session.revoked_at.is_(None),
            models.Session.expires_at > now,
            models.Session.idle_expires_at > now,
        )
        .order_by(models.Session.issued_at.desc())
    )
    if team_id is not None:
        query = query.where(models.User.team_id == team_id)
    return list(db.scalars(query))


def list_sttm_imports(db: Session, project_id: str) -> list[models.SttmImport]:
    return list(
        db.scalars(
            select(models.SttmImport)
            .where(models.SttmImport.project_id == project_id)
            .order_by(models.SttmImport.created_at.desc())
        )
    )


def get_sttm_import(db: Session, import_id: str) -> models.SttmImport | None:
    return db.get(models.SttmImport, import_id)


# --- Audit ---------------------------------------------------


def list_audit(db: Session, team_id: str | None = None, limit: int = 100) -> list[models.AuditEntry]:
    stmt = select(models.AuditEntry).order_by(models.AuditEntry.created_at.desc()).limit(limit)
    if team_id:
        stmt = stmt.where(models.AuditEntry.team_id == team_id)
    return list(db.scalars(stmt))


def record_audit(
    db: Session, *, actor: str, action: str, target: str = "", details: str = "", team_id: str | None = None
) -> models.AuditEntry:
    from app.db.base import new_id

    entry = models.AuditEntry(
        id=new_id("aud"), actor=actor, action=action, target=target, details=details, team_id=team_id
    )
    db.add(entry)
    return entry


def purge_audit_older_than(db: Session, cutoff: datetime) -> int:
    """§30.1 — append-only means no API-level UPDATE/DELETE; this is the one
    place `audit_log` rows are ever removed, called only by the
    `purge-audit-log` retention CLI (`app/manage.py`), never a request handler."""
    result = db.execute(delete(models.AuditEntry).where(models.AuditEntry.created_at < cutoff))
    return result.rowcount or 0
