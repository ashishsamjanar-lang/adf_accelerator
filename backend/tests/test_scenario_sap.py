"""Scenario tests — §1 SAP (docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md).
One test per scenario ID (SAP-01..SAP-18).

Backend only vendors the connector registry (`app/connectors/schemas.py`)
and the naming-*convention* evaluator (`app/standards.py`) — the generator,
qualifier/sanitizer, and structural `validate_draft` checks live only in
`worker/app` (see DEVELOPMENT_NOTES.md's parity note). Scenarios needing those are left
skipped here with a pointer to their real worker-side coverage, rather than
faked with hand-typed literals.
"""
from __future__ import annotations

import pytest

from app.connectors.schemas import ALL_CONNECTORS, CONNECTOR_SCHEMAS


def test_sap_01_native_rfc_odp_connector_not_in_registry():
    """SAP-01 [GAP]: ADF's native SapTable (RFC/ODP) connector is genuinely
    absent from the registry — only sap_hana/sap_bw/sap_ecc/sap_cloud_for_customer
    exist. Real code: app/connectors/schemas.py."""
    assert "sap_table_odp" not in ALL_CONNECTORS
    assert "sap_table" not in ALL_CONNECTORS
    sap_connectors = {k for k in ALL_CONNECTORS if k.startswith("sap")}
    assert sap_connectors == {"sap_hana", "sap_bw", "sap_ecc", "sap_cloud_for_customer"}


def test_sap_17_hana_multi_tenant_db_has_no_dedicated_tenant_field():
    """SAP-17 [VERIFY]: multi-tenant HANA (MDC) needs a tenant DB identifier
    distinct from "database name" — confirmed real gap: sap_hana's field
    list has no `tenant`-named field (only server/port/userName/password)."""
    field_keys = {f.key for f in CONNECTOR_SCHEMAS["sap_hana"].fields}
    assert not any("tenant" in k.lower() for k in field_keys)


@pytest.mark.skip(reason="SAP-02/[VERIFY]: the always-on ADF hard-name-rule check (does the slash in "
                          "/BIC/AZCUSTOMER survive sanitization into a declared artifact name) needs the real "
                          "generator + validator, worker-only. Real coverage: "
                          "worker/tests/test_scenario_smoke_dummy_artifacts.py::test_sap02_slash_named_bw_object_...")
def test_sap_02_slash_in_bw_object_name_is_sanitized_or_blocked():
    pass


@pytest.mark.skip(reason="SAP-03/[DESIGN]: sanitize_for_adf_name (worker/app/generator/qualifier.py) doesn't "
                          "truncate at all, so no length-based collision is possible today — worker-only code. "
                          "Real coverage: worker/tests/test_qualifier.py's sanitize tests confirm no truncation "
                          "occurs for /BIC/AZFIGLACCT vs /BIC/AZFIGLACT2.")
def test_sap_03_truncating_sanitizer_could_collide_bic_and_bi0():
    pass


@pytest.mark.skip(reason="SAP-04/[VERIFY]: always-on ADF hard-name-rule, worker-only. Real coverage: "
                          "worker/tests/test_scenario_coverage_extra.py::test_name_02_leading_digit_pipeline_name_is_blocked.")
def test_sap_04_leading_digit_technical_name_is_blocked():
    pass


@pytest.mark.skip(reason="SAP-05/[VERIFY]: there is no ':snake' (or any) per-segment naming-pattern modifier "
                          "feature anywhere in the codebase — only whole-declared-name case validation exists "
                          "(naming.py's 'case' rule). Confirmed real [GAP], not yet a code path to test.")
def test_sap_05_snake_case_modifier_on_long_z_table_name():
    pass


@pytest.mark.skip(reason="SAP-05 (05b)/[VERIFY]: qualifier-token case-insensitive collision detection, "
                          "worker-only. Real coverage: worker/tests/test_qualifier.py::"
                          "test_case_insensitive_profile_does_not_fold_case_for_collision_purposes — confirms "
                          "this is a real, currently-undetected gap.")
def test_sap_05_case_insensitive_object_names_do_not_collide_silently():
    pass


@pytest.mark.skip(reason="SAP-06/[DESIGN]: no MANDT-filter guidance/warning exists anywhere in the codebase — "
                          "undecided product question, no code to test.")
