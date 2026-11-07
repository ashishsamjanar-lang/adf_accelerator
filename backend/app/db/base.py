"""Declarative base + shared column mixins."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names so Alembic autogenerate is stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_aware_utc(value: datetime | None) -> datetime | None:
    """SQLite (the test suite's metastore, `tests/conftest.py`) drops tzinfo
    on a ``DateTime(timezone=True)`` column on round-trip — Postgres doesn't.
    Any code comparing a stored timestamp against :func:`utcnow` must go
    through this first, or the comparison raises ``TypeError`` on SQLite
    while working by accident on Postgres. A naive value is assumed UTC,
    which every writer in this app already is (:func:`utcnow`, ``func.now()``)."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=utcnow, nullable=False
    )


class OwnershipMixin(TimestampMixin):
    """Every tenant-scoped row carries team/project/creator (AGENTS.md §23.5)."""

    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
