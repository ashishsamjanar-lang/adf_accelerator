"""Application services — orchestration between the API, metastore, and worker.

Endpoints stay thin (§23.4): they parse/serialize and delegate here. Generation,
validation, and Git push are handed to the stateless worker as :class:`Job`
rows (§23.4); this module enqueues them, transitions pipeline state, and
serializes results back to the boundary DTO shape.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, repository
from app.crypto import encrypt_secret
from app.db.base import new_id
from app.errors import ApiError
from app.onboarding import sttm as sttm_rules
from app.standards import evaluate_artifact_names

# Job types the worker understands (see worker/app/jobs/).
JOB_GENERATE = "generate"
JOB_VALIDATE = "validate"
JOB_GIT_PUSH = "git_push"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def team_to_dict(db: Session, team: models.Team) -> dict[str, Any]:
    return {
        "id": team.id,
        "name": team.name,
        "description": team.description,
        "memberCount": repository.team_member_count(db, team.id),
        "projectCount": repository.team_project_count(db, team.id),
    }


def _actor_name(db: Session, actor_id: str | None) -> str | None:
    """Resolve a stored `created_by`/`updated_by`/audit `actor` id to a display name.

    Every one of those columns stores the acting `User.id` (opaque, e.g.
    ``"user-ashish"``); nothing before this resolved it back to something a
    person recognizes. Falls back to the raw id for an actor that no longer
    resolves (deleted user, or a non-user actor like ``"system"``/``"worker"``).
    """
    if not actor_id:
        return None
    user = repository.get_user(db, actor_id)
    return user.name if user else actor_id


def _project_team_id(db: Session, project_id: str | None) -> str | None:
    """Resolve a project's owning team, for audit rows on a registry row
    (onboarding/silver/mart object, STTM import) that has no denormalized
    ``team_id`` column of its own — unlike ``PipelineDefinition``/
    ``ConnectionLibraryEntry``, which do (AGENTS.md §30.7 AQ1)."""
    if not project_id:
        return None
    project = repository.get_project(db, project_id)
    return project.team_id if project else None


def user_to_dict(user: models.User) -> dict[str, Any]:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "username": user.username,
        "role": user.role,
        "teamId": user.team_id,
        "active": user.active,
        "lastSeen": _iso(user.last_seen),
        "mustChangePassword": user.must_change_password,
    }


def project_to_dict(db: Session, project: models.Project, *, include_settings: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": project.id,
        "teamId": project.team_id,
        "name": project.name,
        "description": project.description,
        "pipelineCount": repository.project_pipeline_count(db, project.id),
    }
    if include_settings:
        settings = repository.get_project_settings(db, project.id)
        out["settings"] = settings.document if settings else None
    return out


def template_to_dict(template: models.PipelineTemplate) -> dict[str, Any]:
    doc = dict(template.document or {})
    doc.setdefault("id", template.id)
    doc.setdefault("name", template.name)
    doc.setdefault("version", template.version)
    doc.setdefault("description", template.description)
    doc.setdefault("supportedSourceTypes", [])
    doc.setdefault("supportedTargetTypes", [])
    doc.setdefault("fields", [])
    doc.setdefault("defaults", {})
    return doc


def pipeline_to_dict(db: Session, pipeline: models.PipelineDefinition) -> dict[str, Any]:
    project = repository.get_project(db, pipeline.project_id)
    team = repository.get_team(db, pipeline.team_id)
    return {
        "id": pipeline.id,
        "name": pipeline.name,
        "type": pipeline.template_id,
        "projectId": pipeline.project_id,
        "projectName": project.name if project else pipeline.project_id,
        "teamId": pipeline.team_id,
        "teamName": team.name if team else pipeline.team_id,
        "specVersion": pipeline.spec_version,
        "state": pipeline.state,
        "latestArtifactVersion": pipeline.latest_artifact_version,
        "createdAt": _iso(pipeline.created_at),
        "updatedAt": _iso(pipeline.updated_at),
        "createdBy": pipeline.created_by,
        "createdByName": _actor_name(db, pipeline.created_by),
        "updatedBy": pipeline.updated_by,
        "updatedByName": _actor_name(db, pipeline.updated_by),
        "spec": pipeline.spec or {},
        "draft": pipeline.draft or {},
    }


def artifact_to_dict(artifact: models.Artifact, *, include_content: bool = True) -> dict[str, Any]:
    out = {
        "id": artifact.id,
        "pipelineId": artifact.pipeline_id,
        "pipelineName": artifact.pipeline_name,
        "fileName": artifact.file_name,
        "fileType": artifact.file_type,
        "artifactType": artifact.artifact_type,
        "layer": artifact.layer,
        "version": artifact.version,
        "state": artifact.state,
        "bytes": artifact.bytes,
    }
    if include_content:
        out["content"] = artifact.content
    return out


def push_request_to_dict(push: models.GitPushRequest) -> dict[str, Any]:
    pr = push.pull_request
    return {
        "id": push.id,
        "pipelineId": push.pipeline_id,
        "provider": push.provider,
        "repositoryUrl": push.repository_url,
        "featureBranch": push.feature_branch,
        "baseBranch": push.base_branch,
        "artifactIds": list(push.artifact_ids or []),
        "commitMessage": push.commit_message,
        "commitSha": push.commit_sha,
        "status": push.status,
        "error": push.error,
        "simulated": push.simulated,
        "pullRequest": (
            {
                "id": pr.id,
                "number": pr.number,
                "url": pr.url,
                "title": pr.title,
                "sourceBranch": pr.source_branch,
                "targetBranch": pr.target_branch,
                "status": pr.status,
                "simulated": pr.simulated,
            }
            if pr
            else None
        ),
    }


def environment_to_dict(env: models.Environment) -> dict[str, Any]:
    return {
        "name": env.name,
        "label": env.label,
        "status": env.status,
        "description": env.description,
        "configuration": {
            "name": env.name,
            "storageAccount": env.storage_account,
            "sqlServer": env.sql_server,
            "database": env.database,
            "keyVault": env.key_vault,
            "factoryName": env.factory_name,
            "subscriptionId": env.subscription_id,
            "resourceGroup": env.resource_group,
        },
    }


def audit_to_dict(db: Session, entry: models.AuditEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "timestamp": _iso(entry.created_at),
        "actor": entry.actor,
        "actorName": _actor_name(db, entry.actor),
        "action": entry.action,
        "target": entry.target,
        "details": entry.details,
        "teamId": entry.team_id,
    }


def connection_to_dict(db: Session, connection: models.ConnectionLibraryEntry) -> dict[str, Any]:
    return {
        "id": connection.id,
        "projectId": connection.project_id,
        "teamId": connection.team_id,
        "name": connection.name,
        "role": connection.role,
        "connectorType": connection.connector_type,
        "description": connection.description,
        "draft": connection.draft or {},
        "createdAt": _iso(connection.created_at),
        "updatedAt": _iso(connection.updated_at),
        "createdBy": connection.created_by,
        "createdByName": _actor_name(db, connection.created_by),
    }


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


def enqueue_job(
    db: Session,
    *,
    job_type: str,
    pipeline_id: str | None,
    payload: dict[str, Any],
    actor: str | None,
) -> models.Job:
    job = models.Job(
        id=new_id("job"),
        type=job_type,
        status="queued",
        pipeline_id=pipeline_id,
        payload=payload,
        created_by=actor,
    )
    db.add(job)
    db.flush()
    return job


def wait_for_job(db: Session, job_id: str, *, timeout_s: float = 6.0, poll_s: float = 0.25) -> models.Job | None:
    """Best-effort synchronous wait so the common fast path feels immediate.

    Returns the job in whatever state it reached; callers must handle a job
    still ``queued`` / ``running`` (the worker will finish it out of band).
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        db.expire_all()
        job = db.get(models.Job, job_id)
        if job and job.status in ("succeeded", "failed"):
            return job
        time.sleep(poll_s)
    return db.get(models.Job, job_id)


