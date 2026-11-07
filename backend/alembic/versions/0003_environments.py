"""add environments table

Revision ID: 0003_environments
Revises: 0002_git_push_simulated
Create Date: 2026-09-11

Promotes ``Environment``/``EnvironmentConnection`` from demo-fixture-only
(``frontend/lib/mock-data``'s ``MOCK_ENVIRONMENTS``) to a real metastore table
so ``GET /api/v1/environments`` can serve live per-DEV/QA/PROD connection
config instead of an unconditional ``[]`` (AGENTS.md §23.12 gap 12,
IMPLEMENTATION_PLAN.md gap A-4).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_environments"
down_revision: str | None = "0002_git_push_simulated"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "environments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(32), nullable=False, unique=True),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="not_configured"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("storage_account", sa.String(256), nullable=False, server_default=""),
        sa.Column("sql_server", sa.String(256), nullable=False, server_default=""),
        sa.Column("database", sa.String(256), nullable=False, server_default=""),
        sa.Column("key_vault", sa.String(256), nullable=False, server_default=""),
        sa.Column("factory_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("environments")
