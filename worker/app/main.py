"""Worker entrypoint — connector registry banner + job poll loop (§23.4).

The worker is stateless: it owns the generator / validator / Git adapter and
processes :class:`~app.db.models.Job` rows the API enqueues. It holds no state
between jobs beyond what it writes to the metastore.
"""
from __future__ import annotations

import logging

from app.config import check_production_config, require_secure_fernet_key, settings
from app.connectors import (
    ALL_CONNECTORS,
    DATABASE_CONNECTORS,
    FILE_CONNECTORS,
    LLM_CONNECTORS,
    REGISTRY_VERSION,
    SERVICE_CONNECTORS,
    registry_fingerprint,
)
from app.jobs import run_forever
from app.logging_utils import install_secret_redaction

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
install_secret_redaction()
log = logging.getLogger("worker.main")


def _log_connector_registry() -> None:
    log.info(
        "connector registry v%s (%d connectors: %d database, %d file, %d service, %d llm) fingerprint=%s",
        REGISTRY_VERSION,
        len(ALL_CONNECTORS),
        len(DATABASE_CONNECTORS),
        len(FILE_CONNECTORS),
        len(SERVICE_CONNECTORS),
        len(LLM_CONNECTORS),
        registry_fingerprint()[:12],
    )


def _ensure_schema() -> None:
    try:
        from app.db import Base, engine

        Base.metadata.create_all(bind=engine)
    except Exception as exc:  # pragma: no cover - depends on Postgres availability
        log.warning("schema check skipped: %s", exc)


def run() -> None:
    # Fail-fast (IMPLEMENTATION_PLAN.md gap B-5) — the one config problem
    # worth crashing over, before the poll loop ever claims a job.
    require_secure_fernet_key(settings)
    for problem in check_production_config(settings):
        log.error("Startup config check (APP_ENV=%s): %s", settings.app_env, problem)
    _log_connector_registry()
    _ensure_schema()
    run_forever()


if __name__ == "__main__":
    run()
