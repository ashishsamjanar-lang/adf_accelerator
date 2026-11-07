"""Git-backed artifact catalog + catalog change PRs + onboarding provenance

Revision ID: 0020_git_catalog
Revises: 0019_onboarding_sync_and_integrations
Create Date: 2026-09-26

Purely additive: ``git_catalog_entries`` (AGENTS.md §24.8 catalog index — one
row per indexed repo file per branch, written by the worker's
``catalog_refresh`` job), ``git_catalog_changes`` (§24.9/§24.10 raw-edit /
rename / delete PRs against files already in Git — separate from
``git_push_requests``, which is keyed to one generated pipeline), and
``onboarding_objects.provenance`` (§26.6 ``git-import``).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_git_catalog"
down_revision: str | None = "0019_onboarding_sync_and_integrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "git_catalog_entries",
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("branch", sa.String(256), primary_key=True),
        sa.Column("path", sa.String(1024), primary_key=True),
        sa.Column("artifact_type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(260), nullable=False),
        sa.Column("blob_sha", sa.String(64), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("references", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("parameters", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("indexed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "git_catalog_changes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("new_path", sa.String(1024), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("repository_url", sa.String(512), nullable=False),
        sa.Column("feature_branch", sa.String(256), nullable=False),
        sa.Column("base_branch", sa.String(256), nullable=False),
        sa.Column("commit_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("files", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("delete_paths", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("base_blob_sha", sa.String(64), nullable=True),
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
    op.create_index("ix_git_catalog_changes_project", "git_catalog_changes", ["project_id"])
    op.add_column("onboarding_objects", sa.Column("provenance", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("onboarding_objects", "provenance")
    op.drop_index("ix_git_catalog_changes_project", table_name="git_catalog_changes")
    op.drop_table("git_catalog_changes")
    op.drop_table("git_catalog_entries")
