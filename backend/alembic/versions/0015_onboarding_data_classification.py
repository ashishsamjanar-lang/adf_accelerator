"""Data classification on Bronze onboarding objects

Revision ID: 0015_onboarding_data_classification
Revises: 0014_dq_rules
Create Date: 2026-09-25

Purely additive: one nullable column on ``onboarding_objects`` (public |
internal | confidential | restricted), matching ``silver_objects``/``mart_objects``'
existing ``data_classification`` so all three registry layers classify alike.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_onboarding_data_classification"
down_revision: str | None = "0014_dq_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("onboarding_objects", sa.Column("data_classification", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("onboarding_objects", "data_classification")
