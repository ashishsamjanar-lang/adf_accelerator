"""§24.8 Git-backed catalog, §24.9/§24.10 round-trip edit / rename / delete, and
§26.6 onboarding import + drift — the API half.

The worker reads and writes the repo (``catalog_refresh`` / ``catalog_change``
jobs); everything here works off the ``git_catalog_entries`` index those jobs
maintain, so browsing, "used by", blast radius, and drift never make a provider
call from the API process.

**Ownership (§24.9)** is resolved from the metastore rather than an in-file
stamp: a repo file whose name matches an artifact this app generated for the
project is *tool-owned* — ``in_sync`` when its content still equals the
generated content (JSON-normalized), ``drifted`` when someone edited it in Git /
ADF Studio since. Anything else is ``foreign`` and edits in raw-JSON mode.
"""
from __future__ import annotations

import csv
import io
import json
import posixpath
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, repository
from app.crypto import encrypt_secret
from app.db.base import new_id
from app.errors import ApiError
from app.integration_services import _git_defaults, _sync_row
from app.services import _ONBOARDING_FIELD_MAP, _iso, enqueue_job

JOB_CATALOG_REFRESH = "catalog_refresh"
JOB_CATALOG_CHANGE = "catalog_change"
CATALOG_JOB_TYPES = (JOB_CATALOG_REFRESH, JOB_CATALOG_CHANGE)

# Vendored from worker/app/validator/rules.py (ARTIFACT_NAME_RE) — the rule a
# generated artifact name is validated against; a rename must meet it too.
ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,127}$")
MAX_CHANGE_BYTES = 2 * 1024 * 1024
REFERENCE_TYPES = {
    "LinkedServiceReference": "linked_service",
    "DatasetReference": "dataset",
    "PipelineReference": "pipeline",
    "CredentialReference": "credential",
    "DataFlowReference": "data_flow",
    "IntegrationRuntimeReference": "integration_runtime",
}
# Artifact types a rename/delete/update may target — ADF resources keyed by file name.
EDITABLE_TYPES = {"pipeline", "dataset", "linked_service", "trigger", "credential", "data_flow", "integration_runtime"}


def _project_git(db: Session, project_id: str) -> tuple[dict[str, Any], str]:
    git = _git_defaults(db, project_id)
    return git, (git.get("baseBranch") or "dev").strip()


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def request_catalog_refresh(
    db: Session, project: models.Project, *, branch: str | None, git_token: str | None, actor: str | None
) -> models.Job:
    git, base = _project_git(db, project.id)
    repository_url = (git.get("repositoryUrl") or "").strip()
    if not repository_url:
        raise ApiError(422, "git_not_configured", "Configure the project's Git repository (Project Settings) first.")
    branch = (branch or base).strip()
    job = enqueue_job(
        db,
        job_type=JOB_CATALOG_REFRESH,
        pipeline_id=None,
        payload={
            "projectId": project.id,
            "branch": branch,
            "provider": git.get("provider") or "github",
            "repositoryUrl": repository_url,
            "artifactRoot": git.get("artifactRoot") or "adf/artifacts/",
            "credentialVault": git.get("credentialVault"),
            "gitToken": encrypt_secret(git_token) if git_token else None,
        },
        actor=actor,
    )
    repository.record_audit(
        db, actor=actor or "system", action="catalog.refresh_requested", target=project.id,
        details=branch, team_id=project.team_id,
    )
    return job


# ---------------------------------------------------------------------------
# index reads
# ---------------------------------------------------------------------------


def _entries(db: Session, project_id: str, branch: str) -> list[models.GitCatalogEntry]:
    return list(
        db.scalars(
            select(models.GitCatalogEntry)
            .where(models.GitCatalogEntry.project_id == project_id, models.GitCatalogEntry.branch == branch)
            .order_by(models.GitCatalogEntry.path)
        )
    )


def _same_content(a: str, b: str, path: str) -> bool:
    if path.lower().endswith(".json"):
        try:
            return json.loads(a) == json.loads(b)
        except ValueError:
            pass
    return a.strip() == b.strip()


