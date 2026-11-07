"""Scenario tests — §6 SFTP / flat files (docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md).
One test per scenario ID (SFTP-01..SFTP-08).

Backend vendors the connector registry only (`app/connectors/schemas.py`) —
the generator (dual-trigger generation, dataset defaults) lives only in
`worker/app`. See test_scenario_sap.py's docstring for the general split.
"""
from __future__ import annotations

import pytest

from app.connectors.schemas import CONNECTOR_SCHEMAS


def test_sftp_08_private_key_field_is_a_key_vault_secret_name_never_a_version_pin():
    """SFTP-08 [VERIFY]: `privateKeyContent` must be documented/shaped as a
    Key Vault *secret name* (always resolves the current version), never a
    version-pinned reference — real code: the field is `is_secret=True` with
    help text naming it a secret name, and its placeholder carries no
    version segment."""
    field = next(f for f in CONNECTOR_SCHEMAS["sftp"].fields if f.key == "privateKeyContent")
    assert field.is_secret is True
    assert "secret name" in field.label.lower() or "secret" in field.help.lower()
    assert "/" not in field.placeholder  # no embedded version segment


@pytest.mark.skip(reason="SFTP-01/[DESIGN]: no debounce/settle-time exists for blobEvents triggers anywhere in "
                          "worker/app/generator/codegen.py — undecided product question, confirmed no code path.")
def test_sftp_01_partial_upload_file_arrival_has_no_debounce():
    pass


@pytest.mark.skip(reason="SFTP-02/[REGRESSION]: dual blobEvents+fallback trigger generation is real, worker-only. "
                          "Real coverage: worker/tests/test_scenario_coverage_extra.py::"
                          "test_sftp_02_regr_05_blob_events_with_fallback_generates_both_triggers.")
def test_sftp_02_fallback_schedule_trigger_generated_alongside_blob_events():
    pass


@pytest.mark.skip(reason="SFTP-03/[VERIFY]: filename-pattern matching against a live SFTP directory listing "
                          "requires a live connection this app never makes — no unit-testable code path.")
def test_sftp_03_filename_pattern_mismatch_is_rejected_and_logged():
    pass


@pytest.mark.skip(reason="SFTP-04/[VERIFY]: real CSV/delimiter parsing happens inside ADF's own Copy activity at "
                          "runtime, not in this generator — no code here parses file content, so there's nothing "
                          "to unit-test a quote/escape rule against.")
def test_sftp_04_embedded_pipe_inside_quoted_field_parses_correctly():
    pass


@pytest.mark.skip(reason="SFTP-05/[VERIFY]: encoding mismatches only manifest at ADF runtime against real file "
                          "bytes — no unit-testable code path in this generator.")
def test_sftp_05_wrong_encoding_flags_dq_status_instead_of_mojibake():
    pass


@pytest.mark.skip(reason="SFTP-06/[VERIFY]: sourceFirstRowAsHeader is a per-object dataset-draft field already "
                          "(DatasetFormatSettings), which is worker/backend-shared connector-schema shape, not "
                          "generator behavior needing a dummy-data run — the real assertion (two objects with "
                          "independent header settings) needs the Build form's draft persistence, out of this "
                          "suite's reach (no such endpoint exists in backend/app/api yet).")
def test_sftp_06_header_row_presence_is_per_object_not_project_wide():
    pass


@pytest.mark.skip(reason="SFTP-07/[GAP]/[DESIGN]: no filename-date+row-hash dedupe logic exists anywhere in "
                          "worker/app or backend/app (grepped, no matches) — confirmed real gap.")
def test_sftp_07_duplicate_same_day_re_drop_is_deduped_by_filename_date_and_hash():
    pass
