"""Shared fixtures for API tests (AGENTS.md §18, §23.12 gap #20).

Every test here runs against an isolated in-memory SQLite metastore — never
the dev Postgres — so the suite works with no external service and each test
starts from a clean schema. ``client`` gives a `TestClient` wired to that
database with the mock identity defaulted to PLATFORM_ADMIN; use ``as_user``
to exercise a different role for RBAC tests (§11, §27.4).
"""
from __future__ import annotations

import os

# Hermetic suite: never read the developer's own config/platform.env (it may say
# AUTH_MODE=password, a real DATABASE_URL, ...). Must run before `app.config` is
# imported below — see PLATFORM_ENV_FILE in app/config.py.
os.environ["PLATFORM_ENV_FILE"] = os.path.join(os.path.dirname(__file__), "no-platform.env")

from collections.abc import Iterator  # noqa: E402

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.api.deps import current_user, db_session
from app.db.base import Base
from app.main import app

# gaps.md E2E-04: set TEST_DATABASE_URL (e.g. a throwaway Postgres database) to run the same suite
# against a real server and catch dialect/constraint drift locally. Every test creates and drops
# the whole schema, so never point it at a database holding data you need.
_test_database_url = os.environ.get("TEST_DATABASE_URL")
_engine = (
    create_engine(_test_database_url)
    if _test_database_url
    else create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)

# §29.5 / gaps.md E2E-13: every (method, route) that answered 2xx during this test session — the
# happy-path half of the endpoint contract audit (tests/test_endpoint_contract.py reads it last).
HAPPY_ROUTES: set[tuple[str, str]] = set()


@app.middleware("http")
async def _record_happy_route(request, call_next):
    response = await call_next(request)
    route = request.scope.get("route")
    if route is not None and 200 <= response.status_code < 300:
        HAPPY_ROUTES.add((request.method, route.path))
    return response


def pytest_collection_modifyitems(session, config, items):
    """Run the happy-path audit after every other test, so it sees the whole session's traffic."""
    audit = [i for i in items if i.name == "test_every_route_has_a_happy_path_test"]
    items[:] = [i for i in items if i not in audit] + audit


# One transient (never persisted) user per §11 role — enough to exercise every
# `require_permission` gate without needing a seeded DB row per test.
ROLE_USERS: dict[str, models.User] = {
    "PLATFORM_ADMIN": models.User(
        id="u-admin", name="Test Admin", email="admin@test.local", role="PLATFORM_ADMIN", team_id="team-a"
    ),
    "TEAM_ADMIN": models.User(
        id="u-teamadmin", name="Test Team Admin", email="teamadmin@test.local", role="TEAM_ADMIN", team_id="team-a"
    ),
    "DEVELOPER": models.User(
        id="u-dev", name="Test Developer", email="dev@test.local", role="DEVELOPER", team_id="team-a"
    ),
    "VIEWER": models.User(id="u-viewer", name="Test Viewer", email="viewer@test.local", role="VIEWER", team_id="team-a"),
}


@pytest.fixture(autouse=True)
def _fresh_schema() -> Iterator[None]:
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


def _override_db_session() -> Iterator[Session]:
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture()
def db() -> Iterator[Session]:
    """A session on the same test database the API sees, for arranging fixtures
    directly (e.g. seeding a template — there's no ``POST /templates``).

    Call ``db.commit()`` yourself once you're done writing: this fixture only
    closes the session at teardown, it never commits on your behalf, so
    requests made via ``client`` in the same test see exactly what you meant
    them to.
    """
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _persist_user(user: models.User) -> None:
    """Write a `ROLE_USERS`/`FOREIGN_USER` identity into the test DB too.

    Opt-in (call explicitly from a test), never automatic from `as_user` —
    most tests rely on these identities staying transient (e.g.
    `test_team_members` asserts on the exact membership of a team `as_user`
    already belongs to). Use this only when a test needs the actor to be
    resolvable by id (`repository.get_user`/`list_users`, or tracking-metadata
    name resolution off `created_by`/`updated_by`/audit `actor`) — mirroring
    what the real `mock` auth path and `app/seed.py` do for a real user.
    """
    session = _SessionLocal()
    try:
        session.merge(user)
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def persist_actor():
    """Give a test `_persist_user` directly: ``persist_actor(ROLE_USERS["PLATFORM_ADMIN"])``."""
    return _persist_user


