"""Connector catalog (API layer).

Serves the metadata-driven connector registry to the frontend dynamic form
renderer (AGENTS.md §14 ``GET /api/v1/connectors``, §23.4b). The JSON
linked-service / dataset projection is the worker's job (§23.4); this package
carries the schema catalog only.

:mod:`.schemas` is a vendored copy of ``worker/app/connectors/schemas.py`` —
see that file's header.
"""
from __future__ import annotations

from .schemas import (
    ALL_CONNECTORS,
    CONNECTOR_GROUPS,
    CONNECTOR_SCHEMAS,
    DATABASE_CONNECTORS,
    FILE_CONNECTORS,
    LLM_CONNECTORS,
    REGISTRY_VERSION,
    SERVICE_CONNECTORS,
    ConnectorSchema,
    connector_category,
    connector_schema,
    registry_fingerprint,
)
from .serialize import registry_payload, schema_to_dict, summary_to_dict

__all__ = [
    "ALL_CONNECTORS",
    "CONNECTOR_GROUPS",
    "CONNECTOR_SCHEMAS",
    "DATABASE_CONNECTORS",
    "FILE_CONNECTORS",
    "LLM_CONNECTORS",
    "REGISTRY_VERSION",
    "SERVICE_CONNECTORS",
    "ConnectorSchema",
    "connector_category",
    "connector_schema",
    "registry_fingerprint",
    "registry_payload",
    "schema_to_dict",
    "summary_to_dict",
]
