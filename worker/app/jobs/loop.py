"""The claim / execute / record poll loop."""
from __future__ import annotations

import logging
import os
import socket
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db import models
from app.db.session import session_scope
from app.jobs.runner import dispatch

log = logging.getLogger("worker.loop")

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


def _claim_next(db) -> models.Job | None:
    """Atomically claim one queued job (``FOR UPDATE SKIP LOCKED``)."""
    job = db.scalars(
        select(models.Job)
        .where(models.Job.status == "queued")
        .order_by(models.Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None
    job.status = "running"
    job.claimed_by = WORKER_ID
    job.attempts += 1
    job.started_at = datetime.now(timezone.utc)
    db.flush()
    return job


def run_once() -> bool:
    """Process at most one job. Returns ``True`` if a job was handled."""
    with session_scope() as db:
        job = _claim_next(db)
        if job is None:
            return False
        job_id, job_type = job.id, job.type

    # Execute in its own transaction; on failure roll back every partial write,
    # then record the outcome on the job row in a clean transaction.
    error: str | None = None
    result: dict | None = None
    try:
        with session_scope() as db:
            job = db.get(models.Job, job_id)
            result = dispatch(db, job)
    except Exception as exc:  # noqa: BLE001 - the loop must survive any job failure
        log.exception("Job %s (%s) failed", job_id, job_type)
        error = str(exc)

    with session_scope() as db:
        job = db.get(models.Job, job_id)
        job.finished_at = datetime.now(timezone.utc)
        if error is None and (result or {}).get("state") != "failed":
            job.status = "succeeded"
            job.result = result
            job.error = None
        elif error is None:
            # Domain failure the handler already recorded (and committed) — no retry.
            job.status = "failed"
            job.result = result
            job.error = (result or {}).get("error")
        elif job.attempts >= settings.worker_max_attempts:
            job.status = "failed"
            job.error = error
        else:
            job.status = "queued"  # transient error — retry on the next tick
            job.claimed_by = None
            job.error = error
    return True


def run_forever() -> None:
    log.info("worker %s polling jobs every %.1fs", WORKER_ID, settings.worker_poll_interval)
    while True:
        try:
            worked = run_once()
        except SQLAlchemyError as exc:  # pragma: no cover - depends on Postgres
            log.warning("jobs poll failed (db unavailable?): %s", exc)
            worked = False
        if not worked:
            time.sleep(settings.worker_poll_interval)
