"""audit_log gains team_id, closing AGENTS.md §30.7 AQ1's team-scoping gap

Revision ID: 0012_audit_log_team_id
Revises: 0011_onboarding_objects
Create Date: 2026-09-19

`GET /admin/audit` (`app/api/admin.py`) was reachable by both PLATFORM_ADMIN
and TEAM_ADMIN (`app/rbac.py`'s `("admin", "view_audit")` entry) but returned
every team's audit rows to either — the exact "role name was checked, scope
was not" gap `require_scope`/`scoped_team_id` (`app/rbac.py`, gap #15) already
fixed for projects/pipelines/artifacts, just never applied here. `audit_log`
had no `team_id` column to filter on at all. Adds one, nullable (a handful of
audited actions — environment CRUD, the platform-admin bootstrap CLI — are
genuinely global, not team-owned, and stay PLATFORM_ADMIN-only-visible via a
NULL team_id rather than a fabricated team). Captured at write time
(`app/repository.py::record_audit`) from whatever team-owned resource the
action acted on, denormalized rather than derived from `actor` (a free-text
display name, not a stable id) so a later team reassignment never rewrites
history. Purely additive — no existing column altered.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0012_audit_log_team_id"
down_revision: str | None = "0011_onboarding_objects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("team_id", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        "fk_audit_log_team_id_teams", "audit_log", "teams", ["team_id"], ["id"]
    )
    op.create_index("ix_audit_log_team", "audit_log", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_team", table_name="audit_log")
    op.drop_constraint("fk_audit_log_team_id_teams", "audit_log", type_="foreignkey")
    op.drop_column("audit_log", "team_id")
