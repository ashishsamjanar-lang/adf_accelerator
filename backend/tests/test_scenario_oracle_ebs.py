"""Scenario tests — §4 Oracle EBS (docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md).
One test per scenario ID (EBS-01..EBS-08). See test_scenario_sap.py's
docstring for the worker/backend split this file follows — Oracle EBS uses
the generic `oracle` connector (no dedicated `oracle_ebs` registry entry
exists, confirmed by grepping app/connectors/schemas.py).
"""
from __future__ import annotations

import pytest


@pytest.mark.skip(reason="EBS-01/[VERIFY]: this scenario's own premise turned out to be wrong once checked "
                          "against real code — worker-only. Real coverage: worker/tests/test_qualifier.py::"
                          "test_oracle_upper_cases_a_lowercase_object confirms the `oracle` connector's qualifier "
                          "profile *uppercases* object names (case_convention='upper'), it does not lowercase "
                          "them. `GL_JE_LINES` already being upper-case round-trips unchanged; a lowercase input "
                          "like `gl_je_lines` resolves to `GL_JE_LINES`, not `gl_je_lines`.")
def test_ebs_01_all_caps_underscored_names_lowercase_cleanly():
    pass


@pytest.mark.skip(reason="EBS-02/[DESIGN]: no dedicated org-filter field exists (only free-text sourceFilter) — "
                          "undecided product question, same shape as SAP-06's MANDT question, no code path.")
def test_ebs_02_org_id_multi_org_filter_relies_on_free_text_source_filter():
    pass


@pytest.mark.skip(reason="EBS-04/[VERIFY]: schema-qualified synonym resolution needs the real qualifier, "
                          "worker-only — the `oracle` profile is (schema, object) with upper-case folding "
                          "(confirmed via worker/tests/test_qualifier.py), which resolves whatever schema string "
                          "is given (e.g. 'APPS') literally; it has no concept of 'this is a synonym, the real "
                          "owning schema is different' — that distinction isn't and can't be made from the "
                          "object name alone.")
def test_ebs_04_schema_qualified_synonym_expressed_cleanly():
    pass


@pytest.mark.skip(reason="EBS-05/[VERIFY]: date-only vs. datetime watermark precision only matters against real "
                          "row data at runtime — no unit-testable code path (the generator always emits the same "
                          "`>` comparison regardless of the underlying column's actual precision).")
def test_ebs_05_date_only_watermark_precision_does_not_reprocess_or_skip_a_day():
    pass


@pytest.mark.skip(reason="EBS-06/[VERIFY]: same root cause as XSRC-02 — chunk/watermark TaskId scoping across "
                          "source systems on a shared target is guaranteed by construction (TaskId is a database "
                          "IDENTITY column) — real, not yet a dedicated test.")
def test_ebs_06_chunk_status_scoped_per_source_system_on_shared_target():
    pass


@pytest.mark.skip(reason="EBS-07/[VERIFY]: no cross-artifact dangling-reference validation exists anywhere in "
                          "worker/app/validator/rules.py — each generated artifact set is validated in isolation, "
                          "so removing one source's artifacts genuinely can't reference/flag an unrelated "
                          "pipeline. Real, reassuring, not yet a dedicated test.")
def test_ebs_07_disabling_ebs_orchestrator_does_not_flag_saps_pipeline():
    pass


@pytest.mark.skip(reason="EBS-08/[DESIGN]: no precision/scale concept exists on the columns[] contract anywhere "
                          "in worker/app — confirmed real [GAP], undecided how to express an unbounded Oracle "
                          "NUMBER.")
def test_ebs_08_unbounded_number_column_does_not_force_premature_precision():
    pass
