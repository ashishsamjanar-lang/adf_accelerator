"""Downloadable-template + upload-parse helpers for source objects and linked
services (AGENTS.md §24.17, §26).

Ported from ``frontend/lib/adf/connections.ts``'s ``buildSourceObjectsTemplate``
/ ``parseTablesFile`` / ``buildLinkedServiceTemplate`` / ``parseLinkedServiceFile``
so the CSV shape and parsing rules are identical no matter which layer
generates or reads the file — the browser (instant, no round trip), this API
(for any other client, and for parity), or a future worker-side onboarding
import job (§26.1's "sync-to-Git" / control-table seed path).

Not part of ``schemas.py`` (the worker/backend byte-identical vendored copy):
this module only reads that registry, it isn't part of the ADF-JSON
generation contract, so it carries no drift-guard/fingerprint requirement.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.connectors.schemas import ConnectorSchema, connector_schema, required_fields

# Mirrors `DEFAULT_INTEGRATION_RUNTIME` / `DEFAULT_KEY_VAULT_LINKED_SERVICE`
# in `frontend/lib/adf/connections.ts` — keep these two literals in sync.
DEFAULT_INTEGRATION_RUNTIME = "AutoResolveIntegrationRuntime"
DEFAULT_KEY_VAULT_LINKED_SERVICE = "LS_azure_key_vault"


def _lower_key(raw: str) -> str:
    return re.sub(r"[\s-]", "_", raw.strip().lower())


def _csv_cell(value: str) -> str:
    """Quote a CSV cell only when it needs it (matches `connections.ts`'s `csvCell`)."""
    if any(c in value for c in (",", '"', "\r", "\n")) or value != value.strip():
        return '"' + value.replace('"', '""') + '"'
    return value


def _csv_row(cells: list[str]) -> str:
    return ",".join(_csv_cell(c) for c in cells)


def _split_delimited(line: str) -> list[str]:
    """Quote-aware single-line split for , ; | or tab delimited values (matches `connections.ts`'s `splitDelimited`)."""
    cells: list[str] = []
    current = ""
    in_quotes = False
    delimiter = ""
    for char in line:
        if char == '"':
            in_quotes = not in_quotes
            continue
        is_delimiter = char in (",", ";", "|", "\t")
        if not in_quotes and is_delimiter:
            if not delimiter and char != "\t":
                delimiter = char
            if char == "\t" or char == delimiter:
                cells.append(current)
                current = ""
            else:
                current += char
            continue
        current += char
    cells.append(current)
    return [c.strip() for c in cells]


# =============================================================================
# Source objects (tables / input files) — "Upload file" mode
# =============================================================================

OBJECT_KEYS = ["object", "table", "table_name", "tablename", "source", "name", "object_name", "source_object"]
CATALOG_KEYS = ["catalog", "database", "catalog_name", "db", "source_catalog"]
SCHEMA_KEYS = ["schema", "schema_name", "source_schema"]
QUALIFIER_OVERRIDE_KEYS = ["qualifier_override", "qualifieroverride", "override", "identifier_override"]
CASE_OVERRIDE_KEYS = ["case_override", "caseoverride"]
WATERMARK_KEYS = ["watermark_column", "watermarkcolumn", "watermark", "watermarkcolumnname"]
BEHAVIOR_KEYS = ["loading_behavior", "loadingbehavior", "behavior", "load_behavior", "loadingbehaviour"]


def _normalize_behavior(raw: str | None) -> str | None:
    if not raw:
        return None
    clean = re.sub(r"[_\s-]", "", raw.strip().lower())
    if clean in {"full", "fullload", "snapshot", "truncate"}:
        return "FullLoad"
    if clean in {"delta", "deltaload", "incremental", "watermark"}:
        return "DeltaLoad"
    return None


def _normalize_case_override(raw: str | None) -> str | None:
    clean = re.sub(r"[_\s-]", "", raw.strip().lower()) if raw else ""
    return "as-typed" if clean == "astyped" else None


def _looks_like_header(cells: list[str]) -> bool:
    header_tokens = set(
        OBJECT_KEYS + CATALOG_KEYS + SCHEMA_KEYS + QUALIFIER_OVERRIDE_KEYS + CASE_OVERRIDE_KEYS + WATERMARK_KEYS + BEHAVIOR_KEYS
    )
    return any(_lower_key(c) in header_tokens for c in cells)


def _entry_from_cells(cells: list[str]) -> dict[str, str | None]:
    obj = cells[0].strip() if cells else ""
    watermark = cells[1].strip() if len(cells) > 1 and cells[1].strip() else None
    behavior = _normalize_behavior(cells[2]) if len(cells) > 2 else None
    return {"object": obj, "watermarkColumn": watermark, "loadingBehavior": behavior}


def _entry_from_record(record: dict[str, str]) -> dict[str, str | None]:
    lowered = {_lower_key(k): v.strip() for k, v in record.items()}
    object_key = next((k for k in OBJECT_KEYS if lowered.get(k)), None)
    catalog_key = next((k for k in CATALOG_KEYS if lowered.get(k)), None)
    schema_key = next((k for k in SCHEMA_KEYS if lowered.get(k)), None)
    qualifier_override_key = next((k for k in QUALIFIER_OVERRIDE_KEYS if lowered.get(k)), None)
    case_override_key = next((k for k in CASE_OVERRIDE_KEYS if lowered.get(k)), None)
    watermark_key = next((k for k in WATERMARK_KEYS if lowered.get(k)), None)
    behavior_key = next((k for k in BEHAVIOR_KEYS if lowered.get(k)), None)
    return {
        "object": lowered[object_key] if object_key else "",
        "catalog": lowered[catalog_key] if catalog_key else None,
        "schema": lowered[schema_key] if schema_key else None,
        "qualifierOverride": lowered[qualifier_override_key] if qualifier_override_key else None,
        "caseOverride": _normalize_case_override(lowered[case_override_key]) if case_override_key else None,
        "watermarkColumn": lowered[watermark_key] if watermark_key else None,
        "loadingBehavior": _normalize_behavior(lowered[behavior_key]) if behavior_key else None,
    }


def _parse_delimited_objects(content: str, warnings: list[str]) -> list[dict[str, str | None]]:
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    objects: list[dict[str, str | None]] = []
    seen_content_line = False
    header: list[str] | None = None
    for index, line in enumerate(lines):
        if line.startswith("#"):
            continue
        cells = _split_delimited(line)
        is_first_content_line = not seen_content_line
        seen_content_line = True
        # Header detection only applies to the first non-comment line, so
        # leading `# ...` documentation lines (as in the downloadable
        # template) never push the real header row into being read as data.
        # Once a header is found, every later row is mapped by column name
        # (not position) — a plain positional row is only assumed when no
        # header line was present at all (minimal headerless lists).
        if is_first_content_line and _looks_like_header(cells):
            header = cells
            continue
        entry = _entry_from_record(dict(zip(header, cells))) if header is not None else _entry_from_cells(cells)
        if not entry["object"]:
            warnings.append(f"Line {index + 1}: empty object name — skipped.")
            continue
        objects.append(entry)
    return objects


def _parse_json_objects(content: str, warnings: list[str]) -> list[dict[str, str | None]]:
    parsed = json.loads(content)
    if not isinstance(parsed, list):
        raise ValueError("JSON input must be an array.")
    objects: list[dict[str, str | None]] = []
    for index, item in enumerate(parsed):
        if isinstance(item, str):
            if item.strip():
                objects.append({"object": item.strip(), "watermarkColumn": None, "loadingBehavior": None})
            continue
        if isinstance(item, dict):
            record = {str(k): "" if v is None else str(v) for k, v in item.items()}
            entry = _entry_from_record(record)
            if not entry["object"]:
                warnings.append(f"JSON row {index + 1}: no object/table column found — skipped.")
                continue
            objects.append(entry)
    return objects


@dataclass
class ParsedTablesFile:
    objects: list[dict[str, str | None]]
    warnings: list[str]


def parse_tables_file(content: str, file_name: str) -> ParsedTablesFile:
    """Parse an uploaded table/input-file list — CSV/TSV/plain text with an
    optional header row, or a JSON array of strings/objects. Mirrors
    ``connections.ts``'s ``parseTablesFile`` exactly (same accepted shapes,
    same warnings)."""
    warnings: list[str] = []
    clean_name = file_name.lower()
    try:
        objects = _parse_json_objects(content, warnings) if clean_name.endswith(".json") else _parse_delimited_objects(
            content, warnings
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a warning, not a 500
        return ParsedTablesFile(objects=[], warnings=[f"Could not parse {file_name}: {exc}."])
    seen: set[str] = set()
    unique: list[dict[str, str | None]] = []
    for entry in objects:
        obj = entry["object"]
        if obj in seen:
            warnings.append(f'Duplicate object "{obj}" — kept first occurrence.')
            continue
        seen.add(obj)  # type: ignore[arg-type]
        unique.append(entry)
    if not unique:
        warnings.append("No usable object rows were found in the file.")
    return ParsedTablesFile(objects=unique, warnings=warnings)


def build_source_objects_template() -> str:
    """The downloadable CSV template for one connection's "Source objects
    (tables / input files)" — byte-for-byte the same shape as
    `connections.ts`'s `buildSourceObjectsTemplate`, so a template downloaded
    from either layer round-trips through either layer's parser. Columns are
    every field the object-list data model carries for one object under an
    already-configured connection; a full source→target registry row lives in
    the separate onboarding-registry template instead."""
    lines = [
        "# Source object template — one row per table or input file for this connection.",
        "# object (required): bare name, e.g. customer, ORDERS, or file.csv — or a legacy dotted",
        "#   string (schema.table) when catalog/schema below are left blank.",
        "# catalog (optional): database/catalog segment — only for 3-part sources (Snowflake, SAP HANA with a tenant DB).",
        "# schema (optional): schema segment — leave blank for schema-less sources (SAP OData, Salesforce, ...).",
        "# qualifier_override (optional): exact identifier to query, bypassing catalog/schema parsing entirely.",
        "# case_override (optional): \"as-typed\" to skip automatic case-conformance (e.g. Oracle/SAP HANA upper-casing).",
        "# watermark_column (optional): column used to track a DeltaLoad watermark",
        "# loading_behavior (optional): FullLoad or DeltaLoad — leave blank to use the connection's default load settings",
        "# Delete these sample rows before uploading, or leave them and edit in place.",
        _csv_row(["object", "catalog", "schema", "qualifier_override", "case_override", "watermark_column", "loading_behavior"]),
        _csv_row(["customer", "", "dbo", "", "", "", "FullLoad"]),
        _csv_row(["ORDERS", "MYDB", "SALES", "", "", "MODIFIED_DATE", "DeltaLoad"]),
        _csv_row(["BusinessPartner", "", "", "", "", "", ""]),
    ]
    return "\r\n".join(lines) + "\r\n"


# =============================================================================
# Linked service / source system creation — "Upload file" mode
# =============================================================================

LS_NAME_KEYS = ["name", "linked_service_name", "ls_name", "connection_name"]
LS_CONNECTOR_KEYS = ["connector_type", "connector", "connectortype", "type", "source_system"]
LS_AUTH_KEYS = ["auth_type", "authentication", "authtype", "auth", "authentication_type"]
LS_IR_KEYS = ["integration_runtime", "ir", "integrationruntime"]
LS_KV_KEYS = ["key_vault_linked_service", "keyvault", "key_vault", "kv_linked_service", "vault"]
LS_META_KEYS = set(LS_NAME_KEYS + LS_CONNECTOR_KEYS + LS_AUTH_KEYS + LS_IR_KEYS + LS_KV_KEYS)


def _first_match(record: dict[str, str], keys: list[str]) -> str | None:
    for key in keys:
        value = record.get(key)
        if value:
            return value
    return None


@dataclass
class ParsedLinkedServiceValues:
    name: str | None = None
    connector_type: str | None = None
    auth_type: str | None = None
    integration_runtime: str | None = None
    key_vault_linked_service: str | None = None
    # Keyed by ConnectionField.key — only keys recognized by the connector's schema.
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class ParsedLinkedServiceFile:
    values: list[ParsedLinkedServiceValues]
    warnings: list[str]


def _values_from_record(record: dict[str, str], warnings: list[str]) -> ParsedLinkedServiceValues:
    connector_type = _first_match(record, LS_CONNECTOR_KEYS)
    schema: ConnectorSchema | None = connector_schema(connector_type) if connector_type else None
    fields: dict[str, str] = {}
    for key, value in record.items():
        if key in LS_META_KEYS or not value:
            continue
        if schema is not None:
            matched = next((f.key for f in schema.fields if f.key.lower() == key), None)
            if matched is None:
                warnings.append(f'Column "{key}" is not a field on this connector — ignored.')
                continue
            fields[matched] = value
        else:
            fields[key] = value
    return ParsedLinkedServiceValues(
        name=_first_match(record, LS_NAME_KEYS),
        connector_type=connector_type,
        auth_type=_first_match(record, LS_AUTH_KEYS),
        integration_runtime=_first_match(record, LS_IR_KEYS),
        key_vault_linked_service=_first_match(record, LS_KV_KEYS),
        fields=fields,
    )


def _parse_linked_service_json(content: str, warnings: list[str]) -> list[ParsedLinkedServiceValues]:
    parsed = json.loads(content)
    items = parsed if isinstance(parsed, list) else [parsed]
    values: list[ParsedLinkedServiceValues] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            warnings.append(f"Entry {index + 1}: not an object — skipped.")
            continue
        record = {_lower_key(str(k)): ("" if v is None else str(v)).strip() for k, v in item.items()}
        values.append(_values_from_record(record, warnings))
    if not values:
        warnings.append("JSON input must be an object, or an array of objects.")
    return values


def _parse_linked_service_delimited(content: str, warnings: list[str]) -> list[ParsedLinkedServiceValues]:
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    header: list[str] | None = None
    data_lines: list[list[str]] = []
    for line in lines:
        if line.startswith("#"):
            continue
        cells = _split_delimited(line)
        if header is None:
            header = [_lower_key(c) for c in cells]
            continue
        data_lines.append(cells)
    if not header or not data_lines:
        warnings.append("No data row found — the file needs a header row plus at least one row of values.")
        return []
    values: list[ParsedLinkedServiceValues] = []
    for row in data_lines:
        record = {key: (row[i].strip() if i < len(row) else "") for i, key in enumerate(header)}
        values.append(_values_from_record(record, warnings))
    return values


def parse_linked_service_file(content: str, file_name: str) -> ParsedLinkedServiceFile:
    """Parse an uploaded linked-service file (CSV/TSV/TXT header + one row per
    connection, or a JSON object/array of objects). Mirrors ``connections.ts``'s
    ``parseLinkedServiceFile`` exactly — every row/entry bulk-creates its own
    connection."""
    warnings: list[str] = []
    clean_name = file_name.lower()
    try:
        values = (
            _parse_linked_service_json(content, warnings)
            if clean_name.endswith(".json")
            else _parse_linked_service_delimited(content, warnings)
        )
        for index, value in enumerate(values):
            if not value.name and not value.connector_type:
                warnings.append(f"Row {index + 1}: no linked service name or connector type found.")
        return ParsedLinkedServiceFile(values=values, warnings=warnings)
    except Exception as exc:  # noqa: BLE001 - surfaced as a warning, not a 500
        return ParsedLinkedServiceFile(values=[], warnings=[f"Could not parse {file_name}: {exc}."])


def build_linked_service_template(connector_type: str, auth_type: str) -> str:
    """The downloadable template for a linked service's "Upload file" mode,
    shaped for the connector + auth mechanism currently selected in the form.
    Byte-for-byte the same shape as ``connections.ts``'s
    ``buildLinkedServiceTemplate`` — every data row is its own linked service,
    so N rows bulk-create N connections (all sharing this connector + auth
    type)."""
    schema = connector_schema(connector_type)
    required = {f.key for f in required_fields(schema, auth_type)}
    meta_headers = ["name", "connector_type", "auth_type", "integration_runtime", "key_vault_linked_service"]
    field_headers = [f.key for f in schema.fields]
    auth_list = " | ".join(o.id for o in schema.auth_options)
    required_labels = [f.label for f in schema.fields if f.key in required]

    def sample_row(suffix: str) -> list[str]:
        return [
            f"LS_{connector_type}_{suffix}",
            connector_type,
            auth_type,
            DEFAULT_INTEGRATION_RUNTIME,
            DEFAULT_KEY_VAULT_LINKED_SERVICE,
            *("" if f.is_secret else f.placeholder for f in schema.fields),
        ]

    lines = [
        f"# Linked service template — {schema.display_name} ({schema.adf_type}).",
        "# Every data row = one linked service; add rows to bulk-create several connections at once",
        "#   (all rows must share this connector — a different connector needs its own file).",
        f"# auth_type options for this connector: {auth_list}",
        f"# Required for auth_type \"{auth_type}\": {', '.join(required_labels) if required_labels else 'none'}",
        "# Secret-holding columns must contain a Key Vault *secret name*, never the actual secret value.",
        "# Delete these sample rows before uploading, or leave them and edit in place.",
        _csv_row([*meta_headers, *field_headers]),
        _csv_row(sample_row("source")),
        _csv_row(sample_row("source_2")),
    ]
    return "\r\n".join(lines) + "\r\n"
