"""Versioned API router (AGENTS.md §14 — everything under ``/api/v1``).

The frontend client (``frontend/lib/api/client.ts``) calls ``/api/v1/*``
exclusively (§23.12 gap 3, resolved) — no unversioned alias is served.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    admin,
    artifacts,
    auth,
    catalog,
    connections,
    connectors,
    git,
    integrations,
    managed_identities,
    onboarding,
    pipelines,
    projects,
    registry,
    sso,
    teams,
    templates,
    users,
)


def _domain_router() -> APIRouter:
    router = APIRouter()
    router.include_router(auth.router)
    router.include_router(teams.router)
    router.include_router(projects.router)
    router.include_router(templates.router)
    router.include_router(pipelines.router)
    router.include_router(artifacts.router)
    router.include_router(connections.router)
    router.include_router(git.router)
    router.include_router(git.artifacts_router)
    router.include_router(connectors.router)
    router.include_router(admin.router)
    router.include_router(admin.environments_router)
    router.include_router(users.router)
    router.include_router(registry.router)
    router.include_router(onboarding.router)
    router.include_router(managed_identities.router)
    router.include_router(sso.router)
    router.include_router(integrations.router)
    router.include_router(catalog.router)
    return router


api_router = APIRouter()
api_router.include_router(_domain_router(), prefix="/api/v1")

__all__ = ["api_router"]
