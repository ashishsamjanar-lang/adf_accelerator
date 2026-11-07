"""Onboarding sync-to-Git + per-project Azure integrations

Revision ID: 0019_onboarding_sync_and_integrations
Revises: 0018_sso_configurations
Create Date: 2026-09-26

Purely additive: ``onboarding_syncs`` (AGENTS.md §26.5 "Sync to Git" history +
last-synced snapshot for the per-row diff) and ``project_integrations`` (§29.1
layer-6 live ARM check and the Purview lineage push — config + Key Vault
secret references only).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_onboarding_sync_and_integrations"
down_revision: str | None = "0018_sso_configurations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "onboarding_syncs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("repository_url", sa.String(512), nullable=False),
        sa.Column("feature_branch", sa.String(256), nullable=False),
        sa.Column("base_branch", sa.String(256), nullable=False),
        sa.Column("commit_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("file_paths", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(512), nullable=True),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_onboarding_syncs_project", "onboarding_syncs", ["project_id"])
    op.create_table(
        "project_integrations",
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("project_integrations")
    op.drop_index("ix_onboarding_syncs_project", table_name="onboarding_syncs")
    op.drop_table("onboarding_syncs")