def _generated_by_file(db: Session, project_id: str) -> dict[str, list[tuple[models.PipelineDefinition, models.Artifact]]]:
    """file name -> (pipeline, artifact) for each pipeline's latest generated version."""
    rows = db.execute(
        select(models.PipelineDefinition, models.Artifact)
        .join(models.Artifact, models.Artifact.pipeline_id == models.PipelineDefinition.id)
        .where(
            models.PipelineDefinition.project_id == project_id,
            models.Artifact.version == models.PipelineDefinition.latest_artifact_version,
        )
    ).all()
    out: dict[str, list[tuple[models.PipelineDefinition, models.Artifact]]] = {}
    for pipeline, artifact in rows:
        out.setdefault(artifact.file_name, []).append((pipeline, artifact))
    return out


def _ownership(entry: models.GitCatalogEntry, generated: dict[str, list]) -> dict[str, Any]:
    candidates = generated.get(posixpath.basename(entry.path)) or []
    if not candidates:
        return {"state": "foreign", "pipelineId": None, "pipelineName": None}
    for pipeline, artifact in candidates:
        if _same_content(entry.content, artifact.content, entry.path):
            return {"state": "in_sync", "pipelineId": pipeline.id, "pipelineName": pipeline.name}
    pipeline, _ = max(candidates, key=lambda c: (c[1].created_at is not None, c[1].created_at))
    return {"state": "drifted", "pipelineId": pipeline.id, "pipelineName": pipeline.name}


def _refers_to(source: models.GitCatalogEntry, target: models.GitCatalogEntry) -> bool:
    return any(
        REFERENCE_TYPES.get(r.get("type")) == target.artifact_type and r.get("name") == target.name
        for r in source.references or []
    )


def _used_by(entries: list[models.GitCatalogEntry], target: models.GitCatalogEntry) -> list[models.GitCatalogEntry]:
    return [e for e in entries if e.path != target.path and _refers_to(e, target)]


def _entry_summary(entry: models.GitCatalogEntry) -> dict[str, Any]:
    return {"path": entry.path, "type": entry.artifact_type, "name": entry.name}


def list_catalog(
    db: Session, project_id: str, *, branch: str | None, artifact_type: str | None, q: str | None
) -> dict[str, Any]:
    _, base = _project_git(db, project_id)
    branch = (branch or base).strip()
    entries = _entries(db, project_id, branch)
    generated = _generated_by_file(db, project_id)
    needle = (q or "").strip().lower()
    rows = []
    for entry in entries:
        if artifact_type and entry.artifact_type != artifact_type:
            continue
        # Search covers names *and* references (§24.8 "search by name/reference").
        if needle and needle not in entry.name.lower() and needle not in entry.path.lower() and not any(
            needle in (r.get("name") or "").lower() for r in entry.references or []
        ):
            continue
        rows.append({
            **_entry_summary(entry),
            "blobSha": entry.blob_sha,
            "references": entry.references or [],
            "parameters": entry.parameters or [],
            "usedByCount": len(_used_by(entries, entry)),
            "ownership": _ownership(entry, generated),
        })
    branches = sorted(set(db.scalars(
        select(models.GitCatalogEntry.branch).where(models.GitCatalogEntry.project_id == project_id)
    )))
    return {
        "branch": branch,
        "baseBranch": base,
        "branches": branches,
        "indexedAt": _iso(max((e.indexed_at for e in entries), default=None)),
        "simulated": any(e.simulated for e in entries),
        "entries": rows,
    }


def _require_entry(db: Session, project_id: str, branch: str, path: str) -> models.GitCatalogEntry:
    entry = db.get(models.GitCatalogEntry, (project_id, branch, path))
    if entry is None:
        raise ApiError(404, "catalog_entry_not_found", f"'{path}' is not in the {branch} catalog index — refresh it.")
    return entry


def catalog_artifact(db: Session, project_id: str, *, branch: str | None, path: str) -> dict[str, Any]:
    _, base = _project_git(db, project_id)
    branch = (branch or base).strip()
    entry = _require_entry(db, project_id, branch, path)
    entries = _entries(db, project_id, branch)
    return {
        **_entry_summary(entry),
        "branch": branch,
        "blobSha": entry.blob_sha,
        "content": entry.content,
        "references": entry.references or [],
        "parameters": entry.parameters or [],
        "usedBy": [_entry_summary(e) for e in _used_by(entries, entry)],
        "ownership": _ownership(entry, _generated_by_file(db, project_id)),
        "simulated": entry.simulated,
        "indexedAt": _iso(entry.indexed_at),
    }


