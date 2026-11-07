"""Real backend for the healthcare medallion E2E suite (`healthcare-medallion.spec.ts`).

Boots the actual FastAPI app (`backend/app/main.py`) on a throwaway SQLite metastore so the
Onboarding (Bronze CSV), STTM upload/preview/commit (Silver/Gold) and registry list calls the UI
makes hit the real parser, the real upstream-key resolution and the real registry tables — not a
canned stub. Seeds just the team/project/user the Playwright stub (`api-stub.ts`) already uses.

Usage: `python backend_bridge.py PORT DB_FILE [--keep]`. Prints `READY` on stdout once the server
listens. `--keep` reuses an existing DB_FILE (a Playwright worker restarted mid-run) instead of
starting from an empty metastore.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PORT = int(sys.argv[1])
DB_FILE = Path(sys.argv[2]).resolve()
KEEP = "--keep" in sys.argv[3:]
DB_FILE.parent.mkdir(parents=True, exist_ok=True)
REUSED = KEEP and DB_FILE.exists()
if DB_FILE.exists() and not REUSED:
    DB_FILE.unlink()

# Hermetic: never read the developer's config/platform.env (it may set AUTH_MODE=password or a real
# Postgres URL). Must be set before `app.config` is imported.
os.environ["PLATFORM_ENV_FILE"] = str(DB_FILE.parent / "no-platform.env")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_FILE.as_posix()}"
os.environ["AUTH_MODE"] = "mock"

BACKEND = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))

import uvicorn  # noqa: E402

from app import models  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.main import app  # noqa: E402

Base.metadata.create_all(bind=engine)
if not REUSED:
    with session_scope() as db:
        # Same ids as `api-stub.ts`'s E2E_USER/E2E_PROJECT, so the UI's project picker matches.
        db.add(models.Team(id="team-a", name="Team A", description="E2E"))
        db.add(models.User(id="u-admin", name="Priya Admin", email="priya.admin@stmarys-health.org", username="priya.admin", role="PLATFORM_ADMIN", team_id="team-a"))
        db.add(models.Project(id="proj-a", team_id="team-a", name="Customer Analytics", description="", created_by="u-admin"))


class _Ready(uvicorn.Server):
    async def startup(self, sockets=None):  # type: ignore[override]
        await super().startup(sockets)
        print("READY", flush=True)


_Ready(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")).run()
