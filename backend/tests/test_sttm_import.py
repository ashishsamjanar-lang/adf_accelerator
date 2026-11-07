"""End-to-end test for the upload -> preview -> commit STTM import flow
(AGENTS.md §33.9: `POST .../sttm-import` + `.../sttm-import/{id}/commit`)."""
from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.onboarding.sttm_parser import _DQ_DATA_START_ROW, _HEADER_FIELDS, _HEADER_START_ROW, _TRANSFORM_DATA_START_ROW


def _workbook_bytes(sheets: list[tuple[str, dict[str, str]]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, header in sheets:
        ws = wb.create_sheet(name)
        ws.cell(row=1, column=1, value="Field")
        ws.cell(row=1, column=2, value="Value")
        for i, field_name in enumerate(_HEADER_FIELDS):
            if field_name in header:
                ws.cell(row=_HEADER_START_ROW + i, column=1, value=field_name)
                ws.cell(row=_HEADER_START_ROW + i, column=2, value=header[field_name])
        ws.cell(row=_TRANSFORM_DATA_START_ROW, column=1, value="conform")
        ws.cell(row=_TRANSFORM_DATA_START_ROW, column=2, value="conform_columns")
        ws.cell(row=_TRANSFORM_DATA_START_ROW, column=3, value="a:b")
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _header(entity: str, **overrides) -> dict[str, str]:
    header = {
        "object_key": entity,
        "tag": "finance",
        "curated_entity": entity,
        "source_object_keys": "sql_server.dbo.src",
        "write_behavior": "append",
        "loading_behavior": "FullLoad",
    }
    header.update(overrides)
    return header


@pytest.fixture(autouse=True)
def _bronze_upstream(client: TestClient, seed: dict[str, str]) -> None:
    """E2E-10: commit rejects upstream keys missing from the Bronze registry, so register the ones
    these workbooks reference."""
    for key, obj in (("sql_server.dbo.src", "dbo.src"), ("manual.src", "src")):
        body = {"object_key": key, "source_system": "sql_server", "source_object": obj, "source_type": "table",
                "target_type": "table", "target_file_format": "Parquet", "loading_behavior": "FullLoad"}
        assert client.post("/api/v1/projects/proj-a/onboarding/objects", json=body).status_code == 201


def test_commit_rejects_source_keys_missing_from_bronze(client: TestClient) -> None:
    content = _workbook_bytes([("orphan", _header("orphan", source_object_keys="nowhere.dbo.x"))])
    upload = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    commit = client.post(f"/api/v1/projects/proj-a/silver-objects/sttm-import/{upload['id']}/commit", json={}).json()
    assert commit["committed"] == []
    assert "nowhere.dbo.x" in commit["skipped"][0]["reason"]


def test_upload_then_commit_creates_registry_rows(client: TestClient, seed: dict[str, str]) -> None:
    content = _workbook_bytes([("gl_transaction", _header("gl_transaction")), ("policy", _header("policy"))])
    upload = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert upload.status_code == 201, upload.text
    imp = upload.json()
    assert imp["sheetTotal"] == 2
    assert imp["sheetValid"] == 2
    assert len(imp["sheets"]) == 2

    commit = client.post(f"/api/v1/projects/proj-a/silver-objects/sttm-import/{imp['id']}/commit", json={})
    assert commit.status_code == 200, commit.text
    result = commit.json()
    assert sorted(result["committed"]) == ["gl_transaction", "policy"]
    assert result["skipped"] == []

    listed = client.get("/api/v1/projects/proj-a/silver-objects")
    assert sorted(o["curatedEntity"] for o in listed.json()) == ["gl_transaction", "policy"]


def test_commit_respects_sheet_name_filter(client: TestClient, seed: dict[str, str]) -> None:
    content = _workbook_bytes([("gl_transaction", _header("gl_transaction")), ("policy", _header("policy"))])
    upload = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    imp = upload.json()
    commit = client.post(
        f"/api/v1/projects/proj-a/silver-objects/sttm-import/{imp['id']}/commit",
        json={"sheetNames": ["gl_transaction"]},
    )
    assert commit.json()["committed"] == ["gl_transaction"]
    listed = client.get("/api/v1/projects/proj-a/silver-objects")
    assert [o["curatedEntity"] for o in listed.json()] == ["gl_transaction"]


def test_upload_rejects_a_workbook_exceeding_the_sheet_limit(client: TestClient, seed: dict[str, str]) -> None:
    from app.onboarding.sttm_parser import MAX_SHEETS_PER_WORKBOOK

    sheets = [(f"obj_{i}", _header(f"obj_{i}")) for i in range(MAX_SHEETS_PER_WORKBOOK + 1)]
    content = _workbook_bytes(sheets)
    upload = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert upload.status_code == 422, upload.text
    assert upload.json()["error"]["code"] == "workbook_too_large"


def test_commit_skips_manual_object_without_confirm_overwrite(client: TestClient, seed: dict[str, str]) -> None:
    manual = client.post(
        "/api/v1/projects/proj-a/silver-objects",
        json={
            "objectKey": "gl_transaction",
            "tag": "finance",
            "curatedEntity": "gl_transaction",
            "sourceObjectKeys": ["manual.src"],
            "transformations": [{"name": "c", "type": "conform_columns", "columns": "a:b"}],
        },
    )
    assert manual.status_code == 201

    content = _workbook_bytes([("gl_transaction", _header("gl_transaction"))])
    upload = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    imp = upload.json()

    commit = client.post(f"/api/v1/projects/proj-a/silver-objects/sttm-import/{imp['id']}/commit", json={})
    assert commit.json()["committed"] == []
    assert len(commit.json()["skipped"]) == 1

    forced = client.post(
        f"/api/v1/projects/proj-a/silver-objects/sttm-import/{imp['id']}/commit", json={"confirmOverwrite": True}
    )
    assert forced.json()["committed"] == ["gl_transaction"]
    got = client.get("/api/v1/projects/proj-a/silver-objects/gl_transaction")
    assert got.json()["provenanceSource"] == "upload"


def test_upload_stashes_existing_version_seen_at_preview_time(client: TestClient, seed: dict[str, str]) -> None:
    # STTM-13 (part 1): a brand-new object_key has no existing row yet.
    content = _workbook_bytes([("gl_transaction", _header("gl_transaction"))])
    upload = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert upload.json()["sheets"][0]["existingVersion"] is None

    client.post(f"/api/v1/projects/proj-a/silver-objects/sttm-import/{upload.json()['id']}/commit", json={})

    # Re-uploading now sees the object that commit just created, at version 1.
    reupload = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert reupload.json()["sheets"][0]["existingVersion"] == 1


def test_concurrent_commit_is_detected_via_expected_versions_not_silently_overwritten(
    client: TestClient, seed: dict[str, str]
) -> None:
    """STTM-13: two imports both preview the *same* pre-existing object_key at
    version 1. One commits first (bumping it to version 2). The second commit
    echoes back the version-1 `expectedVersions` it saw at its own preview
    time — it must be skipped as a conflict, never silently applied on top of
    the first commit's change."""
    content = _workbook_bytes([("gl_transaction", _header("gl_transaction"))])

    # Seed an existing object at version 1 via a first upload+commit.
    first_upload = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    client.post(f"/api/v1/projects/proj-a/silver-objects/sttm-import/{first_upload['id']}/commit", json={})
    assert client.get("/api/v1/projects/proj-a/silver-objects/gl_transaction").json()["version"] == 1

    # Two users both preview a fresh upload against that version-1 object.
    upload_a = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    upload_b = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    assert upload_a["sheets"][0]["existingVersion"] == 1
    assert upload_b["sheets"][0]["existingVersion"] == 1

    # User A commits first — bumps gl_transaction to version 2.
    commit_a = client.post(
        f"/api/v1/projects/proj-a/silver-objects/sttm-import/{upload_a['id']}/commit",
        json={"expectedVersions": {"gl_transaction": 1}},
    )
    assert commit_a.json()["committed"] == ["gl_transaction"]
    assert client.get("/api/v1/projects/proj-a/silver-objects/gl_transaction").json()["version"] == 2

    # User B commits second, still believing version 1 — must be rejected, not overwrite A's change.
    commit_b = client.post(
        f"/api/v1/projects/proj-a/silver-objects/sttm-import/{upload_b['id']}/commit",
        json={"expectedVersions": {"gl_transaction": 1}},
    )
    result_b = commit_b.json()
    assert result_b["committed"] == []
    assert len(result_b["skipped"]) == 1
    assert "concurrently" in result_b["skipped"][0]["reason"]
    # The object still reflects A's commit (version 2), untouched by B's attempt.
    assert client.get("/api/v1/projects/proj-a/silver-objects/gl_transaction").json()["version"] == 2


def test_commit_without_expected_versions_keeps_old_always_succeeds_behavior(
    client: TestClient, seed: dict[str, str]
) -> None:
    """A caller that never supplies `expectedVersions` (older client, or the
    "no-arg" `json={}` shape every other test in this file already uses) must
    keep working exactly as before — this is an additive, opt-in guard."""
    content = _workbook_bytes([("gl_transaction", _header("gl_transaction"))])
    first = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    client.post(f"/api/v1/projects/proj-a/silver-objects/sttm-import/{first['id']}/commit", json={})

    second = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    commit = client.post(f"/api/v1/projects/proj-a/silver-objects/sttm-import/{second['id']}/commit", json={})
    assert commit.json()["committed"] == ["gl_transaction"]


def test_upload_rejects_a_file_that_is_not_a_workbook(client: TestClient, seed: dict[str, str]) -> None:
    """E2E-11: a non-xlsx/zip or corrupt upload used to raise BadZipFile -> 500."""
    for name, content in (("notes.xlsx", b"hello"), ("bundle.zip", b"PK\x03\x04broken")):
        upload = client.post(
            "/api/v1/projects/proj-a/silver-objects/sttm-import",
            files={"file": (name, content, "application/octet-stream")},
        )
        assert upload.status_code == 422, upload.text
        assert upload.json()["error"]["code"] == "invalid_workbook"


def test_list_sttm_imports_returns_the_projects_uploads(client: TestClient, seed: dict[str, str]) -> None:
    """§29.5 happy path for GET /projects/{id}/sttm-imports."""
    content = _workbook_bytes([("gl_transaction", _header("gl_transaction"))])
    upload = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("STTM_Silver.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    listed = client.get("/api/v1/projects/proj-a/sttm-imports")
    assert listed.status_code == 200
    assert [i["id"] for i in listed.json()] == [upload["id"]]
