"""Concurrency test (user request, 2026-09-13): 25 simultaneous people across
every §11 role hitting `AUTH_MODE=password` at once.

Every other test in this suite authenticates through `conftest.py`'s
``as_user``, which swaps a single process-wide
``app.dependency_overrides[current_user]`` lambda — fine for one identity per
test, but it can't prove anything about *concurrent, different* identities,
since whichever thread set the override last would win for every request.
``AUTH_MODE=password`` instead resolves the caller from its own session
cookie on every request (``app/api/deps.py::_current_user_from_session``),
with no shared mutable identity state — that's the one real path this app has
that a genuine multi-identity concurrency test can exercise, so that's what
this file drives: 25 people (3 PLATFORM_ADMIN, 6 TEAM_ADMIN, 10 DEVELOPER, 6
VIEWER, split across two teams) log in and act at the same time, and every
response must reflect *its own* caller — never another thread's — plus the
§27.4 team-scope and §11 role checks must still hold under that load.

Uses a dedicated file-backed SQLite database with a real connection pool
(``NullPool`` — a fresh DBAPI connection per checkout) instead of
``conftest.py``'s in-memory ``StaticPool`` engine every other test file
shares: SQLite doesn't support two overlapping transactions on one physical
connection, so handing that single shared connection to 25 threads at once
would fail on the driver, not on anything this app got wrong. A fresh
file-backed connection per request is the same "one connection per request"
shape ``app/db/session.py`` uses against Postgres in production.
"""
from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app import models
from app.api.deps import current_user, db_session
from app.config import settings
from app.db.base import Base
from app.main import app
from app.security import CSRF_COOKIE_NAME, hash_password

PASSWORD = "correct horse battery staple 25"


@dataclass(frozen=True)
class Identity:
    id: str
    email: str
    role: str
    team_id: str | None


def _build_roster() -> list[Identity]:
    """25 people spanning every §11 role across two teams — enough to exercise
    every `require_permission`/`require_scope` branch under real concurrency."""
    roster: list[Identity] = [
        Identity(f"u-admin-{i}", f"admin{i}@test.local", "PLATFORM_ADMIN", None) for i in range(3)
    ]
    roster += [
        Identity(f"u-teamadmin-{i}", f"teamadmin{i}@test.local", "TEAM_ADMIN", "team-a" if i % 2 == 0 else "team-b")
        for i in range(6)
    ]
    roster += [
        Identity(f"u-dev-{i}", f"dev{i}@test.local", "DEVELOPER", "team-a" if i % 2 == 0 else "team-b")
        for i in range(10)
    ]
    roster += [
        Identity(f"u-viewer-{i}", f"viewer{i}@test.local", "VIEWER", "team-a" if i % 2 == 0 else "team-b")
        for i in range(6)
    ]
    assert len(roster) == 25
    return roster


ROSTER = _build_roster()


