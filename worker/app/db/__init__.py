"""Metastore access for the worker (VENDORED).

The worker is stateless but reads/writes the shared metastore to pick up
:class:`~app.db.models.Job` rows and record their results. ``base.py`` and
``models.py`` are vendored byte-for-byte from ``backend/app/`` (separate
container build contexts); keep them in sync.
"""
from __future__ import annotations

from .base import Base, metadata
from .models import (
    Artifact,
    AuditEntry,
    GitPullRequest,
    GitPushRequest,
    Job,
    PipelineDefinition,
    PipelineVersion,
)
from .session import SessionLocal, engine, session_scope

__all__ = [
    "Base",
    "metadata",
    "Artifact",
    "AuditEntry",
    "GitPullRequest",
    "GitPushRequest",
    "Job",
    "PipelineDefinition",
    "PipelineVersion",
    "SessionLocal",
    "engine",
    "session_scope",
]
