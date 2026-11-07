"""Typed governance fields on the Silver/Gold registry

Revision ID: 0022_registry_governance_fields
Revises: 0021_user_username
Create Date: 2026-10-20

gaps.md DMT-08: the Data Modular template's PII/PHI/PCI flags, critical-data-element and
audit-required flags, retention period, regulatory tag and business criticality were only kept as
text in ``notes``. They become nullable typed columns on ``silver_objects`` and ``mart_objects``
(null = not stated). Purely additive.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_registry_governance_fields"
down_revision: str | None = "0021_user_username"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("silver_objects", "mart_objects")
_COLUMNS = (
    ("contains_pii", sa.Boolean()),
    ("contains_phi", sa.Boolean()),
    ("contains_pci", sa.Boolean()),
    ("critical_data_element", sa.Boolean()),
    ("audit_required", sa.Boolean()),
    ("retention_period", sa.String(64)),
    ("regulatory_tag", sa.String(128)),
    ("business_criticality", sa.String(32)),
)


def upgrade() -> None:
    for table in _TABLES:
        for name, type_ in _COLUMNS:
            op.add_column(table, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        for name, _ in reversed(_COLUMNS):
            op.drop_column(table, name)
