"""Git provider adapter (AGENTS.md §9, §23.6).

Provider calls sit behind :class:`GitAdapter`; the rest of the worker only sees
``PushResult`` / ``PullRequestResult``. Hard rules (feature branch != protected
base, atomic multi-file push) are enforced here regardless of provider.

Credentials are resolved from Key Vault by *reference* only — a secret value is
never passed in or logged. When no credential reference is configured the
:class:`~app.gitops.simulated.SimulatedAdapter` is used: it performs the full
validation + ordering and returns a deterministic commit SHA / PR number so the
end-to-end flow is exercisable offline (this replaces the frontend's
``git-push-dialog.tsx`` mock).
"""
from __future__ import annotations

from .adapter import (
    ArtifactBlob,
    GitAdapter,
    GitOpsError,
    ProtectedBranchError,
    PullRequestResult,
    PushResult,
    RepoFile,
    get_adapter,
)

__all__ = [
    "ArtifactBlob",
    "GitAdapter",
    "GitOpsError",
    "ProtectedBranchError",
    "PullRequestResult",
    "PushResult",
    "RepoFile",
    "get_adapter",
]
