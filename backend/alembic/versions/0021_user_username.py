"""Login usernames for AUTH_MODE=password

Revision ID: 0021_user_username
Revises: 0020_git_catalog
Create Date: 2026-09-27

Adds ``users.username`` (unique, lowercase, never contains "@") so a person can
sign in with a username or their email. Existing rows are backfilled from their
email's local part, de-duplicated with a numeric suffix, using the same rule as
``app/security.py::username_from_email``. Nullable so a future SSO
JIT-provisioned account can exist without one.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_user_username"
down_revision: str | None = "0020_git_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


def _base_username(email: str, user_id: str) -> str:
    local = email.split("@", 1)[0].strip().lower()
    candidate = re.sub(r"[^a-z0-9._-]", ".", local).strip("._-")[:56]
    if not _USERNAME_RE.match(candidate):
        candidate = re.sub(r"[^a-z0-9]", "", user_id.lower())[:56] or "user"
    return candidate


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(64), nullable=True))
    op.create_index("ux_users_username", "users", ["username"], unique=True)

    bind = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.String),
        sa.column("email", sa.String),
        sa.column("username", sa.String),
        sa.column("created_at", sa.DateTime),
    )
    taken: set[str] = set()
    rows = bind.execute(sa.select(users.c.id, users.c.email).order_by(users.c.created_at, users.c.id)).all()
    for user_id, email in rows:
        base = _base_username(email or "", user_id)
        username, n = base, 2
        while username in taken:
            username, n = f"{base}-{n}", n + 1
        taken.add(username)
        bind.execute(sa.update(users).where(users.c.id == user_id).values(username=username))


def downgrade() -> None:
    op.drop_index("ux_users_username", table_name="users")
    op.drop_column("users", "username")
