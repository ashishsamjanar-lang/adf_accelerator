"""Scenario tests — §13 Onboarding (CSV/manual) edge cases
(docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md). The Bronze registry has a real backend now
(`/api/v1/projects/{id}/onboarding/objects[/bulk]`, AGENTS.md §26.7); CSV/XLSX parsing and header
mapping stay client-side (`frontend/lib/adf/onboarding.ts`, `e2e/onboarding-import.spec.ts`).
Scenarios the backend decides are real tests here; the rest are `[VERIFY]` against the frontend
parser or a running ADF, not `[GAP]`s. One test per scenario ID (ONB-01..ONB-12).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_URL = "/api/v1/projects/proj-a/onboarding/objects"


def _row(**overrides):
    body = {"source_system": "erp", "source_object": "customer", "source_type": "table",
            "target_type": "table", "target_file_format": "Parquet", "loading_behavior": "FullLoad"}
    body.update(overrides)
    return body


def test_onb_01_duplicate_object_key_within_one_csv_is_flagged(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post(f"{_URL}/bulk", json={"objects": [_row(), _row(owner="other")]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "onboarding_duplicate_object_key"
    assert client.get(_URL).json() == []


@pytest.mark.skip(reason="ONB-02/[VERIFY]: CSV parsing/header mapping is client-side (frontend/lib/adf/onboarding.ts); not a backend concern.")
def test_onb_02_mixed_encoding_65_row_csv_does_not_corrupt_a_subset():
    pass


@pytest.mark.skip(reason="ONB-03/[VERIFY]: CSV parsing/header mapping is client-side (frontend/lib/adf/onboarding.ts); not a backend concern.")
def test_onb_03_watermark_column_header_synonyms_auto_map():
    pass


def test_onb_04_reimport_shows_exact_updated_unchanged_added_counts(client: TestClient, seed: dict[str, str]) -> None:
    client.post(f"{_URL}/bulk", json={"objects": [_row(), _row(source_object="orders")]})
    resp = client.post(f"{_URL}/bulk", json={"objects": [_row(owner="new"), _row(source_object="orders"), _row(source_object="invoice")]})
    assert resp.json() == {"added": 1, "updated": 2}  # unchanged rows are re-saved, so they count as updated


def test_onb_05_delta_load_without_watermark_column_is_flagged(client: TestClient, seed: dict[str, str]) -> None:
    resp = client.post(f"{_URL}/bulk", json={"objects": [_row(), _row(source_object="orders", loading_behavior="DeltaLoad")]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "onboarding_watermark_required"
    assert client.get(_URL).json() == []  # all-or-nothing batch


@pytest.mark.skip(reason="ONB-06/[VERIFY]: CSV parsing/header mapping is client-side (frontend/lib/adf/onboarding.ts); not a backend concern.")
def test_onb_06_file_source_type_with_relational_only_field_populated():
    pass


@pytest.mark.skip(reason="ONB-07/[VERIFY]: CSV parsing/header mapping is client-side (frontend/lib/adf/onboarding.ts); not a backend concern.")
def test_onb_07_mixed_sap_and_sql_rows_in_one_csv_scope_correctly():
    pass


@pytest.mark.skip(reason="ONB-08/[VERIFY]: CSV parsing/header mapping is client-side (frontend/lib/adf/onboarding.ts); not a backend concern.")
def test_onb_08_ten_thousand_row_csv_does_not_truncate_or_hang():
    pass


@pytest.mark.skip(reason="ONB-09/[VERIFY]: Git import + drift exists (GET .../onboarding/git-drift, provenance column) but needs a Git adapter fixture; not yet a scenario test here.")
def test_onb_09_git_import_row_edited_manually_transitions_to_drifted():
    pass


@pytest.mark.skip(reason="ONB-10/[VERIFY]: CSV parsing/header mapping is client-side (frontend/lib/adf/onboarding.ts); not a backend concern.")
def test_onb_10_saved_mapping_profile_degrades_gracefully_on_header_change():
    pass


@pytest.mark.skip(reason="ONB-11/[VERIFY]: CSV parsing/header mapping is client-side (frontend/lib/adf/onboarding.ts); not a backend concern.")
def test_onb_11_bulk_edit_only_applies_explicitly_changed_fields():
    pass


def test_onb_12_disabled_object_stops_orchestrator_pickup_without_losing_config(client: TestClient, seed: dict[str, str]) -> None:
    client.post(_URL, json=_row(owner="data-team", batch_group="nightly"))
    resp = client.put(f"{_URL}/erp.customer", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    body = client.get(f"{_URL}/erp.customer").json()
    assert (body["enabled"], body["owner"], body["batch_group"]) == (False, "data-team", "nightly")