# ---------------------------------------------------------------------------
# rename / reference rewrite
# ---------------------------------------------------------------------------


def _indent_of(content: str) -> int:
    for line in content.splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped:
            return max(len(line) - len(stripped), 1)
    return 4


def _dump_like(doc: Any, original: str) -> str:
    return json.dumps(doc, indent=_indent_of(original), ensure_ascii=False) + ("\n" if original.endswith("\n") else "")


def _rewrite_refs(node: Any, target_type: str, old: str, new: str) -> int:
    count = 0
    if isinstance(node, dict):
        if node.get("referenceName") == old and REFERENCE_TYPES.get(node.get("type")) == target_type:
            node["referenceName"] = new
            count += 1
        for value in node.values():
            count += _rewrite_refs(value, target_type, old, new)
    elif isinstance(node, list):
        for value in node:
            count += _rewrite_refs(value, target_type, old, new)
    return count


def _rewritten(source: models.GitCatalogEntry, target: models.GitCatalogEntry, new_name: str) -> str | None:
    try:
        doc = json.loads(source.content)
    except ValueError:
        return None
    return _dump_like(doc, source.content) if _rewrite_refs(doc, target.artifact_type, target.name, new_name) else None


def catalog_impact(db: Session, project_id: str, *, branch: str | None, path: str) -> dict[str, Any]:
    """Blast radius of renaming/deleting ``path`` — every file that references it."""
    _, base = _project_git(db, project_id)
    branch = (branch or base).strip()
    entry = _require_entry(db, project_id, branch, path)
    return {"path": path, "branch": branch, "inbound": [_entry_summary(e) for e in _used_by(_entries(db, project_id, branch), entry)]}


# ---------------------------------------------------------------------------
# change request (update / rename / delete)
# ---------------------------------------------------------------------------


