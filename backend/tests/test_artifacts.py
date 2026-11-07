"""Artifact endpoint tests (AGENTS.md §8, §14, §23.12 gap #20)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_artifacts_excludes_content(client: TestClient, seed: dict[str, str]) -> None:
    res = client.get("/api/v1/artifacts", params={"pipeline_id": seed["pipeline_id"]})
    assert res.status_code == 200
    [artifact] = res.json()
    assert artifact["id"] == seed["artifact_id"]
    assert artifact["content"] is None


def test_get_artifact_includes_content(client: TestClient, seed: dict[str, str]) -> None:
    res = client.get(f"/api/v1/artifacts/{seed['artifact_id']}")
    assert res.status_code == 200
    assert res.json()["content"] == "{}"


def test_get_unknown_artifact_404s(client: TestClient) -> None:
    res = client.get("/api/v1/artifacts/does-not-exist")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "artifact_not_found"


def test_artifact_diff_counts_nonempty_lines(client: TestClient, seed: dict[str, str], db) -> None:
    from app import models

    db.add(
        models.Artifact(
            id="art-multi",
            pipeline_id=seed["pipeline_id"],
            pipeline_name="pipeline-a",
            file_name="dataset.json",
            file_type="json",
            artifact_type="dataset",
            layer="dataset",
            version="v1.0.0",
            state="generated",
            bytes=10,
            content="line one\n\nline two\n",
        )
    )
    db.commit()

    res = client.get("/api/v1/artifacts/art-multi/diff")
    assert res.status_code == 200
    assert res.json() == {"additions": 2, "deletions": 0}
