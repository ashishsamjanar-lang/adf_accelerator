"""Azure Repos adapter — atomic multi-file push + PR (REST API 7.1).

One ``pushes`` call carries all artifact changes, so the push is atomic
(§17, §23.6). Auth is Basic with an empty username + PAT (a resolved Key Vault
secret, never logged).
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

_URL_RE = re.compile(r"dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/]+)")
_VS_URL_RE = re.compile(r"https?://([^.]+)\.visualstudio\.com/([^/]+)/_git/([^/]+)")


class AzureReposAdapter(GitAdapter):
    provider = "azure_repos"
    api_version = "7.1"

    def __init__(self, repository_url: str, *, credential: str) -> None:
        super().__init__(repository_url, credential=credential)
        match = _URL_RE.search(repository_url) or _VS_URL_RE.search(repository_url)
        if not match:
            raise GitOpsError(f"Could not parse an Azure Repos org/project/repo from '{repository_url}'.")
        self.org, self.project, self.repo = match.group(1), match.group(2), match.group(3)
        self._base = f"https://dev.azure.com/{self.org}/{self.project}/_apis/git/repositories/{self.repo}"

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        sep = "&" if "?" in path else "?"
        url = f"{self._base}{path}{sep}api-version={self.api_version}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        token = base64.b64encode(f":{self._credential}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
        request.add_header("Content-Type", "application/json")
        request.add_header("User-Agent", "adf-accelerator-worker")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed api host
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise GitOpsError(f"Azure Repos {method} {path} failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover
            raise GitOpsError(f"Azure Repos is unreachable: {exc.reason}") from exc

    def _branch_object_id(self, branch: str) -> str | None:
        refs = self._call("GET", f"/refs?filter=heads/{branch}")
        for ref in refs.get("value", []):
            if ref.get("name") == f"refs/heads/{branch}":
                return ref.get("objectId")
        return None

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

        existing = self._branch_object_id(feature_branch)
        old_object_id = existing or self._branch_object_id(base_branch)
        if not old_object_id:
            raise GitOpsError(f"Base branch '{base_branch}' was not found in the repository.")
        # add vs. edit is per *file*, on whichever ref this commit lands on (a
        # new branch starts from base) — not whether the branch exists.
        exists_ref = feature_branch if existing else base_branch
        changes: list[dict[str, Any]] = [
            {
                "changeType": "edit" if self.file_sha(path=blob.path, ref=exists_ref) else "add",
                "item": {"path": f"/{blob.path.lstrip('/')}"},
                "newContent": {"content": blob.content, "contentType": "rawtext"},
            }
            for blob in files
        ]
        changes.extend({"changeType": "delete", "item": {"path": f"/{path.lstrip('/')}"}} for path in deletes or [])

        push = self._call(
            "POST",
            "/pushes",
            {
                "refUpdates": [{"name": f"refs/heads/{feature_branch}", "oldObjectId": old_object_id}],
                "commits": [
                    {
                        "comment": commit_message,
                        "changes": changes,
                    }
                ],
            },
        )
        commit_id = push["commits"][0]["commitId"]
        return PushResult(commit_sha=commit_id, branch=feature_branch, files=len(files) + len(deletes or []))

    def _items(self, query: dict[str, str]) -> Any:
        try:
            return self._call("GET", f"/items?{urllib.parse.urlencode(query)}")
        except GitOpsError as exc:
            if "(404)" in str(exc):
                return None
            raise

    def list_files(self, *, branch: str, prefix: str) -> list[RepoFile]:
        version = {"versionDescriptor.version": branch, "versionDescriptor.versionType": "branch"}
        listing = self._items({"scopePath": "/" + prefix.strip("/"), "recursionLevel": "Full", **version}) or {}
        entries = [
            e for e in listing.get("value", [])
            if not e.get("isFolder") and catalog_candidate(e.get("path", "").lstrip("/"), prefix)
        ][:MAX_CATALOG_FILES]
        files: list[RepoFile] = []
        for entry in entries:
            item = self._items({"path": entry["path"], "includeContent": "true", "$format": "json", **version}) or {}
            content = item.get("content") or ""
            if len(content.encode("utf-8")) > MAX_CATALOG_FILE_BYTES:
                continue
            files.append(RepoFile(path=entry["path"].lstrip("/"), sha=entry.get("objectId", ""), content=content))
        return files

    def file_sha(self, *, path: str, ref: str) -> str | None:
        item = self._items(
            {
                "path": "/" + path.lstrip("/"),
                "versionDescriptor.version": ref,
                "versionDescriptor.versionType": "branch",
                "$format": "json",
            }
        )
        return item.get("objectId") if isinstance(item, dict) else None

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
        pr = self._call(
            "POST",
            "/pullrequests",
            {
                "sourceRefName": f"refs/heads/{source_branch}",
                "targetRefName": f"refs/heads/{target_branch}",
                "title": title,
                "description": body,
                "reviewers": [{"id": r} for r in (reviewers or [])],
            },
        )
        number = pr.get("pullRequestId")
        url = f"https://dev.azure.com/{self.org}/{self.project}/_git/{self.repo}/pullrequest/{number}"
        return PullRequestResult(
            number=number, url=url, source_branch=source_branch, target_branch=target_branch
        )
