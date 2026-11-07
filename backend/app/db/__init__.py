"""Metastore access (AGENTS.md §23.5) — SQLAlchemy 2 + Alembic.

``backend/`` is the *only* layer that talks to Postgres. Models live in
:mod:`app.models`; migrations in ``backend/alembic/``. ``DATABASE_URL`` comes
from ``config/platform.env``.
"""
from __future__ import annotations

from .base import Base, metadata
from .session import SessionLocal, engine, get_session, session_scope

__all__ = ["Base", "metadata", "SessionLocal", "engine", "get_session", "session_scope"]
