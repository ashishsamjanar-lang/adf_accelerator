"""initial metastore

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ts = sa.func.now()


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_ts, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_ts, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="DEVELOPER"),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "team_members",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="DEVELOPER"),
        sa.UniqueConstraint("team_id", "user_id"),
        *_timestamps(),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(64), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "project_settings",
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("document", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.String(200), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "pipeline_templates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("document", sa.JSON(), nullable=False, server_default="{}"),
        *_timestamps(),
    )
    op.create_table(
        "pipeline_definitions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("template_id", sa.String(64), sa.ForeignKey("pipeline_templates.id"), nullable=False),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("spec_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("latest_artifact_version", sa.String(32), nullable=True),
        sa.Column("draft", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("spec", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(64), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "pipeline_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("pipeline_id", sa.String(64), sa.ForeignKey("pipeline_definitions.id"), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("template_version", sa.String(32), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.UniqueConstraint("pipeline_id", "version"),
        *_timestamps(),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("pipeline_id", sa.String(64), sa.ForeignKey("pipeline_definitions.id"), nullable=False),
        sa.Column("pipeline_name", sa.String(128), nullable=False),
        sa.Column("file_name", sa.String(256), nullable=False),
        sa.Column("file_type", sa.String(16), nullable=False, server_default="json"),
        sa.Column("artifact_type", sa.String(32), nullable=False),
        sa.Column("layer", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="generated"),
        sa.Column("bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("repository_path", sa.String(512), nullable=True),
        sa.UniqueConstraint("pipeline_id", "file_name", "version"),
        *_timestamps(),
    )
    op.create_table(
        "git_push_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("pipeline_id", sa.String(64), sa.ForeignKey("pipeline_definitions.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("repository_url", sa.String(512), nullable=False),
        sa.Column("feature_branch", sa.String(256), nullable=False),
        sa.Column("base_branch", sa.String(256), nullable=False, server_default="dev"),
        sa.Column("artifact_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("commit_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "git_pull_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("push_request_id", sa.String(64), sa.ForeignKey("git_push_requests.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_branch", sa.String(256), nullable=False),
        sa.Column("target_branch", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        *_timestamps(),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("pipeline_id", sa.String(64), sa.ForeignKey("pipeline_definitions.id"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_by", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(256), nullable=False, server_default=""),
        sa.Column("details", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
    )


def downgrade() -> None:
    for table in (
        "audit_log",
        "jobs",
        "git_pull_requests",
        "git_push_requests",
        "artifacts",
        "pipeline_versions",
        "pipeline_definitions",
        "pipeline_templates",
        "project_settings",
        "projects",
        "team_members",
        "users",
        "teams",
    ):
        op.drop_table(table)
