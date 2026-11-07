"""Validation layer (AGENTS.md §17, §23.4, §23.9, §23.11).

Runs the §17 pre-push rules plus a hardcoded-secret scan over generated
artifacts. Pure functions — no I/O.

* :func:`validate_spec`      — spec/template/metadata rules (§17.1–4);
* :func:`validate_artifacts` — JSON validity, naming, secret scan (§17.5–6);
* :func:`validate_git`       — repo configured, feature != base branch (§17.7–8);
* :func:`run_validation`     — all of the above -> :class:`ValidationReport`.
"""
from __future__ import annotations

from .naming import NamingViolation, evaluate_name
from .rules import (
    SECRET_PATTERNS,
    ValidationReport,
    scan_for_secrets,
    run_validation,
    validate_artifacts,
    validate_git,
    validate_spec,
)

__all__ = [
    "SECRET_PATTERNS",
    "NamingViolation",
    "ValidationReport",
    "evaluate_name",
    "scan_for_secrets",
    "run_validation",
    "validate_artifacts",
    "validate_git",
    "validate_spec",
]
