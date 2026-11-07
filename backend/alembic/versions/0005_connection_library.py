"""add connection_library table

Revision ID: 0005_connection_library
Revises: 0004_environment_azure_fields
Create Date: 2026-09-13

Promotes the saved-connection store (§24.17's ``connection_library`` —
previously browser-only state in ``frontend/lib/adf/connection-store.ts``,
mirrored to localStorage only) to a real metastore table, so a linked service
+ its table/file onboarding list persists server-side and is shared across a
project's team instead of living in one person's browser.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_connection_library"
down_revision: str | None = "0004_environment_azure_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connection_library",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="source"),
        sa.Column("connector_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("draft", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_connection_library_project_id", "connection_library", ["project_id"])
    op.create_index("ix_connection_library_team_id", "connection_library", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_connection_library_team_id", table_name="connection_library")
    op.drop_index("ix_connection_library_project_id", table_name="connection_library")
    op.drop_table("connection_library")
