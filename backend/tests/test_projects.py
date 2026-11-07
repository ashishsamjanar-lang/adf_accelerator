"""Project + project-settings endpoint tests (AGENTS.md §5, §14, §23.12 gap #20)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_project_requires_existing_team(client: TestClient) -> None:
    res = client.post("/api/v1/projects", json={"teamId": "no-such-team", "name": "Orphan"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "team_not_found"


def test_create_get_and_list_project(client: TestClient, seed: dict[str, str]) -> None:
    created = client.post("/api/v1/projects", json={"teamId": seed["team_id"], "name": "Commerce"})
    assert created.status_code == 201
    body = created.json()
    assert body["teamId"] == seed["team_id"]
    assert body["settings"]["name"] == "Commerce"

    fetched = client.get(f"/api/v1/projects/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]

    listed = client.get("/api/v1/projects", params={"team_id": seed["team_id"]})
    assert body["id"] in {p["id"] for p in listed.json()}


def test_get_and_put_settings_roundtrip(client: TestClient, seed: dict[str, str]) -> None:
    got = client.get(f"/api/v1/projects/{seed['project_id']}/settings")
    assert got.status_code == 200
    assert got.json()["gitDefaults"]["provider"] == "github"

    updated = client.put(
        f"/api/v1/projects/{seed['project_id']}/settings",
        json={**got.json(), "tags": ["finance"]},
    )
    assert updated.status_code == 200
    assert updated.json()["tags"] == ["finance"]
    assert updated.json()["updatedBy"]


def test_put_settings_rejects_raw_secret_values(client: TestClient, seed: dict[str, str]) -> None:
    # A short, slash-free, space-free token still passes as a plausible Key
    # Vault secret *name*; a value with spaces is unambiguously a raw secret,
    # never a reference — that's the case the guard exists to catch.
    res = client.put(
        f"/api/v1/projects/{seed['project_id']}/settings",
        json={"password": "this is a plaintext password"},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "secret_value_rejected"


def test_put_settings_rejects_raw_secret_env_var(client: TestClient, seed: dict[str, str]) -> None:
    # environmentVariables (§22.3.H) rows use a generic key/value shape, so
    # the key-name hint list alone would miss a raw secret here — the
    # explicit isSecret flag on the row must be checked instead.
    res = client.put(
        f"/api/v1/projects/{seed['project_id']}/settings",
        json={"environmentVariables": [{"key": "DB_PASSWORD", "value": "this is a plaintext password", "isSecret": True}]},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "secret_value_rejected"


def test_put_settings_accepts_secret_ref_env_var(client: TestClient, seed: dict[str, str]) -> None:
    res = client.put(
        f"/api/v1/projects/{seed['project_id']}/settings",
        json={"environmentVariables": [{"key": "DB_PASSWORD", "value": "kv-db-password", "isSecret": True}]},
    )
    assert res.status_code == 200
    assert res.json()["environmentVariables"][0]["value"] == "kv-db-password"


def test_put_settings_accepts_feature_branch_pattern(client: TestClient, seed: dict[str, str]) -> None:
    # Regression: "featureBranchPattern" contains the substring "pat" (from
    # "...Pattern"), which used to trip the "pat" secret-key hint meant for
    # PAT (personal access token) fields, and its typical value (a
    # slash-containing branch pattern) then failed the reference-value check
    # — rejecting an ordinary naming pattern as if it were a raw secret.
    res = client.put(
        f"/api/v1/projects/{seed['project_id']}/settings",
        json={"gitDefaults": {"featureBranchPattern": "feature/{ticket}-{pipelineName}"}},
    )
    assert res.status_code == 200
    assert res.json()["gitDefaults"]["featureBranchPattern"] == "feature/{ticket}-{pipelineName}"


def test_put_settings_still_rejects_raw_git_pat(client: TestClient, seed: dict[str, str]) -> None:
    # A field that's actually named for a PAT must still be caught — the
    # word-boundary fix must not weaken detection of a real "pat" field.
    res = client.put(
        f"/api/v1/projects/{seed['project_id']}/settings",
        json={"gitDefaults": {"gitPat": "this is a raw personal access token"}},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "secret_value_rejected"


def test_put_settings_unknown_project_404s(client: TestClient) -> None:
    assert client.put("/api/v1/projects/nope/settings", json={}).status_code == 404
