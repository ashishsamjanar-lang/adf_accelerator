"""Scenario tests — §10 Cross-source fan-in & shared-target collisions
(docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md). Also covers EBS-03 (§4), which
points here. One test per scenario ID (XSRC-01..XSRC-10).

None of these are backend-testable: cross-source merge/fan-in behavior
depends on the Silver/Gold medallion SQL-merge generator
(worker/app/generator/medallion.py) and/or live execution against a real
database — neither exists in backend/app. Each reason below is grounded in
what was actually read in that module or elsewhere in worker/app, not
guessed.
"""
from __future__ import annotations

import pytest


@pytest.mark.skip(reason="XSRC-01/[VERIFY]: the Silver/Gold SCD2 merge's ON-clause key list is exactly whatever "
                          "business/natural keys the author configures (worker/app/generator/medallion.py) — "
                          "there is no automatic `_source_system` injection into that key list, so two sources "
                          "sharing a natural key WOULD merge into one row unless the author explicitly adds "
                          "source-system to the keys. A real, confirmed gap, not yet captured in a dedicated test.")
def test_xsrc_01_merge_key_includes_source_system_not_just_natural_key():
    pass


@pytest.mark.skip(reason="XSRC-02/[VERIFY]: independent per-orchestrator TaskId sequences are guaranteed by "
                          "construction — same root cause as WM-10/CHUNK-09 (TaskId is a database IDENTITY "
                          "column) — real but not yet a dedicated test for the two-orchestrators-same-target case.")
def test_xsrc_02_two_orchestrators_share_target_but_independent_control_state():
    pass


@pytest.mark.skip(reason="XSRC-03/[VERIFY]: no cross-pipeline trigger `dependsOn` generation exists anywhere in "
                          "worker/app/generator/codegen.py — every generated trigger only ever references its own "
                          "pipeline. Confirmed real [GAP]: one pipeline's trigger can't depend on another's run.")
def test_xsrc_03_ebs_tumbling_window_depends_on_sap_pipeline_success():
    pass


@pytest.mark.skip(reason="XSRC-04/[VERIFY]: same root cause as SAP-12 — `_source_system` is not an "
                          "auto-injected column anywhere in the generator; it would have to be manually "
                          "configured per-source as a customMetadata entry.")
def test_xsrc_04_source_system_populated_per_row_for_reconciliation():
    pass


@pytest.mark.skip(reason="XSRC-05/[VERIFY]: targetPath/target table names are free-text strings on SpecDraft, "
                          "never a Catalog-tracked artifact reference (confirmed: no ArtifactType for a 'target "
                          "table' exists in backend/app/models.py) — so there is genuinely nothing to dangle when "
                          "one source's pipeline is removed. A real, reassuring finding, not a gap.")
def test_xsrc_05_removing_one_fan_in_source_has_nothing_to_dangle_against():
    pass


@pytest.mark.skip(reason="XSRC-06/[DESIGN]: no same-target-collision detection (warn or block) exists anywhere "
                          "in worker/app/generator/spec.py's validate_draft or backend/app/standards.py — "
                          "undecided product question, confirmed no code path.")
def test_xsrc_06_new_source_targeting_existing_table_is_flagged_as_warn():
    pass


@pytest.mark.skip(reason="XSRC-07/[VERIFY]: the shared WatermarkValue column genuinely is a plain NVARCHAR "
                          "string in the control table DDL (worker/app/generator/codegen.py's "
                          "_control_table_sql) with no type discrimination — holding both a datetime string and "
                          "an opaque token works today by construction (both are just strings), matching the "
                          "scenario's expectation. Real, but not yet a dedicated test.")
def test_xsrc_07_watermark_value_column_holds_both_datetime_string_and_opaque_pointer():
    pass


@pytest.mark.skip(reason="XSRC-08/[DESIGN]: real, confirmed concern — worker/app/generator/medallion.py's SCD2 "
                          "close-out UPDATE only scopes on the configured business keys (see XSRC-01 above); if "
                          "those keys don't include source-system, one source's load could close out a different "
                          "source's current row sharing the same natural key. Undecided whether this is "
                          "considered the author's responsibility (configure source-system into the keys) or a "
                          "product gap.")
def test_xsrc_08_scd2_close_out_scoped_per_source_system_and_natural_key():
    pass


@pytest.mark.skip(reason="XSRC-09/[DESIGN]: no `_dq_status` concept exists anywhere in the generator (only the "
                          "separate, author-configured Data Quality rules feature) — there is no shared "
                          "vocabulary/layer to test because the scenario's premised feature doesn't exist yet.")
def test_xsrc_09_dq_status_vocabulary_is_applied_by_one_shared_layer():
    pass


@pytest.mark.skip(reason="XSRC-10/[VERIFY]: 'mart refresh waits for all same-cadence orchestrators' needs a real "
                          "multi-pipeline run to observe an aggregate condition's actual behavior — the "
                          "generated If Condition's expression is real code (worker/app/generator/codegen.py) but "
                          "confirming it requires ALL (not ANY) needs a dedicated test not yet written.")
def test_xsrc_10_mart_refresh_waits_for_all_same_cadence_orchestrators():
    pass