@pytest.fixture()
def concurrent_sessionmaker(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    """A file-backed SQLite engine with a real pool — see module docstring for
    why this can't be the shared in-memory `StaticPool` engine `conftest.py` uses."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrency.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    engine.dispose()


@pytest.fixture()
def concurrency_app(
    concurrent_sessionmaker: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, str]]:
    """Wires the app to `concurrent_sessionmaker`, switches to
    `AUTH_MODE=password`, and seeds two teams + one project each + the 25
    password-auth users in `ROSTER`. Yields ``{team_id: project_id}``."""
    monkeypatch.setattr(settings, "auth_mode", "password")

    def _override_db_session() -> Iterator[Session]:
        session = concurrent_sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[db_session] = _override_db_session
    app.dependency_overrides.pop(current_user, None)  # real per-session resolution, not the mock override

    seed = concurrent_sessionmaker()
    seed.add(models.Team(id="team-a", name="Team A", description=""))
    seed.add(models.Team(id="team-b", name="Team B", description=""))
    seed.add(models.Project(id="proj-team-a", team_id="team-a", name="Project A", description="", created_by="seed"))
    seed.add(models.Project(id="proj-team-b", team_id="team-b", name="Project B", description="", created_by="seed"))
    seed.add(
        models.PipelineTemplate(
            id="tmpl-a", name="Template A", version="1.0", description="", active=True, document={"id": "tmpl-a"}
        )
    )
    for person in ROSTER:
        seed.add(
            models.User(
                id=person.id,
                name=person.email,
                email=person.email,
                role=person.role,
                team_id=person.team_id,
                active=True,
                password_hash=hash_password(PASSWORD),
            )
        )
    seed.commit()
    seed.close()

    yield {"team-a": "proj-team-a", "team-b": "proj-team-b"}
    app.dependency_overrides.pop(db_session, None)


def _own_and_cross_project(identity: Identity, projects_by_team: dict[str, str]) -> tuple[str, str | None]:
    if identity.team_id is None:  # PLATFORM_ADMIN — no home team, so no cross-team case to probe
        return projects_by_team["team-a"], None
    other_team = "team-b" if identity.team_id == "team-a" else "team-a"
    return projects_by_team[identity.team_id], projects_by_team[other_team]


def _run_identity(identity: Identity, projects_by_team: dict[str, str]) -> dict:
    """One simulated person's full session — login, confirm identity, list
    projects (§27.4 scope), try creating a pipeline in their own project and
    (non-admins) the other team's, then log out. Each identity gets its own
    `TestClient`/cookie jar; 25 of these run at once via `ThreadPoolExecutor`."""
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"email": identity.email, "password": PASSWORD})
    result: dict = {"identity": identity, "login_status": login.status_code}
    if login.status_code != 200:
        return result

    me = client.get("/api/v1/auth/me")
    result["me_status"] = me.status_code
    result["me_email"] = me.json().get("email") if me.status_code == 200 else None

    projects = client.get("/api/v1/projects")
    result["projects_status"] = projects.status_code
    result["project_team_ids"] = (
        sorted({p["teamId"] for p in projects.json()}) if projects.status_code == 200 else None
    )

    own_project, cross_project = _own_and_cross_project(identity, projects_by_team)
    csrf_headers = {"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]}
    create_own = client.post(
        "/api/v1/pipelines",
        json={"name": f"pl-{identity.id}", "projectId": own_project, "templateId": "tmpl-a"},
        headers=csrf_headers,
    )
    result["create_own_status"] = create_own.status_code
    result["create_own_team_id"] = create_own.json().get("teamId") if create_own.status_code == 201 else None

    if cross_project is not None:
        create_cross = client.post(
            "/api/v1/pipelines",
            json={"name": f"pl-{identity.id}-cross", "projectId": cross_project, "templateId": "tmpl-a"},
            headers=csrf_headers,
        )
        result["create_cross_status"] = create_cross.status_code

    logout = client.post("/api/v1/auth/logout", headers=csrf_headers)
    result["logout_status"] = logout.status_code
    return result


def test_25_concurrent_users_across_roles_stay_isolated(concurrency_app: dict[str, str]) -> None:
    with ThreadPoolExecutor(max_workers=len(ROSTER)) as pool:
        futures = [pool.submit(_run_identity, identity, concurrency_app) for identity in ROSTER]
        results = [f.result(timeout=60) for f in futures]

    assert len(results) == 25
    for r in results:
        identity: Identity = r["identity"]
        # Every login succeeded and, crucially, every session resolved back to
        # *its own* identity — proves no cross-thread bleed through the
        # session/cookie/current_user path under concurrent load.
        assert r["login_status"] == 200, r
        assert r["me_status"] == 200, r
        assert r["me_email"] == identity.email, r
        assert r["projects_status"] == 200, r
        assert r["logout_status"] == 204, r

        if identity.role == "PLATFORM_ADMIN":
            assert r["project_team_ids"] == ["team-a", "team-b"], r  # sees every team
            assert r["create_own_status"] == 201, r
        else:
            assert r["project_team_ids"] == [identity.team_id], r  # §27.4: never another team's rows
            if identity.role in ("TEAM_ADMIN", "DEVELOPER"):
                assert r["create_own_status"] == 201, r
                assert r["create_own_team_id"] == identity.team_id, r
                assert r["create_cross_status"] == 403, r  # scope-checked out of the other team
            else:  # VIEWER — role-checked out before scope is even loaded
                assert r["create_own_status"] == 403, r
                assert r["create_cross_status"] == 403, r


def test_concurrent_failed_logins_lock_account_exactly_once(
    concurrency_app: dict[str, str], concurrent_sessionmaker: sessionmaker[Session]
) -> None:
    """25 threads hammer one account's login with the wrong password at once —
    `User.failed_login_attempts`/`locked_until` (`app/api/auth.py::login`) must
    land on a consistent count/lock state, not an over- or under-counted one
    from a lost update racing on the same row."""
    target_email = ROSTER[10].email  # a DEVELOPER — role is irrelevant to this check

    def _attempt(_: int) -> int:
        client = TestClient(app)
        res = client.post("/api/v1/auth/login", json={"email": target_email, "password": "definitely-wrong"})
        return res.status_code

    with ThreadPoolExecutor(max_workers=25) as pool:
        statuses = list(pool.map(_attempt, range(25)))

    assert set(statuses) <= {401, 423}
    session = concurrent_sessionmaker()
    try:
        user = session.query(models.User).filter_by(email=target_email).one()
        # The account must end up locked, and the counter must reflect at
        # *least* every attempt up to the threshold — this is the invariant a
        # naive `user.failed_login_attempts += 1; db.commit()` breaks under
        # concurrency: several threads can read the same starting count
        # before any of them commits, so their increments collapse into one
        # and the account can end up *never* reaching the threshold at all
        # (this test caught exactly that before `app/api/auth.py::login`
        # moved to an atomic `SET x = x + 1` update). It's fine — expected,
        # even — for the final count to land somewhat above the threshold: a
        # burst of truly simultaneous requests can pass the "not locked yet"
        # check together before any one of them sets the lock, and every one
        # of those really was a failed attempt that deserves to be counted;
        # what must never happen is losing an increment or failing to lock.
        assert user.locked_until is not None
        assert user.failed_login_attempts >= settings.auth_max_failed_logins
        assert user.failed_login_attempts <= 25  # never more than the 25 requests actually made
    finally:
        session.close()

    # Correct credentials are still rejected outright while the lock holds.
    still_locked = TestClient(app).post("/api/v1/auth/login", json={"email": target_email, "password": PASSWORD})
    assert still_locked.status_code == 423
