"""Metastore ORM models (AGENTS.md §23.5).

Source of truth for **application state**; Git is source of truth for
**artifacts**. Every tenant-scoped row carries ownership columns;
``pipeline_versions`` / ``artifact_versions`` are immutable once written.

**Secrets:** only Key Vault name + secret reference is ever stored — never a
secret value, connection string, PAT, or client secret (§1.6, §23.5).

**Indexes** on a handful of tables (``sessions``, ``jobs``, ``connection_library``, and, as of
2026-09-13, ``pipeline_definitions``/``artifacts``) are declared here via ``__table_args__`` with an
explicit name matching the one already used by the Alembic migration that created it
(``docs/DDL.md``'s "helpful indexes" list) — not left implicit — precisely so CI's
`alembic revision --autogenerate` drift check (``.github/workflows/ci.yml``'s ``migrations`` job) sees
the model and the real schema agree instead of proposing to drop them every run.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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
    ``role`` — not a separate membership table (§27.7; the previously-dead
    ``team_members`` join table was retired, see docs/DDL.md). One home team
    per user, matching every RBAC scope check in ``app/rbac.py``.
    """

    __tablename__ = "users"
    # Named to match migration 0021 so alembic autogenerate sees no drift.
    __table_args__ = (Index("ux_users_username", "username", unique=True),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    # Login name for AUTH_MODE=password (migration 0021). Lowercase, unique, never
    # contains "@" (app/security.py USERNAME_RE). Nullable only so a future SSO
    # JIT-provisioned account (app/identity/) can exist without one.
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="DEVELOPER", nullable=False)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- AUTH_MODE=password (§27.2 extension) ---------------------------
    # Null under `mock`/future `oidc`/`saml` — those modes never touch these.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Forced on account creation and on an admin-triggered password reset;
    # cleared by a successful POST /auth/change-password.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who provisioned this account (POST /admin/users caller) — null for the
    # bootstrap platform admin created by `app/manage.py create-platform-admin`.
    invited_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Session(Base):
    """Server-side revocable session for ``AUTH_MODE=password`` (§27.3/§27.7).

    Referenced by an opaque, httpOnly+Secure+SameSite=Lax cookie — never a
    stateless JWT, so a compromised session can be revoked instantly
    (``revoked_at``). Two expiries: ``idle_expires_at`` slides forward on each
    authenticated request, ``expires_at`` is the hard absolute cap regardless
    of activity. ``csrf_token`` backs the double-submit CSRF cookie the
    frontend echoes back as ``X-CSRF-Token`` on state-changing requests — a
    detail this table needed beyond §27.7's original sketch, since a cookie
    session (unlike a bearer token) is otherwise vulnerable to CSRF.
    """

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)


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
    # Who last edited name/template/draft (`OwnershipMixin.created_by` only ever
    # records the original creator) — `updated_at` alone can't say *who* changed
    # a pipeline, only *when* (§23.5 tracking).
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
    # A-1/A-2). Not a foreign key: an instance can be renamed/deleted later
    # without invalidating the immutable history of what a past version targeted.
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
    # Mirrors GitPushRequest.simulated for the PR step specifically — a real
    # push can still be followed by a simulated PR (or vice versa) if the
    # credential stops resolving between the two adapter calls.
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)

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


# --- Environments (IMPLEMENTATION_PLAN.md A-4) -------------------------


class Environment(Base, TimestampMixin):
    """Per-DEV/QA/PROD connection configuration — identifiers/references only.

    This is **not** a deploy plane: no deployment status, no publish action.
    It is the live source of truth for "what's the QA SQL server / storage
    account / Key Vault name" that ``GET /api/v1/environments`` used to fake
    with a hardcoded ``[]`` (§23.12 gap 12). Secrets never live here — only
    the Key Vault *name* a pipeline should resolve secrets from at ADF
    execution time (§1.6/§5/§20).
    """

    __tablename__ = "environments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # DEV | QA | PROD
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="not_configured", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    storage_account: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    sql_server: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    database: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    key_vault: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    # ADF factory this environment's global parameters (A-2) resolve against —
    # a real value, supplied by whoever owns the customer's ARM/Bicep deploy of
    # that factory, never invented by this app (§20 scope boundary).
    factory_name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    # Azure subscription/resource group the factory (and the rest of this
    # environment's resources) lives in — identifiers only, same rule as every
    # other column here: never used by this app to call ARM or authenticate to
    # Azure, only to fully qualify a resource ID in generated artifacts/UI
    # (§20 scope boundary; mirrors the per-project AdfDefaults fields).
    subscription_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    resource_group: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


# --- Connection library (AGENTS.md §24.17) ------------------------------


