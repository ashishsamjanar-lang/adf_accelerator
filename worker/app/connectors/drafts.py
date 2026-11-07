"""Editable linked-service / dataset state (the worker's job payload shape).

Mirrors ``LinkedServiceDraft`` / ``DatasetDraft`` in
``frontend/lib/adf/connections.ts``. Every value comes from the user
(prefilled from Project Settings where configured); nothing is hard-coded at
build time (AGENTS.md §1.5). Secret fields hold the Key Vault secret *name*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import connector_schema

DEFAULT_INTEGRATION_RUNTIME = "AutoResolveIntegrationRuntime"
DEFAULT_KEY_VAULT_LINKED_SERVICE = "LS_azure_key_vault"


@dataclass
class DatasetFormatSettings:
    column_delimiter: str = ","
    row_delimiter: str = "\n"
    quote_char: str = '"'
    # Single backslash — matches frontend/lib/adf/connections.ts `escapeChar: "\\"`. That
    # side used to read `"\\\\"` (a real two-backslash string, a typo — this port's
    # comment claimed parity that didn't exist) until the 2026-09-13 generator-parity
    # test caught it; both sides now genuinely agree, and this is ADF TextFormat's own
    # documented default.
    escape_char: str = "\\"
    first_row_as_header: bool = True
    encoding: str = "utf-8"
    compression: str = "none"  # none | gzip | bzip2 | deflate


@dataclass
class LinkedServiceDraft:
    name: str
    connector_type: str
    auth_type: str
    integration_runtime: str = DEFAULT_INTEGRATION_RUNTIME
    key_vault_linked_service: str = DEFAULT_KEY_VAULT_LINKED_SERVICE
    fields: dict[str, str] = field(default_factory=dict)
    parameterize: list[str] = field(default_factory=list)
    # §32.6 — "system" (default, today's behavior, no extra resource) or "user" (references a
    # `credential` artifact by name). `credential_resource_id` is the UAMI's ARM resource ID; it is
    # carried directly on the draft (rather than resolved server-side from the `managed_identities`
    # table, §32.6/MI3) so the generator stays a pure function of its input, per §23.13's "generator
    # never queries app.* directly" principle already established for §33's registry refs. A future
    # caller (e.g. the Build wizard once MI3's picker lands) resolves a chosen credential name against
    # that table and fills these two fields before generation — no such caller exists yet.
    identity_kind: str = "system"
    credential_name: str = ""
    credential_resource_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LinkedServiceDraft":
        return cls(
            name=str(data.get("name", "")),
            connector_type=str(data.get("connectorType") or data.get("connector_type") or ""),
            auth_type=str(data.get("authType") or data.get("auth_type") or ""),
            integration_runtime=str(
                data.get("integrationRuntime") or data.get("integration_runtime") or DEFAULT_INTEGRATION_RUNTIME
            ),
            key_vault_linked_service=str(
                data.get("keyVaultLinkedService")
                or data.get("key_vault_linked_service")
                or DEFAULT_KEY_VAULT_LINKED_SERVICE
            ),
            fields={str(k): str(v) for k, v in (data.get("fields") or {}).items()},
            parameterize=[str(x) for x in (data.get("parameterize") or [])],
            identity_kind=str(data.get("identityKind") or data.get("identity_kind") or "system"),
            credential_name=str(data.get("credentialName") or data.get("credential_name") or ""),
            credential_resource_id=str(
                data.get("credentialResourceId") or data.get("credential_resource_id") or ""
            ),
        )


@dataclass
class DatasetDraft:
    name: str = ""
    linked_service_name: str = ""
    schema: str = ""
    table: str = ""
    folder_path: str = ""
    file_name: str = ""
    parameterized: bool = True
    format: DatasetFormatSettings = field(default_factory=DatasetFormatSettings)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatasetDraft":
        fmt = data.get("format") or {}
        return cls(
            name=str(data.get("name", "")),
            linked_service_name=str(data.get("linkedServiceName") or data.get("linked_service_name") or ""),
            schema=str(data.get("schema", "")),
            table=str(data.get("table", "")),
            folder_path=str(data.get("folderPath") or data.get("folder_path") or ""),
            file_name=str(data.get("fileName") or data.get("file_name") or ""),
            parameterized=bool(data.get("parameterized", True)),
            format=DatasetFormatSettings(
                column_delimiter=str(fmt.get("columnDelimiter", fmt.get("column_delimiter", ","))),
                row_delimiter=str(fmt.get("rowDelimiter", fmt.get("row_delimiter", "\n"))),
                quote_char=str(fmt.get("quoteChar", fmt.get("quote_char", '"'))),
                escape_char=str(fmt.get("escapeChar", fmt.get("escape_char", "\\"))),
                first_row_as_header=bool(fmt.get("firstRowAsHeader", fmt.get("first_row_as_header", True))),
                encoding=str(fmt.get("encoding", "utf-8")),
                compression=str(fmt.get("compression", "none")),
            ),
        )


def default_linked_service_draft(kind: str, connector_type: str) -> LinkedServiceDraft:
    """Create a linked-service draft prefilled from the connector registry."""
    schema = connector_schema(connector_type)
    return LinkedServiceDraft(
        name=f"LS_{connector_type}_{kind}",
        connector_type=connector_type,
        auth_type=schema.default_auth,
        integration_runtime=DEFAULT_INTEGRATION_RUNTIME,
        key_vault_linked_service=DEFAULT_KEY_VAULT_LINKED_SERVICE,
        fields={},
        parameterize=[],
    )


def default_dataset_draft(kind: str, connector_type: str) -> DatasetDraft:
    return DatasetDraft(parameterized=True, format=DatasetFormatSettings())
