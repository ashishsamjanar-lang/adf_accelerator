"""Pydantic response models for the connector catalog API.

Field names are camelCase on purpose: they are a boundary DTO that must match
the ``ConnectorSchema`` shape consumed by ``frontend/lib/adf/connections.ts``.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AuthOption(BaseModel):
    id: str
    label: str
    description: str | None = None


class FieldOption(BaseModel):
    label: str
    value: str


class ConnectionFieldModel(BaseModel):
    key: str
    label: str
    type: str
    required: bool | None = None
    requiredFor: list[str] | None = None
    placeholder: str | None = None
    help: str | None = None
    options: list[FieldOption] | None = None
    isSecret: bool | None = None


class ConnectorSummary(BaseModel):
    id: str
    displayName: str
    adfType: str
    datasetType: str
    kind: str
    category: str
    defaultAuth: str
    description: str | None = None


class ConnectorDetail(ConnectorSummary):
    datasetLocationType: str | None = None
    authOptions: list[AuthOption]
    fields: list[ConnectionFieldModel]


class ConnectorGroup(BaseModel):
    category: str
    connectors: list[str]


class ConnectorRegistry(BaseModel):
    count: int
    version: str
    fingerprint: str
    connectors: list[ConnectorSummary]
    groups: list[ConnectorGroup]


# --- Upload-file templates + parse (§24.17, §26) --------------------------
#
# Boundary DTOs for the "Upload file" round trip on source objects and
# linked services — see `app.connectors.templates` (ported from
# `frontend/lib/adf/connections.ts`).


class ParseFileIn(BaseModel):
    content: str
    fileName: str


class ParsedSourceObject(BaseModel):
    object: str
    catalog: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    qualifierOverride: str | None = None
    caseOverride: str | None = None
    watermarkColumn: str | None = None
    loadingBehavior: str | None = None

    model_config = {"populate_by_name": True}


class ParseSourceObjectsOut(BaseModel):
    objects: list[ParsedSourceObject]
    warnings: list[str]


class ParsedLinkedServiceValuesModel(BaseModel):
    name: str | None = None
    connectorType: str | None = None
    authType: str | None = None
    integrationRuntime: str | None = None
    keyVaultLinkedService: str | None = None
    fields: dict[str, str] = {}


class ParseLinkedServiceOut(BaseModel):
    values: list[ParsedLinkedServiceValuesModel]
    warnings: list[str]
