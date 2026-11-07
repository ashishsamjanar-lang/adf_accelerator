"""Job dispatch — one handler per job type.

Handlers receive an open :class:`~sqlalchemy.orm.Session` and the
:class:`~app.db.models.Job` row, do their work, mutate the metastore, and
return a JSON-serializable ``result`` dict. Raising :class:`JobError` (or any
exception) marks the job failed and rolls the pipeline into its ``*_FAILED``
state.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.crypto import decrypt_secret
from app.db import models
from app.db.base import new_id
from app.generator import build_generated_artifacts, build_manifest, build_spec
from app.generator.spec import SpecDraft, validate_draft
from app.gitops import ArtifactBlob, GitOpsError, get_adapter
from app.azure import AzureError, arm_validate, purview
from app.jobs import (
    JOB_CATALOG_CHANGE,
    JOB_CATALOG_REFRESH,
    JOB_GENERATE,
    JOB_GIT_PUSH,
    JOB_LIVE_CHECK,
    JOB_ONBOARDING_SYNC,
    JOB_PURVIEW_PUSH,
    JOB_VALIDATE,
)
from app.validator import run_validation

log = logging.getLogger("worker.jobs")


class JobError(RuntimeError):
    """A job failed in a way worth surfacing to the user."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(
    db: Session, *, actor: str, action: str, target: str, details: str, team_id: str | None = None
) -> None:
    db.add(
        models.AuditEntry(
            id=new_id("aud"), actor=actor or "worker", action=action, target=target, details=details, team_id=team_id
        )
    )


