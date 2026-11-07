"""Worker job intake (AGENTS.md §23.4).

The stateless worker polls the shared ``jobs`` table, claims one row at a time
(``FOR UPDATE SKIP LOCKED``), dispatches by ``type``, and records the result.
Job payloads carry the spec/draft + template id — never mutable UI state.

* :mod:`.runner`  — pure-ish dispatch: ``generate`` / ``validate`` / ``git_push``
  / ``onboarding_sync`` / ``live_check`` / ``purview_push``
  / ``catalog_refresh`` / ``catalog_change`` (:mod:`.catalog`);
* :mod:`.loop`    — the claim/execute/record poll loop.
"""
from __future__ import annotations

JOB_GENERATE = "generate"
JOB_VALIDATE = "validate"
JOB_GIT_PUSH = "git_push"
JOB_ONBOARDING_SYNC = "onboarding_sync"  # §26.5 registry → feature branch → PR
JOB_LIVE_CHECK = "live_check"  # §29.1 layer 6, ARM validate-mode (never deploys)
JOB_PURVIEW_PUSH = "purview_push"  # registry lineage → Microsoft Purview (Atlas)
JOB_CATALOG_REFRESH = "catalog_refresh"  # §24.8 read the repo → git_catalog_entries
JOB_CATALOG_CHANGE = "catalog_change"  # §24.9/§24.10 raw edit / rename / delete → PR

JOB_TYPES = (
    JOB_GENERATE,
    JOB_VALIDATE,
    JOB_GIT_PUSH,
    JOB_ONBOARDING_SYNC,
    JOB_LIVE_CHECK,
    JOB_PURVIEW_PUSH,
    JOB_CATALOG_REFRESH,
    JOB_CATALOG_CHANGE,
)

from .loop import run_once, run_forever  # noqa: E402
from .runner import JobError, dispatch  # noqa: E402

__all__ = [
    "JOB_GENERATE",
    "JOB_VALIDATE",
    "JOB_GIT_PUSH",
    "JOB_ONBOARDING_SYNC",
    "JOB_LIVE_CHECK",
    "JOB_PURVIEW_PUSH",
    "JOB_CATALOG_REFRESH",
    "JOB_CATALOG_CHANGE",
    "JOB_TYPES",
    "JobError",
    "dispatch",
    "run_once",
    "run_forever",
]
