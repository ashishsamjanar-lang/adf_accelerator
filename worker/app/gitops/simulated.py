"""Offline adapter — full validation + deterministic results, no network.

Used whenever no Git credential reference is configured. Replaces the frontend
``git-push-dialog.tsx`` setTimeout/random-SHA mock with something that at least
runs the real branch rules and ordering, and is deterministic for a given
artifact set so repeated runs are stable.
"""
from __future__ import annotations

import hashlib

from .adapter import ArtifactBlob, GitAdapter, PullRequestResult, PushResult, RepoFile


class SimulatedAdapter(GitAdapter):
    def __init__(self, repository_url: str, *, provider: str = "simulated") -> None:
        super().__init__(repository_url, credential=None)
        self.provider = provider

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
            from .adapter import GitOpsError

            raise GitOpsError("Nothing to push: the selected artifact set is empty.")
        digest = hashlib.sha1(  # noqa: S324 - non-security, mimics a git object id
            (self.repository_url + feature_branch + commit_message + "".join(f.path + f.content for f in sorted(files, key=lambda b: b.path)) + "".join(sorted(deletes or []))).encode("utf-8")
        ).hexdigest()
        return PushResult(commit_sha=digest, branch=feature_branch, files=len(files) + len(deletes or []), simulated=True)

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
        number = int(hashlib.sha1((self.repository_url + source_branch).encode("utf-8")).hexdigest()[:6], 16) % 900 + 100  # noqa: S324
        url = f"{self.repository_url}/pull/{number}"
        return PullRequestResult(
            number=number, url=url, source_branch=source_branch, target_branch=target_branch, simulated=True
        )

    # There is no remote to read: the catalog job builds a simulated project's
    # index from what this app itself pushed (``app.jobs.catalog``), so these
    # return nothing rather than inventing files.
    def list_files(self, *, branch: str, prefix: str) -> list[RepoFile]:
        return []

    def file_sha(self, *, path: str, ref: str) -> str | None:
        return None
