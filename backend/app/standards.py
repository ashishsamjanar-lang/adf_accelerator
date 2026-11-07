"""Naming-standard evaluator (AGENTS.md §25.3 "naming" category, §25.6 evaluation points).

**Vendored copy** — this mirrors `frontend/lib/adf/naming.ts` (the reference
implementation) and `worker/app/validator/naming.py` (the worker's copy);
keep all three in parity, the same way the generator's three copies are kept
in parity (see DEVELOPMENT_NOTES.md).

Scope: the enforceable subset of §25.3 — prefix, suffix, case and max length
per artifact type, opt-in via `NamingConvention.enforcement` (off/warn/block,
default "off" — §25.1). The backend's only use of this module today is the
pre-push hard gate (§25.6): `services.request_git_push` rejects a push with
`422 policy_violation` when any generated artifact's declared name fails a
rule set to "block". Pure functions — no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Level = Literal["off", "warn", "block"]
ArtifactType = Literal["pipeline", "dataset", "linkedService", "trigger"]

# Maps the worker/backend's snake_case `Artifact.artifact_type` column to the
# camelCase key naming conventions are stored under (frontend/JSON convention).
ARTIFACT_TYPE_KEYS: dict[str, ArtifactType] = {
    "pipeline": "pipeline",
    "dataset": "dataset",
    "linked_service": "linkedService",
    "trigger": "trigger",
}

_CASE_PATTERNS: dict[str, re.Pattern[str]] = {
    "snake": re.compile(r"^[a-z][a-z0-9_]*$"),
    "pascal": re.compile(r"^[A-Z][A-Za-z0-9]*$"),
    "camel": re.compile(r"^[a-z][A-Za-z0-9]*$"),
    "upper": re.compile(r"^[A-Z][A-Z0-9_]*$"),
    "lower": re.compile(r"^[a-z][a-z0-9]*$"),
}


@dataclass
class NamingViolation:
    level: Level
    message: str
    artifact_type: str
    name: str


def _rule_for(convention: dict[str, Any] | None, artifact_type: str) -> dict[str, Any] | None:
    if not convention:
        return None
    key = ARTIFACT_TYPE_KEYS.get(artifact_type, artifact_type)
    return (convention.get("rules") or {}).get(key)


def is_configured(rule: dict[str, Any] | None) -> bool:
    if not rule:
        return False
    return bool(rule.get("prefix") or rule.get("suffix") or rule.get("maxLength") or (rule.get("case") not in (None, "none")))


def build_expected_name(base: str, rule: dict[str, Any] | None) -> str:
    rule = rule or {}
    return f"{rule.get('prefix') or ''}{base}{rule.get('suffix') or ''}"


def evaluate_name(name: str, artifact_type: str, convention: dict[str, Any] | None) -> NamingViolation | None:
    """Evaluate one declared artifact name against the project's naming convention.

    Returns ``None`` when compliant, no rule is configured for the type, or
    enforcement is "off" — i.e. "nothing to report", matching the §25.1
    default-permissive contract (never "definitely valid").
    """
    enforcement: Level = (convention or {}).get("enforcement") or "off"
    if enforcement == "off":
        return None
    rule = _rule_for(convention, artifact_type)
    if not is_configured(rule):
        return None

    trimmed = (name or "").strip()
    if not trimmed:
        return None

    prefix = (rule or {}).get("prefix") or ""
    suffix = (rule or {}).get("suffix") or ""
    max_length = (rule or {}).get("maxLength")
    case = (rule or {}).get("case")

    problems: list[str] = []
    if prefix and not trimmed.startswith(prefix):
        problems.append(f'must start with "{prefix}"')
    if suffix and not trimmed.endswith(suffix):
        problems.append(f'must end with "{suffix}"')
    if max_length and len(trimmed) > max_length:
        problems.append(f"must be at most {max_length} characters")
    if case and case != "none":
        body_end = len(trimmed) - len(suffix) if suffix and trimmed.endswith(suffix) else len(trimmed)
        body = trimmed[len(prefix) : max(len(prefix), body_end)]
        pattern = _CASE_PATTERNS.get(case)
        if body and pattern and not pattern.match(body):
            problems.append(f"must be {case} case")

    if not problems:
        return None
    return NamingViolation(
        level="block" if enforcement == "block" else "warn",
        message=f'"{trimmed}" {", ".join(problems)} — expected something like "{build_expected_name("name", rule)}".',
        artifact_type=artifact_type,
        name=trimmed,
    )


def evaluate_artifact_names(
    artifacts: list[tuple[str, str]], convention: dict[str, Any] | None
) -> list[NamingViolation]:
    """Evaluate a batch of (artifact_type, declared_name) pairs. Convenience wrapper
    for callers (e.g. the pre-push gate) that already have the artifact set in hand."""
    violations: list[NamingViolation] = []
    for artifact_type, name in artifacts:
        violation = evaluate_name(name, artifact_type, convention)
        if violation:
            violations.append(violation)
    return violations