def _resolve_environment(db: Session, draft: SpecDraft) -> models.Environment | None:
    """Look up the ADF instance a draft targets (multi-instance support, gap A-1/A-2).

    ``draft.target_environment_name`` is optional — no instance selected is
    the default, unchanged behavior (``build_generated_artifacts`` falls back
    to the ``<FACTORY_NAME>``/``<ENVIRONMENT_NAME>`` placeholders). A name
    that no longer resolves (deleted after the draft was saved) degrades the
    same way, rather than failing the job.
    """
    name = (draft.target_environment_name or "").strip()
    if not name:
        return None
    return db.scalars(select(models.Environment).where(models.Environment.name == name)).first()


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def _handle_generate(db: Session, job: models.Job) -> dict[str, Any]:
    pipeline = db.get(models.PipelineDefinition, job.pipeline_id)
    if pipeline is None:
        raise JobError(f"Pipeline '{job.pipeline_id}' no longer exists.")

    draft = SpecDraft.from_dict(job.payload.get("draft") or {})
    version = job.payload.get("version") or "v1.0.0"
    template_id = job.payload.get("templateId") or draft.template_id

    issues = [i for i in validate_draft(draft) if i.severity == "error"]
    if issues:
        pipeline.state = "GENERATION_FAILED"
        return {"state": "failed", "issues": [i.to_dict() for i in issues]}

    spec = build_spec(draft)
    resolved_environment = _resolve_environment(db, draft)
    # §25 opt-in Standards: a project's custom zone prefix per medallion layer
    # (Administration > Standards "Layer prefixes") — omitted/None keeps the
    # raw/curated/mart default. See `_handle_validate` below for the parity note.
    project_settings = pipeline.project.settings if pipeline.project else None
    naming_convention = project_settings.document.get("namingConvention") if project_settings else None
    layer_prefixes = (naming_convention or {}).get("layerPrefixes")
    artifacts = build_generated_artifacts(draft, resolved_environment, layer_prefixes)
    manifest = build_manifest(pipeline.name, version, artifacts)

    # Regeneration replaces the artifact set for this version.
    db.execute(
        delete(models.Artifact).where(
            models.Artifact.pipeline_id == pipeline.id, models.Artifact.version == version
        )
    )
    for artifact in artifacts:
        payload = artifact.to_dict()
        db.add(
            models.Artifact(
                id=new_id("art"),
                pipeline_id=pipeline.id,
                pipeline_name=pipeline.name,
                file_name=payload["fileName"],
                file_type=payload["fileType"],
                artifact_type=payload["artifactType"],
                layer=payload["layer"],
                version=version,
                state="generated",
                bytes=payload["bytes"],
                content=payload["content"],
            )
        )
    manifest_str = json.dumps(manifest, indent=2) + "\n"
    db.add(
        models.Artifact(
            id=new_id("art"),
            pipeline_id=pipeline.id,
            pipeline_name=pipeline.name,
            file_name="manifest.json",
            file_type="json",
            artifact_type="parameter",
            layer="metadata",
            version=version,
            state="generated",
            bytes=len(manifest_str.encode("utf-8")),
            content=manifest_str,
        )
    )

    template = db.get(models.PipelineTemplate, template_id)
    # Same overwrite-in-place semantics as the artifacts above: regenerating an
    # unfinished version (still iterating on a draft before anyone reads it) must
    # not crash on `pipeline_versions`' (pipeline_id, version) unique constraint.
    # Once a version is actually pushed/released, callers always regenerate under
    # a new version string, so this never touches a row anyone has read.
    db.execute(
        delete(models.PipelineVersion).where(
            models.PipelineVersion.pipeline_id == pipeline.id, models.PipelineVersion.version == version
        )
    )
    db.add(
        models.PipelineVersion(
            id=new_id("plv"),
            pipeline_id=pipeline.id,
            version=version,
            template_version=(template.version if template else "2.0.0"),
            spec=spec,
            manifest=manifest,
            created_by=job.created_by,
            target_environment_name=resolved_environment.name if resolved_environment else None,
        )
    )
    pipeline.spec = spec
    pipeline.latest_artifact_version = version
    pipeline.state = "GENERATED"
    _audit(
        db,
        actor=job.created_by or "worker",
        action="artifact.generated",
        target=f"{pipeline.name} {version}",
        details=f"{len(artifacts)} artifacts + manifest from template {template_id}.",
        team_id=pipeline.team_id,
    )
    return {"state": "succeeded", "version": version, "artifactCount": len(artifacts), "manifest": manifest}


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _handle_validate(db: Session, job: models.Job) -> dict[str, Any]:
    pipeline = db.get(models.PipelineDefinition, job.pipeline_id)
    if pipeline is None:
        raise JobError(f"Pipeline '{job.pipeline_id}' no longer exists.")

    draft = SpecDraft.from_dict(job.payload.get("draft") or {})
    spec = build_spec(draft)
    # §25 opt-in naming standard, loaded from the project's settings document
    # (frontend Standards / Project Settings write the same `namingConvention`
    # field this reads — see backend/app/standards.py's parity note). Loaded
    # before `build_generated_artifacts` so its own opt-in layer-prefix
    # override (`layerPrefixes`) is baked into the artifacts being validated,
    # not just the separate naming-rule check below.
    project_settings = pipeline.project.settings if pipeline.project else None
    naming_convention = (project_settings.document.get("namingConvention") if project_settings else None)
    layer_prefixes = (naming_convention or {}).get("layerPrefixes")
    artifacts = build_generated_artifacts(draft, _resolve_environment(db, draft), layer_prefixes)
    report = run_validation(spec, artifacts, naming_convention=naming_convention)

    if report.ok:
        pipeline.state = "VALIDATED"
        version = pipeline.latest_artifact_version
        if version:
            for artifact in db.scalars(
                select(models.Artifact).where(
                    models.Artifact.pipeline_id == pipeline.id, models.Artifact.version == version
                )
            ):
                artifact.state = "validated"
    else:
        pipeline.state = "VALIDATION_FAILED"

    _audit(
        db,
        actor=job.created_by or "worker",
        action="pipeline.validated",
        target=f"{pipeline.name} {pipeline.latest_artifact_version or ''}".strip(),
        details=f"{report.state}: {len(report.issues)} issue(s).",
        team_id=pipeline.team_id,
    )
    return {"state": report.state, "issues": [i.to_dict() for i in report.issues]}


# ---------------------------------------------------------------------------
# git_push
# ---------------------------------------------------------------------------


