"""Bronze source onboarding registry (AGENTS.md §26)

Revision ID: 0011_onboarding_objects
Revises: 0010_silver_gold_registry
Create Date: 2026-09-15

Purely additive: one new table, no ``ALTER`` of any existing table. Closes
the gap where `SilverObject.source_object_keys` (migration
`0010_silver_gold_registry`) already documented itself as referencing
`app.onboarding_objects`, but that table never existed — the frontend
registry was `localStorage`-only (`frontend/lib/adf/onboarding-store.ts`).
Column set matches `frontend/types/onboarding.ts`'s `OnboardingObjectEntry`,
not the full aspirational §26.2 catalogue in `docs/DDL.md`. Rollback is a
clean ``DROP TABLE`` with zero blast radius on any other table.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_onboarding_objects"
down_revision: str | None = "0010_silver_gold_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ts = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "onboarding_objects",
        sa.Column("object_key", sa.String(256), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("batch_group", sa.String(128), nullable=True),
        sa.Column("owner", sa.String(200), nullable=True),
        sa.Column("source_system", sa.String(128), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False, server_default="table"),
        sa.Column("source_schema", sa.String(128), nullable=True),
        sa.Column("source_object", sa.String(256), nullable=False),
        sa.Column("source_query", sa.Text(), nullable=True),
        sa.Column("target_type", sa.String(16), nullable=False, server_default="table"),
        sa.Column("target_schema", sa.String(128), nullable=True),
        sa.Column("target_object", sa.String(256), nullable=True),
        sa.Column("target_path", sa.String(512), nullable=True),
        sa.Column("target_file_name", sa.String(256), nullable=True),
        sa.Column("target_file_format", sa.String(32), nullable=False, server_default="Parquet"),
        sa.Column("write_behavior", sa.String(16), nullable=True, server_default="append"),
        sa.Column("upsert_keys", sa.String(512), nullable=True),
        sa.Column("loading_behavior", sa.String(16), nullable=False, server_default="FullLoad"),
        sa.Column("watermark_column", sa.String(128), nullable=True),
        sa.Column("watermark_column_type", sa.String(16), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_ts, nullable=False),
    )
    op.create_index("ix_onboarding_objects_project", "onboarding_objects", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_onboarding_objects_project", table_name="onboarding_objects")
    op.drop_table("onboarding_objects")
