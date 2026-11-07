"""add subscription_id/resource_group to environments

Revision ID: 0004_environment_azure_fields
Revises: 0003_environments
Create Date: 2026-09-11

Adds the Azure subscription/resource group identifiers alongside the existing
per-DEV/QA/PROD storage account/SQL server/database/Key Vault/factory name
columns (``0003_environments``) so a factory can be fully qualified
(``/subscriptions/{sub}/resourceGroups/{rg}/.../factories/{factoryName}``) in
generated artifacts/UI. Identifiers only, same rule as every other column on
this table — never used by this app to call ARM or authenticate to Azure
(§20 scope boundary).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_environment_azure_fields"
down_revision: str | None = "0003_environments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("environments", sa.Column("subscription_id", sa.String(64), nullable=False, server_default=""))
    op.add_column("environments", sa.Column("resource_group", sa.String(256), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("environments", "resource_group")
    op.drop_column("environments", "subscription_id")