# ADF's canonical Git-managed folder per artifact type (AGENTS.md §24.11) — a
# real ADF Studio Git-integrated repo groups resources this way, and Studio's
# own Git mode won't recognize a factory's resources otherwise. Previously
# every artifact was pushed flat under ``artifact_root`` regardless of type,
# which produced a repo ADF's Git integration would not actually import
# cleanly (IMPLEMENTATION_PLAN.md gap A-2/folder-layout). Types not listed
# here (``parameter`` — the parameters file, control-table SQL, manifest.json)
# are not ADF Git resources at all, so they stay at the artifact root.
_ADF_FOLDER_BY_ARTIFACT_TYPE = {
    "credential": "credential",
    "linked_service": "linkedService",
    "dataset": "dataset",
    "pipeline": "pipeline",
    "trigger": "trigger",
    "factory": "factory",
}


def _artifact_blobs(db: Session, push: models.GitPushRequest, artifact_root: str) -> list[ArtifactBlob]:
    rows = db.scalars(
        select(models.Artifact).where(models.Artifact.id.in_(push.artifact_ids))
    ).all()
    root = artifact_root.strip("/")
    prefix = f"{root}/" if root else ""
    blobs: list[ArtifactBlob] = []
    for row in rows:
        folder = _ADF_FOLDER_BY_ARTIFACT_TYPE.get(row.artifact_type)
        path = f"{prefix}{folder}/{row.file_name}" if folder else f"{prefix}{row.file_name}"
        blobs.append(ArtifactBlob(path=path, content=row.content))
    return blobs


def _take_git_token(job: models.Job) -> str | None:
    """Per-push token: the API stores it encrypted (app/crypto.py, gap #17), so
    even if this scrub's commit is rolled back by a later exception, whatever
    lingers in job.payload is ciphertext, never a usable credential. Decrypt
    only the local variable used for this call."""
    encrypted_token = job.payload.get("gitToken")
    if encrypted_token is None:
        return None
    job.payload = {**job.payload, "gitToken": None}
    flag_modified(job, "payload")
    try:
        return decrypt_secret(encrypted_token)
    except ValueError as exc:
        raise JobError(str(exc)) from exc


