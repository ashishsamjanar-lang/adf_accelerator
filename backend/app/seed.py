"""Idempotent demo seed — keeps live mode at parity with the frontend
``lib/mock-data`` fixtures so a fresh stack is immediately explorable (§23.2).
"""
from __future__ import annotations

from sqlalchemy import select

from app import models
from app.db.session import session_scope
from app.security import username_from_email
from app.templates import TEMPLATE_DOCUMENTS

_TEAMS = [
    ("team-platform", "Platform Engineering", "Owns the control plane and shared templates."),
    ("team-data-products", "Data Products", "Builds governed product-grade datasets."),
    ("team-finance", "Finance Analytics", "Regulated analytics for the finance organization."),
]
_USERS = [
    ("user-ashish", "Ashish Kumar", "ashish@contoso.com", "PLATFORM_ADMIN", "team-platform"),
    ("user-sarah", "Sarah Lee", "sarah@contoso.com", "DEVELOPER", "team-data-products"),
    ("user-james", "James Okoro", "james@contoso.com", "DEVELOPER", "team-finance"),
]
_PROJECTS = [
    ("proj-customer360", "team-platform", "Customer 360", "Unified customer records across sources."),
    ("proj-commerce", "team-data-products", "Commerce Platform", "Order and product event ingestion."),
    ("proj-finance", "team-finance", "Finance Lake", "Ledger and transaction lakehouse."),
]
_GIT_DEFAULTS = {
    "provider": "azure_repos",
    "repositoryUrl": "https://dev.azure.com/contoso/adf-accelerator-artifacts",
    "organization": "contoso",
    "project": "adf-accelerator",
    "repository": "adf-accelerator-artifacts",
    "featureBranchPattern": "feature/{pipeline-name}-{timestamp}",
    "baseBranch": "dev",
    "artifactRoot": "adf/artifacts/",
    "commitMessageConvention": "feat(artifacts): {pipeline} v{version} — {artifact_count} files",
    "prTitleTemplate": "feat: {pipeline} v{version}",
    "prBodyTemplate": "## Summary\n- Pipeline: {pipeline}\n- Version: {version}\n- Template: {template}",
    "reviewers": ["team-platform-leads"],
}
_PIPELINES = [
    ("pl-customer-incremental", "customer_incremental", "incremental_load", "proj-customer360", "user-ashish"),
    ("pl-orders-daily", "orders_daily_full", "full_load", "proj-commerce", "user-sarah"),
    ("pl-product-metadata", "product_metadata_sync", "metadata_driven_load", "proj-commerce", "user-sarah"),
    ("pl-finance-transactions", "finance_transactions", "incremental_load", "proj-finance", "user-james"),
]


_ENVIRONMENTS = [
    ("env-dev", "DEV", "DEV", "Fast iteration environment.", "not_configured"),
    ("env-qa", "QA", "QA", "Team-admin approval is required before promotion from DEV.", "not_configured"),
    ("env-prod", "PROD", "PROD", "Platform-admin approval is required before promotion from QA.", "not_configured"),
]


def seed_if_empty() -> None:
    with session_scope() as db:
        if db.scalar(select(models.Team).limit(1)) is not None:
            _seed_templates(db)  # templates may still need topping up on upgrade
            _seed_environments(db)  # environments may still need topping up on upgrade
            return

        for tid, name, desc in _TEAMS:
            db.add(models.Team(id=tid, name=name, description=desc))
        for uid, name, email, role, team_id in _USERS:
            db.add(
                models.User(
                    id=uid, name=name, email=email, username=username_from_email(email), role=role, team_id=team_id
                )
            )
        for pid, team_id, name, desc in _PROJECTS:
            db.add(models.Project(id=pid, team_id=team_id, name=name, description=desc, created_by="user-ashish"))
            db.add(
                models.ProjectSettings(
                    project_id=pid,
                    document={
                        "projectId": pid,
                        "name": name,
                        "description": desc,
                        "teamId": team_id,
                        "tags": [],
                        "gitDefaults": _GIT_DEFAULTS,
                        "customMetadata": [],
                    },
                    updated_by="Ashish Kumar",
                )
            )
        _seed_templates(db)
        _seed_environments(db)
        for pl_id, name, template_id, project_id, created_by in _PIPELINES:
            team_id = next(t for p, t, *_ in _PROJECTS if p == project_id)
            db.add(
                models.PipelineDefinition(
                    id=pl_id,
                    name=name,
                    template_id=template_id,
                    project_id=project_id,
                    team_id=team_id,
                    state="DRAFT",
                    draft={"name": name, "projectId": project_id, "templateId": template_id},
                    spec={},
                    created_by=created_by,
                )
            )
        db.add(
            models.AuditEntry(
                id="aud-seed",
                actor="system",
                action="metastore.seeded",
                target="demo dataset",
                details=f"{len(_TEAMS)} teams, {len(_PROJECTS)} projects, {len(_PIPELINES)} pipelines.",
            )
        )


def _seed_environments(db) -> None:
    """Seed DEV/QA/PROD rows so the admin UI has something to fill in.

    Intentionally left ``not_configured`` with blank storage/SQL/Key
    Vault/factory fields — real values are the customer's own infrastructure
    (IMPLEMENTATION_PLAN.md A-4) and must never be invented by this app.
    """
    existing = {e.name for e in db.scalars(select(models.Environment))}
    for env_id, name, label, description, initial_status in _ENVIRONMENTS:
        if name in existing:
            continue
        db.add(
            models.Environment(
                id=env_id,
                name=name,
                label=label,
                status=initial_status,
                description=description,
            )
        )


def _seed_templates(db) -> None:
    existing = {t.id for t in db.scalars(select(models.PipelineTemplate))}
    for doc in TEMPLATE_DOCUMENTS:
        if doc["id"] in existing:
            continue
        db.add(
            models.PipelineTemplate(
                id=doc["id"],
                name=doc["name"],
                version=doc["version"],
                description=doc["description"],
                active=True,
                document=doc,
            )
        )
