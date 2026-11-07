"""Scenario tests — §2 Salesforce (docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md).
One test per scenario ID (SF-01..SF-08). See test_scenario_sap.py's
docstring for the worker/backend split this file follows.
"""
from __future__ import annotations

import pytest


@pytest.mark.skip(reason="SF-01/[VERIFY]: this was a REAL, CONFIRMED BUG, now fixed — sanitize_for_adf_name "
                          "(worker/app/generator/qualifier.py, mirrored in frontend/lib/adf/qualifier.ts) used to "
                          "collapse Salesforce's literal `__c`/`__r` custom-field suffix into `_c`/`_r`. Fixed "
                          "2026-09-14 (only illegal-character runs collapse now; untouched double underscores "
                          "survive). Worker-only. Real coverage: worker/tests/test_qualifier.py::"
                          "test_sanitize_preserves_a_legitimate_double_underscore_not_touching_any_illegal_character "
                          "and frontend/lib/adf/qualifier.test.ts's matching case.")
def test_sf_01_double_underscore_custom_field_suffix_survives_sanitization():
    pass


@pytest.mark.skip(reason="SF-02/[VERIFY]: no 15-char/18-char Salesforce-Id normalization exists anywhere in "
                          "worker/app (grepped — no matches) — confirmed real [GAP].")
def test_sf_02_fifteen_vs_eighteen_char_id_forms_do_not_double_count():
    pass


@pytest.mark.skip(reason="SF-03/[VERIFY]: watermark round-trip precision through the control table only matters "
                          "once real data flows through a real SQL Server column — no unit-testable code path "
                          "(the generator only emits SQL text, e.g. worker/tests/test_scenario_coverage_extra.py "
                          "confirms the query shape, but round-trip precision needs a real database).")
def test_sf_03_systemmodstamp_millisecond_precision_round_trips():
    pass


@pytest.mark.skip(reason="SF-04/[VERIFY]: batchCount-is-per-draft is real, worker-only, and already covered "
                          "generically by worker/tests/test_scenario_coverage_extra.py::"
                          "test_sap_13_batch_count_is_a_genuine_per_draft_field_not_a_shared_default (same field, "
                          "same mechanism — not source-type-specific).")
def test_sf_04_bulk_api_batch_count_is_independently_settable():
    pass


@pytest.mark.skip(reason="SF-05/[VERIFY]: naming-pattern-collision between Account and Account__c depends on the "
                          "real qualifier/sanitizer, worker-only. Given the SF-01 fix, `Account` -> `Account` and "
                          "`Account__c` -> `Account__c` sanitize to distinct tokens (no collision) — not yet "
                          "captured in a dedicated test.")
def test_sf_05_account_vs_account_custom_object_do_not_collide():
    pass


@pytest.mark.skip(reason="SF-06/[VERIFY]: no dotted-relationship-field flattening exists anywhere in worker/app "
                          "(same root cause as WD-03 — no flatten function exists) — confirmed real [GAP].")
def test_sf_06_dotted_relationship_field_name_flattens_cleanly():
    pass


@pytest.mark.skip(reason="SF-07/[DESIGN]: no _source_row_hash function exists anywhere in worker/app (grepped — "
                          "no matches) — there is no business-column-list concept to include CurrencyIsoCode in.")
def test_sf_07_multicurrency_hash_includes_currency_iso_code():
    pass


@pytest.mark.skip(reason="SF-08/[VERIFY]: deleteHandling exists now (soft_delete/cdc, see WM-07/WM-08), but nothing "
                          "cross-checks a Salesforce recycle-bin retention window against it — confirmed real [GAP].")
def test_sf_08_soft_delete_window_compatible_with_hard_delete_handling_mode():
    pass