def _handle_git_push(db: Session, job: models.Job) -> dict[str, Any]:
    push = db.get(models.GitPushRequest, job.payload.get("pushRequestId"))
    if push is None:
        raise JobError("The Git push request no longer exists.")
    pipeline = db.get(models.PipelineDefinition, push.pipeline_id)
    direct_token = _take_git_token(job)
    adapter = get_adapter(
        push.provider,
        push.repository_url,
        credential_vault=job.payload.get("credentialVault"),
        direct_token=direct_token,
    )
    pr_only = bool(job.payload.get("prOnly"))
    pr_title = job.payload.get("prTitle") or f"feat: {pipeline.name if pipeline else push.pipeline_id}"
    pr_body = job.payload.get("prBody") or ""
    reviewers = job.payload.get("reviewers") or []

    try:
        if not pr_only:
            blobs = _artifact_blobs(db, push, job.payload.get("artifactRoot") or "adf/artifacts/")
            result = adapter.push_artifacts(
                feature_branch=push.feature_branch,
                base_branch=push.base_branch,
                files=blobs,
                commit_message=push.commit_message,
            )
            push.commit_sha = result.commit_sha
            push.status = "pushed"
            push.simulated = result.simulated
            for row in db.scalars(select(models.Artifact).where(models.Artifact.id.in_(push.artifact_ids))):
                row.state = "committed"
                row.repository_path = next(
                    (b.path for b in blobs if b.path.endswith(row.file_name)), row.repository_path
                )
            if pipeline:
                pipeline.state = "PUSHED"

        pr = adapter.open_pull_request(
            source_branch=push.feature_branch,
            target_branch=push.base_branch,
            title=pr_title,
            body=pr_body,
            reviewers=reviewers,
        )
        existing_pr = push.pull_request
        if existing_pr is None:
            db.add(
                models.GitPullRequest(
                    id=new_id("pr"),
                    push_request_id=push.id,
                    number=pr.number,
                    url=pr.url,
                    title=pr_title,
                    source_branch=pr.source_branch,
                    target_branch=pr.target_branch,
                    status="open",
                    simulated=pr.simulated,
                )
            )
        else:
            existing_pr.number = pr.number
            existing_pr.url = pr.url
            existing_pr.simulated = pr.simulated
        push.status = "pr_created"
        if pipeline:
            pipeline.state = "PR_CREATED"
        _audit(
            db,
            actor=job.created_by or "worker",
            action="pr.created",
            target=f"{pipeline.name if pipeline else push.pipeline_id}",
            details=f"PR {pr.number} {push.feature_branch} -> {push.base_branch}"
            + (" (simulated)" if pr.simulated else ""),
            team_id=pipeline.team_id if pipeline else None,
        )
        return {
            "state": "succeeded",
            "commitSha": push.commit_sha,
            "simulated": push.simulated,
            "pullRequest": {"number": pr.number, "url": pr.url, "simulated": pr.simulated},
        }
    except GitOpsError as exc:
        # Domain failure: record it and return (do NOT raise — a raise would roll
        # back this state and trigger a blind retry). The loop marks the job failed.
        push.status = "failed"
        push.error = str(exc)
        if pipeline:
            pipeline.state = "GIT_PUSH_FAILED" if not pr_only else "PR_CREATION_FAILED"
        _audit(
            db,
            actor=job.created_by or "worker",
            action="git.push_failed",
            target=pipeline.name if pipeline else push.pipeline_id,
            details=str(exc),
            team_id=pipeline.team_id if pipeline else None,
        )
        return {"state": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# onboarding sync-to-Git (§26.5)
# ---------------------------------------------------------------------------


def _project_team(db: Session, project_id: str) -> str | None:
    project = db.get(models.Project, project_id)
    return project.team_id if project else None


def _handle_onboarding_sync(db: Session, job: models.Job) -> dict[str, Any]:
    """Push the registry files the API validated (control-table SQL,
    ``objects.csv``, lineage) to the feature branch, then open the PR."""
    sync = db.get(models.OnboardingSync, job.payload.get("syncId"))
    if sync is None:
        raise JobError("The onboarding sync request no longer exists.")
    direct_token = _take_git_token(job)
    adapter = get_adapter(
        sync.provider,
        sync.repository_url,
        credential_vault=job.payload.get("credentialVault"),
        direct_token=direct_token,
    )
    files = [ArtifactBlob(path=f["path"], content=f["content"]) for f in job.payload.get("files") or []]
    team_id = _project_team(db, sync.project_id)
    try:
        pushed = adapter.push_artifacts(
            feature_branch=sync.feature_branch,
            base_branch=sync.base_branch,
            files=files,
            commit_message=sync.commit_message,
        )
        sync.commit_sha = pushed.commit_sha
        sync.simulated = pushed.simulated
        sync.status = "pushed"
        pr = adapter.open_pull_request(
            source_branch=sync.feature_branch,
            target_branch=sync.base_branch,
            title=job.payload.get("prTitle") or "chore(onboarding): sync registry",
            body=job.payload.get("prBody") or "",
            reviewers=job.payload.get("reviewers") or [],
        )
        sync.pr_number, sync.pr_url = pr.number, pr.url
        sync.simulated = sync.simulated or pr.simulated
        sync.status = "pr_created"
        sync.error = None
        _audit(
            db,
            actor=job.created_by or "worker",
            action="onboarding.synced",
            target=sync.project_id,
            details=f"{len(files)} files, PR {pr.number} {sync.feature_branch} -> {sync.base_branch}"
            + (" (simulated)" if sync.simulated else ""),
            team_id=team_id,
        )
        return {
            "state": "succeeded",
            "commitSha": sync.commit_sha,
            "simulated": sync.simulated,
            "pullRequest": {"number": pr.number, "url": pr.url},
        }
    except GitOpsError as exc:
        sync.status = "failed"
        sync.error = str(exc)
        _audit(
            db,
            actor=job.created_by or "worker",
            action="onboarding.sync_failed",
            target=sync.project_id,
            details=str(exc),
            team_id=team_id,
        )
        return {"state": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# §29.1 layer 6 live check + Purview lineage push (project_integrations)
# ---------------------------------------------------------------------------


def _enabled_integration(db: Session, project_id: str, kind: str) -> models.ProjectIntegration | None:
    row = db.get(models.ProjectIntegration, (project_id, kind))
    return row if row is not None and row.enabled else None


def _handle_live_check(db: Session, job: models.Job) -> dict[str, Any]:
    """ARM validate-mode only — never deploys. Warn-only: the pipeline's
    lifecycle state is not touched whatever the outcome."""
    project_id = job.payload.get("projectId") or ""
    integration = _enabled_integration(db, project_id, "live_validation")
    if integration is None:
        return {"state": "failed", "error": "Live validation is not enabled for this project."}
    pipeline = db.get(models.PipelineDefinition, job.pipeline_id)
    if pipeline is None:
        raise JobError("The pipeline no longer exists.")
    query = select(models.Artifact).where(models.Artifact.pipeline_id == pipeline.id)
    if pipeline.latest_artifact_version:
        query = query.where(models.Artifact.version == pipeline.latest_artifact_version)
    artifacts = [(a.artifact_type, a.content) for a in db.scalars(query)]
    try:
        result = arm_validate.run_live_check(
            integration.config, artifacts, deployment_name=f"adfacc-livecheck-{job.id}"[:64]
        )
    except AzureError as exc:
        return {"state": "failed", "error": str(exc)}
    outcome = "passed" if result["passed"] else f"{len(result['issues'])} finding(s)"
    _audit(
        db,
        actor=job.created_by or "worker",
        action="validation.live_check",
        target=pipeline.name,
        details=f"{outcome} ({result['resourceCount']} resources, sandbox {result['sandbox']['resourceGroup']})",
        team_id=pipeline.team_id,
    )
    return {"state": "succeeded", **result}


def _handle_purview_push(db: Session, job: models.Job) -> dict[str, Any]:
    project_id = job.payload.get("projectId") or ""
    integration = _enabled_integration(db, project_id, "purview")
    if integration is None:
        return {"state": "failed", "error": "The Purview integration is not enabled for this project."}
    lineage = job.payload.get("lineage")
    if not isinstance(lineage, dict):
        raise JobError("The Purview push carries no lineage document.")
    try:
        result = purview.push_lineage(integration.config, lineage, project_id=project_id)
    except AzureError as exc:
        return {"state": "failed", "error": str(exc)}
    _audit(
        db,
        actor=job.created_by or "worker",
        action="lineage.purview_pushed",
        target=project_id,
        details=f"{result['processes']} processes, {result['datasets']} datasets -> {result['account']}",
        team_id=_project_team(db, project_id),
    )
    return {"state": "succeeded", **result}


# ---------------------------------------------------------------------------
# §24.8 catalog refresh + §24.9/§24.10 catalog change PRs (app/jobs/catalog.py)
# ---------------------------------------------------------------------------


def _handle_catalog_refresh(db: Session, job: models.Job) -> dict[str, Any]:
    from app.jobs.catalog import handle_catalog_refresh

    return handle_catalog_refresh(db, job, take_token=_take_git_token, audit=_audit)


def _handle_catalog_change(db: Session, job: models.Job) -> dict[str, Any]:
    from app.jobs.catalog import handle_catalog_change

    return handle_catalog_change(db, job, take_token=_take_git_token, audit=_audit)


_HANDLERS = {
    JOB_GENERATE: _handle_generate,
    JOB_VALIDATE: _handle_validate,
    JOB_GIT_PUSH: _handle_git_push,
    JOB_ONBOARDING_SYNC: _handle_onboarding_sync,
    JOB_LIVE_CHECK: _handle_live_check,
    JOB_PURVIEW_PUSH: _handle_purview_push,
    JOB_CATALOG_REFRESH: _handle_catalog_refresh,
    JOB_CATALOG_CHANGE: _handle_catalog_change,
}


def dispatch(db: Session, job: models.Job) -> dict[str, Any]:
    handler = _HANDLERS.get(job.type)
    if handler is None:
        raise JobError(f"Unknown job type '{job.type}'.")
    return handler(db, job)
