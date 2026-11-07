"""Tests for `/api/v1/projects/{id}/onboarding/mapping-profiles` (AGENTS.md §26.3/§26.7)."""
from __future__ import annotations

from fastapi.testclient import TestClient

URL = "/api/v1/projects/proj-a/onboarding/mapping-profiles"


def test_save_list_and_upsert_by_case_insensitive_name(client: TestClient, seed: dict[str, str]) -> None:
    first = client.post(URL, json={"name": " SAP export ", "headerMap": {"Tab": "source_object"}})
    assert first.status_code == 200, first.text
    assert first.json()["name"] == "SAP export"

    again = client.post(URL, json={"name": "sap EXPORT", "headerMap": {"System": "source_system"}})
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]

    rows = client.get(URL).json()
    assert len(rows) == 1
    assert rows[0]["headerMap"] == {"System": "source_system"}


def test_rejects_blank_name_and_empty_map(client: TestClient, seed: dict[str, str]) -> None:
    assert client.post(URL, json={"name": " ", "headerMap": {"a": "b"}}).json()["error"]["code"] == "mapping_profile_name_required"
    assert client.post(URL, json={"name": "x", "headerMap": {}}).json()["error"]["code"] == "mapping_profile_empty"


def test_delete_and_404(client: TestClient, seed: dict[str, str]) -> None:
    pid = client.post(URL, json={"name": "p", "headerMap": {"a": "source_object"}}).json()["id"]
    assert client.delete(f"{URL}/{pid}").status_code == 204
    assert client.get(URL).json() == []
    assert client.delete(f"{URL}/{pid}").status_code == 404


def test_viewer_can_list_but_not_save(client: TestClient, seed: dict[str, str], as_user) -> None:
    as_user("VIEWER")
    assert client.get(URL).status_code == 200
    assert client.post(URL, json={"name": "p", "headerMap": {"a": "source_object"}}).status_code == 403


def test_foreign_team_is_forbidden(client: TestClient, seed: dict[str, str], as_foreign_user) -> None:
    assert client.get(URL).status_code == 403


def test_bulk_import_records_applied_profile(client: TestClient, seed: dict[str, str]) -> None:
    pid = client.post(URL, json={"name": "SAP export", "headerMap": {"TAB": "source_object"}}).json()["id"]
    row = {
        "source_system": "erp", "source_object": "customer", "source_type": "table", "target_type": "table",
        "target_file_format": "Parquet", "loading_behavior": "FullLoad",
    }
    bulk = "/api/v1/projects/proj-a/onboarding/objects/bulk"
    assert client.post(bulk, json={"objects": [row], "file_name": "x.csv", "mapping_profile_id": pid}).status_code == 200
    history = client.get("/api/v1/projects/proj-a/onboarding/imports").json()
    assert history[0]["mappingProfileId"] == pid
    assert history[0]["mappingProfileName"] == "SAP export"

    bad = client.post(bulk, json={"objects": [row], "mapping_profile_id": "omp-nope"})
    assert bad.json()["error"]["code"] == "mapping_profile_not_found"
