"""Provider-neutral Git adapter interface + factory."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class GitOpsError(RuntimeError):
    """Any provider-side failure. Message is safe to surface to the user."""


class ProtectedBranchError(GitOpsError):
    """Attempted to push to (or PR into the wrong direction of) a protected branch."""


@dataclass(frozen=True)
class ArtifactBlob:
    """One file to commit. ``path`` is repo-relative (artifact root already applied)."""

    path: str
    content: str


@dataclass(frozen=True)
class RepoFile:
    """One file read back from the repository (§24.8 catalog index)."""

    path: str
    sha: str
    content: str


# Catalog reads are bounded so one huge repo can't stall the worker.
MAX_CATALOG_FILES = 2000
MAX_CATALOG_FILE_BYTES = 1024 * 1024
CATALOG_EXTENSIONS = (".json", ".sql", ".csv")


def catalog_candidate(path: str, prefix: str) -> bool:
    """True for a repo path the catalog indexes: under ``prefix``, a known extension."""
    root = prefix.strip("/")
    inside = not root or path == root or path.startswith(f"{root}/")
    return inside and path.lower().endswith(CATALOG_EXTENSIONS)


@dataclass(frozen=True)
class PushResult:
    commit_sha: str
    branch: str
    files: int
    simulated: bool = False


@dataclass(frozen=True)
class PullRequestResult:
    number: int | None
    url: str | None
    source_branch: str
    target_branch: str
    simulated: bool = False


class GitAdapter(ABC):
    """One concrete subclass per provider (§23.6)."""

    provider: str = "generic"

    def __init__(self, repository_url: str, *, credential: str | None = None) -> None:
        self.repository_url = repository_url.rstrip("/")
        self._credential = credential  # a resolved token, or None -> caller should use SimulatedAdapter

    # -- rules enforced for every provider -------------------------------

    def _guard_branches(self, feature_branch: str, base_branch: str) -> None:
        feature = (feature_branch or "").strip()
        base = (base_branch or "dev").strip()
        if not feature:
            raise GitOpsError("A feature branch name is required.")
        if feature == base:
            raise ProtectedBranchError(
                f"Refusing to push to the protected base branch '{base}'. Push to a feature branch and open a PR."
            )

    # -- provider operations -------------------------------------------

    @abstractmethod
    def push_artifacts(
        self,
        *,
        feature_branch: str,
        base_branch: str,
        files: list[ArtifactBlob],
        commit_message: str,
        deletes: list[str] | None = None,
    ) -> PushResult:
        """Create/update ``feature_branch`` off ``base_branch`` and commit ``files`` atomically.

        ``deletes`` (repo-relative paths) are removed in the same commit — the
        rename/delete PRs of §24.10."""

    @abstractmethod
    def list_files(self, *, branch: str, prefix: str) -> list[RepoFile]:
        """Every catalog candidate under ``prefix`` on ``branch`` with its blob sha + text
        (bounded by ``MAX_CATALOG_FILES``/``MAX_CATALOG_FILE_BYTES``)."""

    @abstractmethod
    def file_sha(self, *, path: str, ref: str) -> str | None:
        """Current blob sha of ``path`` on ``ref``, or None when the file doesn't exist."""

    @abstractmethod
    def open_pull_request(
        self,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        body: str,
        reviewers: list[str] | None = None,
    ) -> PullRequestResult:
        """Open a PR ``source_branch`` -> ``target_branch`` (never the reverse)."""


def get_adapter(
    provider: str,
    repository_url: str,
    *,
    credential_vault: str | None = None,
    direct_token: str | None = None,
) -> GitAdapter:
    # A per-push token (never persisted) wins over the configured reference.
    from .credential_vault import resolve_credential

    token = (direct_token or "").strip() or resolve_credential(credential_vault)
    if not token:
        from .simulated import SimulatedAdapter

        # This is the fallback that used to be silent (§23.12 gap #14): no
        # credential resolved, so the "push" is about to be entirely fake.
        # ``PushResult.simulated``/``PullRequestResult.simulated`` (already
        # True from SimulatedAdapter) now get persisted and surfaced on
        # GitPushOut — this log line is the operator-facing half of the fix.
        logger.warning(
            "No Git credential resolved for provider=%s repository=%s (credential_vault=%s); "
            "using the offline SimulatedAdapter instead of a real push.",
            provider,
            repository_url,
            credential_vault,
        )
        return SimulatedAdapter(repository_url, provider=provider)

    if provider == "gitlab":
        from .gitlab import GitLabAdapter

        return GitLabAdapter(repository_url, credential=token)
    if provider in ("github", "custom"):
        from .github import GitHubAdapter

        return GitHubAdapter(repository_url, credential=token)
    if provider in ("azure_repos",):
        from .azure_repos import AzureReposAdapter

        return AzureReposAdapter(repository_url, credential=token)

    from .simulated import SimulatedAdapter

    return SimulatedAdapter(repository_url, provider=provider)
