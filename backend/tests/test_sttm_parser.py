"""Tests for `app/onboarding/sttm_parser.py` — the authoritative server-side
STTM workbook re-parser (AGENTS.md §33.9)."""
from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook

from app.onboarding.sttm_parser import (
    MAX_SHEETS_PER_WORKBOOK,
    WorkbookTooLargeError,
    _DQ_DATA_START_ROW,
    _HEADER_FIELDS,
    _HEADER_START_ROW,
    _TRANSFORM_DATA_START_ROW,
    parse_workbook,
)


def _write_object_sheet(wb: Workbook, sheet_name: str, header: dict[str, str], transformations: list[list[str]], dq_rules: list[list[str]]) -> None:
    ws = wb.create_sheet(sheet_name)
    ws.cell(row=1, column=1, value="Field")
    ws.cell(row=1, column=2, value="Value")
    for i, field_name in enumerate(_HEADER_FIELDS):
        ws.cell(row=_HEADER_START_ROW + i, column=1, value=field_name)
        if field_name in header:
            ws.cell(row=_HEADER_START_ROW + i, column=2, value=header[field_name])
    for r, row in enumerate(transformations):
        for c, val in enumerate(row):
            ws.cell(row=_TRANSFORM_DATA_START_ROW + r, column=c + 1, value=val)
    for r, row in enumerate(dq_rules):
        for c, val in enumerate(row):
            ws.cell(row=_DQ_DATA_START_ROW + r, column=c + 1, value=val)


def _bytes(wb: Workbook) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _base_header(**overrides) -> dict[str, str]:
    header = {
        "object_key": "gl_transaction",
        "tag": "finance",
        "curated_entity": "gl_transaction",
        "source_object_keys": "sql_server.dbo.gl_transaction",
        "write_behavior": "append",
        "loading_behavior": "FullLoad",
    }
    header.update(overrides)
    return header


def test_parses_a_valid_silver_sheet_end_to_end() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _write_object_sheet(
        wb,
        "gl_transaction",
        _base_header(),
        transformations=[["conform", "conform_columns", "TransactionId:transaction_id", "", "", "", "", "", "", "", ""]],
        dq_rules=[["not_null_amount", "amount", "IS_NOT_NULL", "", "warn"]],
    )
    report = parse_workbook(_bytes(wb), "silver")
    assert report.sheet_total == 1
    assert report.sheet_valid == 1
    assert report.sheet_error == 0
    sheet = report.sheets[0]
    assert sheet.valid, sheet.issues
    assert sheet.parsed["curatedEntity"] == "gl_transaction"
    assert sheet.parsed["targetTable"] == "curated.gl_transaction"
    assert sheet.parsed["transformations"][0]["type"] == "conform_columns"
    assert sheet.parsed["dataQualityRules"][0]["rule"] == "IS_NOT_NULL"


def test_target_table_honors_a_projects_custom_zone_prefix() -> None:
    """§25.3 Standards "Layer prefixes" — threaded through `parse_workbook` -> `_parse_object_sheet`
    -> `layer_target_table_name`, same as the manual create/update path in test_silver_objects.py."""
    wb = Workbook()
    wb.remove(wb.active)
    _write_object_sheet(
        wb,
        "gl_transaction",
        _base_header(),
        transformations=[["conform", "conform_columns", "TransactionId:transaction_id", "", "", "", "", "", "", "", ""]],
        dq_rules=[],
    )
    report = parse_workbook(_bytes(wb), "silver", {"silver": "cur"})
    assert report.sheets[0].valid
    assert report.sheets[0].parsed["targetTable"] == "cur.gl_transaction"


def test_reserved_sheets_are_skipped() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    wb.create_sheet("Instructions")
    wb.create_sheet("Available_Transformations")
    wb.create_sheet("EXAMPLE_gl_transaction")
    _write_object_sheet(wb, "real_object", _base_header(), transformations=[], dq_rules=[])
    report = parse_workbook(_bytes(wb), "silver")
    assert report.sheet_total == 1
    assert report.sheets[0].sheet_name == "real_object"


def test_missing_required_fields_produce_issues_not_an_exception() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _write_object_sheet(wb, "bad_sheet", {}, transformations=[], dq_rules=[])
    report = parse_workbook(_bytes(wb), "silver")
    assert report.sheet_valid == 0
    assert report.sheet_error == 1
    assert not report.sheets[0].valid
    assert any(i["field"] == "tag" for i in report.sheets[0].issues)


def test_gold_sheet_requires_mart_domain() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _write_object_sheet(
        wb, "gl_fact", _base_header(curated_entity="gl_fact"),
        transformations=[["agg", "aggregate", "", "", "", "", "", "", "", "customer_id", "SUM(amount) AS total"]],
        dq_rules=[],
    )
    report = parse_workbook(_bytes(wb), "gold")
    assert not report.sheets[0].valid
    assert any(i["field"] == "martDomain" for i in report.sheets[0].issues)


