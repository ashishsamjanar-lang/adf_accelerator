"""Scenario tests — §8 Event Hub / streaming (docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md).
One test per scenario ID (EVT-01..EVT-07). See test_scenario_sap.py's
docstring for the worker/backend split this file follows.
"""
from __future__ import annotations

import pytest

from app.connectors.schemas import ALL_CONNECTORS, CONNECTOR_SCHEMAS


def test_evt_01_azure_event_hub_connector_selectable_with_correct_adf_types():
    """EVT-01 [REGRESSION]: `azure_event_hub` is selectable and its registry
    entry declares the correct ADF linked-service/dataset types."""
    assert "azure_event_hub" in ALL_CONNECTORS
    schema = CONNECTOR_SCHEMAS["azure_event_hub"]
    assert schema.adf_type == "AzureEventHubs"
    assert schema.dataset_type == "AvroFormat"


@pytest.mark.skip(reason="EVT-02/[VERIFY]: millisecond-collision dedupe/ordering depends on the composite "
                          "watermark generator (real, worker-only, covered generically by "
                          "worker/tests/test_scenario_smoke_dummy_artifacts.py's composite-watermark test) but no "
                          "test exercises event_ts+event_id specifically yet.")
def test_evt_02_same_millisecond_events_dedupe_and_order_correctly():
    pass


@pytest.mark.skip(reason="EVT-03/[VERIFY]: confirmed by reading the real trigger generator "
                          "(worker/app/generator/codegen.py's _trigger_artifact) — a TumblingWindowTrigger's "
                          "typeProperties never includes a self-referencing `dependsOn` entry (only frequency/"
                          "interval/startTime/delay/maxConcurrency/retryPolicy). Confirmed real [GAP]: nothing "
                          "prevents window overlap today.")
def test_evt_03_tumbling_window_has_self_dependency_wired():
    pass


@pytest.mark.skip(reason="EVT-04/[DESIGN]/[VERIFY]: schema-drift handling for a new Avro field needs a live "
                          "streaming run to observe — no unit-testable code path (no `_dq_status` concept exists "
                          "in the generator at all, only the separate Data Quality rules feature, which is "
                          "author-configured, not automatic on schema drift).")
def test_evt_04_new_avro_field_mid_stream_lands_as_warn_not_hard_fail():
    pass


@pytest.mark.skip(reason="EVT-05/[DESIGN]: trigger-type defaulting by source volume is a UI/product decision "
                          "(frontend Build form), not something the worker generator decides — no backend code "
                          "path.")
def test_evt_05_tumbling_window_not_defaulted_for_low_volume_source():
    pass


@pytest.mark.skip(reason="EVT-06/[VERIFY]: documents a real, confirmed gap — worker-only. Real coverage: "
                          "worker/tests/test_scenario_coverage_extra.py::"
                          "test_evt_06_watermark_table_parameter_is_always_present_even_when_unused.")
def test_evt_06_event_hub_ingestion_does_not_force_watermark_table_param():
    pass


@pytest.mark.skip(reason="EVT-07/[VERIFY]: writeBehavior defaults to 'Insert' (never Upsert) is real, "
                          "worker-only. Real coverage: worker/tests/test_scenario_coverage_extra.py::"
                          "test_evt_07_write_behavior_defaults_to_insert_never_upsert — confirms this is already "
                          "working correctly, not a gap.")
def test_evt_07_append_only_target_does_not_default_to_upsert():
    pass
