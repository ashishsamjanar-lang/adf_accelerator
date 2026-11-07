"""Metastore ORM models (AGENTS.md §23.5) — VENDORED copy of backend/app/models.py.

Source of truth for **application state**; Git is source of truth for
**artifacts**. Every tenant-scoped row carries ownership columns;
``pipeline_versions`` / ``artifact_versions`` are immutable once written.

**Secrets:** only Key Vault name + secret reference is ever stored — never a
secret value, connection string, PAT, or client secret (§1.6, §23.5).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, OwnershipMixin, TimestampMixin

# --- Lifecycle state sets (§10 / types/index.ts) ----------------------------

PIPELINE_STATES = (
    "DRAFT",
    "GENERATED",
    "VALIDATED",
    "READY_TO_PUSH",
    "PUSHING",
    "PUSHED",
    "PR_CREATED",
    "GENERATION_FAILED",
    "VALIDATION_FAILED",
    "GIT_PUSH_FAILED",
    "PR_CREATION_FAILED",
)
ARTIFACT_FILE_STATES = ("generated", "validated", "committed")  # §23.12 gap 10


# --- Identity --------------------------------------------------------------


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    projects: Mapped[list["Project"]] = relationship(back_populates="team")


class User(Base, TimestampMixin):
    """A user's team/role assignment is these two columns — ``team_id`` +
    ``role`` — not a separate membership table; the previously-vendored
    ``TeamMember``/``team_members`` was dead schema on the backend side
    (declared, never inserted/queried) and was dropped there (migration
    0008) — removed here too to keep this vendored copy in parity. This
    model doesn't vendor the backend's password-auth columns (§27.2
    extension) since the worker never authenticates a user or reads them.
    ``sessions`` (§27.3) is skipped entirely for the same reason. Likewise,
    ``app.connection_library`` (§24.17, migration 0005) is deliberately not
    vendored here: the worker only ever reads a pipeline's already-resolved
    `SpecDraft` JSON (linked services etc. inlined at generate time), never
    the connection-library table itself — see docs/DDL.md's doc-sync note
    (2026-09-13) for the full list of what this file intentionally omits.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="DEVELOPER", nullable=False)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Projects -------------------------------------------------------------


class Project(Base, OwnershipMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    team: Mapped[Team] = relationship(back_populates="projects")
    settings: Mapped["ProjectSettings | None"] = relationship(
        back_populates="project", uselist=False, cascade="all, delete-orphan"
    )
    pipelines: Mapped[list["PipelineDefinition"]] = relationship(back_populates="project")


class ProjectSettings(Base, TimestampMixin):
    """Full settings aggregate (§5) — one JSON document per project."""

    __tablename__ = "project_settings"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)

    project: Mapped[Project] = relationship(back_populates="settings")


# --- Templates + pipelines ----------------------------------------------


class PipelineTemplate(Base, TimestampMixin):
    __tablename__ = "pipeline_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class PipelineDefinition(Base, OwnershipMixin):
    __tablename__ = "pipeline_definitions"
    __table_args__ = (
        Index("ix_pipeline_definitions_project", "project_id"),
        Index("ix_pipeline_definitions_team", "team_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    template_id: Mapped[str] = mapped_column(ForeignKey("pipeline_templates.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)
    spec_version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    latest_artifact_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The current draft/spec inputs (SpecDraft camelCase payload).
    draft: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Last built LogicalPipelineSpec (§23.3).
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Who last edited name/template/draft (kept in parity with backend/app/models.py).
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    project: Mapped[Project] = relationship(back_populates="pipelines")
    versions: Mapped[list["PipelineVersion"]] = relationship(
        back_populates="pipeline", cascade="all, delete-orphan", order_by="PipelineVersion.created_at"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="pipeline", cascade="all, delete-orphan")


class PipelineVersion(Base, TimestampMixin):
    """Immutable once written — regeneration creates a new row (§23.5)."""

    __tablename__ = "pipeline_versions"
    __table_args__ = (UniqueConstraint("pipeline_id", "version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipeline_definitions.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    template_version: Mapped[str] = mapped_column(String(32), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Which registered ADF instance (`environments.name`) this version was
    # generated for — nullable, multi-instance support (IMPLEMENTATION_PLAN.md
    # A-1/A-2). Vendored in parity with backend/app/models.py.
    target_environment_name: Mapped[str | None] = mapped_column(String(32), nullable=True)

    pipeline: Mapped[PipelineDefinition] = relationship(back_populates="versions")


# --- Artifacts ---------------------------------------------------------


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("pipeline_id", "file_name", "version"),
        Index("ix_artifacts_pipeline", "pipeline_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipeline_definitions.id"), nullable=False)
    pipeline_name: Mapped[str] = mapped_column(String(128), nullable=False)
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), default="json", nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    layer: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="generated", nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    repository_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    pipeline: Mapped[PipelineDefinition] = relationship(back_populates="artifacts")


# --- Git --------------------------------------------------------------


class GitPushRequest(Base, TimestampMixin):
    __tablename__ = "git_push_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipeline_definitions.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    repository_url: Mapped[str] = mapped_column(String(512), nullable=False)
    feature_branch: Mapped[str] = mapped_column(String(256), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(256), default="dev", nullable=False)
    artifact_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    commit_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # True when no Git credential resolved and the worker used the offline
    # SimulatedAdapter instead of a real provider call (§23.12 gap #14).
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    pull_request: Mapped["GitPullRequest | None"] = relationship(
        back_populates="push_request", uselist=False, cascade="all, delete-orphan"
    )


class GitPullRequest(Base, TimestampMixin):
    __tablename__ = "git_pull_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    push_request_id: Mapped[str] = mapped_column(ForeignKey("git_push_requests.id"), nullable=False)
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_branch: Mapped[str] = mapped_column(String(256), nullable=False)
    target_branch: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    push_request: Mapped[GitPushRequest] = relationship(back_populates="pull_request")


# --- Jobs (worker hand-off) -----------------------------------------


class Job(Base, TimestampMixin):
    """A unit of work handed to the stateless worker (§23.4).

    The worker polls ``status = 'queued'`` rows, claims one atomically, runs it,
    and writes ``result`` / ``error``. The payload carries the spec + template
    id — never mutable UI state.
    """

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # generate | validate | git_push
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    pipeline_id: Mapped[str | None] = mapped_column(ForeignKey("pipeline_definitions.id"), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


# --- Audit ----------------------------------------------------------


class AuditEntry(Base, TimestampMixin):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_team", "team_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    details: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Vendored twin of backend/app/models.py's column — kept in parity even
    # though the worker never scope-queries it, since both services run
    # `create_all` against the same metastore (AGENTS.md §30.7 AQ1).
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), nullable=True)