# ---------------------------------------------------------------------------
# Pipeline lifecycle
# ---------------------------------------------------------------------------


def create_pipeline(
    db: Session, *, name: str, project_id: str, template_id: str, draft: dict[str, Any], actor: str | None
) -> models.PipelineDefinition:
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(404, "project_not_found", f"Project '{project_id}' does not exist.")
    template = repository.get_template(db, template_id)
    if template is None or not template.active:
        raise ApiError(422, "template_invalid", f"Template '{template_id}' does not exist or is not active.")

    merged_draft = {**(draft or {}), "name": name, "projectId": project_id, "templateId": template_id}
    pipeline = models.PipelineDefinition(
        id=new_id("pl"),
        name=name,
        template_id=template_id,
        project_id=project_id,
        team_id=project.team_id,
        state="DRAFT",
        draft=merged_draft,
        spec={},
        created_by=actor,
    )
    db.add(pipeline)
    repository.record_audit(
        db, actor=actor or "system", action="pipeline.created", target=name, details=f"Template {template_id}.", team_id=project.team_id
    )
    db.flush()
    return pipeline


def update_pipeline(
    db: Session,
    pipeline: models.PipelineDefinition,
    *,
    name: str | None,
    template_id: str | None,
    draft: dict[str, Any] | None,
    actor: str | None = None,
    expected_updated_at: str | None = None,
) -> models.PipelineDefinition:
    # §24.14 — reject a save built on a stale copy instead of silently overwriting someone else's edit.
    if expected_updated_at is not None and expected_updated_at != _iso(pipeline.updated_at):
        raise ApiError(
            409,
            "version_conflict",
            f"Pipeline '{pipeline.name}' was changed by someone else since you opened it. Reload it and re-apply your edits.",
        )
    if name is not None:
        pipeline.name = name
    if template_id is not None:
        template = repository.get_template(db, template_id)
        if template is None or not template.active:
            raise ApiError(422, "template_invalid", f"Template '{template_id}' does not exist or is not active.")
        pipeline.template_id = template_id
    if draft is not None:
        pipeline.draft = {**draft, "name": pipeline.name, "projectId": pipeline.project_id, "templateId": pipeline.template_id}
    # Editing inputs invalidates the generated set.
    if pipeline.state not in ("DRAFT",):
        pipeline.state = "DRAFT"
    pipeline.updated_by = actor
    repository.record_audit(
        db, actor=actor or "system", action="pipeline.updated", target=pipeline.name, details="Name/template/draft updated.", team_id=pipeline.team_id
    )
    db.flush()
    return pipeline


def request_generate(db: Session, pipeline: models.PipelineDefinition, *, actor: str | None) -> models.Job:
    version = _next_version(db, pipeline)
    job = enqueue_job(
        db,
        job_type=JOB_GENERATE,
        pipeline_id=pipeline.id,
        payload={"draft": pipeline.draft, "templateId": pipeline.template_id, "version": version},
        actor=actor,
    )
    db.flush()
    return job


def request_validate(db: Session, pipeline: models.PipelineDefinition, *, actor: str | None) -> models.Job:
    job = enqueue_job(
        db,
        job_type=JOB_VALIDATE,
        pipeline_id=pipeline.id,
        payload={
            "draft": pipeline.draft,
            "templateId": pipeline.template_id,
            "version": pipeline.latest_artifact_version,
        },
        actor=actor,
    )
    db.flush()
    return job


def _next_version(db: Session, pipeline: models.PipelineDefinition) -> str:
    latest = repository.latest_version(db, pipeline.id)
    if latest is None:
        return "v1.0.0"
    try:
        major, minor, patch = (int(p) for p in latest.version.lstrip("v").split("."))
        return f"v{major}.{minor + 1}.0"
    except ValueError:
        return f"v1.0.{len(pipeline.versions) + 1}"


# ---------------------------------------------------------------------------
# Git push
# ---------------------------------------------------------------------------


def _project_git_settings(db: Session, pipeline: models.PipelineDefinition) -> dict[str, Any]:
    settings = repository.get_project_settings(db, pipeline.project_id)
    doc = settings.document if settings else {}
    return dict((doc or {}).get("gitDefaults") or {})


