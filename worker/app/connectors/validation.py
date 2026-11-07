"""Linked-service / dataset draft validation.

Port of ``validateLinkedServiceDraft`` / ``validateDatasetDraft`` from
``frontend/lib/adf/connections.ts``. The worker's validator runs these before
generation so a bad connection is caught the same way the UI catches it
(AGENTS.md §17, §23.11).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .dataset import DatasetDefaults, dataset_name
from .drafts import DatasetDraft, LinkedServiceDraft
from .linked_service import _raw
from .schemas import connector_schema, required_fields, uses_secret_fields

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,63}$")

Severity = Literal["error", "warning", "info"]


@dataclass
class ValidationIssue:
    severity: Severity
    message: str
    field: str | None = None


def validate_linked_service_draft(ls: LinkedServiceDraft, field: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    schema = connector_schema(ls.connector_type)
    label = ls.name.strip() or schema.display_name

    if not ls.name.strip():
        issues.append(ValidationIssue("error", f"Linked service name is required ({schema.display_name}).", field))
    elif not _NAME_RE.match(ls.name.strip()):
        issues.append(ValidationIssue(
            "error",
            f'Linked service name "{ls.name}" must start with a letter and contain only '
            "letters, digits, and underscores (2–64 chars).",
            field,
        ))

    if not ls.integration_runtime.strip():
        issues.append(ValidationIssue("error", f'Integration runtime is required for "{label}".', field))

    for req in required_fields(schema, ls.auth_type):
        if not _raw(ls, req):
            issues.append(ValidationIssue(
                "error",
                f'{schema.display_name} linked service "{label}": "{req.label}" is required for '
                f"{ls.auth_type.replace('_', ' ')}.",
                field,
            ))

    if uses_secret_fields(schema, ls.auth_type) and not ls.key_vault_linked_service.strip():
        issues.append(ValidationIssue(
            "error",
            f'A Key Vault linked service is required because "{label}" references secret names.',
            field,
        ))
    if ls.identity_kind == "user" and not ls.credential_name.strip():
        issues.append(ValidationIssue(
            "error",
            f'"{label}" uses a user-assigned managed identity — pick a registered credential.',
            field,
        ))
    return issues


def validate_dataset_draft(
    ds: DatasetDraft, ls: LinkedServiceDraft, defaults: DatasetDefaults, field: str
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    schema = connector_schema(ls.connector_type)
    name = dataset_name(ds, defaults)

    if not _NAME_RE.match(name):
        issues.append(ValidationIssue(
            "error",
            f'Dataset name "{name or "(empty)"}" must start with a letter and contain only '
            "letters, digits, and underscores (2–64 chars).",
            field,
        ))
    if not ds.linked_service_name.strip():
        issues.append(ValidationIssue("error", f'Dataset "{name}" must reference a linked service.', field))

    if schema.kind == "file":
        if not (ds.folder_path.strip() or defaults.folder_path):
            issues.append(ValidationIssue("error", f'Dataset "{name}": folder path is required.', field))
        if not (ds.file_name.strip() or defaults.file_name):
            issues.append(ValidationIssue("error", f'Dataset "{name}": file name is required.', field))
    elif schema.kind in ("service", "llm"):
        if not (ds.table.strip() or ds.folder_path.strip() or defaults.table):
            issues.append(ValidationIssue(
                "warning",
                f'Dataset "{name}": no object / relative URL set — it must come from the control table.',
                field,
            ))
    elif not (ds.schema.strip() or defaults.schema) or not (ds.table.strip() or defaults.table):
        issues.append(ValidationIssue("error", f'Dataset "{name}": schema and table are required.', field))
    return issues
