"""SSO configuration + group-to-role mappings (§27 ID2)

Revision ID: 0018_sso_configurations
Revises: 0017_onboarding_mapping_profiles
Create Date: 2026-09-26

Purely additive: ``sso_configurations`` and ``sso_role_mappings`` — admin-edited
identity-provider metadata. No login path reads them yet (the OIDC/SAML adapter
is still target design). Client secrets are Key Vault references only.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_sso_configurations"
down_revision: str | None = "0017_onboarding_mapping_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sso_configurations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("protocol", sa.String(16), nullable=False),
        sa.Column("issuer_url", sa.String(512), nullable=True),
        sa.Column("client_id", sa.String(256), nullable=True),
        sa.Column("client_secret_key_vault", sa.String(128), nullable=True),
        sa.Column("client_secret_name", sa.String(128), nullable=True),
        sa.Column("metadata_url", sa.String(512), nullable=True),
        sa.Column("entity_id", sa.String(512), nullable=True),
        sa.Column("group_claim", sa.String(128), nullable=False),
        sa.Column("jit_provisioning", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", name="uq_sso_configurations_name"),
    )
    op.create_table(
        "sso_role_mappings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "configuration_id", sa.String(64), sa.ForeignKey("sso_configurations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("group_value", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id"), nullable=True),
    )
    op.create_index("ix_sso_role_mappings_configuration", "sso_role_mappings", ["configuration_id"])


def downgrade() -> None:
    op.drop_index("ix_sso_role_mappings_configuration", table_name="sso_role_mappings")
    op.drop_table("sso_role_mappings")
    op.drop_table("sso_configurations")
