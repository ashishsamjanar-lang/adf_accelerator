"""Managed identities registry (AGENTS.md §32.6, gap #21 phase MI3)

Revision ID: 0013_managed_identities
Revises: 0012_audit_log_team_id
Create Date: 2026-09-19

Purely additive: one new table, no ``ALTER`` of any existing table. An
admin-curated pick-list of user-assigned managed identities (UAMI) a builder
selects instead of typing a raw ARM resource ID freehand — see
`app.models.ManagedIdentity`'s docstring. Rollback is a clean ``DROP TABLE``
with zero blast radius on any other table.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_managed_identities"
down_revision: str | None = "0012_audit_log_team_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ts = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "managed_identities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("resource_id", sa.String(512), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_ts, nullable=False),
        sa.UniqueConstraint("team_id", "name", name="uq_managed_identities_team_name"),
    )
    op.create_index("ix_managed_identities_team", "managed_identities", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_managed_identities_team", table_name="managed_identities")
    op.drop_table("managed_identities")