class ConnectionLibraryEntry(Base, OwnershipMixin):
    """A saved, reusable ADF linked service + its table/file onboarding list
    and dataset customization (§24.17's ``connection_library``).

    Promotes what was previously browser-only state
    (``frontend/lib/adf/connection-store.ts``, localStorage) to shared,
    team-visible metastore rows: any project member can now see and reuse a
    connection a teammate configured, and it survives clearing the browser.
    The frontend keeps a localStorage mirror as an offline cache/fallback —
    same "aggressively try the API, fall back to local" shape as every other
    resource (``lib/api/hooks.tsx``'s ``useResource``).

    ``draft`` carries the connector-driven linked service, the source
    objects list (+ whether it was typed in or uploaded), and the dataset
    shape — i.e. everything but the identity columns below. Secrets never
    appear in it: linked-service secret fields hold only a Key Vault secret
    *name* (§1.6, §23.5), exactly as the frontend's ``LinkedServiceDraft``
    already enforces.
    """

    __tablename__ = "connection_library"
    __table_args__ = (
        Index("ix_connection_library_project_id", "project_id"),
        Index("ix_connection_library_team_id", "team_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    # Denormalized from the owning project, same pattern as
    # PipelineDefinition.team_id — lets list/scope queries filter by team
    # without a join (see `app.rbac.scoped_team_id` / `require_scope`).
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="source", nullable=False)  # source|target|both
    connector_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    draft: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


# --- Managed identities (AGENTS.md §32.6, gap #21 phase MI3) -------------


class ManagedIdentity(Base, TimestampMixin):
    """An admin-curated user-assigned managed identity (UAMI) a builder can pick
    instead of typing a raw ARM resource ID freehand (§32.5/§32.6). The ARM
    resource ID is a reference to infrastructure the platform team provisions
    out-of-band (Bicep/Terraform) — not a secret (§1.6) and never touches Key
    Vault; only *who may register one* is access-controlled (§32.8,
    ``manage_identities`` in `app/rbac.py`, TEAM_ADMIN+). This table is a pure
    pick-list: nothing here is queried by the generator itself, which stays a
    pure function of its `SpecDraft` input (§23.13) — a caller resolves a
    chosen identity's name/resource ID into `LinkedServiceDraft.credential_name`/
    `credential_resource_id` (`worker/app/connectors/drafts.py`) before generation.
    """

    __tablename__ = "managed_identities"
    __table_args__ = (
        UniqueConstraint("team_id", "name", name="uq_managed_identities_team_name"),
        Index("ix_managed_identities_team", "team_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(512), nullable=False)
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # diagnostics only
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


# --- Bronze Source Onboarding Registry (AGENTS.md §26) -------------------


class OnboardingObject(Base, TimestampMixin):
    """One onboarded source object (Bronze), CSV/Excel-uploaded or hand-entered
    (§26.1/§26.3). Column set matches the fields `frontend/types/onboarding.ts`
    ``OnboardingObjectEntry`` actually uses today, not the full aspirational
    §26.2 catalogue (`docs/DDL.md`'s ``(planned)`` block) — this closes the gap
    where the frontend registry (`lib/adf/onboarding-store.ts`) was
    `localStorage`-only while `SilverObject.source_object_keys` already
    documented itself as pointing at this table. Extending toward the full
    §26.2 field set (file-format detail columns, contract/quality columns,
    provenance) is additive later, same as Silver/Gold's own O1->O4 phasing.
    """

    __tablename__ = "onboarding_objects"
    __table_args__ = (Index("ix_onboarding_objects_project", "project_id"),)

    object_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    batch_group: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # public | internal | confidential | restricted — same vocabulary as silver/mart_objects.
    data_classification: Mapped[str | None] = mapped_column(String(16), nullable=True)

    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="table")  # table|view|query|file
    source_schema: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_object: Mapped[str] = mapped_column(String(256), nullable=False)
    source_query: Mapped[str | None] = mapped_column(Text, nullable=True)

    target_type: Mapped[str] = mapped_column(String(16), nullable=False, default="table")  # table|file
    target_schema: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_object: Mapped[str | None] = mapped_column(String(256), nullable=True)
    target_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_file_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    target_file_format: Mapped[str] = mapped_column(String(32), nullable=False, default="Parquet")

    write_behavior: Mapped[str | None] = mapped_column(String(16), nullable=True, default="append")
    upsert_keys: Mapped[str | None] = mapped_column(String(512), nullable=True)  # pipe-separated, e.g. "id|updated_at"

    loading_behavior: Mapped[str] = mapped_column(String(16), nullable=False, default="FullLoad")
    watermark_column: Mapped[str | None] = mapped_column(String(128), nullable=True)
    watermark_column_type: Mapped[str | None] = mapped_column(String(16), nullable=True)

    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)  # draft|valid|invalid
    # §26.6 where the row last came from: manual | upload | git-import (None = before tracking).
    provenance: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DqRule(Base):
    """A data-quality rule authored on a Bronze onboarding object (§30.4/§30.5).

    Deliberately Bronze-only (``onboarding_objects``) — Silver/Gold DQ rules
    (§33) ride inline as the ``data_quality_rules`` JSONB column on
    ``silver_objects``/``mart_objects`` instead, per §33.4's explicit
    divergence (avoids generalizing this table's single FK across three
    different parent tables). This table is schema-only for now: nothing
    generates a pipeline activity from a row here yet (that's §30.7 AQ2/AQ3,
    still open) — the *pipeline-level* `dataQualityRules` field
    (`SpecDraft.dataQualityRules`) is the one that already generates
    Lookup/Fail activities, and has no table of its own (§30.4's
    implementation note).
    """

    __tablename__ = "dq_rules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "object_key"],
            ["onboarding_objects.project_id", "onboarding_objects.object_key"],
        ),
        Index("ix_dq_rules_object", "project_id", "object_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(256), nullable=False)
    check_type: Mapped[str] = mapped_column(String(64), nullable=False)  # not_null|unique|range|row_count|freshness|...
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    enforcement: Mapped[str] = mapped_column(String(16), default="warn", nullable=False)  # off|warn|block
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --- STTM-Based Silver/Gold Object Registry (AGENTS.md §33, IMPLEMENTATION_PLAN.md P1) ---------


class OnboardingImport(Base):
    """One row per committed Bronze bulk upload into ``onboarding_objects``
    (§26.8) — the Bronze analogue of ``SttmImport``. ``file_hash`` is a
    server-computed SHA-256 of the committed rows (the file itself is parsed
    client-side and never reaches the server). ``mapping_profile_id`` is the
    ``onboarding_mapping_profiles`` row applied on the §26.3 Map step, if any
    (no FK — a later profile delete must not rewrite import history)."""

    __tablename__ = "onboarding_imports"
    __table_args__ = (Index("ix_onboarding_imports_project", "project_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    row_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_valid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_error: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="upsert", nullable=False)
    mapping_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    committed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OnboardingMappingProfile(Base, TimestampMixin):
    """§26.3 Map step — a named, reusable upload-header → registry-column map,
    saved per project (§26.7 ``/mapping-profiles``). Replaces the browser-only
    localStorage store; ``header_map`` is ``{uploaded header: column name}``."""

    __tablename__ = "onboarding_mapping_profiles"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_onboarding_mapping_profiles_project_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    header_map: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SttmImport(Base):
    """One row per uploaded STTM workbook (§33.4/§33.9) — layer-aware,
    multi-sheet analogue of the still-planned ``app.onboarding_imports``
    (bronze, §26.8). ``report`` carries the per-sheet parse/validation result
    the upload preview screen (§33.9's ``sttm-import`` endpoint) showed the
    user before commit."""

    __tablename__ = "sttm_imports"
    __table_args__ = (Index("ix_sttm_imports_project", "project_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)  # silver | gold
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    sheet_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sheet_valid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sheet_error: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="upsert", nullable=False)
    committed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SilverObject(Base, TimestampMixin):
    """One curated (Silver) entity, hand-authored or bulk-onboarded via an
    STTM Excel workbook (§33). ``transformations``/``data_quality_rules``
    store the exact shapes ``frontend/lib/adf/medallion.ts``'s
    ``TransformationRule[]`` and ``SpecDraft.dataQualityRules`` already use —
    no new transformation/DQ engine (§33.2). ``target_table`` is a cache of
    ``layerTargetTableName("silver", ...)``, recomputed on every write, never
    independently editable — keeps "the target name is always computed"
    invariant (§16) true for registry rows too.
    """

    __tablename__ = "silver_objects"
    __table_args__ = (
        UniqueConstraint("project_id", "curated_entity", name="uq_silver_objects_entity"),
        Index("ix_silver_objects_project", "project_id"),
        Index("ix_silver_objects_tag", "project_id", "tag"),
    )

    object_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # optimistic concurrency
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tag: Mapped[str] = mapped_column(String(128), nullable=False)  # mandatory classification (§33's explicit ask)
    curated_entity: Mapped[str] = mapped_column(String(128), nullable=False)
    source_object_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # app.onboarding_objects keys
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    batch_group: Mapped[str | None] = mapped_column(String(128), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(128), nullable=True)
    write_behavior: Mapped[str] = mapped_column(String(16), default="append", nullable=False)
    loading_behavior: Mapped[str] = mapped_column(String(16), default="FullLoad", nullable=False)
    watermark_column: Mapped[str | None] = mapped_column(String(128), nullable=True)
    watermark_column_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    business_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    transformations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)  # max 10
    data_quality_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)  # max 5
    target_table: Mapped[str] = mapped_column(String(256), nullable=False)
    data_classification: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # DMT-08: typed governance fields (Data Modular Section 1; null = not stated).
    contains_pii: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    contains_phi: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    contains_pci: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    critical_data_element: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    audit_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    retention_period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    regulatory_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_criticality: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    provenance_source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)  # upload | manual
    import_id: Mapped[str | None] = mapped_column(ForeignKey("sttm_imports.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)  # draft|valid|invalid
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MartObject(Base, TimestampMixin):
    """One business mart (Gold) entity — same shape as :class:`SilverObject`
    plus a mandatory ``mart_domain``; ``source_object_keys`` references
    ``app.silver_objects`` rows instead of bronze ``onboarding_objects``
    (§33.4)."""

    __tablename__ = "mart_objects"
    __table_args__ = (
        UniqueConstraint("project_id", "mart_domain", "curated_entity", name="uq_mart_objects_entity"),
        Index("ix_mart_objects_project", "project_id"),
        Index("ix_mart_objects_tag", "project_id", "tag"),
    )

    object_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tag: Mapped[str] = mapped_column(String(128), nullable=False)
    mart_domain: Mapped[str] = mapped_column(String(128), nullable=False)
    curated_entity: Mapped[str] = mapped_column(String(128), nullable=False)
    source_object_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # app.silver_objects keys
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    batch_group: Mapped[str | None] = mapped_column(String(128), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(128), nullable=True)
    write_behavior: Mapped[str] = mapped_column(String(16), default="append", nullable=False)
    loading_behavior: Mapped[str] = mapped_column(String(16), default="FullLoad", nullable=False)
    watermark_column: Mapped[str | None] = mapped_column(String(128), nullable=True)
    watermark_column_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    business_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    transformations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    data_quality_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    target_table: Mapped[str] = mapped_column(String(256), nullable=False)
    data_classification: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # DMT-08: typed governance fields (Data Modular Section 1; null = not stated).
    contains_pii: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    contains_phi: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    contains_pci: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    critical_data_element: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    audit_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    retention_period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    regulatory_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_criticality: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    provenance_source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    import_id: Mapped[str | None] = mapped_column(ForeignKey("sttm_imports.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


# --- Audit ----------------------------------------------------------


class AuditEntry(Base, TimestampMixin):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_team", "team_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    details: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Captured at write time from whatever team-owned resource the action acted
    # on (AGENTS.md §30.7 AQ1) — denormalized rather than derived from `actor`
    # (a free-text name, not a stable id) so a later team reassignment or role
    # change never rewrites history. NULL for platform-global actions (e.g.
    # environment CRUD) — those stay visible to PLATFORM_ADMIN only, never to a
    # team-scoped query.
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), nullable=True)


class SsoConfiguration(Base, TimestampMixin):
    """One identity-provider configuration (§27.2/§27.5, ID2) — admin-editable
    metadata only. The OIDC/SAML adapter that would *use* it is still target
    design (``current_user()`` 501s on those AUTH_MODEs); this row is never read
    by any login path yet. The client secret is a Key Vault reference only
    (vault name + secret name, rule #6) — never a value."""

    __tablename__ = "sso_configurations"
    __table_args__ = (UniqueConstraint("name", name="uq_sso_configurations_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False)  # oidc | saml
    issuer_url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # OIDC
    client_id: Mapped[str | None] = mapped_column(String(256), nullable=True)  # OIDC
    client_secret_key_vault: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_secret_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # SAML
    entity_id: Mapped[str | None] = mapped_column(String(512), nullable=True)  # SAML
    group_claim: Mapped[str] = mapped_column(String(128), nullable=False, default="groups")
    jit_provisioning: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    role_mappings: Mapped[list[SsoRoleMapping]] = relationship(
        back_populates="configuration", cascade="all, delete-orphan", order_by="SsoRoleMapping.position"
    )


class SsoRoleMapping(Base):
    """IdP group → (role, team) mapping for JIT provisioning (§27.5). First match
    by ``position`` wins once the adapter exists."""

    __tablename__ = "sso_role_mappings"
    __table_args__ = (Index("ix_sso_role_mappings_configuration", "configuration_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    configuration_id: Mapped[str] = mapped_column(
        ForeignKey("sso_configurations.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    group_value: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), nullable=True)

    configuration: Mapped[SsoConfiguration] = relationship(back_populates="role_mappings")


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
