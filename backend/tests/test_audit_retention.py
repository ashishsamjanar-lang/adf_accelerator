"""Audit log retention purge tests (AGENTS.md §30.1/§30.8).

Append-only from the API's own perspective (no route ever UPDATEs/DELETEs an
`audit_log` row) — `repository.purge_audit_older_than` is the one sanctioned
exception, called only by the `purge-audit-log` CLI (`app/manage.py`), never
a request handler. This tests the repository function directly (the CLI
itself opens a real `session_scope()` DB connection, not this suite's
in-memory SQLite fixture).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app import models, repository


def _entry(db: Session, entry_id: str, created_at: datetime) -> models.AuditEntry:
    entry = models.AuditEntry(id=entry_id, actor="u-admin", action="test.action", target="x", created_at=created_at)
    db.add(entry)
    return entry


def test_purge_removes_only_rows_older_than_cutoff(db: Session) -> None:
    now = datetime.now(timezone.utc)
    _entry(db, "aud-old-1", now - timedelta(days=500))
    _entry(db, "aud-old-2", now - timedelta(days=401))
    _entry(db, "aud-recent", now - timedelta(days=10))
    db.commit()

    cutoff = now - timedelta(days=400)
    removed = repository.purge_audit_older_than(db, cutoff)
    db.commit()

    assert removed == 2
    remaining_ids = {row.id for row in db.query(models.AuditEntry).all()}
    assert remaining_ids == {"aud-recent"}


def test_purge_is_a_no_op_when_nothing_is_old_enough(db: Session) -> None:
    now = datetime.now(timezone.utc)
    _entry(db, "aud-recent", now - timedelta(days=1))
    db.commit()

    removed = repository.purge_audit_older_than(db, now - timedelta(days=400))
    db.commit()

    assert removed == 0
    assert db.query(models.AuditEntry).count() == 1
