"""add simulated flag to git push/pull requests

Revision ID: 0002_git_push_simulated
Revises: 0001_initial
Create Date: 2026-09-11

Persists ``PushResult.simulated`` / ``PullRequestResult.simulated`` (the worker
already computes both — see ``worker/app/gitops/simulated.py``) so the API can
surface an honest "this was simulated, no real Git call happened" signal on
``GitPushOut`` instead of silently discarding it (AGENTS.md §23.12 gap #14).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_git_push_simulated"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_push_requests",
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "git_pull_requests",
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("git_pull_requests", "simulated")
    op.drop_column("git_push_requests", "simulated")