def test_duplicate_object_key_across_sheets_is_flagged() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _write_object_sheet(
        wb, "sheet_one", _base_header(),
        transformations=[["conform", "conform_columns", "a:b", "", "", "", "", "", "", "", ""]],
        dq_rules=[],
    )
    _write_object_sheet(
        wb, "sheet_two", _base_header(),
        transformations=[["conform", "conform_columns", "a:b", "", "", "", "", "", "", "", ""]],
        dq_rules=[],
    )
    report = parse_workbook(_bytes(wb), "silver")
    assert report.sheet_total == 2
    assert report.sheet_valid == 1
    assert report.sheet_error == 1
    dup = next(s for s in report.sheets if s.sheet_name == "sheet_two")
    assert not dup.valid
    assert any("duplicates object_key" in i["message"] for i in dup.issues)


def test_dq_rule_column_not_in_transformation_output_warns_but_does_not_block() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _write_object_sheet(
        wb, "gl_transaction", _base_header(),
        transformations=[["conform", "conform_columns", "TransactionId:transaction_id", "", "", "", "", "", "", "", ""]],
        dq_rules=[["not_null_amount", "amount", "IS_NOT_NULL", "", "warn"]],
    )
    report = parse_workbook(_bytes(wb), "silver")
    sheet = report.sheets[0]
    assert sheet.valid, sheet.issues  # never blocks
    assert report.sheet_valid == 1
    warnings = [i for i in sheet.issues if i["severity"] == "warning"]
    assert len(warnings) == 1
    assert "amount" in warnings[0]["message"]


def test_dq_rule_column_matching_transformation_output_has_no_warning() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _write_object_sheet(
        wb, "gl_transaction", _base_header(),
        transformations=[["conform", "conform_columns", "TransactionId:transaction_id", "", "", "", "", "", "", "", ""]],
        dq_rules=[["not_null_txn", "transaction_id", "IS_NOT_NULL", "", "warn"]],
    )
    report = parse_workbook(_bytes(wb), "silver")
    assert report.sheets[0].issues == []


def test_dq_column_check_skipped_when_no_column_shaping_transformation_present() -> None:
    # Only `dedupe` present — it never renames/drops columns, so there is no
    # column-shaping info to check the DQ rule's column against; must not warn.
    wb = Workbook()
    wb.remove(wb.active)
    _write_object_sheet(
        wb, "gl_transaction", _base_header(),
        transformations=[["dedupe_gl", "dedupe", "", "transaction_id", "modified_date", "", "", "", "", "", ""]],
        dq_rules=[["not_null_amount", "amount", "IS_NOT_NULL", "", "warn"]],
    )
    report = parse_workbook(_bytes(wb), "silver")
    assert report.sheets[0].valid
    assert report.sheets[0].issues == []


def test_workbook_exceeding_sheet_limit_is_rejected_not_silently_truncated() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    for i in range(MAX_SHEETS_PER_WORKBOOK + 1):
        _write_object_sheet(wb, f"obj_{i}", _base_header(object_key=f"obj_{i}", curated_entity=f"obj_{i}"), transformations=[], dq_rules=[])
    with pytest.raises(WorkbookTooLargeError) as exc_info:
        parse_workbook(_bytes(wb), "silver")
    assert str(MAX_SHEETS_PER_WORKBOOK + 1) in str(exc_info.value)


def test_workbook_at_exactly_the_sheet_limit_is_accepted() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    for i in range(MAX_SHEETS_PER_WORKBOOK):
        _write_object_sheet(wb, f"obj_{i}", _base_header(object_key=f"obj_{i}", curated_entity=f"obj_{i}"), transformations=[], dq_rules=[])
    report = parse_workbook(_bytes(wb), "silver")
    assert report.sheet_total == MAX_SHEETS_PER_WORKBOOK


def test_join_transformation_parses_on_silver_after_widening() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _write_object_sheet(
        wb, "gl_transaction_enriched", _base_header(curated_entity="gl_transaction_enriched"),
        transformations=[
            ["join_cc", "join", "", "", "", "", "raw.cost_center", "LEFT", "a.cc_id = b.cc_id", "", ""],
        ],
        dq_rules=[],
    )
    report = parse_workbook(_bytes(wb), "silver")
    assert report.sheets[0].valid, report.sheets[0].issues
    assert report.sheets[0].parsed["transformations"][0]["type"] == "join"


def test_chain_column_check_warns_on_a_column_an_earlier_projection_dropped():
    from app.onboarding.sttm import validate_transformation_rules

    rules = [
        {"name": "conform", "type": "conform_columns", "columns": "PAT_ID:patient_id"},
        {"name": "dedupe", "type": "dedupe", "keyColumns": "patient_id", "orderColumn": "UPDATE_DATE"},
    ]
    warnings = [i for i in validate_transformation_rules("silver", rules) if i.severity == "warning"]
    assert len(warnings) == 1 and '"UPDATE_DATE"' in warnings[0].message
    rules[0]["columns"] = "PAT_ID:patient_id, UPDATE_DATE"
    assert [i for i in validate_transformation_rules("silver", rules) if i.severity == "warning"] == []
