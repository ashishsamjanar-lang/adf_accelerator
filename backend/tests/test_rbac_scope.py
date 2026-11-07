"""§27.4 scope tests: the caller holds the *right role* but the target
resource belongs to a *different team* (AGENTS.md §27.4). Closes the
remainder of gap #15 — `test_rbac.py`'s matrix can't tell this apart from a
plain role failure, because every `ROLE_USERS` entry there is `team-a`, same
as the `seed` fixture. See `app/rbac.py`'s `require_scope`/`scoped_team_id`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


@pytest.fixture()
def foreign_seed(db: Session, seed: dict[str, str]) -> dict[str, str]:
    """A second team ("team-b") with its own project/pipeline/artifact,
    alongside `seed`'s "team-a" — proves a team-a caller can't reach team-b's
    rows (and that list endpoints don't leak them either)."""
    db.add(models.Team(id="team-b", name="Team B", description="Second seed team."))
    db.add(models.Project(id="proj-b", team_id="team-b", name="Project B", description="", created_by="u-admin"))
    db.add(
        models.ProjectSettings(
            project_id="proj-b",
            document={
                "projectId": "proj-b",
                "gitDefaults": {
                    "provider": "github",
                    "repositoryUrl": "https://example.test/repo-b.git",
                    "baseBranch": "dev",
                },
            },
            updated_by="Test Admin",
        )
    )
    db.add(
        models.PipelineDefinition(
            id="pl-b",
            name="pipeline-b",
            template_id="tmpl-a",
            project_id="proj-b",
            team_id="team-b",
            state="GENERATED",
            draft={"name": "pipeline-b", "projectId": "proj-b", "templateId": "tmpl-a"},
            spec={},
            latest_artifact_version="v1.0.0",
            created_by="u-admin",
        )
    )
    db.add(
        models.Artifact(
            id="art-b",
            pipeline_id="pl-b",
            pipeline_name="pipeline-b",
            file_name="pipeline-b.json",
            file_type="json",
            artifact_type="pipeline",
            layer="pipeline",
            version="v1.0.0",
            state="generated",
            bytes=2,
            content="{}",
        )
    )
    db.add(
        models.ConnectionLibraryEntry(
            id="conn-b",
            project_id="proj-b",
            team_id="team-b",
            name="connection-b",
            role="source",
            connector_type="sql_server",
            draft={},
            created_by="u-admin",
        )
    )
    db.commit()
    return {
        **seed,
        "team_b_id": "team-b",
        "project_b_id": "proj-b",
        "pipeline_b_id": "pl-b",
        "artifact_b_id": "art-b",
        "connection_b_id": "conn-b",
    }


# --- Single-resource endpoints: right role, wrong team -> 403 ------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/v1/projects/proj-b", None),
        ("GET", "/api/v1/projects/proj-b/settings", None),
        ("PUT", "/api/v1/projects/proj-b/settings", {}),
        ("GET", "/api/v1/pipelines/pl-b", None),
        ("PUT", "/api/v1/pipelines/pl-b", {"name": "renamed"}),
        ("POST", "/api/v1/pipelines/pl-b/generate", None),
        ("POST", "/api/v1/pipelines/pl-b/validate", None),
        ("GET", "/api/v1/pipelines/pl-b/validation", None),
        ("POST", "/api/v1/pipelines/pl-b/commit", None),
        ("GET", "/api/v1/artifacts/art-b", None),
        ("GET", "/api/v1/artifacts/art-b/diff", None),
        ("POST", "/api/v1/git/push", {"pipelineId": "pl-b", "featureBranch": "feature/x"}),
        (
            "POST",
            "/api/v1/pipelines",
            {"name": "x", "projectId": "proj-b", "templateId": "tmpl-a"},
        ),
        ("GET", "/api/v1/connections/conn-b", None),
        ("PUT", "/api/v1/connections/conn-b", {"name": "renamed"}),
        ("DELETE", "/api/v1/connections/conn-b", None),
        (
            "POST",
            "/api/v1/connections",
            {"projectId": "proj-b", "name": "x", "connectorType": "sql_server", "draft": {}},
        ),
    ],
)
def test_team_a_developer_cannot_reach_team_b_resource(
    client: TestClient,
    as_user,
    foreign_seed: dict[str, str],
    method: str,
    path: str,
    body: dict | None,
) -> None:
    as_user("DEVELOPER")  # team-a, per conftest.ROLE_USERS
    response = client.request(method, path, json=body)
    assert response.status_code == 403, f"{method} {path}: {response.text}"
    assert response.json()["error"]["code"] == "forbidden"