def _enforce_naming_standard(db: Session, pipeline: models.PipelineDefinition, selected: list[models.Artifact]) -> None:
    """§25.6 pre-push hard gate — reject the push when a "block" naming rule fails.

    Defense in depth: the worker's `validate` job already surfaces the same
    violations inline (worker/app/validator/rules.py), but nothing requires a
    pipeline to have been validated before Push to Git, so this is the one
    place a "block" naming rule is actually guaranteed to run before
    artifacts leave the metastore. `warn`/`off` never block here — those are
    surfaced to the user at validate time instead (§25.6's evaluation table).
    """
    project = pipeline.project
    settings = project.settings if project else None
    convention = settings.document.get("namingConvention") if settings else None
    if not convention or (convention.get("enforcement") or "off") != "block":
        return

    named: list[tuple[str, str]] = []
    for artifact in selected:
        if artifact.file_type != "json":
            continue
        try:
            parsed = json.loads(artifact.content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed.get("name"):
            named.append((artifact.artifact_type, str(parsed["name"])))

    violations = [v for v in evaluate_artifact_names(named, convention) if v.level == "block"]
    if violations:
        details = "; ".join(f"{v.artifact_type}: {v.message}" for v in violations)
        raise ApiError(
            422,
            "policy_violation",
            f"{len(violations)} artifact name(s) violate the project's naming standard.",
            details=details,
        )


def missing_required_tags(annotations: list[Any], keys: list[str]) -> list[str]:
    """§25.3 tagging.required (gaps.md GOV-05/WD-06): the required tag keys with no matching
    annotation. An annotation `key` or `key:value` (the Build wizard's project-tag format)
    satisfies `key`, case-insensitively."""
    present = {str(a).split(":", 1)[0].strip().lower() for a in annotations if str(a).strip()}
    return [k for k in keys if k.strip() and k.strip().lower() not in present]


def _enforce_required_tags(pipeline: models.PipelineDefinition, selected: list[models.Artifact]) -> None:
    """§25.6 pre-push hard gate for the project's `requiredTags` policy (block only; warn is
    reported by the Standards compliance scan instead, like naming's warn level)."""
    settings = pipeline.project.settings if pipeline.project else None
    policy = (settings.document.get("requiredTags") if settings else None) or {}
    keys = [str(k) for k in policy.get("keys") or [] if str(k).strip()]
    if (policy.get("enforcement") or "off") != "block" or not keys:
        return
    problems: list[str] = []
    for artifact in selected:
        if artifact.artifact_type != "pipeline" or artifact.file_type != "json":
            continue
        try:
            parsed = json.loads(artifact.content)
        except (json.JSONDecodeError, TypeError):
            continue
        annotations = ((parsed.get("properties") or {}).get("annotations") or []) if isinstance(parsed, dict) else []
        if missing := missing_required_tags(annotations, keys):
            problems.append(f"{parsed.get('name', artifact.file_name)}: missing {', '.join(missing)}")
    if problems:
        raise ApiError(
            422,
            "policy_violation",
            f"{len(problems)} pipeline(s) lack the project's required tags ({', '.join(keys)}).",
            details="; ".join(problems),
        )


def request_git_push(
    db: Session,
    pipeline: models.PipelineDefinition,
    *,
    artifact_ids: list[str],
    provider: str | None,
    repository_url: str | None,
    feature_branch: str,
    base_branch: str,
    commit_message: str | None,
    pr_title: str | None,
    pr_body: str | None,
    reviewers: list[str],
    actor: str | None,
    git_token: str | None = None,
) -> models.GitPushRequest:
    git_defaults = _project_git_settings(db, pipeline)
    provider = provider or git_defaults.get("provider") or "github"
    repository_url = repository_url or git_defaults.get("repositoryUrl") or ""
    base_branch = base_branch or git_defaults.get("baseBranch") or "dev"

    # §17.7–9 / §23.6 — always-on safety rules enforced before any write.
    if not repository_url.strip():
        raise ApiError(422, "git_not_configured", "A Git repository must be configured before pushing.")
    if not feature_branch.strip():
        raise ApiError(422, "feature_branch_required", "A feature branch is required.")
    if feature_branch.strip() == base_branch.strip():
        raise ApiError(
            422, "protected_branch", f"The feature branch must not equal the protected base branch '{base_branch}'."
        )

    selected = repository.list_artifacts(db, pipeline.id)
    if pipeline.latest_artifact_version:
        selected = [a for a in selected if a.version == pipeline.latest_artifact_version]
    if artifact_ids:
        by_id = {a.id: a for a in selected}
        missing = [aid for aid in artifact_ids if aid not in by_id]
        if missing:
            raise ApiError(422, "artifact_not_found", f"Unknown artifact id(s): {', '.join(missing)}.")
        selected = [by_id[aid] for aid in artifact_ids]
    if not selected:
        raise ApiError(422, "no_artifacts", "Generate and select at least one artifact before pushing.")

    _enforce_naming_standard(db, pipeline, selected)
    _enforce_required_tags(pipeline, selected)

    artifact_root = (git_defaults.get("artifactRoot") or "adf/artifacts/").rstrip("/") + "/"
    push = models.GitPushRequest(
        id=new_id("push"),
        pipeline_id=pipeline.id,
        provider=provider,
        repository_url=repository_url,
        feature_branch=feature_branch.strip(),
        base_branch=base_branch.strip(),
        artifact_ids=[a.id for a in selected],
        commit_message=commit_message
        or f"feat(artifacts): {pipeline.name} {pipeline.latest_artifact_version or 'v1.0.0'} — {len(selected)} files",
        status="pending",
        created_by=actor,
    )
    db.add(push)
    pipeline.state = "PUSHING"
    enqueue_job(
        db,
        job_type=JOB_GIT_PUSH,
        pipeline_id=pipeline.id,
        payload={
            "pushRequestId": push.id,
            "artifactRoot": artifact_root,
            "prTitle": pr_title or git_defaults.get("prTitleTemplate") or f"feat: {pipeline.name}",
            "prBody": pr_body or git_defaults.get("prBodyTemplate") or "",
            "reviewers": reviewers or git_defaults.get("reviewers") or [],
            "credentialVault": git_defaults.get("credentialVault"),
            # Per-push PAT: encrypted at rest (app/crypto.py, gap #17) so anything
            # that survives the worker's post-use scrub is ciphertext, never a
            # usable credential. The worker uses it for the provider call then
            # scrubs it from the job payload so it is never durably stored (§25.5).
            "gitToken": encrypt_secret(git_token) if git_token else None,
        },
        actor=actor,
    )
    repository.record_audit(
        db,
        actor=actor or "system",
        action="git.push_requested",
        target=f"{pipeline.name} {pipeline.latest_artifact_version or ''}".strip(),
        details=f"{len(selected)} artifacts -> {feature_branch}.",
        team_id=pipeline.team_id,
    )
    db.flush()
    return push


def latest_validation_result(db: Session, pipeline: models.PipelineDefinition) -> dict[str, Any]:
    job = db.scalars(
        select(models.Job)
        .where(models.Job.pipeline_id == pipeline.id, models.Job.type == JOB_VALIDATE)
        .order_by(models.Job.created_at.desc())
        .limit(1)
    ).first()
    if job is None:
        return {
            "pipelineId": pipeline.id,
            "state": "pending",
            "issues": [],
            "validatedAt": None,
            "correlationId": str(uuid.uuid4()),
        }
    result = job.result or {}
    return {
        "pipelineId": pipeline.id,
        "state": result.get("state", "pending" if job.status in ("queued", "running") else "failed"),
        "issues": result.get("issues", []),
        "validatedAt": _iso(job.finished_at),
        "correlationId": job.id,
    }


# ---------------------------------------------------------------------------
# Connection library (§24.17) — server-backed replacement for the browser-only
# `lib/adf/connection-store.ts`. A "connection" bundles a linked service, its
# source-objects (table/file) onboarding list, and a dataset shape into one
# reusable, project-scoped row.
# ---------------------------------------------------------------------------

_CONNECTION_ROLES = {"source", "target", "both"}


def _validate_connection_role(role: str) -> None:
    if role not in _CONNECTION_ROLES:
        raise ApiError(
            422,
            "connection_role_invalid",
            f"role must be one of {sorted(_CONNECTION_ROLES)}, got '{role}'.",
        )


def create_connection(
    db: Session,
    *,
    project_id: str,
    name: str,
    role: str,
    connector_type: str,
    description: str,
    draft: dict[str, Any],
    actor: str | None,
) -> models.ConnectionLibraryEntry:
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(404, "project_not_found", f"Project '{project_id}' does not exist.")
    if not name.strip():
        raise ApiError(422, "connection_name_required", "A connection name is required.")
    _validate_connection_role(role)
    connection = models.ConnectionLibraryEntry(
        id=new_id("conn"),
        project_id=project_id,
        team_id=project.team_id,
        name=name.strip(),
        role=role,
        connector_type=connector_type,
        description=description,
        draft=draft or {},
        created_by=actor,
    )
    db.add(connection)
    repository.record_audit(db, actor=actor or "system", action="connection.created", target=connection.name, team_id=connection.team_id)
    db.flush()
    return connection


def update_connection(
    db: Session,
    connection: models.ConnectionLibraryEntry,
    *,
    name: str | None,
    role: str | None,
    connector_type: str | None,
    description: str | None,
    draft: dict[str, Any] | None,
) -> models.ConnectionLibraryEntry:
    if name is not None:
        if not name.strip():
            raise ApiError(422, "connection_name_required", "A connection name is required.")
        connection.name = name.strip()
    if role is not None:
        _validate_connection_role(role)
        connection.role = role
    if connector_type is not None:
        connection.connector_type = connector_type
    if description is not None:
        connection.description = description
    if draft is not None:
        connection.draft = draft
    db.flush()
    return connection


def delete_connection(db: Session, connection: models.ConnectionLibraryEntry, *, actor: str | None) -> None:
    repository.record_audit(db, actor=actor or "system", action="connection.deleted", target=connection.name, team_id=connection.team_id)
    db.delete(connection)
    db.flush()


# ---------------------------------------------------------------------------
# Managed identities (AGENTS.md §32.6, gap #21 phase MI3)
# ---------------------------------------------------------------------------


def managed_identity_to_dict(db: Session, identity: models.ManagedIdentity) -> dict[str, Any]:
    return {
        "id": identity.id,
        "teamId": identity.team_id,
        "name": identity.name,
        "resourceId": identity.resource_id,
        "clientId": identity.client_id,
        "createdAt": _iso(identity.created_at),
        "updatedAt": _iso(identity.updated_at),
        "createdBy": identity.created_by,
        "createdByName": _actor_name(db, identity.created_by),
    }


def create_managed_identity(
    db: Session, *, team_id: str, name: str, resource_id: str, client_id: str | None, actor: str | None
) -> models.ManagedIdentity:
    if not name.strip():
        raise ApiError(422, "managed_identity_name_required", "A name is required.")
    if not resource_id.strip():
        raise ApiError(422, "managed_identity_resource_id_required", "An ARM resource ID is required.")
    identity = models.ManagedIdentity(
        id=new_id("mi"),
        team_id=team_id,
        name=name.strip(),
        resource_id=resource_id.strip(),
        client_id=(client_id or "").strip() or None,
        created_by=actor,
    )
    db.add(identity)
    repository.record_audit(
        db, actor=actor or "system", action="managed_identity.created", target=identity.name, team_id=team_id
    )
    db.flush()
    return identity


def update_managed_identity(
    db: Session,
    identity: models.ManagedIdentity,
    *,
    name: str | None,
    resource_id: str | None,
    client_id: str | None,
) -> models.ManagedIdentity:
    if name is not None:
        if not name.strip():
            raise ApiError(422, "managed_identity_name_required", "A name is required.")
        identity.name = name.strip()
    if resource_id is not None:
        if not resource_id.strip():
            raise ApiError(422, "managed_identity_resource_id_required", "An ARM resource ID is required.")
        identity.resource_id = resource_id.strip()
    if client_id is not None:
        identity.client_id = client_id.strip() or None
    db.flush()
    return identity


def delete_managed_identity(db: Session, identity: models.ManagedIdentity, *, actor: str | None) -> None:
    repository.record_audit(
        db, actor=actor or "system", action="managed_identity.deleted", target=identity.name, team_id=identity.team_id
    )
    db.delete(identity)
    db.flush()


# ---------------------------------------------------------------------------
# Bronze Source Onboarding Registry (AGENTS.md §26) — server-backed replacement
# for the browser-only `lib/adf/onboarding-store.ts`.
# ---------------------------------------------------------------------------

_ONBOARDING_FIELD_MAP: list[str] = [
    "enabled",
    "batch_group",
    "owner",
    "data_classification",
    "source_system",
    "source_type",
    "source_schema",
    "source_object",
    "source_query",
    "target_type",
    "target_schema",
    "target_object",
    "target_path",
    "target_file_name",
    "target_file_format",
    "write_behavior",
    "upsert_keys",
    "loading_behavior",
    "watermark_column",
    "watermark_column_type",
    "notes",
    "status",
    "provenance",
]


def onboarding_object_to_dict(obj: models.OnboardingObject) -> dict[str, Any]:
    data: dict[str, Any] = {"object_key": obj.object_key, "project_id": obj.project_id, "version": obj.version}
    for field in _ONBOARDING_FIELD_MAP:
        data[field] = getattr(obj, field)
    data["created_by"] = obj.created_by
    data["updated_by"] = obj.updated_by
    data["created_at"] = _iso(obj.created_at)
    data["updated_at"] = _iso(obj.updated_at)
    return data


def _set_onboarding_fields(obj: models.OnboardingObject, data: dict[str, Any], *, partial: bool) -> None:
    for field in _ONBOARDING_FIELD_MAP:
        if partial and field not in data:
            continue
        if field in data:
            setattr(obj, field, data[field])


_DATA_CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
# §26.6 where a row last came from.
ONBOARDING_PROVENANCES = ("manual", "upload", "git-import")


def _validate_onboarding_body(data: dict[str, Any]) -> None:
    if not (data.get("source_object") or "").strip():
        raise ApiError(422, "onboarding_source_object_required", "source_object is required.")
    if not (data.get("source_system") or "").strip():
        raise ApiError(422, "onboarding_source_system_required", "source_system is required.")
    if data.get("data_classification") not in (None, "", *_DATA_CLASSIFICATIONS):
        raise ApiError(
            422,
            "onboarding_data_classification_invalid",
            f"data_classification must be one of {', '.join(_DATA_CLASSIFICATIONS)}.",
        )
    if data.get("provenance") not in (None, "", *ONBOARDING_PROVENANCES):
        raise ApiError(
            422,
            "onboarding_provenance_invalid",
            f"provenance must be one of {', '.join(ONBOARDING_PROVENANCES)}.",
        )
    if data.get("loading_behavior") == "DeltaLoad" and not (data.get("watermark_column") or "").strip():
        raise ApiError(
            422,
            "onboarding_watermark_required",
            "watermark_column is required when loading_behavior is 'DeltaLoad'.",
        )
    if data.get("write_behavior") in ("upsert", "merge") and not (data.get("upsert_keys") or "").strip():
        raise ApiError(
            422,
            "onboarding_upsert_keys_required",
            "upsert_keys is required when write_behavior is 'upsert' or 'merge'.",
        )


def create_onboarding_object(
    db: Session, project_id: str, body: dict[str, Any], *, actor: str | None
) -> models.OnboardingObject:
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(404, "project_not_found", f"Project '{project_id}' does not exist.")
    _validate_onboarding_body(body)
    object_key = (body.get("object_key") or "").strip() or f"{body['source_system']}.{body['source_object']}"
    if repository.get_onboarding_object(db, project_id, object_key) is not None:
        raise ApiError(409, "object_exists", f"An onboarding object with key '{object_key}' already exists.")
    obj = models.OnboardingObject(
        object_key=object_key,
        project_id=project_id,
        version=1,
        created_by=actor,
        updated_by=actor,
    )
    _set_onboarding_fields(obj, {**body, "provenance": body.get("provenance") or "manual"}, partial=False)
    db.add(obj)
    repository.record_audit(db, actor=actor or "system", action="onboarding_object.created", target=object_key, team_id=project.team_id)
    db.flush()
    return obj


def update_onboarding_object(
    db: Session, obj: models.OnboardingObject, body: dict[str, Any], *, actor: str | None
) -> models.OnboardingObject:
    merged = {field: body.get(field, getattr(obj, field)) for field in _ONBOARDING_FIELD_MAP}
    _validate_onboarding_body(merged)
    _set_onboarding_fields(obj, body, partial=True)
    obj.version += 1
    obj.updated_by = actor
    repository.record_audit(db, actor=actor or "system", action="onboarding_object.updated", target=obj.object_key, team_id=_project_team_id(db, obj.project_id))
    db.flush()
    return obj


def delete_onboarding_object(db: Session, obj: models.OnboardingObject, *, hard: bool, actor: str | None) -> None:
    if hard:
        repository.record_audit(db, actor=actor or "system", action="onboarding_object.deleted", target=obj.object_key, team_id=_project_team_id(db, obj.project_id))
        db.delete(obj)
    else:
        obj.enabled = False
        obj.updated_by = actor
        repository.record_audit(db, actor=actor or "system", action="onboarding_object.disabled", target=obj.object_key, team_id=_project_team_id(db, obj.project_id))
    db.flush()


# ---------------------------------------------------------------------------
# Data Quality rules (AGENTS.md §30.4/§30.5) — schema-only for now; nothing
# generates a pipeline activity from a row here yet (§30.7 AQ2/AQ3).
# ---------------------------------------------------------------------------

DQ_CHECK_TYPES = frozenset({"not_null", "unique", "range", "row_count", "freshness"})
DQ_ENFORCEMENT_LEVELS = frozenset({"off", "warn", "block"})


def dq_rule_to_dict(rule: models.DqRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "projectId": rule.project_id,
        "objectKey": rule.object_key,
        "checkType": rule.check_type,
        "config": rule.config or {},
        "enforcement": rule.enforcement,
        "createdBy": rule.created_by,
        "createdAt": _iso(rule.created_at),
    }


def create_dq_rule(
    db: Session,
    obj: models.OnboardingObject,
    *,
    check_type: str,
    config: dict[str, Any] | None,
    enforcement: str,
    actor: str | None,
) -> models.DqRule:
    if check_type not in DQ_CHECK_TYPES:
        raise ApiError(422, "dq_rule_check_type_invalid", f"check_type must be one of {sorted(DQ_CHECK_TYPES)}.")
    if enforcement not in DQ_ENFORCEMENT_LEVELS:
        raise ApiError(422, "dq_rule_enforcement_invalid", f"enforcement must be one of {sorted(DQ_ENFORCEMENT_LEVELS)}.")
    rule = models.DqRule(
        id=new_id("dq"),
        project_id=obj.project_id,
        object_key=obj.object_key,
        check_type=check_type,
        config=config or {},
        enforcement=enforcement,
        created_by=actor,
    )
    db.add(rule)
    repository.record_audit(
        db, actor=actor or "system", action="dq_rule.created", target=f"{obj.object_key}:{check_type}",
        team_id=_project_team_id(db, obj.project_id),
    )
    db.flush()
    return rule


def delete_dq_rule(db: Session, rule: models.DqRule, *, actor: str | None) -> None:
    repository.record_audit(
        db, actor=actor or "system", action="dq_rule.deleted", target=f"{rule.object_key}:{rule.check_type}",
        team_id=_project_team_id(db, rule.project_id),
    )
    db.delete(rule)
    db.flush()


def bulk_upsert_onboarding_objects(
    db: Session,
    project_id: str,
    rows: list[dict[str, Any]],
    *,
    actor: str | None,
    file_name: str | None = None,
    mapping_profile_id: str | None = None,
) -> dict[str, int]:
    """Always additive (matches `bulkUpsertOnboardingObjects`'s contract, §26.3
    step 4 "append/upsert-by-objectKey"): a row whose `object_key` matches an
    existing one is updated in place; every other row is inserted. Nothing
    already in the registry is ever removed by this call."""
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(404, "project_not_found", f"Project '{project_id}' does not exist.")
    if mapping_profile_id:
        profile = repository.get_mapping_profile(db, mapping_profile_id)
        if profile is None or profile.project_id != project_id:
            raise ApiError(422, "mapping_profile_not_found", f"Mapping profile '{mapping_profile_id}' is not in this project.")
    seen: set[str] = set()
    for body in rows:  # ONB-01: two rows with one key would silently collapse into the last one.
        key = (body.get("object_key") or "").strip() or f"{body.get('source_system')}.{body.get('source_object')}"
        if key in seen:
            raise ApiError(422, "onboarding_duplicate_object_key", f"object_key '{key}' appears more than once in this upload.")
        seen.add(key)
    added = 0
    updated = 0
    for body in rows:
        _validate_onboarding_body(body)
        body = {**body, "provenance": body.get("provenance") or ("upload" if file_name else "manual")}
        object_key = (body.get("object_key") or "").strip() or f"{body['source_system']}.{body['source_object']}"
        existing = repository.get_onboarding_object(db, project_id, object_key)
        if existing is not None:
            _set_onboarding_fields(existing, body, partial=False)
            existing.version += 1
            existing.updated_by = actor
            updated += 1
        else:
            obj = models.OnboardingObject(
                object_key=object_key, project_id=project_id, version=1, created_by=actor, updated_by=actor
            )
            _set_onboarding_fields(obj, body, partial=False)
            db.add(obj)
            added += 1
    repository.record_audit(
        db, actor=actor or "system", action="onboarding_object.bulk_upsert", target=f"{added} added, {updated} updated", team_id=project.team_id
    )
    # §26.8 audit trail. The batch is all-or-nothing (any invalid row raised above), so
    # row_error is always 0 for a committed import.
    db.add(
        models.OnboardingImport(
            id=new_id("obi"),
            project_id=project_id,
            file_name=(file_name or "").strip()[:256] or "(manual bulk upsert)",
            file_hash=hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
            row_total=len(rows),
            row_valid=added + updated,
            row_error=0,
            mode="upsert",
            mapping_profile_id=mapping_profile_id or None,
            committed_by=actor,
            committed_at=datetime.now(timezone.utc),
            report={"added": added, "updated": updated},
        )
    )
    db.flush()
    return {"added": added, "updated": updated}


def mapping_profile_to_dict(profile: models.OnboardingMappingProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "projectId": profile.project_id,
        "name": profile.name,
        "headerMap": profile.header_map or {},
        "updatedBy": profile.updated_by,
        "updatedAt": _iso(profile.updated_at),
    }


def upsert_mapping_profile(
    db: Session, project: models.Project, name: str, header_map: dict[str, str], *, actor: str | None
) -> models.OnboardingMappingProfile:
    trimmed = (name or "").strip()
    if not trimmed:
        raise ApiError(422, "mapping_profile_name_required", "A mapping profile needs a name.")
    if len(trimmed) > 128:
        raise ApiError(422, "mapping_profile_name_too_long", "Mapping profile names are at most 128 characters.")
    cleaned = {str(k): str(v) for k, v in (header_map or {}).items() if str(k).strip() and str(v).strip()}
    if not cleaned:
        raise ApiError(422, "mapping_profile_empty", "A mapping profile must map at least one header.")
    existing = next(
        (p for p in repository.list_mapping_profiles(db, project.id) if p.name.lower() == trimmed.lower()), None
    )
    if existing is None:
        existing = models.OnboardingMappingProfile(id=new_id("omp"), project_id=project.id, name=trimmed)
        db.add(existing)
    existing.name = trimmed
    existing.header_map = cleaned
    existing.updated_by = actor
    repository.record_audit(
        db, actor=actor or "system", action="onboarding_mapping_profile.saved", target=trimmed, team_id=project.team_id
    )
    db.flush()
    db.refresh(existing)
    return existing


def delete_mapping_profile(
    db: Session, profile: models.OnboardingMappingProfile, *, team_id: str | None, actor: str | None
) -> None:
    repository.record_audit(
        db, actor=actor or "system", action="onboarding_mapping_profile.deleted", target=profile.name, team_id=team_id
    )
    db.delete(profile)
    db.flush()


def onboarding_import_to_dict(db: Session, imp: models.OnboardingImport) -> dict[str, Any]:
    report = imp.report or {}
    return {
        "id": imp.id,
        "projectId": imp.project_id,
        "fileName": imp.file_name,
        "fileHash": imp.file_hash,
        "rowTotal": imp.row_total,
        "rowValid": imp.row_valid,
        "rowError": imp.row_error,
        "mode": imp.mode,
        "added": int(report.get("added", 0)),
        "updated": int(report.get("updated", 0)),
        "mappingProfileId": imp.mapping_profile_id,
        # Resolved at read time; None once the profile has been deleted (the id is kept for the record).
        "mappingProfileName": (
            profile.name
            if imp.mapping_profile_id and (profile := repository.get_mapping_profile(db, imp.mapping_profile_id))
            else None
        ),
        "committedBy": imp.committed_by,
        "committedByName": _actor_name(db, imp.committed_by),
        "committedAt": _iso(imp.committed_at),
        "createdAt": _iso(imp.created_at),
    }


# ---------------------------------------------------------------------------
# STTM Silver/Gold Object Registry (AGENTS.md §33, IMPLEMENTATION_PLAN.md P1/P2)
# ---------------------------------------------------------------------------

_REGISTRY_MODEL: dict[str, type] = {"silver": models.SilverObject, "mart": models.MartObject}
_MEDALLION_LAYER: dict[str, str] = {"silver": "silver", "mart": "gold"}


def _validate_registry_body(layer: str, medallion_layer: str, data: dict[str, Any]) -> None:
    issues = sttm_rules.validate_object_header(medallion_layer, data)
    issues += sttm_rules.validate_transformation_rules(medallion_layer, data.get("transformations") or [])
    issues += sttm_rules.validate_data_quality_rules(data.get("dataQualityRules") or [])
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise ApiError(
            422,
            "sttm_validation_error",
            f"{errors[0].message}" + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""),
            details=json.dumps([i.to_dict() for i in issues]),
        )


