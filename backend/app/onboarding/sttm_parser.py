"""Server-side STTM Excel workbook parser (AGENTS.md §33.5/§33.9), the
**authoritative** re-parse — the frontend's client-side parser
(`frontend/lib/adf/sttm.ts`, P3) exists only for instant upload-preview
feedback; this module is what actually commits rows to
``app.silver_objects``/``app.mart_objects``. Not subject to §18's
byte-identical generator-parity discipline (this produces DB rows, not ADF
artifacts) — only rule *equivalence* with the frontend parser matters, and
this backend copy wins on any disagreement (§33.9).

**Sheet layout** (must stay in lockstep with `frontend/lib/adf/sttm.ts`'s
`buildSttmTemplate` — the one place both sides' row/column numbers are
defined): one fixed, deterministic row layout per object sheet, column A/B a
"Field | Value" pair table for the object header, followed by a
TRANSFORMATIONS block (max 10 rows) and a DATA QUALITY RULES block (max 5
rows), each its own little table with its own header row. See
`_HEADER_FIELDS`/`_TRANSFORM_COLUMNS`/`_DQ_COLUMNS` below for the exact
row/column numbers.
"""
from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.onboarding import sttm_data_modular as data_modular
from app.onboarding.sttm import (
    MAX_DQ_RULES,
    MAX_TRANSFORMATIONS,
    Issue,
    check_dq_columns_against_transformations,
    layer_target_table_name,
    to_snake,
    validate_data_quality_rules,
    validate_object_header,
    validate_transformation_rules,
)

MAX_SHEETS_PER_WORKBOOK = 200  # §33.6 rule 10 guardrail (also caps workbooks per Data Modular .zip bundle)
MAX_BUNDLE_ENTRY_BYTES = 20 * 1024 * 1024  # uncompressed size cap per workbook inside a .zip bundle


class BundleEntryTooLargeError(ValueError):
    """A workbook inside a Data Modular `.zip` bundle is larger than `MAX_BUNDLE_ENTRY_BYTES`
    uncompressed — rejected before it is decompressed (zip-bomb guard)."""

    def __init__(self, entry: str, size: int) -> None:
        super().__init__(
            f'"{entry}" in the bundle is {size} bytes uncompressed, over the {MAX_BUNDLE_ENTRY_BYTES}-byte limit.'
        )


class WorkbookTooLargeError(ValueError):
    """Raised by `parse_workbook` when a workbook has more real object sheets
    than `MAX_SHEETS_PER_WORKBOOK` (§33.6 rule 10). The upload is rejected
    outright — the caller (`app/api/registry.py`'s `sttm_import` route) is
    expected to translate this into a 4xx error. Previously this guardrail
    silently truncated to the first 200 sheets and parsed that partial,
    surprising subset instead of telling the uploader anything was dropped."""

    def __init__(self, sheet_count: int) -> None:
        self.sheet_count = sheet_count
        super().__init__(
            f"Workbook has {sheet_count} object sheets, exceeding the {MAX_SHEETS_PER_WORKBOOK}-sheet-per-workbook "
            "limit. Split it into multiple uploads."
        )

class InvalidWorkbookError(ValueError):
    """Raised by `parse_workbook` when the upload isn't a readable `.xlsx`/`.zip` (E2E-11) — the
    route turns it into a 422 instead of letting `BadZipFile` & co. surface as a 500."""


_RESERVED_EXACT = {"Instructions", "Available_Transformations"}
_RESERVED_PREFIXES = ("EXAMPLE_",)

# Object-header block: column A = field name, column B = value. One field per
# row, starting at row 2 (row 1 is the "Field | Value" column header).
_HEADER_FIELDS: list[str] = [
    "object_key",
    "tag",
    "curated_entity",
    "mart_domain",
    "source_object_keys",
    "enabled",
    "owner",
    "batch_group",
    "domain",
    "write_behavior",
    "loading_behavior",
    "watermark_column",
    "watermark_column_type",
    "business_keys",
    "data_classification",
    "notes",
]
_HEADER_START_ROW = 2  # row 1 is "Field" | "Value"
_TRANSFORM_SECTION_ROW = _HEADER_START_ROW + len(_HEADER_FIELDS) + 1  # blank row, then section title
_TRANSFORM_COLUMNS: list[str] = [
    "name", "type", "columns", "key_columns", "order_column", "cast_expression",
    "join_entity", "join_type", "join_condition", "group_by_columns", "aggregate_expression",
]
_TRANSFORM_HEADER_ROW = _TRANSFORM_SECTION_ROW + 1
_TRANSFORM_DATA_START_ROW = _TRANSFORM_HEADER_ROW + 1

_DQ_SECTION_ROW = _TRANSFORM_DATA_START_ROW + MAX_TRANSFORMATIONS + 1
_DQ_COLUMNS: list[str] = ["name", "column", "rule", "value", "enforcement"]
_DQ_HEADER_ROW = _DQ_SECTION_ROW + 1
_DQ_DATA_START_ROW = _DQ_HEADER_ROW + 1


def _cell(ws, row: int, col: int) -> Any:
    value = ws.cell(row=row, column=col).value
    return value


