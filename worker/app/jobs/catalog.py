"""§24.8 Git-backed catalog index + §24.9/§24.10 catalog change PRs — the worker half.

``catalog_refresh`` reads the repo under the project's artifact root on one
branch and rewrites that branch's ``git_catalog_entries``; ``catalog_change``
commits a raw edit / rename / delete the API already validated, then opens the
PR. Both reuse the provider adapters in ``app.gitops``.

**Simulated projects** (no Git credential resolves) have no remote to read, so
their index is a *virtual repo*: every simulated pipeline push, onboarding sync
and catalog change this app made for the project, applied in order as if each PR
had merged. It is branch-agnostic — whatever branch was asked for gets the same
virtual tree — and every entry is flagged ``simulated``.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import models
from app.gitops import ArtifactBlob, GitOpsError, RepoFile, get_adapter
from app.gitops.adapter import MAX_CATALOG_FILES, catalog_candidate
from app.gitops.simulated import SimulatedAdapter

# ADF Git-mode folder (§24.11) -> catalog artifact type.
FOLDER_TYPES = {
    "pipeline": "pipeline",
    "dataset": "dataset",
    "linkedService": "linked_service",
    "trigger": "trigger",
    "factory": "factory",
    "credential": "credential",
    "dataflow": "data_flow",
    "integrationRuntime": "integration_runtime",
    "managedVirtualNetwork": "managed_virtual_network",
}


def git_blob_sha(content: str) -> str:
    """The sha Git itself would give this text — stable ids for the virtual repo."""
    data = content.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 - git object id, not security


def _references(node: Any, out: set[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        name, ref_type = node.get("referenceName"), node.get("type")
        if isinstance(name, str) and isinstance(ref_type, str) and ref_type.endswith("Reference"):
            out.add((ref_type, name))
        for value in node.values():
            _references(value, out)
    elif isinstance(node, list):
        for value in node:
            _references(value, out)


def parse_repo_file(path: str, content: str, artifact_root: str) -> dict[str, Any]:
    """Classify one repo file and pull out its name, references and parameters."""
    root = artifact_root.strip("/")
    relative = path[len(root) + 1:] if root and path.startswith(f"{root}/") else path
    parts = relative.split("/")
    artifact_type = FOLDER_TYPES.get(parts[0], "metadata") if len(parts) > 1 else "metadata"
    stem = posixpath.splitext(parts[-1])[0]
    entry: dict[str, Any] = {"artifact_type": artifact_type, "name": stem, "references": [], "parameters": []}
    if not path.lower().endswith(".json"):
        return entry
    try:
        doc = json.loads(content)
    except ValueError:
        return entry
    if not isinstance(doc, dict):
        return entry
    if isinstance(doc.get("name"), str) and doc["name"].strip():
        entry["name"] = doc["name"].strip()
    refs: set[tuple[str, str]] = set()
    _references(doc.get("properties", doc), refs)
    entry["references"] = [{"type": t, "name": n} for t, n in sorted(refs)]
    params = (doc.get("properties") or {}).get("parameters") if isinstance(doc.get("properties"), dict) else None
    if isinstance(params, dict):
        entry["parameters"] = sorted(params)
    elif isinstance(params, list):
        entry["parameters"] = sorted(p["name"] for p in params if isinstance(p, dict) and isinstance(p.get("name"), str))
    return entry


# ---------------------------------------------------------------------------
# simulated virtual repo
# ---------------------------------------------------------------------------


def _virtual_repo(db: Session, project_id: str, prefix: str) -> list[RepoFile]:
    """Every simulated write this app made for the project, applied oldest-first."""
    events: list[tuple[Any, dict[str, str], list[str]]] = []

    pushes = db.execute(
        select(models.GitPushRequest)
        .join(models.PipelineDefinition, models.PipelineDefinition.id == models.GitPushRequest.pipeline_id)
        .where(
            models.PipelineDefinition.project_id == project_id,
            models.GitPushRequest.simulated.is_(True),
            models.GitPushRequest.status.in_(("pushed", "pr_created")),
        )
    ).scalars()
    for push in pushes:
        rows = db.scalars(select(models.Artifact).where(models.Artifact.id.in_(push.artifact_ids or []))).all()
        events.append((push.created_at, {r.repository_path: r.content for r in rows if r.repository_path}, []))

    syncs = {
        s.id: s
        for s in db.scalars(
            select(models.OnboardingSync).where(
                models.OnboardingSync.project_id == project_id,
                models.OnboardingSync.simulated.is_(True),
                models.OnboardingSync.status.in_(("pushed", "pr_created")),
            )
        )
    }
    if syncs:
        for job in db.scalars(select(models.Job).where(models.Job.type == "onboarding_sync")):
            sync = syncs.get((job.payload or {}).get("syncId"))
            if sync is not None:
                files = {f["path"]: f["content"] for f in (job.payload or {}).get("files") or []}
                events.append((sync.created_at, files, []))

    for change in db.scalars(
        select(models.GitCatalogChange).where(
            models.GitCatalogChange.project_id == project_id,
            models.GitCatalogChange.simulated.is_(True),
            models.GitCatalogChange.status == "pr_created",
        )
    ):
        events.append((change.created_at, {f["path"]: f["content"] for f in change.files or []}, list(change.delete_paths or [])))

    tree: dict[str, str] = {}
    for _, files, deletes in sorted(events, key=lambda e: e[0]):
        for path in deletes:
            tree.pop(path, None)
        tree.update(files)
    paths = sorted(p for p in tree if catalog_candidate(p, prefix))[:MAX_CATALOG_FILES]
    return [RepoFile(path=p, sha=git_blob_sha(tree[p]), content=tree[p]) for p in paths]


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def handle_catalog_refresh(db: Session, job: models.Job, *, take_token, audit) -> dict[str, Any]:
    payload = job.payload or {}
    project_id = payload.get("projectId") or ""
    branch = (payload.get("branch") or "dev").strip()
    artifact_root = payload.get("artifactRoot") or "adf/artifacts/"
    adapter = get_adapter(
        payload.get("provider") or "github",
        payload.get("repositoryUrl") or "",
        credential_vault=payload.get("credentialVault"),
        direct_token=take_token(job),
    )
    simulated = isinstance(adapter, SimulatedAdapter)
    try:
        files = _virtual_repo(db, project_id, artifact_root) if simulated else adapter.list_files(
            branch=branch, prefix=artifact_root
        )
    except GitOpsError as exc:
        return {"state": "failed", "error": str(exc)}

    db.execute(
        delete(models.GitCatalogEntry).where(
            models.GitCatalogEntry.project_id == project_id, models.GitCatalogEntry.branch == branch
        )
    )
    for f in files:
        parsed = parse_repo_file(f.path, f.content, artifact_root)
        db.add(
            models.GitCatalogEntry(
                project_id=project_id,
                branch=branch,
                path=f.path,
                blob_sha=f.sha,
                content=f.content,
                simulated=simulated,
                **parsed,
            )
        )
    project = db.get(models.Project, project_id)
    audit(
        db,
        actor=job.created_by or "worker",
        action="catalog.refreshed",
        target=project_id,
        details=f"{len(files)} files on {branch}" + (" (simulated)" if simulated else ""),
        team_id=project.team_id if project else None,
    )
    return {"state": "succeeded", "branch": branch, "count": len(files), "simulated": simulated}


def handle_catalog_change(db: Session, job: models.Job, *, take_token, audit) -> dict[str, Any]:
    change = db.get(models.GitCatalogChange, (job.payload or {}).get("changeId"))
    if change is None:
        return {"state": "failed", "error": "The catalog change no longer exists."}
    adapter = get_adapter(
        change.provider,
        change.repository_url,
        credential_vault=(job.payload or {}).get("credentialVault"),
        direct_token=take_token(job),
    )
    project = db.get(models.Project, change.project_id)
    team_id = project.team_id if project else None
    try:
        # §24.14 optimistic concurrency: the file must still be what the user
        # loaded. A simulated repo has no concurrent writers to race.
        if change.base_blob_sha and not isinstance(adapter, SimulatedAdapter):
            current = adapter.file_sha(path=change.path, ref=change.base_branch)
            if current != change.base_blob_sha:
                change.status = "conflict"
                change.error = (
                    f"{change.path} changed on {change.base_branch} since it was loaded "
                    f"({change.base_blob_sha[:7]} → {(current or 'deleted')[:7]}). Refresh the catalog and re-apply."
                )
                audit(db, actor=job.created_by or "worker", action="catalog.change_conflict",
                      target=change.path, details=change.error, team_id=team_id)
                return {"state": "failed", "error": change.error, "code": "git_conflict", "theirSha": current}
        pushed = adapter.push_artifacts(
            feature_branch=change.feature_branch,
            base_branch=change.base_branch,
            files=[ArtifactBlob(path=f["path"], content=f["content"]) for f in change.files or []],
            commit_message=change.commit_message,
            deletes=list(change.delete_paths or []),
        )
        change.commit_sha, change.simulated, change.status = pushed.commit_sha, pushed.simulated, "pushed"
        payload = job.payload or {}
        pr = adapter.open_pull_request(
            source_branch=change.feature_branch,
            target_branch=change.base_branch,
            title=payload.get("prTitle") or change.commit_message,
            body=payload.get("prBody") or "",
            reviewers=payload.get("reviewers") or [],
        )
        change.pr_number, change.pr_url = pr.number, pr.url
        change.simulated = change.simulated or pr.simulated
        change.status, change.error = "pr_created", None
        audit(
            db,
            actor=job.created_by or "worker",
            action=f"catalog.{change.mode}",
            target=change.path,
            details=f"PR {pr.number} {change.feature_branch} -> {change.base_branch}"
            + (f" (renamed to {change.new_path})" if change.new_path else "")
            + (" (simulated)" if change.simulated else ""),
            team_id=team_id,
        )
        return {"state": "succeeded", "commitSha": change.commit_sha, "pullRequest": {"number": pr.number, "url": pr.url}}
    except GitOpsError as exc:
        change.status, change.error = "failed", str(exc)
        audit(db, actor=job.created_by or "worker", action="catalog.change_failed",
              target=change.path, details=str(exc), team_id=team_id)
        return {"state": "failed", "error": str(exc)}