def test_sap_06_missing_mandt_client_filter_warns():
    pass


@pytest.mark.skip(reason="SAP-07/[GAP]: check_sap_extraction_support (worker/app/generator/qualifier.py) is the "
                          "real code for this and DOES warn on BSEG — but it lives only in worker. Real coverage: "
                          "worker/tests/test_qualifier.py::test_known_pool_cluster_table_warns.")
def test_sap_07_cluster_table_bseg_onboarded_as_plain_table():
    pass


@pytest.mark.skip(reason="SAP-08/[VERIFY]: no target-column-name-generation or row-hash function exists anywhere "
                          "in worker/app (grepped for target_column_name/_source_row_hash/flatten — no matches) "
                          "— confirmed this part of the metadata contract described in the docs isn't implemented "
                          "as distinct code, so there's nothing to unit-test a 40-char-name-truncation claim against.")
def test_sap_08_forty_char_field_name_not_truncated():
    pass


@pytest.mark.skip(reason="SAP-09/[VERIFY]: no `dataClassification` field exists on the columns[] contract "
                          "anywhere in the codebase — confirmed real [GAP], no code path to test.")
def test_sap_09_matnr_length_extension_18_to_40_is_flagged():
    pass


@pytest.mark.skip(reason="SAP-10/[VERIFY]: no `watermarkColumnType` cross-check against the actual value shape "
                          "exists in worker/app/generator/spec.py's validate_draft — confirmed real [GAP].")
def test_sap_10_zero_padded_numeric_key_stored_as_string():
    pass


@pytest.mark.skip(reason="SAP-11/[VERIFY]: requires a live SAP connection / real query execution against sentinel "
                          "date values — no unit-testable code path (this app only generates the SQL text, it "
                          "never runs it).")
def test_sap_11_sentinel_empty_date_not_null_is_handled():
    pass


@pytest.mark.skip(reason="SAP-12/[VERIFY]: '_source_system' is not an auto-injected concept anywhere in "
                          "worker/app/generator/codegen.py — it would have to be configured manually as a "
                          "customMetadata entry (see REGR-01 coverage in worker/tests/test_scenario_coverage_extra.py), "
                          "which is a materially different (manual, not automatic) guarantee than the scenario assumes.")
def test_sap_12_two_sap_systems_have_distinct_source_system_values():
    pass


@pytest.mark.skip(reason="SAP-13/[VERIFY]: batchCount-is-per-draft is real, worker-only (SpecDraft is a worker "
                          "type). Real coverage: worker/tests/test_scenario_coverage_extra.py::"
                          "test_sap_13_batch_count_is_a_genuine_per_draft_field_not_a_shared_default.")
def test_sap_13_batch_count_is_per_orchestrator_not_project_default():
    pass


@pytest.mark.skip(reason="SAP-14/[DESIGN]: an ODP-delta-queue-reinit-recovery distinction is a documentation/"
                          "runbook question, not something the codebase encodes as a checkable rule.")
def test_sap_14_odp_delta_queue_reinit_has_bigger_blast_radius():
    pass


@pytest.mark.skip(reason="SAP-15/[VERIFY]: validate_draft's presence-only check on sourceObject is real, "
                          "worker-only. Real coverage: worker/tests/test_scenario_coverage_extra.py::"
                          "test_sap_15_business_description_source_object_passes_presence_only_validation.")
def test_sap_15_business_description_instead_of_technical_name_passes_validate():
    pass


@pytest.mark.skip(reason="SAP-16/[VERIFY]: chunkColumn-is-a-single-string-field is real, worker-only. Real "
                          "coverage: worker/tests/test_scenario_coverage_extra.py::"
                          "test_sap_16_chunk_column_is_a_single_free_text_field_no_composite_support.")
def test_sap_16_fiscal_period_chunking_not_expressible_today():
    pass


@pytest.mark.skip(reason="SAP-18/[DESIGN]: environment drift between Dev/QA/Prod SAP transports requires a live "
                          "SAP connection this tool never makes (confirmed: no such connection exists anywhere "
                          "in worker/app or backend/app) — an operational/runbook concern, not a code path.")
def test_sap_18_environment_drift_field_missing_in_prod_not_caught_by_validate():
    pass
