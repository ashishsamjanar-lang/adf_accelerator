"""Connector catalog endpoints (AGENTS.md §14, §23.4b).

``GET /api/v1/connectors``          — registry summary + category groups
``GET /api/v1/connectors/groups``   — just the grouped picker lists
``GET /api/v1/connectors/{type}``   — full schema for one connector

Read-only; the registry is static metadata. Adding a connector means adding a
:class:`~app.connectors.schemas.ConnectorSchema`, never a new endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.connectors import (
    ALL_CONNECTORS,
    CONNECTOR_SCHEMAS,
    REGISTRY_VERSION,
    connector_schema,
    registry_fingerprint,
    registry_payload,
    schema_to_dict,
)
from app.connectors.templates import (
    build_linked_service_template,
    build_source_objects_template,
    parse_linked_service_file,
    parse_tables_file,
)
from app.errors import ApiError

from .models import (
    ConnectorDetail,
    ConnectorGroup,
    ConnectorRegistry,
    ParseFileIn,
    ParseLinkedServiceOut,
    ParsedLinkedServiceValuesModel,
    ParseSourceObjectsOut,
)

# No RBAC gate here on purpose: the registry (and the template/parse helpers
# below, which only read it) is static, non-tenant metadata with no secrets
# (§11's "View" row doesn't apply — there is nothing team- or project-scoped
# to protect), and gating it behind `current_user` would make a read-only,
# DB-independent endpoint fail whenever the metastore is unreachable, which
# defeats the point of it being static.
router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.get("", response_model=ConnectorRegistry)
def list_connectors() -> ConnectorRegistry:
    payload = registry_payload()
    return ConnectorRegistry(
        count=payload["count"],
        version=REGISTRY_VERSION,
        fingerprint=registry_fingerprint(),
        connectors=payload["connectors"],
        groups=payload["groups"],
    )


@router.get("/groups", response_model=list[ConnectorGroup])
def list_connector_groups() -> list[ConnectorGroup]:
    return [ConnectorGroup(**g) for g in registry_payload()["groups"]]


# --- Upload-file templates + parse (§24.17, §26) --------------------------
#
# Declared before `/{connector_type}` (below) so these literal/multi-segment
# paths are never shadowed by that catch-all — same reasoning as `/groups`
# above. Kept here rather than under `/api/v1/connections` because, like the
# rest of this router, they only read the static registry: no project/team,
# no RBAC, no DB round trip. The frontend already builds/parses these
# client-side for zero-latency downloads and instant upload previews
# (`lib/adf/connections.ts`) — these endpoints exist for parity and for any
# other caller (a future worker-side onboarding import job, an external
# script), not to replace that client-side path.


@router.get("/templates/source-objects", response_class=Response)
def source_objects_template() -> Response:
    content = build_source_objects_template()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="source_objects_template.csv"'},
    )


@router.post("/templates/source-objects/parse", response_model=ParseSourceObjectsOut)
def parse_source_objects(body: ParseFileIn) -> ParseSourceObjectsOut:
    parsed = parse_tables_file(body.content, body.fileName)
    return ParseSourceObjectsOut(objects=parsed.objects, warnings=parsed.warnings)


@router.post("/linked-service/parse", response_model=ParseLinkedServiceOut)
def parse_linked_service(body: ParseFileIn) -> ParseLinkedServiceOut:
    parsed = parse_linked_service_file(body.content, body.fileName)
    values = [
        ParsedLinkedServiceValuesModel(
            name=v.name,
            connectorType=v.connector_type,
            authType=v.auth_type,
            integrationRuntime=v.integration_runtime,
            keyVaultLinkedService=v.key_vault_linked_service,
            fields=v.fields,
        )
        for v in parsed.values
    ]
    return ParseLinkedServiceOut(values=values, warnings=parsed.warnings)


@router.get("/{connector_type}", response_model=ConnectorDetail)
def get_connector(connector_type: str) -> ConnectorDetail:
    if connector_type not in CONNECTOR_SCHEMAS:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "connector_not_found",
            f"Unknown connector '{connector_type}'.",
            f"Known connectors: {', '.join(ALL_CONNECTORS)}",
        )
    return ConnectorDetail(**schema_to_dict(CONNECTOR_SCHEMAS[connector_type]))


@router.get("/{connector_type}/linked-service-template", response_class=Response)
def linked_service_template(connector_type: str, auth_type: str | None = None) -> Response:
    schema = connector_schema(connector_type)
    if connector_type not in CONNECTOR_SCHEMAS:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "connector_not_found",
            f"Unknown connector '{connector_type}'.",
            f"Known connectors: {', '.join(ALL_CONNECTORS)}",
        )
    resolved_auth = auth_type or schema.default_auth
    content = build_linked_service_template(connector_type, resolved_auth)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="linked_service_{connector_type}_template.csv"'
        },
    )