def request_catalog_change(
    db: Session,
    project: models.Project,
    *,
    mode: str,
    path: str,
    content: str | None,
    new_name: str | None,
    rewrite_references: bool,
    force: bool,
    feature_branch: str,
    commit_message: str | None,
    pr_title: str | None,
    pr_body: str | None,
    git_token: str | None,
    actor: str | None,
) -> models.GitCatalogChange:
    git, base = _project_git(db, project.id)
    repository_url = (git.get("repositoryUrl") or "").strip()
    feature_branch = feature_branch.strip()
    if not repository_url:
        raise ApiError(422, "git_not_configured", "Configure the project's Git repository (Project Settings) first.")
    if not feature_branch:
        raise ApiError(422, "feature_branch_required", "A feature branch is required.")
    if feature_branch == base:
        raise ApiError(422, "protected_branch", f"The feature branch must not equal the protected base branch '{base}'.")
    if mode not in ("update", "rename", "delete"):
        raise ApiError(422, "catalog_mode_invalid", "mode must be update, rename, or delete.")

    # Changes always apply against the base branch index — the branch the PR targets.
    entry = _require_entry(db, project.id, base, path)
    entries = _entries(db, project.id, base)
    inbound = _used_by(entries, entry)
    files: list[dict[str, str]] = []
    deletes: list[str] = []
    new_path: str | None = None

    if mode == "update":
        text = content or ""
        if len(text.encode("utf-8")) > MAX_CHANGE_BYTES:
            raise ApiError(422, "catalog_content_too_large", "The edited file exceeds 2 MB.")
        if path.lower().endswith(".json"):
            try:
                doc = json.loads(text)
            except ValueError as exc:
                raise ApiError(422, "catalog_json_invalid", f"The edited file is not valid JSON: {exc}.") from exc
            # ADF keys a resource on its file name — renaming happens through mode=rename only.
            if isinstance(doc, dict) and entry.artifact_type in EDITABLE_TYPES and doc.get("name") != entry.name:
                raise ApiError(
                    422, "catalog_name_changed",
                    f"'name' must stay '{entry.name}' (it is the file name). Use Rename to change it.",
                )
        if _same_content(text, entry.content, path) and text == entry.content:
            raise ApiError(422, "catalog_no_changes", "The file is unchanged.")
        files.append({"path": path, "content": text})
    else:
        if entry.artifact_type not in EDITABLE_TYPES:
            raise ApiError(422, "catalog_type_not_editable", f"A {entry.artifact_type} file can't be renamed or deleted here.")

    if mode == "rename":
        new_name = (new_name or "").strip()
        if not ARTIFACT_NAME_RE.match(new_name):
            raise ApiError(
                422, "catalog_name_invalid",
                "The new name must start with a letter and use only letters, digits, and '_' (2–128 characters).",
            )
        new_path = f"{posixpath.dirname(path)}/{new_name}{posixpath.splitext(path)[1]}"
        if db.get(models.GitCatalogEntry, (project.id, base, new_path)) is not None:
            raise ApiError(409, "catalog_name_taken", f"'{new_path}' already exists on {base}.")
        try:
            doc = json.loads(entry.content)
        except ValueError as exc:
            raise ApiError(422, "catalog_json_invalid", "Only a valid JSON artifact can be renamed.") from exc
        if isinstance(doc, dict):
            doc["name"] = new_name
        files.append({"path": new_path, "content": _dump_like(doc, entry.content)})
        deletes.append(path)
        if inbound and not rewrite_references and not force:
            raise ApiError(
                409, "catalog_has_inbound_references",
                f"{len(inbound)} artifact(s) reference {entry.name}: {', '.join(e.name for e in inbound)}. "
                "Rewrite their references in the same PR, or confirm to leave them dangling.",
            )
        if rewrite_references:
            for source in inbound:
                updated = _rewritten(source, entry, new_name)
                if updated is not None:
                    files.append({"path": source.path, "content": updated})

    if mode == "delete":
        if inbound and not force:
            raise ApiError(
                409, "catalog_has_inbound_references",
                f"{len(inbound)} artifact(s) still reference {entry.name}: {', '.join(e.name for e in inbound)}. "
                "Confirm to delete anyway.",
            )
        deletes.append(path)

    default_message = {
        "update": f"chore(adf): update {entry.name}",
        "rename": f"refactor(adf): rename {entry.name} to {new_name}",
        "delete": f"chore(adf): delete {entry.name}",
    }[mode]
    change = models.GitCatalogChange(
        id=new_id("gcc"),
        project_id=project.id,
        mode=mode,
        path=path,
        new_path=new_path,
        provider=git.get("provider") or "github",
        repository_url=repository_url,
        feature_branch=feature_branch,
        base_branch=base,
        commit_message=(commit_message or "").strip() or default_message,
        files=files,
        delete_paths=deletes,
        base_blob_sha=entry.blob_sha or None,
        status="pending",
        created_by=actor,
    )
    db.add(change)
    touched = [f["path"] for f in files] + deletes
    enqueue_job(
        db,
        job_type=JOB_CATALOG_CHANGE,
        pipeline_id=None,
        payload={
            "changeId": change.id,
            "projectId": project.id,
            "prTitle": (pr_title or "").strip() or change.commit_message,
            "prBody": pr_body or "Files:\n" + "\n".join(f"- {p}" for p in touched),
            "reviewers": git.get("reviewers") or [],
            "credentialVault": git.get("credentialVault"),
            "gitToken": encrypt_secret(git_token) if git_token else None,
        },
        actor=actor,
    )
    repository.record_audit(
        db, actor=actor or "system", action=f"catalog.{mode}_requested", target=path,
        details=f"{len(files)} written, {len(deletes)} deleted -> {feature_branch}", team_id=project.team_id,
    )
    return change


def catalog_change_to_dict(change: models.GitCatalogChange) -> dict[str, Any]:
    return {
        "id": change.id,
        "projectId": change.project_id,
        "mode": change.mode,
        "path": change.path,
        "newPath": change.new_path,
        "featureBranch": change.feature_branch,
        "baseBranch": change.base_branch,
        "commitMessage": change.commit_message,
        "filePaths": [f["path"] for f in change.files or []],
        "deletePaths": list(change.delete_paths or []),
        "status": change.status,
        "commitSha": change.commit_sha,
        "prNumber": change.pr_number,
        "prUrl": change.pr_url,
        "simulated": change.simulated,
        "error": change.error,
        "createdAt": _iso(change.created_at),
    }


def list_catalog_changes(db: Session, project_id: str, *, limit: int = 20) -> list[models.GitCatalogChange]:
    return list(
        db.scalars(
            select(models.GitCatalogChange)
            .where(models.GitCatalogChange.project_id == project_id)
            .order_by(models.GitCatalogChange.created_at.desc(), models.GitCatalogChange.id.desc())
            .limit(limit)
        )
    )