def _is_reserved(sheet_name: str) -> bool:
    return sheet_name in _RESERVED_EXACT or any(sheet_name.startswith(p) for p in _RESERVED_PREFIXES)


def _snake_key(camel: str) -> str:
    """`source_object_keys` -> `sourceObjectKeys`, matching the wire shape
    `validate_object_header`/`RegistryObjectIn` expect."""
    parts = camel.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


@dataclass
class ParsedSheet:
    sheet_name: str
    object_key: str | None
    curated_entity: str | None
    valid: bool
    issues: list[dict[str, Any]] = field(default_factory=list)
    parsed: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheetName": self.sheet_name,
            "objectKey": self.object_key,
            "curatedEntity": self.curated_entity,
            "valid": self.valid,
            "issues": self.issues,
            "parsed": self.parsed,
        }


@dataclass
class ParseReport:
    file_hash: str
    sheet_total: int
    sheet_valid: int
    sheet_error: int
    sheets: list[ParsedSheet]


def _parse_object_sheet(ws, layer: str, layer_prefixes: dict[str, Any] | None = None) -> ParsedSheet:
    header: dict[str, str] = {}
    for i, field_name in enumerate(_HEADER_FIELDS):
        raw = _cell(ws, _HEADER_START_ROW + i, 2)
        header[field_name] = "" if raw is None else str(raw).strip()

    transformations: list[dict[str, Any]] = []
    for r in range(MAX_TRANSFORMATIONS):
        row_no = _TRANSFORM_DATA_START_ROW + r
        values = [_cell(ws, row_no, c + 1) for c in range(len(_TRANSFORM_COLUMNS))]
        if not any(v not in (None, "") for v in values):
            continue
        rule: dict[str, Any] = {}
        for col_name, val in zip(_TRANSFORM_COLUMNS, values):
            if val in (None, ""):
                continue
            rule[_snake_key(col_name)] = str(val).strip()
        if rule:
            transformations.append(rule)

    dq_rules: list[dict[str, Any]] = []
    for r in range(MAX_DQ_RULES):
        row_no = _DQ_DATA_START_ROW + r
        values = [_cell(ws, row_no, c + 1) for c in range(len(_DQ_COLUMNS))]
        if not any(v not in (None, "") for v in values):
            continue
        rule = {}
        for col_name, val in zip(_DQ_COLUMNS, values):
            if val in (None, ""):
                continue
            rule[col_name] = str(val).strip()
        if rule:
            dq_rules.append(rule)

    object_key = header.get("object_key") or to_snake(header.get("curated_entity"))
    body: dict[str, Any] = {
        "objectKey": object_key,
        "enabled": header.get("enabled", "TRUE").upper() != "FALSE",
        "tag": header.get("tag", ""),
        "curatedEntity": header.get("curated_entity", ""),
        "martDomain": header.get("mart_domain") or None,
        "sourceObjectKeys": [s.strip() for s in header.get("source_object_keys", "").split("|") if s.strip()],
        "owner": header.get("owner") or None,
        "batchGroup": header.get("batch_group") or None,
        "domain": header.get("domain") or None,
        "writeBehavior": header.get("write_behavior") or "append",
        "loadingBehavior": header.get("loading_behavior") or "FullLoad",
        "watermarkColumn": header.get("watermark_column") or None,
        "watermarkColumnType": header.get("watermark_column_type") or None,
        "businessKeys": [s.strip() for s in header.get("business_keys", "").split(",") if s.strip()],
        "transformations": transformations,
        "dataQualityRules": dq_rules,
        "dataClassification": header.get("data_classification") or None,
        "notes": header.get("notes", ""),
    }
    return _validated_sheet(ws.title, layer, body, [], layer_prefixes)


def _validated_sheet(
    sheet_name: str, layer: str, body: dict[str, Any], pre_issues: list[Issue], layer_prefixes: dict[str, Any] | None
) -> ParsedSheet:
    """The one validation tail both template layouts share — the legacy per-sheet layout and the
    Data Modular template (§33.14) build the same wire `body`, then run the same §33.6 checks."""
    transformations = body["transformations"]
    dq_rules = body["dataQualityRules"]
    issues = list(pre_issues)
    issues += validate_object_header(layer, body)
    issues += validate_transformation_rules(layer, transformations)
    issues += validate_data_quality_rules(dq_rules)
    issues += check_dq_columns_against_transformations(transformations, dq_rules)
    has_errors = any(i.severity == "error" for i in issues)

    if not has_errors:
        body["targetTable"] = layer_target_table_name(
            layer, entity=body["curatedEntity"], domain=body.get("martDomain"), layer_prefixes=layer_prefixes
        )

    return ParsedSheet(
        sheet_name=sheet_name,
        object_key=body.get("objectKey") or None,
        curated_entity=body.get("curatedEntity") or None,
        valid=not has_errors,
        issues=[i.to_dict() for i in issues],
        parsed=body if not has_errors else None,
    )


def _parse_data_modular_sheet(ws, label: str, layer: str, layer_prefixes: dict[str, Any] | None) -> ParsedSheet:
    body, issues = data_modular.to_registry_body(data_modular.read_sheet(ws), layer)
    return _validated_sheet(label, layer, body, issues, layer_prefixes)