def registry_object_to_dict(db: Session, obj: Any, *, layer: str) -> dict[str, Any]:
    return {
        "objectKey": obj.object_key,
        "projectId": obj.project_id,
        "version": obj.version,
        "enabled": obj.enabled,
        "tag": obj.tag,
        "curatedEntity": obj.curated_entity,
        "martDomain": getattr(obj, "mart_domain", None),
        "sourceObjectKeys": obj.source_object_keys or [],
        "owner": obj.owner,
        "batchGroup": obj.batch_group,
        "domain": obj.domain,
        "writeBehavior": obj.write_behavior,
        "loadingBehavior": obj.loading_behavior,
        "watermarkColumn": obj.watermark_column,
        "watermarkColumnType": obj.watermark_column_type,
        "businessKeys": obj.business_keys or [],
        "transformations": obj.transformations or [],
        "dataQualityRules": obj.data_quality_rules or [],
        "targetTable": obj.target_table,
        "dataClassification": obj.data_classification,
        **{wire: getattr(obj, attr) for wire, attr in _REGISTRY_GOVERNANCE_FIELDS},
        "notes": obj.notes,
        "provenanceSource": obj.provenance_source,
        "importId": obj.import_id,
        "status": obj.status,
        "createdBy": obj.created_by,
        "createdByName": _actor_name(db, obj.created_by),
        "updatedBy": obj.updated_by,
        "updatedByName": _actor_name(db, obj.updated_by),
        "createdAt": _iso(obj.created_at),
        "updatedAt": _iso(obj.updated_at),
        "warnings": [
            w.to_dict()
            for w in sttm_rules.check_dq_columns_against_transformations(obj.transformations or [], obj.data_quality_rules or [])
        ],
    }


