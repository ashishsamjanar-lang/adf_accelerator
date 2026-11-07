"""add pipeline_definitions.updated_by

Revision ID: 0006_pipeline_updated_by
Revises: 0005_connection_library
Create Date: 2026-09-13

`created_by` only ever records the original creator; nothing tracked *who*
last edited a pipeline's name/template/draft, only `updated_at` (*when*).
Adds `updated_by` so pipeline tracking metadata (§23.5) is symmetric with
`created_by`.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_pipeline_updated_by"
down_revision: str | None = "0005_connection_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pipeline_definitions", sa.Column("updated_by", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_definitions", "updated_by")
