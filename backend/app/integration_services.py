"""§26.5 Onboarding sync-to-Git, §29.1 layer-6 live check, and the Purview
lineage push — the API half. Like Git push, the API only validates, records,
and enqueues; the worker makes every provider/Azure call.

The registry files (control-table SQL, ``objects.csv``, lineage) are built by
the Onboarding page's existing builders (``frontend/lib/adf/onboarding.ts``) —
there is no server-side port of them. The API pins their paths to a fixed
allowlist, caps their size, and refuses the sync when the client built them
from a registry that no longer matches the server's (``objectKeys`` check), so
the stored ``snapshot`` and the pushed files describe the same rows.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, repository
from app.crypto import encrypt_secret
from app.db.base import new_id
from app.errors import ApiError
from app.services import _iso, enqueue_job, onboarding_object_to_dict

JOB_ONBOARDING_SYNC = "onboarding_sync"
JOB_LIVE_CHECK = "live_check"
JOB_PURVIEW_PUSH = "purview_push"
INTEGRATION_JOB_TYPES = (JOB_ONBOARDING_SYNC, JOB_LIVE_CHECK, JOB_PURVIEW_PUSH)

# ---------------------------------------------------------------------------
# Onboarding sync-to-Git (§26.5)
# ---------------------------------------------------------------------------

SYNC_FILES = ("adf_control_table.sql", "objects.csv", "registry-lineage.json")
SYNC_REQUIRED_FILES = ("adf_control_table.sql", "objects.csv")
MAX_SYNC_FILE_BYTES = 5 * 1024 * 1024
# Row fields that change on every write without the object itself changing.
_VOLATILE = {"project_id", "version", "created_by", "updated_by", "created_at", "updated_at"}


def _sync_row(obj: models.OnboardingObject) -> dict[str, Any]:
    return {k: v for k, v in onboarding_object_to_dict(obj).items() if k not in _VOLATILE}


def _git_defaults(db: Session, project_id: str) -> dict[str, Any]:
    settings = repository.get_project_settings(db, project_id)
    return dict(((settings.document if settings else {}) or {}).get("gitDefaults") or {})


def request_onboarding_sync(
    db: Session,
    project: models.Project,
    *,
    files: list[dict[str, str]],
    object_keys: list[str],
    provider: str | None,
    repository_url: str | None,
    feature_branch: str,
    base_branch: str | None,
    commit_message: str | None,
    pr_title: str | None,
    pr_body: str | None,
    reviewers: list[str],
    git_token: str | None,
    actor: str | None,
) -> models.OnboardingSync:
    git = _git_defaults(db, project.id)
    provider = provider or git.get("provider") or "github"
    repository_url = (repository_url or git.get("repositoryUrl") or "").strip()
    base_branch = (base_branch or git.get("baseBranch") or "dev").strip()
    feature_branch = feature_branch.strip()
    # Same always-on safety rules as a pipeline push (§17.7–9 / §23.6).
    if not repository_url:
        raise ApiError(422, "git_not_configured", "A Git repository must be configured before syncing.")
    if not feature_branch:
        raise ApiError(422, "feature_branch_required", "A feature branch is required.")
    if feature_branch == base_branch:
        raise ApiError(
            422, "protected_branch", f"The feature branch must not equal the protected base branch '{base_branch}'."
        )

    rows = repository.list_onboarding_objects(db, project.id)
    if not rows:
        raise ApiError(422, "no_objects", "Add at least one onboarding object before syncing.")
    if sorted(set(object_keys)) != sorted(r.object_key for r in rows):
        raise ApiError(
            409, "registry_changed", "The registry changed since these files were built — reload and sync again."
        )

    by_name: dict[str, str] = {}
    for f in files:
        name, content = (f.get("name") or "").strip(), f.get("content") or ""
        if name not in SYNC_FILES:
            raise ApiError(422, "sync_file_not_allowed", f"'{name}' is not a registry sync file ({', '.join(SYNC_FILES)}).")
        if len(content.encode("utf-8")) > MAX_SYNC_FILE_BYTES:
            raise ApiError(422, "sync_file_too_large", f"'{name}' exceeds {MAX_SYNC_FILE_BYTES // (1024 * 1024)} MB.")
        by_name[name] = content
    missing = [n for n in SYNC_REQUIRED_FILES if not by_name.get(n, "").strip()]
    if missing:
        raise ApiError(422, "sync_file_missing", f"Missing required sync file(s): {', '.join(missing)}.")

    root = (git.get("artifactRoot") or "adf/artifacts/").strip("/")
    prefix = f"{root}/metadata/" if root else "metadata/"
    blobs = [{"path": f"{prefix}{name}", "content": by_name[name]} for name in SYNC_FILES if name in by_name]

    sync = models.OnboardingSync(
        id=new_id("osync"),
        project_id=project.id,
        provider=provider,
        repository_url=repository_url,
        feature_branch=feature_branch,
        base_branch=base_branch,
        commit_message=commit_message or f"chore(onboarding): sync {len(rows)} registry objects",
        file_paths=[b["path"] for b in blobs],
        snapshot={r.object_key: _sync_row(r) for r in rows},
        status="pending",
        created_by=actor,
    )
    db.add(sync)
    enqueue_job(
        db,
        job_type=JOB_ONBOARDING_SYNC,
        pipeline_id=None,
        payload={
            "syncId": sync.id,
            "projectId": project.id,
            "files": blobs,
            "prTitle": pr_title or f"chore(onboarding): sync {project.name} registry",
            "prBody": pr_body or git.get("prBodyTemplate") or "",
            "reviewers": reviewers or git.get("reviewers") or [],
            "credentialVault": git.get("credentialVault"),
            "gitToken": encrypt_secret(git_token) if git_token else None,
        },
        actor=actor,
    )
    repository.record_audit(
        db,
        actor=actor or "system",
        action="onboarding.sync_requested",
        target=project.id,
        details=f"{len(rows)} objects, {len(blobs)} files -> {feature_branch}.",
        team_id=project.team_id,
    )
    return sync


def onboarding_sync_to_dict(sync: models.OnboardingSync) -> dict[str, Any]:
    return {
        "id": sync.id,
        "projectId": sync.project_id,
        "provider": sync.provider,
        "repositoryUrl": sync.repository_url,
        "featureBranch": sync.feature_branch,
        "baseBranch": sync.base_branch,
        "commitMessage": sync.commit_message,
        "filePaths": list(sync.file_paths or []),
        "objectCount": len(sync.snapshot or {}),
        "status": sync.status,
        "commitSha": sync.commit_sha,
        "prNumber": sync.pr_number,
        "prUrl": sync.pr_url,
        "simulated": sync.simulated,
        "error": sync.error,
        "createdBy": sync.created_by,
        "createdAt": _iso(sync.created_at),
    }


def list_onboarding_syncs(db: Session, project_id: str, *, limit: int = 20) -> list[models.OnboardingSync]:
    query = (
        select(models.OnboardingSync)
        .where(models.OnboardingSync.project_id == project_id)
        .order_by(models.OnboardingSync.created_at.desc(), models.OnboardingSync.id.desc())
        .limit(limit)
    )
    return list(db.scalars(query))


def onboarding_sync_diff(db: Session, project_id: str) -> dict[str, Any]:
    """Per-row diff of the current registry vs. the last *successful* sync's
    snapshot (§26.5 "shows a per-row diff vs. the last synced commit")."""
    base = db.scalars(
        select(models.OnboardingSync)
        .where(
            models.OnboardingSync.project_id == project_id,
            models.OnboardingSync.status.in_(("pushed", "pr_created")),
        )
        .order_by(models.OnboardingSync.created_at.desc(), models.OnboardingSync.id.desc())
        .limit(1)
    ).first()
    current = {r.object_key: _sync_row(r) for r in repository.list_onboarding_objects(db, project_id)}
    previous: dict[str, dict[str, Any]] = dict(base.snapshot or {}) if base else {}
    changed = []
    for key in sorted(set(current) & set(previous)):
        fields = sorted(f for f in set(current[key]) | set(previous[key]) if current[key].get(f) != previous[key].get(f))
        if fields:
            changed.append({"objectKey": key, "fields": fields})
    return {
        "baseSyncId": base.id if base else None,
        "baseCommitSha": base.commit_sha if base else None,
        "basePrUrl": base.pr_url if base else None,
        "added": sorted(set(current) - set(previous)),
        "removed": sorted(set(previous) - set(current)),
        "changed": changed,
        "unchanged": len(set(current) & set(previous)) - len(changed),
    }


# ---------------------------------------------------------------------------
# Project integrations: live_validation (§29.1 layer 6) + purview
# ---------------------------------------------------------------------------

_AUTH_FIELDS = ("tenantId", "clientId", "clientSecretRef")
INTEGRATION_FIELDS: dict[str, tuple[str, ...]] = {
    "live_validation": (*_AUTH_FIELDS, "subscriptionId", "resourceGroup", "location", "factoryName"),
    "purview": (*_AUTH_FIELDS, "accountName"),
}
INTEGRATION_REQUIRED: dict[str, tuple[str, ...]] = {
    "live_validation": ("subscriptionId", "resourceGroup", "location"),
    "purview": ("accountName",),
}
# A Key Vault secret name (or local-dev env var name). A pasted client secret
# almost always contains `~` or `.`, so this also catches the rule-6 mistake of
# storing the value instead of the reference.
_SECRET_REF_RE = re.compile(r"^[A-Za-z0-9_-]{1,127}$")


def _require_kind(kind: str) -> None:
    if kind not in INTEGRATION_FIELDS:
        raise ApiError(404, "integration_unknown", f"Unknown integration '{kind}' ({', '.join(INTEGRATION_FIELDS)}).")


def integration_to_dict(project_id: str, kind: str, row: models.ProjectIntegration | None) -> dict[str, Any]:
    config = dict(row.config or {}) if row else {}
    return {
        "projectId": project_id,
        "kind": kind,
        "enabled": bool(row.enabled) if row else False,
        "config": {field: config.get(field, "") for field in INTEGRATION_FIELDS[kind]},
        "updatedBy": row.updated_by if row else None,
        "updatedAt": _iso(row.updated_at) if row else None,
    }


def get_integration(db: Session, project_id: str, kind: str) -> models.ProjectIntegration | None:
    _require_kind(kind)
    return db.get(models.ProjectIntegration, (project_id, kind))


def put_integration(
    db: Session, project: models.Project, kind: str, *, enabled: bool, config: dict[str, Any], actor: str | None
) -> models.ProjectIntegration:
    _require_kind(kind)
    unknown = sorted(set(config) - set(INTEGRATION_FIELDS[kind]))
    if unknown:
        raise ApiError(422, "integration_field_unknown", f"Unknown field(s) for '{kind}': {', '.join(unknown)}.")
    clean = {field: str(config.get(field) or "").strip() for field in INTEGRATION_FIELDS[kind]}
    ref = clean["clientSecretRef"]
    if ref and not _SECRET_REF_RE.match(ref):
        raise ApiError(
            422,
            "secret_reference_invalid",
            "clientSecretRef must be a Key Vault secret name (letters, digits, '-', '_') — never the secret value.",
        )
    if ref and not (clean["tenantId"] and clean["clientId"]):
        raise ApiError(422, "integration_auth_incomplete", "tenantId and clientId are required with a clientSecretRef.")
    if enabled:
        missing = [f for f in INTEGRATION_REQUIRED[kind] if not clean[f]]
        if missing:
            raise ApiError(422, "integration_incomplete", f"Required to enable '{kind}': {', '.join(missing)}.")
    row = db.get(models.ProjectIntegration, (project.id, kind))
    if row is None:
        row = models.ProjectIntegration(project_id=project.id, kind=kind)
        db.add(row)
    row.enabled = enabled
    row.config = clean
    row.updated_by = actor
    repository.record_audit(
        db,
        actor=actor or "system",
        action="integration.updated",
        target=f"{project.id}:{kind}",
        details="enabled" if enabled else "disabled",
        team_id=project.team_id,
    )
    return row


def _require_enabled(db: Session, project_id: str, kind: str, label: str) -> None:
    row = db.get(models.ProjectIntegration, (project_id, kind))
    if row is None or not row.enabled:
        raise ApiError(409, "integration_not_enabled", f"{label} is not enabled for this project (Project Settings).")


def request_live_check(db: Session, project: models.Project, pipeline_id: str, *, actor: str | None) -> models.Job:
    _require_enabled(db, project.id, "live_validation", "Live ARM validation")
    pipeline = repository.get_pipeline(db, pipeline_id)
    if pipeline is None or pipeline.project_id != project.id:
        raise ApiError(404, "pipeline_not_found", f"Pipeline '{pipeline_id}' does not exist in this project.")
    if not pipeline.latest_artifact_version:
        raise ApiError(422, "no_artifacts", "Generate the pipeline before running a live check.")
    job = enqueue_job(
        db, job_type=JOB_LIVE_CHECK, pipeline_id=pipeline.id, payload={"projectId": project.id}, actor=actor
    )
    repository.record_audit(
        db, actor=actor or "system", action="validation.live_check_requested", target=pipeline.name,
        details=f"{pipeline.latest_artifact_version}", team_id=project.team_id,
    )
    return job


MAX_LINEAGE_BYTES = 5 * 1024 * 1024


def request_purview_push(
    db: Session, project: models.Project, lineage: dict[str, Any], *, actor: str | None
) -> models.Job:
    _require_enabled(db, project.id, "purview", "The Purview integration")
    jobs = lineage.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ApiError(422, "lineage_empty", "The lineage document has no jobs to push.")
    if len(json.dumps(lineage).encode("utf-8")) > MAX_LINEAGE_BYTES:
        raise ApiError(422, "lineage_too_large", "The lineage document exceeds 5 MB.")
    job = enqueue_job(
        db, job_type=JOB_PURVIEW_PUSH, pipeline_id=None, payload={"projectId": project.id, "lineage": lineage},
        actor=actor,
    )
    repository.record_audit(
        db, actor=actor or "system", action="lineage.purview_requested", target=project.id,
        details=f"{len(jobs)} lineage jobs", team_id=project.team_id,
    )
    return job


def integration_job_to_dict(job: models.Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status,
        "result": job.result,
        "error": job.error,
        "createdAt": _iso(job.created_at),
        "finishedAt": _iso(job.finished_at),
    }