# ---------------------------------------------------------------------------
# §26.6 onboarding import + drift against metadata/objects.csv in Git
# ---------------------------------------------------------------------------

# Registry bookkeeping, not object content — never a drift.
_DRIFT_IGNORED = {"status", "provenance"}


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value).strip()


def _typed_row(raw: dict[str, str], columns: list[str]) -> dict[str, Any]:
    row: dict[str, Any] = {"object_key": raw.get("object_key", "").strip()}
    for column in columns:
        text = (raw.get(column) or "").strip()
        if column == "enabled":
            row[column] = text.upper() not in ("FALSE", "0", "NO", "N")
        elif column == "notes":
            row[column] = text
        else:
            row[column] = text or None
    return row


def onboarding_git_drift(db: Session, project_id: str, *, branch: str | None) -> dict[str, Any]:
    """Compare the registry with ``metadata/objects.csv`` as it now is in Git.

    ``changedInGit``/``changedLocally`` say which side moved since the last
    successful sync (§26.5 snapshot) — a row changed only in Git is the
    "someone edited Git directly" drift §26.6 describes."""
    git, base = _project_git(db, project_id)
    branch = (branch or base).strip()
    root = (git.get("artifactRoot") or "adf/artifacts/").strip("/")
    path = f"{root}/metadata/objects.csv" if root else "metadata/objects.csv"
    entry = db.get(models.GitCatalogEntry, (project_id, branch, path))
    indexed_any = db.scalars(
        select(models.GitCatalogEntry.path).where(
            models.GitCatalogEntry.project_id == project_id, models.GitCatalogEntry.branch == branch
        ).limit(1)
    ).first() is not None
    result: dict[str, Any] = {
        "branch": branch, "path": path, "indexed": indexed_any, "available": entry is not None,
        "blobSha": entry.blob_sha if entry else None, "rows": [], "inSync": 0,
    }
    if entry is None:
        return result

    reader = csv.DictReader(io.StringIO(entry.content.lstrip("﻿")))
    headers = [h.strip() for h in reader.fieldnames or []]
    if "object_key" not in headers:
        raise ApiError(422, "objects_csv_invalid", f"{path} has no object_key column.")
    columns = [c for c in _ONBOARDING_FIELD_MAP if c in headers and c not in _DRIFT_IGNORED]
    git_rows = {
        r["object_key"]: r
        for r in (_typed_row({k.strip(): v for k, v in raw.items() if k}, columns) for raw in reader)
        if r["object_key"]
    }

    registry = {o.object_key: _sync_row(o) for o in repository.list_onboarding_objects(db, project_id)}
    last_sync = db.scalars(
        select(models.OnboardingSync)
        .where(models.OnboardingSync.project_id == project_id, models.OnboardingSync.status.in_(("pushed", "pr_created")))
        .order_by(models.OnboardingSync.created_at.desc(), models.OnboardingSync.id.desc())
        .limit(1)
    ).first()
    snapshot: dict[str, dict[str, Any]] = dict(last_sync.snapshot or {}) if last_sync else {}

    def differs(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
        if a is None or b is None:
            return (a is None) != (b is None)
        return any(_cell(a.get(c)) != _cell(b.get(c)) for c in columns)

    rows = []
    for key in sorted(set(git_rows) | set(registry)):
        theirs, mine = git_rows.get(key), registry.get(key)
        if theirs and mine:
            fields = [
                {"field": c, "registry": _cell(mine.get(c)), "git": _cell(theirs.get(c))}
                for c in columns if _cell(mine.get(c)) != _cell(theirs.get(c))
            ]
            if not fields:
                result["inSync"] += 1
                continue
            status = "drifted"
        else:
            fields = []
            status = "only_in_git" if theirs else "only_in_registry"
        base_row = snapshot.get(key)
        rows.append({
            "objectKey": key,
            "status": status,
            "fields": fields,
            "changedInGit": bool(snapshot) and differs(base_row, theirs),
            "changedLocally": bool(snapshot) and differs(base_row, mine),
            "gitRow": theirs,
        })
    result["rows"] = rows
    result["lastSyncCommitSha"] = last_sync.commit_sha if last_sync else None
    return result
