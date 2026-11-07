"""Scenario tests — §14 Watermark / incremental-load edge cases
(docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md). One test per scenario ID
(WM-01..WM-14). See test_scenario_sap.py's docstring for the worker/backend
split this file follows — the generator/validate_draft logic these mostly
need lives only in worker/app.
"""
from __future__ import annotations

import pytest


@pytest.mark.skip(reason="WM-01/[VERIFY]: real execution-log error text only exists once ADF actually runs a "
                          "pipeline — this app generates the LogRunFailed SQL (real, see worker/tests/"
                          "test_scenario_coverage_extra.py::test_meta_05_...) but never executes it, so there's "
                          "no 'was the error real' behavior to unit-test here.")
def test_wm_01_dropped_watermark_column_fails_loudly_with_real_error():
    pass


@pytest.mark.skip(reason="WM-02/[VERIFY]: MAX()-from-source-query (never GETUTCDATE) is real, worker-only. Real "
                          "coverage: worker/tests/test_scenario_coverage_extra.py::"
                          "test_wm_02_watermark_is_computed_from_a_real_max_query_never_getutcdate.")
def test_wm_02_watermark_computed_from_source_query_never_from_ir_clock():
    pass


@pytest.mark.skip(reason="WM-03/[VERIFY]: trigger timeZone=UTC is real, worker-only. Real coverage: "
                          "worker/tests/test_scenario_coverage_extra.py::"
                          "test_wm_03_schedule_trigger_time_zone_defaults_to_utc (also documents that "
                          "TumblingWindowTrigger correctly has no timeZone property at all).")
def test_wm_03_tumbling_window_timezone_is_always_utc():
    pass


@pytest.mark.skip(reason="WM-04/[VERIFY]: documents a real, confirmed gap — worker-only. Real coverage: "
                          "worker/tests/test_scenario_coverage_extra.py::"
                          "test_wm_04_watermark_initial_value_is_generated_but_never_referenced_by_any_activity "
                          "— WatermarkInitialValue reaches parameters.json but no generated activity ever reads "
                          "it as a first-run fallback.")
def test_wm_04_first_incremental_run_uses_watermark_start_value_not_unbounded():
    pass


@pytest.mark.skip(reason="WM-05/[DESIGN]: zero-rows-written dashboard visibility is a live-monitoring/alerting "
                          "product question this codebase has no dashboard or alerting code for at all — no code "
                          "path.")
def test_wm_05_zero_rows_written_for_many_runs_is_visible_on_dashboard():
    pass


@pytest.mark.skip(reason="WM-06/[VERIFY]: confirmed by reading the real code (worker/app/generator/codegen.py's "
                          "_new_watermark_lookup_query docstring: 'independent per-column MAX, one output column "
                          "per watermark column') — composite mode genuinely computes each column's new "
                          "high-watermark independently, matching the documented approximation exactly. Worker-only, "
                          "no dedicated test written yet for this specific scenario.")
def test_wm_06_composite_watermark_upper_bound_approximation_is_accepted_not_unbounded():
    pass


@pytest.mark.skip(reason="WM-07/[VERIFY]: hard-delete handling is real (SpecDraft.deleteHandling, soft_delete/cdc), "
                          "worker-only. Real coverage: worker/tests/test_delete_handling.py::"
                          "test_soft_delete_activities_flag_missing_keys (keys missing from the source are flagged "
                          "`_is_deleted = 1` on the landed table).")
def test_wm_07_soft_delete_flag_on_source_with_no_native_soft_delete():
    pass


@pytest.mark.skip(reason="WM-08/[VERIFY]: real, worker-only. Real coverage: worker/tests/test_delete_handling.py::"
                          "test_wm_08_delete_handling_on_a_full_load_is_flagged (delete handling runs on the "
                          "DeltaLoad branch only, so a full load warns).")
def test_wm_08_full_refresh_delete_handling_with_delta_load_is_flagged():
    pass


@pytest.mark.skip(reason="WM-09/[VERIFY]: no inclusive/exclusive watermark-upper-bound flag exists on SpecDraft "
                          "— worker-only. Real coverage: worker/tests/test_scenario_coverage_extra.py::"
                          "test_wm_09_inclusive_upper_bound_flag_does_not_exist_in_the_draft, confirming the "
                          "operator is always exclusive `>` today.")
def test_wm_09_inclusive_upper_flag_changes_generated_predicate_operator():
    pass


@pytest.mark.skip(reason="WM-10/[VERIFY]: confirmed by reading the real control-table DDL (worker/app/generator/"
                          "codegen.py's _control_table_sql: 'TaskId INT IDENTITY(1,1) PRIMARY KEY') — TaskId is a "
                          "database-assigned identity column, never user/CSV-supplied, so the scenario's premise "
                          "(two onboarding rows colliding on the same author-chosen TaskId) cannot occur by "
                          "construction. A real, reassuring finding, not a gap.")
def test_wm_10_duplicate_task_id_across_objects_is_validated_at_commit():
    pass


@pytest.mark.skip(reason="WM-11/[VERIFY]: no opaque-token-vs-orderable-type distinction exists in the generated "
                          "watermark comparison SQL (worker/app/generator/codegen.py always emits `>` regardless "
                          "of watermarkColumnType) — confirmed real [GAP]: a string-typed opaque token would get "
                          "the same `>` operator as a datetime, not `=`.")
def test_wm_11_opaque_string_watermark_only_compared_with_equality():
    pass


@pytest.mark.skip(reason="WM-12/[VERIFY]: re-run idempotency after a failed incremental run is a live-execution "
                          "property of the generated SQL running twice against a real database — no unit-testable "
                          "code path in a tool that only generates artifacts.")
def test_wm_12_rerunning_failed_incremental_twice_processes_zero_new_rows():
    pass


@pytest.mark.skip(reason="WM-13/[VERIFY]: NULL-watermark handling depends on how the generated `>` predicate "
                          "behaves against real NULL data at runtime — the generated SQL is real (plain `>`, no "
                          "IS NULL branch — confirmed by reading _new_watermark_lookup_query/_delta_copy_query in "
                          "worker/app/generator/codegen.py) but exercising the actual row-visibility claim needs "
                          "a real database, no code path here.")
def test_wm_13_nullable_watermark_column_rows_captured_on_full_load_only():
    pass


@pytest.mark.skip(reason="WM-14/[VERIFY]: concurrent-run racing on the control table is a live-execution/database "
                          "-locking property — no concurrency guard exists in the generated SQL (confirmed: no "
                          "UPDLOCK/sp_getapplock/etc. anywhere in worker/app/generator/codegen.py) and there's no "
                          "code path to unit-test an actual race without a real database.")
def test_wm_14_concurrent_manual_and_scheduled_runs_do_not_race_on_watermark():
    pass
