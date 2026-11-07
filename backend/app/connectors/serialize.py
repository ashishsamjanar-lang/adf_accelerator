"""Serialize connector schemas to the frontend ``ConnectorSchema`` JSON shape.

The keys here match ``frontend/lib/adf/connections.ts`` exactly (camelCase) so
the API response drops straight into the existing dynamic form renderer.
"""
from __future__ import annotations

from typing import Any

from .schemas import (
    ALL_CONNECTORS,
    CONNECTOR_GROUPS,
    ConnectionField,
    ConnectorSchema,
    connector_category,
    connector_schema,
)


def field_to_dict(f: ConnectionField) -> dict[str, Any]:
    out: dict[str, Any] = {"key": f.key, "label": f.label, "type": f.type}
    if f.required:
        out["required"] = True
    if f.required_for:
        out["requiredFor"] = list(f.required_for)
    if f.placeholder:
        out["placeholder"] = f.placeholder
    if f.help:
        out["help"] = f.help
    if f.options:
        out["options"] = [{"label": label, "value": value} for label, value in f.options]
    if f.is_secret:
        out["isSecret"] = True
    return out


def schema_to_dict(s: ConnectorSchema) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": s.id,
        "displayName": s.display_name,
        "adfType": s.adf_type,
        "datasetType": s.dataset_type,
        "kind": s.kind,
        "category": s.category or connector_category(s.id),
        "defaultAuth": s.default_auth,
        "authOptions": [
            {"id": a.id, "label": a.label, **({"description": a.description} if a.description else {})}
            for a in s.auth_options
        ],
        "fields": [field_to_dict(f) for f in s.fields],
    }
    if s.dataset_location_type:
        out["datasetLocationType"] = s.dataset_location_type
    if s.description:
        out["description"] = s.description
    return out


def summary_to_dict(connector_type: str) -> dict[str, Any]:
    s = connector_schema(connector_type)
    return {
        "id": s.id,
        "displayName": s.display_name,
        "adfType": s.adf_type,
        "datasetType": s.dataset_type,
        "kind": s.kind,
        "category": connector_category(connector_type),
        "defaultAuth": s.default_auth,
        "description": s.description,
    }


def registry_payload() -> dict[str, Any]:
    """The full ``GET /connectors`` body: summaries + category groups."""
    return {
        "count": len(ALL_CONNECTORS),
        "connectors": [summary_to_dict(k) for k in ALL_CONNECTORS],
        "groups": [
            {"category": g["category"], "connectors": list(g["connectors"])}
            for g in CONNECTOR_GROUPS
        ],
    }
