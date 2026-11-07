"""password auth + sessions; drop dead team_members

Revision ID: 0008_password_auth_identity
Revises: 0007_pipeline_version_target_environment
Create Date: 2026-09-13

Adds the interim ``AUTH_MODE=password`` identity layer (AGENTS.md §27.2
extension): password columns on ``users``, and the ``sessions`` table §27.7
already sketched as "(planned)" — now live. Also drops ``team_members``:
it was declared in ``models.py``/``docs/DDL.md`` but never inserted into or
queried by any code path (team assignment has only ever been
``users.team_id`` + ``users.role``, which is what every RBAC scope check in
``app/rbac.py`` already assumes) — kept per "no attribute should be left
behind" rather than half-wiring an unused multi-team-membership table.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_password_auth_identity"
down_revision: str | None = "0007_pipeline_version_target_environment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("password_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("invited_by", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_users_invited_by_users", "users", "users", ["invited_by"], ["id"]
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("csrf_token", sa.String(64), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.drop_table("team_members")


def downgrade() -> None:
    op.create_table(
        "team_members",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="DEVELOPER"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("team_id", "user_id"),
    )

    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")

    op.drop_constraint("fk_users_invited_by_users", "users", type_="foreignkey")
    op.drop_column("users", "invited_by")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "password_updated_at")
    op.drop_column("users", "password_hash")
