"""GitHub adapter — atomic multi-file commit via the Git Data API.

blobs -> tree -> commit -> update ref, so a multi-artifact push either lands
whole or not at all (§17, §23.6). Uses only the stdlib (``urllib``); the token
is a resolved Key Vault secret, never logged.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.parse
import urllib.error
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

_REPO_RE = re.compile(r"github\.com[/:]+([^/]+)/([^/.]+)")


class GitHubAdapter(GitAdapter):
    provider = "github"
    api_base = "https://api.github.com"

    def __init__(self, repository_url: str, *, credential: str) -> None:
        super().__init__(repository_url, credential=credential)
        match = _REPO_RE.search(repository_url)
        if not match:
            raise GitOpsError(f"Could not parse a github.com owner/repo from '{repository_url}'.")
        self.owner, self.repo = match.group(1), match.group(2)

    # -- HTTP -----------------------------------------------------------

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._credential}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("User-Agent", "adf-accelerator-worker")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed api host
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise GitOpsError(f"GitHub {method} {path} failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover
            raise GitOpsError(f"GitHub is unreachable: {exc.reason}") from exc

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

        base_ref = self._call("GET", f"/git/ref/heads/{base_branch}")
        base_sha = base_ref["object"]["sha"]
        base_commit = self._call("GET", f"/git/commits/{base_sha}")
        base_tree_sha = base_commit["tree"]["sha"]

        tree_entries = []
        for blob in files:
            created = self._call("POST", "/git/blobs", {"content": blob.content, "encoding": "utf-8"})
            tree_entries.append({"path": blob.path, "mode": "100644", "type": "blob", "sha": created["sha"]})
        # A null sha removes the path from the new tree (Git Data API).
        tree_entries.extend({"path": path, "mode": "100644", "type": "blob", "sha": None} for path in deletes or [])
        tree = self._call("POST", "/git/trees", {"base_tree": base_tree_sha, "tree": tree_entries})
        commit = self._call(
            "POST", "/git/commits", {"message": commit_message, "tree": tree["sha"], "parents": [base_sha]}
        )

        ref_path = f"/git/refs/heads/{feature_branch}"
        try:
            self._call("GET", f"/git/ref/heads/{feature_branch}")
            self._call("PATCH", ref_path, {"sha": commit["sha"], "force": True})
        except GitOpsError:
            self._call("POST", "/git/refs", {"ref": f"refs/heads/{feature_branch}", "sha": commit["sha"]})

        return PushResult(commit_sha=commit["sha"], branch=feature_branch, files=len(files) + len(deletes or []))

    def list_files(self, *, branch: str, prefix: str) -> list[RepoFile]:
        ref = urllib.parse.quote(branch, safe="")
        tree = self._call("GET", f"/git/trees/{ref}?recursive=1")
        entries = [
            e for e in tree.get("tree", [])
            if e.get("type") == "blob" and catalog_candidate(e.get("path", ""), prefix)
            and (e.get("size") or 0) <= MAX_CATALOG_FILE_BYTES
        ][:MAX_CATALOG_FILES]
        files: list[RepoFile] = []
        for entry in entries:
            blob = self._call("GET", f"/git/blobs/{entry['sha']}")
            content = base64.b64decode(blob.get("content") or "").decode("utf-8", "replace")
            files.append(RepoFile(path=entry["path"], sha=entry["sha"], content=content))
        return files

    def file_sha(self, *, path: str, ref: str) -> str | None:
        qs = urllib.parse.urlencode({"ref": ref})
        try:
            found = self._call("GET", f"/contents/{urllib.parse.quote(path)}?{qs}")
        except GitOpsError as exc:
            if "(404)" in str(exc):
                return None
            raise
        return found.get("sha") if isinstance(found, dict) else None

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
            "/pulls",
            {"title": title, "body": body, "head": source_branch, "base": target_branch},
        )
        if reviewers:
            try:
                self._call("POST", f"/pulls/{pr['number']}/requested_reviewers", {"reviewers": reviewers})
            except GitOpsError:
                pass  # reviewers are best-effort; the PR itself is created
        return PullRequestResult(
            number=pr["number"], url=pr["html_url"], source_branch=source_branch, target_branch=target_branch
        )