# DMT-08: typed governance fields on silver_objects/mart_objects (wire key, column).
_REGISTRY_GOVERNANCE_FIELDS: list[tuple[str, str]] = [
    ("containsPii", "contains_pii"),
    ("containsPhi", "contains_phi"),
    ("containsPci", "contains_pci"),
    ("criticalDataElement", "critical_data_element"),
    ("auditRequired", "audit_required"),
    ("retentionPeriod", "retention_period"),
    ("regulatoryTag", "regulatory_tag"),
    ("businessCriticality", "business_criticality"),
]

_REGISTRY_FIELD_MAP: list[tuple[str, str]] = [
    ("enabled", "enabled"),
    ("tag", "tag"),
    ("curatedEntity", "curated_entity"),
    ("martDomain", "mart_domain"),
    ("sourceObjectKeys", "source_object_keys"),
    ("owner", "owner"),
    ("batchGroup", "batch_group"),
    ("domain", "domain"),
    ("writeBehavior", "write_behavior"),
    ("loadingBehavior", "loading_behavior"),
    ("watermarkColumn", "watermark_column"),
    ("watermarkColumnType", "watermark_column_type"),
    ("businessKeys", "business_keys"),
    ("transformations", "transformations"),
    ("dataQualityRules", "data_quality_rules"),
    ("dataClassification", "data_classification"),
    *_REGISTRY_GOVERNANCE_FIELDS,
    ("notes", "notes"),
]


