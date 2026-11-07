"""Bronze data-quality rules table (AGENTS.md §30.4/§30.5)

Revision ID: 0014_dq_rules
Revises: 0013_managed_identities
Create Date: 2026-09-19

Purely additive: one new table, no ``ALTER`` of any existing table. Schema-only
for now — nothing generates a pipeline activity from a row here yet (§30.7
AQ2/AQ3 remain open); the already-live pipeline-level `dataQualityRules`
field is unaffected. Deliberately Bronze-only (`onboarding_objects`) — see
`app.models.DqRule`'s docstring for why Silver/Gold don't use this table.
Rollback is a clean ``DROP TABLE`` with zero blast radius on any other table.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_dq_rules"
down_revision: str | None = "0013_managed_identities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ts = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "dq_rules",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("object_key", sa.String(256), nullable=False),
        sa.Column("check_type", sa.String(64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("enforcement", sa.String(16), nullable=False, server_default="warn"),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts, nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "object_key"],
            ["onboarding_objects.project_id", "onboarding_objects.object_key"],
        ),
    )
    op.create_index("ix_dq_rules_object", "dq_rules", ["project_id", "object_key"])


def downgrade() -> None:
    op.drop_index("ix_dq_rules_object", table_name="dq_rules")
    op.drop_table("dq_rules")
