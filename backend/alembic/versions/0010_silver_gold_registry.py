"""STTM-based Silver/Gold object registry (AGENTS.md §33, IMPLEMENTATION_PLAN.md P1)

Revision ID: 0010_silver_gold_registry
Revises: 0009_pipeline_artifact_lookup_indexes
Create Date: 2026-09-14

Purely additive (§33.11): three new tables, no ``ALTER`` of any existing
table, no FK from any pre-existing table into these. Rollback is a clean
``DROP TABLE`` with zero blast radius on bronze onboarding, pipelines,
connections, or auth. Full DDL rationale: ``docs/DDL.md`` §1's "STTM-Based
Silver/Gold Object Registry" block; data-model contract: ``AGENTS.md`` §33.4.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_silver_gold_registry"
down_revision: str | None = "0009_pipeline_artifact_lookup_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ts = sa.func.now()


def _registry_columns() -> list[sa.Column]:
    """Columns shared by silver_objects/mart_objects — everything except the
    primary key and the layer-specific mandatory fields (curated_entity is
    shared; mart_objects additionally has mart_domain)."""
    return [
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tag", sa.String(128), nullable=False),
        sa.Column("source_object_keys", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("owner", sa.String(200), nullable=True),
        sa.Column("batch_group", sa.String(128), nullable=True),
        sa.Column("domain", sa.String(128), nullable=True),
        sa.Column("write_behavior", sa.String(16), nullable=False, server_default="append"),
        sa.Column("loading_behavior", sa.String(16), nullable=False, server_default="FullLoad"),
        sa.Column("watermark_column", sa.String(128), nullable=True),
        sa.Column("watermark_column_type", sa.String(16), nullable=True),
        sa.Column("business_keys", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("transformations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("data_quality_rules", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("target_table", sa.String(256), nullable=False),
        sa.Column("data_classification", sa.String(16), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("provenance_source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_ts, nullable=False),
    ]


def upgrade() -> None:
    # One row per uploaded STTM workbook — created first since silver_objects/
    # mart_objects.import_id references it.
    op.create_table(
        "sttm_imports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(256), nullable=False),
        sa.Column("file_hash", sa.String(128), nullable=False),
        sa.Column("sheet_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sheet_valid", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sheet_error", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mode", sa.String(16), nullable=False, server_default="upsert"),
        sa.Column("committed_by", sa.String(64), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts, nullable=False),
    )
    op.create_index("ix_sttm_imports_project", "sttm_imports", ["project_id"])

    op.create_table(
        "silver_objects",
        sa.Column("object_key", sa.String(256), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("curated_entity", sa.String(128), nullable=False),
        sa.Column("import_id", sa.String(64), sa.ForeignKey("sttm_imports.id"), nullable=True),
        *_registry_columns(),
        sa.UniqueConstraint("project_id", "curated_entity", name="uq_silver_objects_entity"),
    )
    op.create_index("ix_silver_objects_project", "silver_objects", ["project_id"])
    op.create_index("ix_silver_objects_tag", "silver_objects", ["project_id", "tag"])

    op.create_table(
        "mart_objects",
        sa.Column("object_key", sa.String(256), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("mart_domain", sa.String(128), nullable=False),
        sa.Column("curated_entity", sa.String(128), nullable=False),
        sa.Column("import_id", sa.String(64), sa.ForeignKey("sttm_imports.id"), nullable=True),
        *_registry_columns(),
        sa.UniqueConstraint("project_id", "mart_domain", "curated_entity", name="uq_mart_objects_entity"),
    )
    op.create_index("ix_mart_objects_project", "mart_objects", ["project_id"])
    op.create_index("ix_mart_objects_tag", "mart_objects", ["project_id", "tag"])


def downgrade() -> None:
    op.drop_index("ix_mart_objects_tag", table_name="mart_objects")
    op.drop_index("ix_mart_objects_project", table_name="mart_objects")
    op.drop_table("mart_objects")
    op.drop_index("ix_silver_objects_tag", table_name="silver_objects")
    op.drop_index("ix_silver_objects_project", table_name="silver_objects")
    op.drop_table("silver_objects")
    op.drop_index("ix_sttm_imports_project", table_name="sttm_imports")
    op.drop_table("sttm_imports")
