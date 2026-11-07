"""ADF dataset JSON projection.

Port of ``buildDatasetProperties`` / ``datasetName`` from
``frontend/lib/adf/connections.ts``. Parameterized datasets expose
SchemaName/TableName (relational), FolderPath/FileName (file) or RelativeUrl
(REST / SaaS / LLM) as dataset parameters — the metadata-driven pattern feeds
them from the control table at runtime (AGENTS.md §2, §23.4a).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .drafts import DatasetDraft, DatasetFormatSettings, LinkedServiceDraft
from .schemas import connector_schema

Params = dict[str, dict[str, Any]]


@dataclass
class DatasetDefaults:
    schema: str = ""
    table: str = ""
    folder_path: str = ""
    file_name: str = ""
    fallback_name: str = "DS_connection"


def format_properties(fmt: DatasetFormatSettings) -> dict[str, Any]:
    props: dict[str, Any] = {
        "columnDelimiter": fmt.column_delimiter,
        "rowDelimiter": fmt.row_delimiter,
        "quoteChar": fmt.quote_char,
        "escapeChar": fmt.escape_char,
        "encoding": fmt.encoding or "utf-8",
        "firstRowAsHeader": fmt.first_row_as_header,
    }
    if fmt.compression != "none":
        props["compressionCodec"] = fmt.compression
    return props


def build_dataset_properties(
    ds: DatasetDraft, ls: LinkedServiceDraft, defaults: DatasetDefaults
) -> dict[str, Any]:
    schema = connector_schema(ls.connector_type)
    parameters: Params = {}
    props: dict[str, Any] = {
        "linkedServiceName": {
            "referenceName": ls.name or ds.linked_service_name,
            "type": "LinkedServiceReference",
        },
        "parameters": parameters,
        "annotations": [],
    }

    if schema.kind == "file":
        props["type"] = schema.dataset_type
        props["format"] = format_properties(ds.format)
        folder_value = ds.folder_path.strip() or defaults.folder_path
        file_value = ds.file_name.strip() or defaults.file_name
        location_type = schema.dataset_location_type or "AzureBlobFSLocation"
        if ds.parameterized:
            parameters["FolderPath"] = {"type": "String", "defaultValue": folder_value}
            parameters["FileName"] = {"type": "String", "defaultValue": file_value}
            props["location"] = {
                "type": location_type,
                "folderPath": "@{dataset().FolderPath}",
                "fileName": "@{dataset().FileName}",
            }
        else:
            props["location"] = {
                "type": location_type,
                "folderPath": folder_value,
                "fileName": file_value,
            }
    elif schema.kind in ("service", "llm"):
        # REST / SaaS / LLM: an object-name (relativeUrl) dataset, no SQL shape.
        props["type"] = schema.dataset_type
        object_value = ds.table.strip() or ds.folder_path.strip() or defaults.table
        if ds.parameterized:
            parameters["RelativeUrl"] = {"type": "String", "defaultValue": object_value}
            props["relativeUrl"] = "@{dataset().RelativeUrl}"
        elif object_value:
            props["relativeUrl"] = object_value
    else:
        props["type"] = schema.dataset_type
        schema_value = ds.schema.strip() or defaults.schema
        table_value = ds.table.strip() or defaults.table
        if ds.parameterized:
            parameters["SchemaName"] = {"type": "String", "defaultValue": schema_value}
            parameters["TableName"] = {"type": "String", "defaultValue": table_value}
            schema_value, table_value = "@{dataset().SchemaName}", "@{dataset().TableName}"
        # RUN-07: ADF reads a table dataset's schema/table from typeProperties (properties.schema is
        # the column-structure array), and has no sqlTableType property.
        props["typeProperties"] = {"schema": schema_value, "table": table_value}
    return props


def dataset_name(ds: DatasetDraft, defaults: DatasetDefaults) -> str:
    return ds.name.strip() or defaults.fallback_name