def _set_registry_fields(
    obj: Any, data: dict[str, Any], *, medallion_layer: str, partial: bool, layer_prefixes: dict[str, Any] | None = None
) -> None:
    """Copies `RegistryObjectIn`/`RegistryObjectUpdateIn`-shaped `data` onto
    `obj`. `partial=True` (update) only touches keys actually present in
    `data`; `partial=False` (create) sets every field, defaulting sensibly.
    `target_table` is always recomputed, never taken from the payload — keeps
    the "target name is always computed" invariant (§16) true for registry
    rows, same as the Build wizard's own Silver/Gold fields. `layer_prefixes`
    is the project's Standards-configured custom zone prefix per layer
    (§25.3) — omitted/None keeps the curated/mart default.
    """
    for wire_key, attr in _REGISTRY_FIELD_MAP:
        if partial and wire_key not in data:
            continue
        if attr == "mart_domain" and medallion_layer != "gold":
            continue  # silver_objects has no mart_domain column
        value = data.get(wire_key)
        if not partial and value is None and attr not in ("mart_domain",):
            continue  # leave the column's model-level default in place
        setattr(obj, attr, value)
    obj.target_table = sttm_rules.layer_target_table_name(
        medallion_layer, entity=obj.curated_entity, domain=getattr(obj, "mart_domain", None), layer_prefixes=layer_prefixes
    )


def _validate_registry_uniqueness(
    db: Session, layer: str, project_id: str, *, object_key: str, curated_entity: str, mart_domain: str | None, exclude_self: bool
) -> None:
    existing_key = (
        repository.get_silver_object(db, project_id, object_key)
        if layer == "silver"
        else repository.get_mart_object(db, project_id, object_key)
    )
    if existing_key is not None and not exclude_self:
        raise ApiError(409, "object_exists", f"An object with key '{object_key}' already exists.")
    existing_entity = (
        repository.get_silver_object_by_entity(db, project_id, curated_entity)
        if layer == "silver"
        else repository.get_mart_object_by_entity(db, project_id, mart_domain or "", curated_entity)
    )
    if existing_entity is not None and (not exclude_self or existing_entity.object_key != object_key):
        raise ApiError(
            409,
            "entity_exists",
            f"A {layer} object for curated_entity '{curated_entity}'"
            + (f" in mart_domain '{mart_domain}'" if layer == "mart" else "")
            + " already exists.",
        )


def project_layer_prefixes(db: Session, project_id: str) -> dict[str, Any] | None:
    """A project's Standards-configured custom zone prefix per medallion layer
    (§25.3, `NamingConvention.layerPrefixes`) — `None` when unset, same
    "read the live settings document" pattern as `worker/app/jobs/runner.py`."""
    settings = repository.get_project_settings(db, project_id)
    naming_convention = (settings.document.get("namingConvention") if settings else None) or {}
    return naming_convention.get("layerPrefixes")


def create_registry_object(db: Session, layer: str, project_id: str, body: dict[str, Any], *, actor: str | None) -> Any:
    project = repository.get_project(db, project_id)
    if project is None:
        raise ApiError(404, "project_not_found", f"Project '{project_id}' does not exist.")
    medallion_layer = _MEDALLION_LAYER[layer]
    object_key = (body.get("objectKey") or "").strip() or sttm_rules.to_snake(body.get("curatedEntity"))
    _validate_registry_body(layer, medallion_layer, body)
    _validate_registry_uniqueness(
        db, layer, project_id,
        object_key=object_key, curated_entity=body.get("curatedEntity", ""), mart_domain=body.get("martDomain"),
        exclude_self=False,
    )
    model = _REGISTRY_MODEL[layer]
    obj = model(
        object_key=object_key,
        project_id=project_id,
        version=1,
        target_table="",  # filled by _set_registry_fields below
        created_by=actor,
        updated_by=actor,
        provenance_source=body.get("provenanceSource", "manual"),
        import_id=body.get("importId"),
        status="valid",
    )
    _set_registry_fields(obj, body, medallion_layer=medallion_layer, partial=False, layer_prefixes=project_layer_prefixes(db, project_id))
    db.add(obj)
    repository.record_audit(db, actor=actor or "system", action=f"{layer}_object.created", target=object_key, team_id=project.team_id)
    db.flush()
    return obj