def _workbook_sheets(
    file_bytes: bytes, layer: str, layer_prefixes: dict[str, Any] | None, file_name: str | None
) -> list[ParsedSheet]:
    """One `ParsedSheet` per object in one `.xlsx`. A Data Modular workbook describes exactly one
    target table, so it yields one entry labelled by its file name (the commit step selects by it)."""
    wb = load_workbook(BytesIO(file_bytes), data_only=True, read_only=True)
    template = wb[data_modular.TEMPLATE_SHEET] if data_modular.TEMPLATE_SHEET in wb.sheetnames else None
    if template is not None and data_modular.is_data_modular_sheet(template):
        label = PurePosixPath(file_name).stem if file_name else data_modular.TEMPLATE_SHEET
        return [_parse_data_modular_sheet(template, label, layer, layer_prefixes)]
    sheet_names = [name for name in wb.sheetnames if not _is_reserved(name)]
    if len(sheet_names) > MAX_SHEETS_PER_WORKBOOK:
        raise WorkbookTooLargeError(len(sheet_names))
    return [_parse_object_sheet(wb[name], layer, layer_prefixes) for name in sheet_names]


def _bundle_sheets(file_bytes: bytes, layer: str, layer_prefixes: dict[str, Any] | None) -> list[ParsedSheet]:
    """Data Modular's layer-wise export (§33.14): a `.zip` holding one template workbook per table."""
    with zipfile.ZipFile(BytesIO(file_bytes)) as archive:
        entries = [
            info for info in archive.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".xlsx") and not PurePosixPath(info.filename).name.startswith(("~$", "."))
        ]
        if len(entries) > MAX_SHEETS_PER_WORKBOOK:
            raise WorkbookTooLargeError(len(entries))
        sheets: list[ParsedSheet] = []
        for info in sorted(entries, key=lambda i: i.filename):
            if info.file_size > MAX_BUNDLE_ENTRY_BYTES:
                raise BundleEntryTooLargeError(info.filename, info.file_size)
            sheets.extend(_workbook_sheets(archive.read(info), layer, layer_prefixes, info.filename))
    return sheets


def _is_bundle(file_bytes: bytes, file_name: str | None) -> bool:
    """An `.xlsx` is itself a zip, so a bundle is told apart by holding `.xlsx` entries, not workbook XML."""
    if not zipfile.is_zipfile(BytesIO(file_bytes)):
        return False
    if file_name and file_name.lower().endswith(".zip"):
        return True
    with zipfile.ZipFile(BytesIO(file_bytes)) as archive:
        names = archive.namelist()
    return "[Content_Types].xml" not in names and any(n.lower().endswith(".xlsx") for n in names)


def parse_workbook(
    file_bytes: bytes, layer: str, layer_prefixes: dict[str, Any] | None = None, file_name: str | None = None
) -> ParseReport:
    """`layer_prefixes` is the project's Standards-configured custom zone
    prefix per medallion layer (§25.3) — omitted/None keeps the curated/mart
    default for every parsed sheet's computed `targetTable`.

    Accepts the legacy per-sheet layout, a Data Modular template workbook
    (§33.14), or a `.zip` of Data Modular workbooks; `file_name` labels a
    single Data Modular workbook's one entry and helps tell a bundle apart."""
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    try:
        if _is_bundle(file_bytes, file_name):
            parsed_sheets = _bundle_sheets(file_bytes, layer, layer_prefixes)
        else:
            parsed_sheets = _workbook_sheets(file_bytes, layer, layer_prefixes, file_name)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError, EOFError) as exc:
        raise InvalidWorkbookError(
            f"{file_name or 'The upload'} is not a readable .xlsx workbook or .zip bundle ({type(exc).__name__})."
        ) from exc

    seen_object_keys: dict[str, str] = {}  # object_key -> first sheet name that used it
    seen_sheet_names: set[str] = set()
    sheets: list[ParsedSheet] = []
    for parsed in parsed_sheets:
        name = parsed.sheet_name
        if name in seen_sheet_names:
            # Two bundle entries with the same file stem in different folders: keep labels unique,
            # the commit step selects by label.
            name = parsed.sheet_name = f"{name} ({len(sheets) + 1})"
        seen_sheet_names.add(name)
        if parsed.object_key:
            first_sheet = seen_object_keys.get(parsed.object_key)
            if first_sheet is not None:
                parsed.valid = False
                parsed.parsed = None
                parsed.issues.append(
                    {
                        "severity": "error",
                        "message": f'Sheet "{name}" duplicates object_key "{parsed.object_key}" already used by sheet "{first_sheet}".',
                        "field": "objectKey",
                    }
                )
            else:
                seen_object_keys[parsed.object_key] = name
        sheets.append(parsed)

    sheet_valid = sum(1 for s in sheets if s.valid)
    sheet_error = len(sheets) - sheet_valid
    return ParseReport(
        file_hash=file_hash, sheet_total=len(sheets), sheet_valid=sheet_valid, sheet_error=sheet_error, sheets=sheets
    )
