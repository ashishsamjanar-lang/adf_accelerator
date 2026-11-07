from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.config import check_production_config, require_secure_fernet_key, settings
from app.errors import install_error_handlers
from app.logging_utils import install_secret_redaction

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
install_secret_redaction()
log = logging.getLogger("app.startup")

# Fail-fast (IMPLEMENTATION_PLAN.md gap B-5): the one config problem worth
# crashing startup over, checked at import time so a misconfigured container
# never serves a single request. Advisory checks (below, in the lifespan) are
# logged loudly instead — see app/config.py for why the two are split.
require_secure_fernet_key(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    for problem in check_production_config(settings):
        log.error("Startup config check (APP_ENV=%s): %s", settings.app_env, problem)

    # Dev convenience: ensure the schema exists and is seeded. Production runs
    # `alembic upgrade head` explicitly; this stays idempotent and never blocks
    # startup if Postgres is briefly unavailable (the frontend falls back to
    # demo data until the API is reachable).
    try:
        from app.db import Base, engine
        import app.models  # noqa: F401
        from app.seed import seed_if_empty

        Base.metadata.create_all(bind=engine)
        seed_if_empty()
    except Exception as exc:  # pragma: no cover - depends on Postgres availability
        log.warning("Startup DB init skipped: %s", exc)
    yield


app = FastAPI(title="ADF Pipeline Accelerator API", version="0.1.0", lifespan=lifespan)

install_error_handlers(app)
app.include_router(api_router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "environment": settings.app_env}


@app.get("/api", tags=["system"])
def api_root() -> dict[str, str]:
    return {"name": settings.app_name, "status": "ready"}
