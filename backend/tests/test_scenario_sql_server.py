"""Scenario tests — §5 SQL Server (docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md).
One test per scenario ID (SQL-01..SQL-08). See test_scenario_sap.py's
docstring for the worker/backend split this file follows.
"""
from __future__ import annotations

import pytest


@pytest.mark.skip(reason="SQL-01/[VERIFY]: watermarkColumnType has no 'rowversion'/binary(8) option and no "
                          "special-cased comparison SQL anywhere in worker/app/generator/codegen.py (only "
                          "datetime|int|bigint|string) — confirmed real [GAP], author must pick 'string' as a "
                          "workaround with no generator support for the hex comparison semantics.")
def test_sql_01_rowversion_binary_watermark_column_type():
    pass


@pytest.mark.skip(reason="SQL-02/[VERIFY]: no identifier-bracketing function exists anywhere in "
                          "worker/app/generator/codegen.py — schema/table are always emitted as "
                          "@pipeline().parameters.SchemaName/TableName expression references (ADF's dataset "
                          "typeProperties resolve the identifier structurally, not via raw SQL string "
                          "concatenation), so there is no generator code path that would need a `[Order]`-style "
                          "bracket and none exists to test.")
def test_sql_02_reserved_word_table_name_is_bracketed():
    pass


@pytest.mark.skip(reason="SQL-03/[DESIGN]: no collation-awareness exists anywhere in the codebase — undecided "
                          "product question, no code path.")
def test_sql_03_case_sensitive_collation_not_assumed_case_insensitive():
    pass


@pytest.mark.skip(reason="SQL-04/[VERIFY]: schema-qualified name resolution (staging.PolicyMaster vs "
                          "dbo.PolicyMaster) is real, worker-only code (generator/qualifier.py's "
                          "resolve_qualified_name) — already covered for the general 2-part case by "
                          "worker/tests/test_qualifier.py::test_two_part_relational_unchanged_behavior.")
def test_sql_04_schema_qualified_names_beyond_dbo_do_not_collide():
    pass


@pytest.mark.skip(reason="SQL-05: Full Load row-count warning is real, worker-only (validate_draft). Real "
                          "coverage: worker/tests/test_scenario_smoke_dummy_artifacts.py::"
                          "test_sql05_large_full_load_table_warns_but_never_blocks.")
def test_sql_05_huge_table_on_full_load_template_warns():
    pass


@pytest.mark.skip(reason="SQL-06/[VERIFY]: IR-outage mid-run restart-safety is a live-execution/runtime property "
                          "of the control table + ADF's own run engine — no unit-testable code path in a tool "
                          "that only generates artifacts and never executes them.")
def test_sql_06_ir_outage_mid_run_only_fails_in_flight_children():
    pass


@pytest.mark.skip(reason="SQL-07/[VERIFY]: switching authType on an existing linked-service draft in the Build "
                          "UI is a frontend form-state concern (frontend/lib/adf/connections.ts) with no backend "
                          "API persistence endpoint to exercise here — the worker/backend layers only ever see a "
                          "complete draft, never a 'before/after a UI toggle' transition.")
def test_sql_07_switching_windows_to_sql_auth_leaves_no_stale_fields():
    pass


@pytest.mark.skip(reason="SQL-08/[REGRESSION]: composite-watermark lexicographic tuple predicate is real, "
                          "worker-only. Real coverage: worker/tests/test_scenario_smoke_dummy_artifacts.py::"
                          "test_sql08_composite_watermark_emits_the_documented_lexicographic_tuple_predicate.")
def test_sql_08_composite_watermark_captures_same_timestamp_different_id():
    pass
