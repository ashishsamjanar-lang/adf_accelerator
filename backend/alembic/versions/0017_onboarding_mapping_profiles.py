"""Bronze onboarding saved mapping profiles

Revision ID: 0017_onboarding_mapping_profiles
Revises: 0016_onboarding_imports
Create Date: 2026-09-26

Purely additive: ``onboarding_mapping_profiles`` (AGENTS.md §26.3/§26.7) — named
upload-header maps per project, replacing the browser-only localStorage store.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_onboarding_mapping_profiles"
down_revision: str | None = "0016_onboarding_imports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "onboarding_mapping_profiles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("header_map", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_onboarding_mapping_profiles_project_name"),
    )


def downgrade() -> None:
    op.drop_table("onboarding_mapping_profiles")
