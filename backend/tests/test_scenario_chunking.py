"""Scenario tests — §15 Chunking & high-volume load edge cases
(docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md). One test per scenario ID
(CHUNK-01..CHUNK-10). See test_scenario_sap.py's docstring for the
worker/backend split this file follows — the chunk-plan generator lives
only in worker/app.
"""
from __future__ import annotations

import pytest


@pytest.mark.skip(reason="CHUNK-01/[VERIFY]: re-run only picking up PENDING/FAILED chunks is real, worker-only. "
                          "Real coverage: worker/tests/test_scenario_coverage_extra.py::"
                          "test_chunk_01_03_generate_chunk_plan_sql_is_idempotent_via_where_not_exists (the "
                          "LookupPendingChunks query filters on Status IN ('PENDING','FAILED')).")
def test_chunk_01_rerun_only_picks_up_pending_and_failed_chunks():
    pass


@pytest.mark.skip(reason="CHUNK-02/[VERIFY]: maxParallelChunks-is-per-draft is the same real, worker-only "
                          "mechanism already covered generically by worker/tests/test_scenario_coverage_extra.py's "
                          "per-draft field tests (batchCount/retryCount) — not yet a dedicated maxParallelChunks "
                          "test.")
def test_chunk_02_max_parallel_chunks_is_per_object_not_shared_default():
    pass


@pytest.mark.skip(reason="CHUNK-03/[VERIFY]: idempotent chunk-plan generation via WHERE NOT EXISTS is real, "
                          "worker-only. Real coverage: worker/tests/test_scenario_coverage_extra.py::"
                          "test_chunk_01_03_generate_chunk_plan_sql_is_idempotent_via_where_not_exists.")
def test_chunk_03_generating_chunk_plan_twice_does_not_duplicate_ranges():
    pass


@pytest.mark.skip(reason="CHUNK-04/[VERIFY]: pathological chunk-count behavior on a gappy chunk column only "
                          "manifests against a real table's actual row distribution — no unit-testable code path "
                          "(the generated SQL computes chunk count from MIN/MAX bounds divided by chunk size, "
                          "confirmed by reading _chunked_load_activities in worker/app/generator/codegen.py — "
                          "gaps in the ID range are a known, accepted limitation of that approach, not something "
                          "code can be asserted against without a real database).")
def test_chunk_04_gappy_chunk_column_does_not_produce_pathological_chunk_count():
    pass


@pytest.mark.skip(reason="CHUNK-05/[VERIFY]: distinguishing 'still pending' from 'all chunks done, merge broke' "
                          "needs a real run's actual chunk-plan/merge-step statuses — no unit-testable code path.")
def test_chunk_05_all_chunks_succeeded_but_merge_fails_is_diagnosed_distinctly():
    pass


@pytest.mark.skip(reason="CHUNK-06/[VERIFY]: documents a real, confirmed gap — worker-only. Real coverage: "
                          "worker/tests/test_scenario_coverage_extra.py::"
                          "test_chunk_06_chunking_combined_with_delta_load_is_not_rejected_by_validate_draft.")
def test_chunk_06_chunking_restricted_to_initial_load_not_delta_load():
    pass


@pytest.mark.skip(reason="CHUNK-07/[VERIFY]: chunkColumn==watermarkColumn state-leakage between the chunked seed "
                          "and the ongoing incremental pipeline is a live-execution/control-table-state property "
                          "— no unit-testable code path (the generator treats the two pipelines' parameters "
                          "independently; whether their real runtime values conflict needs a real control table).")
def test_chunk_07_chunk_column_same_as_watermark_column_has_no_state_leakage():
    pass


@pytest.mark.skip(reason="CHUNK-08/[VERIFY]: documents a real, confirmed gap — worker-only. Real coverage: "
                          "worker/tests/test_scenario_coverage_extra.py::"
                          "test_chunk_08_guid_chunk_column_is_accepted_with_no_type_check.")
def test_chunk_08_guid_chunk_column_is_rejected_or_warned():
    pass


@pytest.mark.skip(reason="CHUNK-09/[VERIFY]: two objects' chunk plans not cross-referencing each other is "
                          "guaranteed by construction — confirmed by reading the real DDL (worker/app/generator/"
                          "codegen.py's adf_chunk_plan table has PRIMARY KEY (TaskId, ChunkId), and TaskId is a "
                          "database IDENTITY column per-object, same root cause as WM-10 in test_scenario_watermark.py) "
                          "— a real, reassuring finding, not a gap, but not yet captured in a dedicated test.")
def test_chunk_09_two_objects_chunk_plans_in_one_batch_do_not_cross_reference():
    pass


@pytest.mark.skip(reason="CHUNK-10/[VERIFY]: single-numeric-chunkColumn limitation (no composite fiscal-period "
                          "support) is real, worker-only. Real coverage: worker/tests/test_scenario_coverage_extra.py::"
                          "test_sap_16_chunk_column_is_a_single_free_text_field_no_composite_support.")
def test_chunk_10_sap_fiscal_period_chunking_falls_short_of_144_chunk_design():
    pass
