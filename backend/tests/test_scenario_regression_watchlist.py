"""Scenario tests — §20 Regression watchlist: the seven 2026-09-12
parity-gap fixes (docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md). Re-run after any
future change to `lib/adf/{spec,pipeline-codegen}.ts` or
`worker/app/generator/{spec,codegen}.py` / connector-registry counterparts.
One test per item (REGR-01..REGR-07).

All seven are real, generator-level behaviors — worker-only (backend
doesn't vendor the generator). Every one now has real dummy-data coverage in
worker/tests/, listed below.
"""
from __future__ import annotations

import pytest


@pytest.mark.skip(reason="REGR-01: real coverage — worker/tests/test_scenario_coverage_extra.py::"
                          "test_regr_01_custom_metadata_columns_appear_as_copy_source_additional_columns.")
def test_regr_01_standard_metadata_columns_appear_on_copy_activity_sink():
    pass


@pytest.mark.skip(reason="REGR-02: real coverage — worker/tests/test_scenario_coverage_extra.py::"
                          "test_regr_02_execution_logging_lookups_have_the_documented_dependson_wiring.")
def test_regr_02_execution_logging_lookups_have_correct_dependson_wiring():
    pass


@pytest.mark.skip(reason="REGR-03: real coverage — worker/tests/test_scenario_smoke_dummy_artifacts.py::"
                          "test_sql08_composite_watermark_emits_the_documented_lexicographic_tuple_predicate "
                          "(tuple predicate) plus test_regr_02_* above (dependsOn chain, same underlying "
                          "activities).")
def test_regr_03_composite_watermark_predicate_and_dependency_chain_present():
    pass


@pytest.mark.skip(reason="REGR-04: real coverage — worker/tests/test_scenario_coverage_extra.py::"
                          "test_regr_04_chunked_historical_seed_generates_the_full_documented_activity_set.")
def test_regr_04_chunked_historical_seed_generates_full_activity_set():
    pass


@pytest.mark.skip(reason="REGR-05: real coverage — worker/tests/test_scenario_coverage_extra.py::"
                          "test_sftp_02_regr_05_blob_events_with_fallback_generates_both_triggers.")
def test_regr_05_blob_events_trigger_with_fallback_generates_both_artifacts():
    pass


@pytest.mark.skip(reason="REGR-06: real coverage — worker/tests/test_scenario_coverage_extra.py::"
                          "test_regr_06_redirect_incompatible_rows_requires_rejects_linked_service_to_validate.")
def test_regr_06_redirect_incompatible_row_settings_requires_rejects_fields_to_validate():
    pass


@pytest.mark.skip(reason="REGR-07: real coverage — worker/tests/test_scenario_coverage_extra.py::"
                          "test_regr_07_workday_and_event_hub_are_selectable_with_matching_worker_backend_fingerprint "
                          "(also confirmed directly in this file's sibling: test_scenario_workday.py::"
                          "test_wd_01_* and test_scenario_event_hub.py::test_evt_01_* for the backend side).")
def test_regr_07_workday_and_event_hub_selectable_across_all_three_registries():
    pass
