"""add pipeline_versions.target_environment_name

Revision ID: 0007_pipeline_version_target_environment
Revises: 0006_pipeline_updated_by
Create Date: 2026-09-13

Multi-ADF-instance support (IMPLEMENTATION_PLAN.md A-1/A-2): a generated
version now optionally records which registered `environments` row
(ADF instance) it was generated for, so `globalParameters.json`'s resolved
factory/environment name (once real, not the `<FACTORY_NAME>` placeholder)
is traceable back to the version that produced it. Nullable — most rows
remain "no instance selected", unchanged from today.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_pipeline_version_target_environment"
down_revision: str | None = "0006_pipeline_updated_by"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pipeline_versions", sa.Column("target_environment_name", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_versions", "target_environment_name")