def update_registry_object(db: Session, layer: str, obj: Any, body: dict[str, Any], *, actor: str | None) -> Any:
    expected_version = body.get("expectedVersion")
    if expected_version is not None and expected_version != obj.version:
        raise ApiError(
            409,
            "version_conflict",
            f"Object was modified concurrently (expected version {expected_version}, current {obj.version}).",
        )
    medallion_layer = _MEDALLION_LAYER[layer]
    new_entity = body.get("curatedEntity", obj.curated_entity)
    new_domain = body.get("martDomain", getattr(obj, "mart_domain", None))
    if "curatedEntity" in body or "martDomain" in body:
        _validate_registry_uniqueness(
            db, layer, obj.project_id,
            object_key=obj.object_key, curated_entity=new_entity, mart_domain=new_domain, exclude_self=True,
        )
    merged = {
        "tag": body.get("tag", obj.tag),
        "curatedEntity": new_entity,
        "martDomain": new_domain,
        "sourceObjectKeys": body.get("sourceObjectKeys", obj.source_object_keys),
        "writeBehavior": body.get("writeBehavior", obj.write_behavior),
        "loadingBehavior": body.get("loadingBehavior", obj.loading_behavior),
        "watermarkColumn": body.get("watermarkColumn", obj.watermark_column),
        "watermarkColumnType": body.get("watermarkColumnType", obj.watermark_column_type),
        "businessKeys": body.get("businessKeys", obj.business_keys),
        "transformations": body.get("transformations", obj.transformations),
        "dataQualityRules": body.get("dataQualityRules", obj.data_quality_rules),
    }
    _validate_registry_body(layer, medallion_layer, merged)
    _set_registry_fields(
        obj, body, medallion_layer=medallion_layer, partial=True, layer_prefixes=project_layer_prefixes(db, obj.project_id)
    )
    obj.version += 1
    obj.updated_by = actor
    repository.record_audit(db, actor=actor or "system", action=f"{layer}_object.updated", target=obj.object_key, team_id=_project_team_id(db, obj.project_id))
    db.flush()
    return obj


def delete_registry_object(db: Session, layer: str, obj: Any, *, hard: bool, actor: str | None) -> None:
    if hard:
        repository.record_audit(db, actor=actor or "system", action=f"{layer}_object.deleted", target=obj.object_key, team_id=_project_team_id(db, obj.project_id))
        db.delete(obj)
    else:
        obj.enabled = False
        obj.updated_by = actor
        repository.record_audit(db, actor=actor or "system", action=f"{layer}_object.disabled", target=obj.object_key, team_id=_project_team_id(db, obj.project_id))
    db.flush()


def sttm_import_to_dict(db: Session, imp: models.SttmImport) -> dict[str, Any]:
    report = imp.report or {}
    return {
        "id": imp.id,
        "projectId": imp.project_id,
        "layer": imp.layer,
        "fileName": imp.file_name,
        "fileHash": imp.file_hash,
        "sheetTotal": imp.sheet_total,
        "sheetValid": imp.sheet_valid,
        "sheetError": imp.sheet_error,
        "mode": imp.mode,
        "committedBy": imp.committed_by,
        "committedAt": _iso(imp.committed_at),
        "sheets": report.get("sheets", []),
        "createdAt": _iso(imp.created_at),
    }


def _known_upstream_keys(db: Session, project_id: str, layer: str) -> set[str]:
    """Keys a Silver/Gold `sourceObjectKeys` entry may point at: Bronze onboarding objects (by key or
    bare source object name), plus Silver objects for a Gold upload."""
    keys: set[str] = set()
    for obj in repository.list_onboarding_objects(db, project_id):
        keys.update({obj.object_key, obj.source_object})
    if layer != "silver":
        keys.update(obj.object_key for obj in repository.list_silver_objects(db, project_id))
    return keys


