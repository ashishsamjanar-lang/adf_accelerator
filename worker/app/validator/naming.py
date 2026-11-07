"""Naming-standard evaluator (AGENTS.md §25.3 "naming" category, §25.6, §23.9/§23.11 pre-validate).

**Vendored copy** — mirrors `frontend/lib/adf/naming.ts` (the reference
implementation) and `backend/app/standards.py`; keep all three in parity, the
same way the generator's three copies are kept in parity (DEVELOPMENT_NOTES.md).

Scope: the enforceable subset of §25.3 — prefix, suffix, case and max length
per artifact type, opt-in via `NamingConvention.enforcement`
(off/warn/block, default "off" — §25.1). Feeds `validate_artifacts` in
`rules.py`: a "block" rule fails validation (severity "error", same as the
always-on ADF-name rule); a "warn" rule surfaces as severity "warning".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Level = Literal["off", "warn", "block"]

# Maps the generator's snake_case `artifact_type` to the camelCase key naming
# conventions are stored under (the frontend/JSON convention document).
ARTIFACT_TYPE_KEYS: dict[str, str] = {
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
    enforcement is "off" (§25.1 default-permissive contract).
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
    )
