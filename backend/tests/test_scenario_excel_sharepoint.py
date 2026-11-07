"""Scenario tests — §9 Excel / SharePoint upload (docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md).
One test per scenario ID (XLS-01..XLS-05). See test_scenario_sap.py's
docstring for the worker/backend split this file follows.
"""
from __future__ import annotations

import pytest

from app.connectors.schemas import ALL_CONNECTORS, CONNECTOR_SCHEMAS


def test_xls_03_sharepoint_online_list_connector_exists_distinct_from_sftp_blob():
    """XLS-03 [VERIFY]: this was actually already fixed — the registry has a
    dedicated `sharepoint_online_list` connector (service principal / Azure
    AD app auth), distinct from the generic file connectors."""
    assert "sharepoint_online_list" in ALL_CONNECTORS
    schema = CONNECTOR_SCHEMAS["sharepoint_online_list"]
    assert schema.adf_type == "SharePointOnlineList"
    assert schema.kind == "service"


@pytest.mark.skip(reason="XLS-01/[VERIFY]/[DESIGN]: merged-cell/multi-header Excel parsing happens inside ADF's "
                          "own Copy activity at runtime — this generator never parses spreadsheet content, no "
                          "unit-testable code path here.")
def test_xls_01_merged_cells_multi_header_workbook_is_handled_or_flagged():
    pass


@pytest.mark.skip(reason="XLS-02/[VERIFY]: manual-vs-scheduled-run idempotency for a FullLoad/replace pattern is "
                          "a live-execution property (does a truncate-and-load twice in a row still land "
                          "correctly) — no unit-testable code path in a tool that only generates artifacts.")
def test_xls_02_manual_override_run_does_not_collide_with_scheduled_run():
    pass


@pytest.mark.skip(reason="XLS-04/[DESIGN]: whether a swap-table/transaction-wrapped pattern is used for "
                          "truncate+load is an undecided product question — the generated Copy activity for a "
                          "full load truncates then loads directly (no swap-table activities exist anywhere in "
                          "worker/app/generator/codegen.py), confirming today's behavior does have a brief "
                          "partial-read window, but no code encodes a decision either way.")
def test_xls_04_full_replace_does_not_expose_partial_read_mid_refresh():
    pass


@pytest.mark.skip(reason="XLS-05/[VERIFY]: schema-drift detection across fiscal-year column renames needs a real "
                          "run to compare two actual file schemas — no unit-testable code path (no schema-drift "
                          "comparison function exists anywhere in worker/app).")
def test_xls_05_renamed_column_header_across_fiscal_years_surfaces_warning():
    pass
