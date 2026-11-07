"""add pipeline/artifact lookup indexes documented in docs/DDL.md

Revision ID: 0009_pipeline_artifact_lookup_indexes
Revises: 0008_password_auth_identity
Create Date: 2026-09-13

``docs/DDL.md`` has always listed ``ix_pipeline_definitions_project``,
``ix_pipeline_definitions_team``, and ``ix_artifacts_pipeline`` alongside the
other "helpful indexes for the lookups the API layer actually does" — and
`app/repository.py`'s ``list_pipelines``/``list_artifacts`` do filter on
exactly these columns (``project_id``/``team_id`` on ``pipeline_definitions``,
``pipeline_id`` on ``artifacts``) — but no migration ever actually created
them; a concurrency-test + DDL-sync pass (2026-09-13, user request: "no
attribute should be left behind") found the doc had drifted from reality in
this direction too, not just the other. ``ix_git_push_requests_pipeline`` and
``ix_audit_log_target`` were the same kind of doc/reality gap but with no
matching query to justify them yet (nothing filters ``git_push_requests`` by
``pipeline_id`` or ``audit_log`` by ``target`` today) — marked ``(planned)``
in the doc instead of fabricated here, per this app's own no-fabricated-schema
rule (§1.5) applied to indexes, not just column values.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_pipeline_artifact_lookup_indexes"
down_revision: str | None = "0008_password_auth_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_pipeline_definitions_project", "pipeline_definitions", ["project_id"])
    op.create_index("ix_pipeline_definitions_team", "pipeline_definitions", ["team_id"])
    op.create_index("ix_artifacts_pipeline", "artifacts", ["pipeline_id"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_pipeline", table_name="artifacts")
    op.drop_index("ix_pipeline_definitions_team", table_name="pipeline_definitions")
    op.drop_index("ix_pipeline_definitions_project", table_name="pipeline_definitions")