def _upstream_key_aliases(db: Session, project_id: str, layer: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """DMT-12: bare names a Data Modular `Source_Object` uses (`raw_customer`, `customer`) -> the
    registry key they unambiguously point at (`crm.dbo.customer`), plus each ambiguous name with
    every key it could mean."""
    candidates: dict[str, set[str]] = {}
    targets = [(obj.object_key, obj.source_object) for obj in repository.list_onboarding_objects(db, project_id)]
    if layer != "silver":
        targets += [(obj.object_key, obj.curated_entity) for obj in repository.list_silver_objects(db, project_id)]
    for key, name in targets:
        bare = sttm_rules.to_snake(name.split(".")[-1])
        for alias in (bare, f"raw_{bare}"):
            candidates.setdefault(alias, set()).add(key)
    own_keys = {key for key, _ in targets}
    candidates = {alias: keys for alias, keys in candidates.items() if alias not in own_keys}
    unique = {alias: next(iter(keys)) for alias, keys in candidates.items() if len(keys) == 1}
    ambiguous = {alias: sorted(keys) for alias, keys in candidates.items() if len(keys) > 1}
    return unique, ambiguous


def _registry_tables(db: Session, project_id: str, sheets: list[dict[str, Any]], layer: str) -> dict[str, set[str]]:
    """Bare table name -> the tables in this project's registries that produce it: Bronze landed tables
    (an onboarding row's explicit target, else `raw.<source_system>_<object>` — the Build wizard's
    Source-system naming), Silver/Gold registry targets, plus the objects in this upload."""
    prefixes = project_layer_prefixes(db, project_id)
    raw = sttm_rules.resolve_zone_prefix("bronze", prefixes)
    tables: dict[str, set[str]] = {}

    def add(bare: str, table: str) -> None:
        tables.setdefault(sttm_rules.to_snake(bare), set()).add(table.lower())

    for obj in repository.list_onboarding_objects(db, project_id):
        bare = obj.source_object.split(".")[-1]
        if obj.target_object:
            add(obj.target_object, f"{obj.target_schema}.{obj.target_object}" if obj.target_schema else obj.target_object)
            add(bare, f"{obj.target_schema}.{obj.target_object}" if obj.target_schema else obj.target_object)
        else:
            add(bare, f"{raw}.{sttm_rules.to_snake(obj.source_system)}_{sttm_rules.to_snake(bare)}")
    for obj in [*repository.list_silver_objects(db, project_id), *repository.list_mart_objects(db, project_id)]:
        add(obj.curated_entity, obj.target_table)
    registry_layer = "silver" if layer == "silver" else "gold"
    for sheet in sheets:
        parsed = sheet.get("parsed") or {}
        if sheet.get("valid") and parsed.get("curatedEntity"):
            add(parsed["curatedEntity"], sttm_rules.layer_target_table_name(
                registry_layer, entity=parsed["curatedEntity"], domain=parsed.get("martDomain"), layer_prefixes=prefixes
            ))
    return tables


def _resolve_join_entities(db: Session, project_id: str, layer: str, sheets: list[dict[str, Any]]) -> None:
    """A join step's table (an STTM `Lookup_Table`, e.g. `curated.ICD10_CODE`) resolved against the tables
    the registries actually produce, the way DMT-12 resolves `Source_Object`: kept when it is one of them,
    rewritten when its bare name matches exactly one, warned when ambiguous or produced by nothing."""
    tables = _registry_tables(db, project_id, sheets, layer)
    produced = set().union(*tables.values()) if tables else set()
    for sheet in sheets:
        parsed = sheet.get("parsed")
        if not sheet.get("valid") or not parsed:
            continue
        for step in parsed.get("transformations") or []:
            entity = (step.get("joinEntity") or "").strip() if step.get("type") == "join" else ""
            if not entity or entity.lower() in produced:
                continue
            candidates = sorted(tables.get(sttm_rules.to_snake(entity.split(".")[-1]), set()))
            issue: dict[str, str]
            if len(candidates) == 1:
                step["joinEntity"] = candidates[0]
                issue = {"severity": "warning", "message": f'Join table "{entity}" resolved to {candidates[0]}, the registry object that produces it.'}
            elif candidates:
                issue = {"severity": "warning", "message": f'Join table "{entity}" matches several registry tables ({", ".join(candidates)}) — left as is; edit the join to pick one.'}
            else:
                issue = {"severity": "warning", "message": f'Join table "{entity}" isn\'t produced by any Bronze/Silver/Gold registry object in this project — onboard it, or make sure it exists on the target store.'}
            sheet.setdefault("issues", []).append({**issue, "field": "transformations"})


def create_sttm_import(
    db: Session, *, project_id: str, layer: str, file_name: str, file_hash: str, sheets: list[dict[str, Any]], actor: str | None
) -> models.SttmImport:
    registry_layer = "silver" if layer == "silver" else "mart"
    upstream = _known_upstream_keys(db, project_id, layer)
    aliases, ambiguous = _upstream_key_aliases(db, project_id, layer)
    _resolve_join_entities(db, project_id, layer, sheets)
    for sheet in sheets:
        parsed = sheet.get("parsed")
        if not sheet.get("valid") or not parsed:
            continue
        keys = parsed.get("sourceObjectKeys") or []
        resolved = [aliases.get(k, k) for k in keys]
        if resolved != keys:
            parsed["sourceObjectKeys"] = list(dict.fromkeys(resolved))
        # E2E-10 / §33.13 assumption 3: an unresolved upstream key is a soft warning at preview.
        keys = parsed.get("sourceObjectKeys") or []
        for key in (k for k in keys if k in ambiguous):
            sheet.setdefault("issues", []).append(
                {"severity": "warning", "field": "sourceObjectKeys", "message": f'Source object "{key}" matches several registry objects ({", ".join(ambiguous[key])}) — left unresolved; edit sourceObjectKeys to pick one.'}
            )
        dangling = [k for k in keys if k not in upstream and k not in ambiguous]
        if dangling:
            where = "Bronze onboarding registry" if layer == "silver" else "Bronze or Silver registries"
            sheet.setdefault("issues", []).append(
                {"severity": "warning", "field": "sourceObjectKeys", "message": f"Source object key(s) not found in this project's {where}: {', '.join(dangling)}."}
            )
        object_key = parsed.get("objectKey")
        if not object_key:
            continue
        existing = (
            repository.get_silver_object(db, project_id, object_key)
            if registry_layer == "silver"
            else repository.get_mart_object(db, project_id, object_key)
        )
        # Captured now, at upload/preview time — this is what a later commit's
        # `expectedVersions[objectKey]` compares against, so a concurrent edit
        # between preview and commit is actually detectable (§33.9/§33.11).
        sheet["existingVersion"] = existing.version if existing is not None else None
    sheet_valid = sum(1 for s in sheets if s.get("valid"))
    imp = models.SttmImport(
        id=new_id("sttm"),
        project_id=project_id,
        layer=layer,
        file_name=file_name,
        file_hash=file_hash,
        sheet_total=len(sheets),
        sheet_valid=sheet_valid,
        sheet_error=len(sheets) - sheet_valid,
        mode="upsert",
        report={"sheets": sheets},
    )
    db.add(imp)
    repository.record_audit(db, actor=actor or "system", action="sttm_import.uploaded", target=file_name, team_id=_project_team_id(db, project_id))
    db.flush()
    return imp


def commit_sttm_import(
    db: Session,
    imp: models.SttmImport,
    *,
    sheet_names: list[str] | None,
    confirm_overwrite: bool,
    actor: str | None,
    expected_versions: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Upserts every valid (optionally filtered) sheet in `imp.report` into
    the registry table for `imp.layer`. Additive/upsert-by-object_key only
    (§33.6 rule 8/§33.5's "never destructive-replace" convention) — a sheet
    whose object_key collides with an existing **manually-created** row
    (`provenance_source == "manual"`) is skipped unless `confirm_overwrite`.

    `expected_versions` (§33.9/§33.11, object_key -> version) is the client's
    concurrency guard: when a key is present, the commit is checked against
    *that* version — what the client actually saw at preview time — instead
    of the row's version read fresh just now (which, read this late, can
    never disagree with itself and so could never actually catch a
    concurrent edit; that was the pre-fix bug, tracked as STTM-13). A key
    absent from the map, or no map at all, falls back to the old
    always-succeeds-by-request-order behavior, for callers that never learned
    the object's pre-commit version.
    """
    layer = "silver" if imp.layer == "silver" else "mart"
    sheets = (imp.report or {}).get("sheets", [])
    # E2E-10 / §33.13 assumption 3: an upstream key that still doesn't resolve at commit is a hard
    # error for that sheet (preview only warns). Re-read now, so Bronze rows added since preview count.
    upstream = _known_upstream_keys(db, imp.project_id, imp.layer)
    committed: list[str] = []
    skipped: list[dict[str, str]] = []
    for sheet in sheets:
        if not sheet.get("valid") or not sheet.get("parsed"):
            continue
        name = sheet["sheetName"]
        if sheet_names is not None and name not in sheet_names:
            continue
        parsed = dict(sheet["parsed"])
        dangling = [k for k in parsed.get("sourceObjectKeys") or [] if k not in upstream]
        if dangling:
            where = "Bronze onboarding registry" if layer == "silver" else "Bronze or Silver registries"
            skipped.append({"sheetName": name, "reason": f"Source object key(s) not found in this project's {where}: {', '.join(dangling)}."})
            continue
        object_key = parsed.get("objectKey") or sttm_rules.to_snake(parsed.get("curatedEntity"))
        existing = (
            repository.get_silver_object(db, imp.project_id, object_key)
            if layer == "silver"
            else repository.get_mart_object(db, imp.project_id, object_key)
        )
        if existing is not None and existing.provenance_source == "manual" and not confirm_overwrite:
            skipped.append({"sheetName": name, "reason": "Would overwrite a manually-created object; confirm_overwrite not set."})
            continue
        parsed["provenanceSource"] = "upload"
        parsed["importId"] = imp.id
        try:
            if existing is not None:
                if expected_versions and object_key in expected_versions:
                    parsed["expectedVersion"] = expected_versions[object_key]
                else:
                    parsed["expectedVersion"] = existing.version
                updated = update_registry_object(db, layer, existing, parsed, actor=actor)
                # `_set_registry_fields`'s shared field map deliberately excludes
                # provenance/import tracking (a normal PUT must never let a
                # caller flip those) — set them explicitly on this bulk-import
                # path only.
                updated.provenance_source = "upload"
                updated.import_id = imp.id
                db.flush()
            else:
                parsed["objectKey"] = object_key
                create_registry_object(db, layer, imp.project_id, parsed, actor=actor)
            committed.append(name)
        except ApiError as exc:
            skipped.append({"sheetName": name, "reason": exc.message})
    imp.committed_by = actor
    imp.committed_at = datetime.now(timezone.utc)
    repository.record_audit(db, actor=actor or "system", action="sttm_import.committed", target=imp.file_name, team_id=_project_team_id(db, imp.project_id))
    db.flush()
    return {"committed": committed, "skipped": skipped}
