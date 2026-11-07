"""GitLab adapter — atomic multi-file commit via the Commits API (REST v4).

One ``POST /projects/:id/repository/commits`` call carries every artifact
change as a single list of "actions", so the push is atomic (§17, §23.6) —
unlike GitHub's Git Data API there's no separate blob/tree/commit dance.
Auth is a ``PRIVATE-TOKEN`` header (a resolved Key Vault secret, never
logged). Works against gitlab.com and self-hosted instances alike: the host
and full (possibly nested-subgroup) project path both come from
``repository_url``, never hardcoded — this was previously routed through
``GitHubAdapter`` (github.com-only), which made every real GitLab push fail.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .adapter import (
    MAX_CATALOG_FILE_BYTES,
    MAX_CATALOG_FILES,
    ArtifactBlob,
    GitAdapter,
    GitOpsError,
    PullRequestResult,
    PushResult,
    RepoFile,
    catalog_candidate,
)

# Group 1: scheme + host. Group 2: full project path, incl. nested subgroups,
# with an optional trailing ".git" and/or slash stripped.
_URL_RE = re.compile(r"^(https?://[^/]+)/([^?#]+?)(?:\.git)?/?$")


class GitLabAdapter(GitAdapter):
    """Talks to gitlab.com or any self-hosted GitLab (REST API v4)."""

    provider = "gitlab"

    def __init__(self, repository_url: str, *, credential: str) -> None:
        super().__init__(repository_url, credential=credential)
        match = _URL_RE.match(self.repository_url)
        project_path = match.group(2) if match else ""
        if not match or not project_path:
            raise GitOpsError(f"Could not parse a GitLab host/project path from '{repository_url}'.")
        self.host = match.group(1)
        self.project_path = project_path
        self._api_root = f"{self.host}/api/v4"
        self._base = f"{self._api_root}/projects/{urllib.parse.quote(project_path, safe='')}"

    # -- HTTP -----------------------------------------------------------

    def _request(self, url: str, method: str, body: dict[str, Any] | None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("PRIVATE-TOKEN", self._credential)
        request.add_header("Content-Type", "application/json")
        request.add_header("User-Agent", "adf-accelerator-worker")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - host comes from the configured repo URL
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
            if method == "GET" and exc.code == 404:
                return None  # existence probes (branch/file) treat a 404 as "not found", not an error
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise GitOpsError(f"GitLab {method} {url} failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover
            raise GitOpsError(f"GitLab is unreachable: {exc.reason}") from exc

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._request(f"{self._base}{path}", method, body)

    def _branch_exists(self, branch: str) -> bool:
        return self._call("GET", f"/repository/branches/{urllib.parse.quote(branch, safe='')}") is not None

    def _file_exists(self, path: str, ref: str) -> bool:
        qs = urllib.parse.urlencode({"ref": ref})
        return self._call("GET", f"/repository/files/{urllib.parse.quote(path, safe='')}?{qs}") is not None

    # -- operations ---------------------------------------------------

    def push_artifacts(
        self,
        *,
        feature_branch: str,
        base_branch: str,
        files: list[ArtifactBlob],
        commit_message: str,
        deletes: list[str] | None = None,
    ) -> PushResult:
        self._guard_branches(feature_branch, base_branch)
        if not files and not deletes:
            raise GitOpsError("Nothing to push: the selected artifact set is empty.")

        branch_exists = self._branch_exists(feature_branch)
        # Per-file action must match reality: "create" 400s if the file is
        # already there, "update" 400s if it isn't — probe against whichever
        # ref this commit will actually land on.
        exists_ref = feature_branch if branch_exists else base_branch
        actions = [
            {
                "action": "update" if self._file_exists(blob.path, exists_ref) else "create",
                "file_path": blob.path,
                "content": blob.content,
            }
            for blob in files
        ]
        actions.extend({"action": "delete", "file_path": path} for path in deletes or [])
        body: dict[str, Any] = {
            "branch": feature_branch,
            "commit_message": commit_message,
            "actions": actions,
        }
        if not branch_exists:
            body["start_branch"] = base_branch  # creates feature_branch off base_branch in the same call
        commit = self._call("POST", "/repository/commits", body)
        return PushResult(commit_sha=commit["id"], branch=feature_branch, files=len(files) + len(deletes or []))

    def list_files(self, *, branch: str, prefix: str) -> list[RepoFile]:
        entries: list[dict[str, Any]] = []
        page = 1
        while len(entries) < MAX_CATALOG_FILES:
            qs = urllib.parse.urlencode(
                {"ref": branch, "path": prefix.strip("/"), "recursive": "true", "per_page": 100, "page": page}
            )
            batch = self._call("GET", f"/repository/tree?{qs}") or []
            entries.extend(e for e in batch if e.get("type") == "blob" and catalog_candidate(e.get("path", ""), prefix))
            if len(batch) < 100:
                break
            page += 1
        files: list[RepoFile] = []
        for entry in entries[:MAX_CATALOG_FILES]:
            blob = self._call("GET", f"/repository/blobs/{entry['id']}") or {}
            if (blob.get("size") or 0) > MAX_CATALOG_FILE_BYTES:
                continue
            content = base64.b64decode(blob.get("content") or "").decode("utf-8", "replace")
            files.append(RepoFile(path=entry["path"], sha=entry["id"], content=content))
        return files

    def file_sha(self, *, path: str, ref: str) -> str | None:
        qs = urllib.parse.urlencode({"ref": ref})
        found = self._call("GET", f"/repository/files/{urllib.parse.quote(path, safe='')}?{qs}")
        return found.get("blob_id") if isinstance(found, dict) else None

    def open_pull_request(
        self,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        body: str,
        reviewers: list[str] | None = None,
    ) -> PullRequestResult:
        self._guard_branches(source_branch, target_branch)
        mr = self._call(
            "POST",
            "/merge_requests",
            {
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": body,
            },
        )
        if reviewers:
            try:
                reviewer_ids = []
                for username in reviewers:
                    qs = urllib.parse.urlencode({"username": username})
                    users = self._request(f"{self._api_root}/users?{qs}", "GET", None)
                    if users:
                        reviewer_ids.append(users[0]["id"])
                if reviewer_ids:
                    self._call("PUT", f"/merge_requests/{mr['iid']}", {"reviewer_ids": reviewer_ids})
            except GitOpsError:
                pass  # reviewers are best-effort (and Premium-only on some tiers); the MR itself is created
        return PullRequestResult(
            number=mr["iid"], url=mr["web_url"], source_branch=source_branch, target_branch=target_branch
        )
