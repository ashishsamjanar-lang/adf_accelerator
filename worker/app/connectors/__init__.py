"""Metadata-driven connector engine (worker layer).

Canonical Python port of ``frontend/lib/adf/connections.ts``:

* :mod:`.schemas`         — the connector registry + derived groupings;
* :mod:`.drafts`          — editable linked-service / dataset state;
* :mod:`.linked_service`  — linked-service JSON projection (KV secret refs);
* :mod:`.dataset`         — dataset JSON projection (parameterized objects);
* :mod:`.validation`      — draft validation (§17 rules);
* :mod:`.serialize`       — the ``ConnectorSchema`` JSON shape the API serves.

The backend vendors a byte-identical copy of :mod:`.schemas`; the
``REGISTRY_FINGERPRINT`` test in each tree guards against drift.
"""
from __future__ import annotations

from .dataset import DatasetDefaults, build_dataset_properties, dataset_name
from .drafts import (
    DEFAULT_INTEGRATION_RUNTIME,
    DEFAULT_KEY_VAULT_LINKED_SERVICE,
    DatasetDraft,
    DatasetFormatSettings,
    LinkedServiceDraft,
    default_dataset_draft,
    default_linked_service_draft,
)
from .linked_service import build_linked_service_properties, key_vault_secret
from .schemas import (
    ALL_CONNECTORS,
    CONNECTOR_GROUPS,
    CONNECTOR_SCHEMAS,
    DATABASE_CONNECTORS,
    FILE_CONNECTORS,
    GENERIC_SCHEMA,
    LLM_CONNECTORS,
    REGISTRY_VERSION,
    SERVICE_CONNECTORS,
    ConnectionField,
    ConnectorAuthOption,
    ConnectorSchema,
    connector_category,
    connector_schema,
    dataset_adf_type,
    is_file_connector,
    linked_service_adf_type,
    registry_fingerprint,
    required_fields,
    uses_secret_fields,
    visible_fields,
)
from .serialize import registry_payload, schema_to_dict, summary_to_dict
from .validation import ValidationIssue, validate_dataset_draft, validate_linked_service_draft

__all__ = [
    "ALL_CONNECTORS",
    "CONNECTOR_GROUPS",
    "CONNECTOR_SCHEMAS",
    "DATABASE_CONNECTORS",
    "DEFAULT_INTEGRATION_RUNTIME",
    "DEFAULT_KEY_VAULT_LINKED_SERVICE",
    "FILE_CONNECTORS",
    "GENERIC_SCHEMA",
    "LLM_CONNECTORS",
    "REGISTRY_VERSION",
    "SERVICE_CONNECTORS",
    "ConnectionField",
    "ConnectorAuthOption",
    "ConnectorSchema",
    "DatasetDefaults",
    "DatasetDraft",
    "DatasetFormatSettings",
    "LinkedServiceDraft",
    "ValidationIssue",
    "build_dataset_properties",
    "build_linked_service_properties",
    "connector_category",
    "connector_schema",
    "dataset_adf_type",
    "dataset_name",
    "default_dataset_draft",
    "default_linked_service_draft",
    "is_file_connector",
    "key_vault_secret",
    "linked_service_adf_type",
    "registry_fingerprint",
    "registry_payload",
    "required_fields",
    "schema_to_dict",
    "summary_to_dict",
    "uses_secret_fields",
    "validate_dataset_draft",
    "validate_linked_service_draft",
    "visible_fields",
]
