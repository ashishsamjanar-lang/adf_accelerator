"""pytest for app/jobs/loop.py's claim / execute / record cycle
(AGENTS.md §23.4, §23.12 gap #20).

``dispatch`` (the actual generate/validate/git_push business logic) is
monkeypatched to a controllable stub — this file tests the *loop's* own
contract (atomically claim one queued job, run it in its own transaction,
record success/domain-failure/transient-failure-with-retry), not the
handlers, which have their own coverage need (tracked, not yet written).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base, new_id
from app.jobs import loop as loop_module

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def _fake_session_scope() -> Iterator[Session]:
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    Base.metadata.create_all(_engine)
    monkeypatch.setattr(loop_module, "session_scope", _fake_session_scope)
    yield
    Base.metadata.drop_all(_engine)


def _make_queued_job(job_type: str = "generate") -> str:
    with _fake_session_scope() as db:
        job = models.Job(id=new_id("job"), type=job_type, status="queued", payload={})
        db.add(job)
        db.flush()
        return job.id


def _get_job(job_id: str) -> models.Job:
    with _fake_session_scope() as db:
        return db.get(models.Job, job_id)


def test_run_once_returns_false_when_no_job_is_queued():
    assert loop_module.run_once() is False


def test_run_once_claims_the_job_before_dispatching(monkeypatch):
    job_id = _make_queued_job()
    seen_status = {}

    def fake_dispatch(db, job):
        seen_status["status"] = job.status
        seen_status["claimed_by"] = job.claimed_by
        seen_status["attempts"] = job.attempts
        return {"state": "succeeded"}

    monkeypatch.setattr(loop_module, "dispatch", fake_dispatch)
    assert loop_module.run_once() is True
    assert seen_status["status"] == "running"
    assert seen_status["claimed_by"] == loop_module.WORKER_ID
    assert seen_status["attempts"] == 1


def test_run_once_records_success(monkeypatch):
    job_id = _make_queued_job()
    monkeypatch.setattr(loop_module, "dispatch", lambda db, job: {"state": "succeeded", "commitSha": "abc123"})
    assert loop_module.run_once() is True

    job = _get_job(job_id)
    assert job.status == "succeeded"
    assert job.result == {"state": "succeeded", "commitSha": "abc123"}
    assert job.error is None
    assert job.finished_at is not None


def test_run_once_records_a_domain_failure_without_retrying(monkeypatch):
    job_id = _make_queued_job()
    monkeypatch.setattr(loop_module, "dispatch", lambda db, job: {"state": "failed", "error": "branch protected"})
    assert loop_module.run_once() is True

    job = _get_job(job_id)
    assert job.status == "failed"
    assert job.error == "branch protected"
    assert job.attempts == 1  # not retried — the handler already committed its own failure state


def test_run_once_retries_a_transient_failure_below_max_attempts(monkeypatch):
    job_id = _make_queued_job()

    def boom(db, job):
        raise RuntimeError("database connection dropped")

    monkeypatch.setattr(loop_module, "dispatch", boom)
    assert loop_module.run_once() is True

    job = _get_job(job_id)
    assert job.status == "queued"  # requeued for another attempt
    assert job.claimed_by is None
    assert job.error == "database connection dropped"
    assert job.attempts == 1


def test_run_once_stops_retrying_at_max_attempts(monkeypatch):
    from app.config import settings

    job_id = _make_queued_job()

    def boom(db, job):
        raise RuntimeError("still broken")

    monkeypatch.setattr(loop_module, "dispatch", boom)

    for _ in range(settings.worker_max_attempts - 1):
        assert loop_module.run_once() is True
        assert _get_job(job_id).status == "queued"

    assert loop_module.run_once() is True
    job = _get_job(job_id)
    assert job.status == "failed"
    assert job.attempts == settings.worker_max_attempts
    assert job.error == "still broken"


def test_run_once_only_claims_one_job_at_a_time(monkeypatch):
    first = _make_queued_job()
    second = _make_queued_job()
    calls = []

    def fake_dispatch(db, job):
        calls.append(job.id)
        return {"state": "succeeded"}

    monkeypatch.setattr(loop_module, "dispatch", fake_dispatch)
    assert loop_module.run_once() is True
    assert len(calls) == 1
    # the other job is untouched until the next tick
    remaining_status = {first: _get_job(first).status, second: _get_job(second).status}
    assert sorted(remaining_status.values()) == ["queued", "succeeded"]