# --- Environments (IMPLEMENTATION_PLAN.md A-4; multi-ADF-instance support) --
# Vendored read-only mirror of backend/app/models.py::Environment — the
# worker only ever reads this table (to resolve a draft's
# target_environment_name at generate/validate time), never writes it.


class Environment(Base, TimestampMixin):
    __tablename__ = "environments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="not_configured", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    storage_account: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    sql_server: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    database: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    key_vault: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    factory_name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    subscription_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    resource_group: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


# --- §26.5 Onboarding sync-to-Git · §29 layer 6 / Purview integrations ----------


class OnboardingSync(Base, TimestampMixin):
    """One §26.5 "Sync to Git" of a project's Bronze registry: the control-table
    SQL + ``metadata/objects.csv`` (+ registry lineage) pushed to a feature
    branch, then a PR into the base branch. ``snapshot`` is the registry rows as
    pushed, keyed by ``object_key`` — the base the next sync's per-row diff is
    computed against."""

    __tablename__ = "onboarding_syncs"
    __table_args__ = (Index("ix_onboarding_syncs_project", "project_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    repository_url: Mapped[str] = mapped_column(String(512), nullable=False)
    feature_branch: Mapped[str] = mapped_column(String(256), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(256), nullable=False)
    commit_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    file_paths: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ProjectIntegration(Base, TimestampMixin):
    """Per-project external-service config: ``live_validation`` (§29.1 layer 6,
    ARM validate-mode against a sandbox resource group) and ``purview`` (lineage
    push). Identifiers + a Key Vault secret *reference* only — never a secret."""

    __tablename__ = "project_integrations"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class GitCatalogEntry(Base):
    """§24.8 Git-backed catalog index — one row per indexed repo file per
    branch, rewritten wholesale by the worker's ``catalog_refresh`` job.
    ``content`` is the file text (bounded), so detail views, "Open in Build",
    and §26.6 Git import/drift never need another provider round-trip."""

    __tablename__ = "git_catalog_entries"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    branch: Mapped[str] = mapped_column(String(256), primary_key=True)
    path: Mapped[str] = mapped_column(String(1024), primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(260), nullable=False)
    blob_sha: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    references: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    parameters: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GitCatalogChange(Base, TimestampMixin):
    """§24.9/§24.10 a change to files already in Git — raw-JSON update,
    rename (delete old path + add new, optional inbound-reference rewrite), or
    delete — pushed to a feature branch then PR'd into the base branch.
    ``files``/``delete_paths`` are exactly what the commit writes/removes."""

    __tablename__ = "git_catalog_changes"
    __table_args__ = (Index("ix_git_catalog_changes_project", "project_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # update | rename | delete
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    new_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    repository_url: Mapped[str] = mapped_column(String(512), nullable=False)
    feature_branch: Mapped[str] = mapped_column(String(256), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(256), nullable=False)
    commit_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    files: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list, nullable=False)
    delete_paths: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Blob sha of ``path`` as the user loaded it — the optimistic-concurrency base (§24.14).
    base_blob_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
