"""Bronze onboarding bulk-upload audit trail

Revision ID: 0016_onboarding_imports
Revises: 0015_onboarding_data_classification
Create Date: 2026-09-25

Purely additive: ``onboarding_imports`` (AGENTS.md §26.8), one row per committed
bulk upload into ``onboarding_objects`` — the Bronze analogue of ``sttm_imports``.
``mapping_profile_id`` stays null until the §26.3 Map step exists.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_onboarding_imports"
down_revision: str | None = "0015_onboarding_data_classification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "onboarding_imports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("file_name", sa.String(256), nullable=False),
        sa.Column("file_hash", sa.String(128), nullable=False),
        sa.Column("row_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("row_valid", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("row_error", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mode", sa.String(16), nullable=False, server_default="upsert"),
        sa.Column("mapping_profile_id", sa.String(64), nullable=True),
        sa.Column("committed_by", sa.String(64), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_onboarding_imports_project", "onboarding_imports", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_onboarding_imports_project", table_name="onboarding_imports")
    op.drop_table("onboarding_imports")