@pytest.fixture()
def as_user():
    """Override the mock identity for this test. Usage: ``as_user("DEVELOPER")``."""

    def _set(role: str = "PLATFORM_ADMIN") -> models.User:
        user = ROLE_USERS[role]
        app.dependency_overrides[current_user] = lambda: user
        return user

    yield _set
    app.dependency_overrides.pop(current_user, None)


# A second team's DEVELOPER — every `ROLE_USERS` entry is `team-a` (the
# `seed` fixture's team), so RBAC-matrix tests can't tell "wrong role" apart
# from "right role, wrong team" (§27.4). This fixture is that second case.
FOREIGN_USER = models.User(
    id="u-foreign-dev", name="Foreign Developer", email="foreign@test.local", role="DEVELOPER", team_id="team-b"
)


@pytest.fixture()
def as_foreign_user():
    """Override the mock identity to a DEVELOPER on ``team-b`` — for §27.4
    scope tests. Independent of ``as_user`` so either can run last and win."""
    app.dependency_overrides[current_user] = lambda: FOREIGN_USER
    yield FOREIGN_USER
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture()
def client(as_user) -> Iterator[TestClient]:
    app.dependency_overrides[db_session] = _override_db_session
    as_user("PLATFORM_ADMIN")
    yield TestClient(app)
    app.dependency_overrides.pop(db_session, None)


@pytest.fixture()
def raw_client() -> Iterator[TestClient]:
    """A `TestClient` with only the DB overridden — `current_user` runs for
    real, for tests that exercise mock-identity resolution itself (§27.2)."""
    app.dependency_overrides[db_session] = _override_db_session
    yield TestClient(app)
    app.dependency_overrides.pop(db_session, None)


@pytest.fixture()
def seed(db: Session) -> dict[str, str]:
    """Minimal fixture graph shared by most router tests: one team, one
    project (with a configured Git default so push/commit don't 422 on
    "not configured"), one active template, one pipeline with a generated
    artifact at its current version."""
    db.add(models.Team(id="team-a", name="Team A", description="Seed team."))
    db.add(
        models.Project(id="proj-a", team_id="team-a", name="Project A", description="Seed project.", created_by="u-admin")
    )
    db.add(
        models.ProjectSettings(
            project_id="proj-a",
            document={
                "projectId": "proj-a",
                "gitDefaults": {
                    "provider": "github",
                    "repositoryUrl": "https://example.test/repo.git",
                    "baseBranch": "dev",
                },
            },
            updated_by="Test Admin",
        )
    )
    db.add(
        models.PipelineTemplate(
            id="tmpl-a", name="Template A", version="1.0", description="", active=True, document={"id": "tmpl-a"}
        )
    )
    pipeline = models.PipelineDefinition(
        id="pl-a",
        name="pipeline-a",
        template_id="tmpl-a",
        project_id="proj-a",
        team_id="team-a",
        state="GENERATED",
        draft={"name": "pipeline-a", "projectId": "proj-a", "templateId": "tmpl-a"},
        spec={},
        latest_artifact_version="v1.0.0",
        created_by="u-admin",
    )
    db.add(pipeline)
    db.add(
        models.Artifact(
            id="art-a",
            pipeline_id="pl-a",
            pipeline_name="pipeline-a",
            file_name="pipeline-a.json",
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
            id="conn-a",
            project_id="proj-a",
            team_id="team-a",
            name="connection-a",
            role="source",
            connector_type="sql_server",
            draft={},
            created_by="u-admin",
        )
    )
    db.commit()
    return {
        "team_id": "team-a",
        "project_id": "proj-a",
        "template_id": "tmpl-a",
        "pipeline_id": "pl-a",
        "artifact_id": "art-a",
        "connection_id": "conn-a",
    }