def test_team_a_team_admin_cannot_reach_team_b_resource(
    client: TestClient, as_user, foreign_seed: dict[str, str]
) -> None:
    as_user("TEAM_ADMIN")  # team-a
    assert client.get("/api/v1/projects/proj-b").status_code == 403
    assert client.put("/api/v1/projects/proj-b/settings", json={}).status_code == 403


def test_team_b_developer_cannot_reach_team_a_resource(
    client: TestClient, as_foreign_user, foreign_seed: dict[str, str]
) -> None:
    """Symmetric to the team-a cases above, using the dedicated `team-b`
    identity (`as_foreign_user`) instead of a `team-a` caller reaching into
    `team-b` — same check, opposite direction."""
    assert client.get("/api/v1/projects/proj-a").status_code == 403
    assert client.get("/api/v1/pipelines/pl-a").status_code == 403
    assert client.get("/api/v1/connections/conn-a").status_code == 403


def test_platform_admin_still_reaches_every_team(client: TestClient, as_user, foreign_seed: dict[str, str]) -> None:
    as_user("PLATFORM_ADMIN")
    assert client.get("/api/v1/projects/proj-b").status_code == 200
    assert client.get("/api/v1/pipelines/pl-b").status_code == 200
    assert client.get("/api/v1/artifacts/art-b").status_code == 200
    assert client.get("/api/v1/connections/conn-b").status_code == 200


def test_nonexistent_target_still_404s_before_scope(client: TestClient, as_user, foreign_seed: dict[str, str]) -> None:
    """A missing id is a 404, never a 403 — scope only applies once the row
    is known to exist (matches the existing not-found behavior everywhere
    else in the API)."""
    as_user("DEVELOPER")
    response = client.get("/api/v1/projects/proj-does-not-exist")
    assert response.status_code == 404


# --- List endpoints: no filter must not leak the other team's rows -------


def test_project_list_defaults_to_callers_own_team(client: TestClient, as_user, foreign_seed: dict[str, str]) -> None:
    as_user("DEVELOPER")  # team-a
    ids = {p["id"] for p in client.get("/api/v1/projects").json()}
    assert ids == {"proj-a"}


def test_project_list_rejects_explicit_foreign_team_filter(
    client: TestClient, as_user, foreign_seed: dict[str, str]
) -> None:
    as_user("DEVELOPER")  # team-a
    response = client.get("/api/v1/projects", params={"team_id": "team-b"})
    assert response.status_code == 403


def test_pipeline_list_defaults_to_callers_own_team(client: TestClient, as_user, foreign_seed: dict[str, str]) -> None:
    as_user("DEVELOPER")
    ids = {p["id"] for p in client.get("/api/v1/pipelines").json()}
    assert ids == {"pl-a"}


def test_artifact_list_defaults_to_callers_own_team(client: TestClient, as_user, foreign_seed: dict[str, str]) -> None:
    as_user("DEVELOPER")
    ids = {a["id"] for a in client.get("/api/v1/artifacts").json()}
    assert ids == {"art-a"}


def test_connection_list_defaults_to_callers_own_team(client: TestClient, as_user, foreign_seed: dict[str, str]) -> None:
    as_user("DEVELOPER")
    ids = {c["id"] for c in client.get("/api/v1/connections").json()}
    assert ids == {"conn-a"}


def test_platform_admin_list_still_sees_every_team(client: TestClient, as_user, foreign_seed: dict[str, str]) -> None:
    as_user("PLATFORM_ADMIN")
    ids = {p["id"] for p in client.get("/api/v1/projects").json()}
    assert ids == {"proj-a", "proj-b"}
