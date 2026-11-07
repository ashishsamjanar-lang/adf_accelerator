"""RBAC matrix — one case per (resource, action) in ``app.rbac.PERMISSIONS``,
checked against every §11 role (AGENTS.md §11, §23.12 gap #15).

Before ``app/rbac.py`` existed, every router but ``admin.py`` accepted any
authenticated caller regardless of role; this file is the regression test for
that: each case below asserts the exact role set that should succeed, and
that every other role gets a 403 with the standard error envelope.
"""
from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

# Mirrors `tests.conftest.ROLE_USERS` — every role the `as_user` fixture can act as.
ALL_ROLES = {"PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER", "VIEWER"}


def _case(method: str, path: str, allowed: set[str], body: dict | None = None):
    return pytest.param(method, path, allowed, body, id=f"{method} {path}")


CASES = [
    _case("GET", "/api/v1/teams", ALL_ROLES),
    _case("POST", "/api/v1/teams", {"PLATFORM_ADMIN"}, {"name": "New Team"}),
    _case("GET", "/api/v1/projects", ALL_ROLES),
    _case("POST", "/api/v1/projects", {"PLATFORM_ADMIN"}, {"teamId": "team-a", "name": "New Project"}),
    _case("PUT", "/api/v1/projects/proj-a/settings", {"PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER"}, {}),
    _case("GET", "/api/v1/templates", ALL_ROLES),
    _case("GET", "/api/v1/pipelines", ALL_ROLES),
    _case(
        "POST",
        "/api/v1/pipelines",
        {"PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER"},
        {"name": "new-pipeline", "projectId": "proj-a", "templateId": "tmpl-a"},
    ),
    _case("PUT", "/api/v1/pipelines/pl-a", {"PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER"}, {"name": "renamed"}),
    _case("POST", "/api/v1/pipelines/pl-a/generate", {"PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER"}),
    _case("POST", "/api/v1/pipelines/pl-a/validate", {"PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER"}),
    _case("GET", "/api/v1/artifacts", ALL_ROLES),
    _case(
        "POST",
        "/api/v1/git/push",
        {"PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER"},
        {"pipelineId": "pl-a", "featureBranch": "feature/rbac-test"},
    ),
    _case("GET", "/api/v1/admin/users", {"PLATFORM_ADMIN", "TEAM_ADMIN"}),
    _case("GET", "/api/v1/admin/audit", {"PLATFORM_ADMIN", "TEAM_ADMIN"}),
    _case(
        "POST",
        "/api/v1/admin/users",
        {"PLATFORM_ADMIN"},
        {"name": "RBAC Case", "email": "rbac-case@test.local", "role": "VIEWER"},
    ),
]


@pytest.mark.parametrize("method,path,allowed,body", CASES)
def test_rbac_matrix(
    client: TestClient,
    as_user: Callable[[str], object],
    seed: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    allowed: set[str],
    body: dict | None,
) -> None:
    # generate/validate normally poll for the worker to finish the job; there is
    # no worker in this suite, so make a successful call return immediately
    # instead of blocking for the full `wait_for_job` timeout.
    monkeypatch.setattr("app.api.pipelines.wait_for_job", lambda *a, **k: None)

    for role in ALL_ROLES:
        as_user(role)
        response = client.request(method, path, json=body)
        if role in allowed:
            assert response.status_code < 400, f"{role} should be allowed on {method} {path}: {response.text}"
        else:
            assert response.status_code == 403, f"{role} should be forbidden on {method} {path}: {response.text}"
            assert response.json()["error"]["code"] == "forbidden"


def test_platform_admin_bypasses_every_gate(client: TestClient, as_user, seed: dict[str, str]) -> None:
    """PLATFORM_ADMIN is a superset role even for (resource, action) pairs
    that name a narrower allowed set — this is `_allowed`'s explicit bypass,
    not an accident of the table."""
    as_user("PLATFORM_ADMIN")
    assert client.get("/api/v1/admin/users").status_code == 200
    assert client.get("/api/v1/admin/audit").status_code == 200


def test_viewer_is_read_only_everywhere_it_matters(client: TestClient, as_user, seed: dict[str, str]) -> None:
    as_user("VIEWER")
    assert client.get("/api/v1/pipelines").status_code == 200
    assert client.post("/api/v1/pipelines", json={"name": "x", "projectId": "proj-a", "templateId": "tmpl-a"}).status_code == 403
    assert (
        client.post("/api/v1/git/push", json={"pipelineId": "pl-a", "featureBranch": "feature/x"}).status_code == 403
    )
