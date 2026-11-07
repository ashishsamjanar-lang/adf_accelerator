"""AGENTS.md §29.5 / gaps.md E2E-13 — endpoint-by-endpoint contract audit.

Every `/api/v1` route is walked from the live FastAPI app:

- `test_every_gated_route_refuses_each_disallowed_role` reads each route's
  `require_permission(resource, action)` dependency and calls the route as every role that
  `PERMISSIONS` doesn't allow, asserting a 403 `forbidden`. The role check is a dependency, so it
  runs before path/body validation and dummy path values are enough.
- `test_every_route_is_rbac_gated_or_explicitly_open` fails when a new route ships with neither a
  permission dependency nor an entry in `_OPEN_ROUTES`, so the audit can't silently go stale.
- `test_every_route_has_a_happy_path_test` (full-suite runs only) fails when no test in the session
  drove a route to a 2xx response; routes that can't succeed offline are listed in `_NO_HAPPY_PATH`.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from app.main import app
from app.rbac import ALL_ROLES, PERMISSIONS
from tests.conftest import HAPPY_ROUTES

# Routes reachable without a role check, each for a stated reason.
_OPEN_ROUTES = {
    # Identity bootstrap: sign-in/out and "who am I" must work before any role is known.
    "/api/v1/auth/me", "/api/v1/auth/config", "/api/v1/auth/login", "/api/v1/auth/logout",
    "/api/v1/auth/change-password",
    "/api/v1/auth/sso/{configuration_id}/login", "/api/v1/auth/sso/{configuration_id}/callback",
    # AUTH_MODE=mock developer helpers (404 in any other mode).
    "/api/v1/auth/dev/users", "/api/v1/auth/dev/switch-user",
    # Connector registry: static schema metadata and stateless template parsing, deliberately
    # ungated (see connectors.py).
    "/api/v1/connectors", "/api/v1/connectors/groups", "/api/v1/connectors/{connector_type}",
    "/api/v1/connectors/{connector_type}/linked-service-template", "/api/v1/connectors/linked-service/parse",
    "/api/v1/connectors/templates/source-objects", "/api/v1/connectors/templates/source-objects/parse",
}


# Routes with no offline 2xx: they need a live identity provider or are still 501 stubs.
_NO_HAPPY_PATH = {
    "/api/v1/auth/sso/{configuration_id}/login", "/api/v1/auth/sso/{configuration_id}/callback",
}


def _api_routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/v1")]


def _permission(route: APIRoute) -> tuple[str, str] | None:
    """The (resource, action) of the route's `require_permission` dependency, if any."""
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        call = dep.call
        if call is not None and getattr(call, "__qualname__", "").startswith("require_permission."):
            closure = inspect.getclosurevars(call).nonlocals
            return closure["resource"], closure["action"]
        stack.extend(dep.dependencies)
    return None


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


_GATED = [
    pytest.param(route, method, role, id=f"{method} {route.path} as {role}")
    for route in _api_routes()
    if (perm := _permission(route)) is not None
    for method in sorted(route.methods - {"HEAD", "OPTIONS"})
    for role in sorted(ALL_ROLES - PERMISSIONS[perm])
]


@pytest.mark.parametrize(("route", "method", "role"), _GATED)
def test_every_gated_route_refuses_each_disallowed_role(client, as_user, route: APIRoute, method: str, role: str) -> None:
    as_user(role)
    resp = client.request(method, _concrete(route.path), json={})
    assert resp.status_code == 403, f"{method} {route.path} as {role}: {resp.status_code} {resp.text[:200]}"
    assert resp.json()["error"]["code"] == "forbidden"


def test_the_audit_covers_every_permission_tuple_in_use() -> None:
    """Sanity: the walk finds the gated routes (a refactor of `require_permission` would otherwise
    make the parametrised test above vacuously empty)."""
    used = {p for r in _api_routes() if (p := _permission(r))}
    assert len(used) >= 20
    assert used <= set(PERMISSIONS)


def test_every_route_is_rbac_gated_or_explicitly_open() -> None:
    ungated = sorted({r.path for r in _api_routes() if _permission(r) is None} - _OPEN_ROUTES)
    assert not ungated, f"Routes with no require_permission and not listed in _OPEN_ROUTES: {ungated}"


def test_every_route_has_a_happy_path_test(request) -> None:
    """Runs last (conftest reorders it) and only on a full-suite run, where `HAPPY_ROUTES` holds
    every route some test drove to a 2xx response."""
    collected = {Path(str(i.fspath)).name for i in request.session.items}
    if collected != {p.name for p in Path(__file__).parent.glob("test_*.py")}:
        pytest.skip("happy-path audit needs the full backend suite")
    missing = sorted(
        f"{method} {route.path}"
        for route in _api_routes()
        for method in route.methods - {"HEAD", "OPTIONS"}
        if (method, route.path) not in HAPPY_ROUTES and route.path not in _NO_HAPPY_PATH
    )
    assert not missing, f"Routes no test drives to a 2xx response: {missing}"
