# Data Definition Language (DDL) reference

This file is the single reference for every table this product touches, split by the boundary that
already governs the codebase (a metadata-driven control plane):

| | Store | Engine | Schema | Who reads/writes it |
|---|---|---|---|---|
| **1. Application metastore** | this app's own Postgres | PostgreSQL 15+ | `app` | `backend/` (SQLAlchemy 2 + Alembic) and the worker's vendored subset (`worker/app/db/models.py`). Holds *application state* — teams, users, projects, pipeline drafts, artifact versions, onboarding registries, Git push history, jobs, audit log. **ADF never connects to this database.** |
| **2. ADF execution-plane control store** | the customer's own Azure SQL Database (or SQL Server) that generated pipelines connect to at runtime | Azure SQL / SQL Server (T‑SQL) | `control` | Generated ADF pipelines only, via a linked service. Holds the metadata‑driven Copy Data control table(s) a running pipeline `Lookup`s to decide what/how to copy. **This app never queries this database** — it only *generates* the DDL + seed `INSERT`s as Git artifacts (`adf_control_table.sql` and siblings) and pushes them through the normal feature-branch → PR flow (§17, §23.6). |

Keeping these apart matters because they have different owners, lifecycles, and security boundaries:
schema 1 is this app's internal state and is never exposed to ADF; schema 2 is customer runtime data
that ADF's managed identity / linked service reads and writes, and this app never stores secrets for it
(§1.6, §23.5) — only a Key Vault name + secret reference.

**Runnable script.** [adf_forge_ddl.sql](adf_forge_ddl.sql) is generated from this file by
[scripts/build_ddl_sql.py](scripts/build_ddl_sql.py): live tables only (no **(planned)** tables or
columns), schemas renamed `adf_forge_app` / `adf_forge_control`, and every inline column comment below
emitted as a Postgres `COMMENT ON COLUMN` so it is visible in `psql \d+` and any catalog tool. Edit this
file, then re-run `python docs/scripts/build_ddl_sql.py` — never hand-edit the `.sql`.

---

## 1. Application metastore — schema `app` (PostgreSQL)

Source of truth for application state. Git remains the source of truth for
*artifacts* — this schema never stores generated ADF JSON as the canonical copy, only a cached
`content`/`repository_path` for display and diffing.

Conventions:
- **Every column carries an inline `--` comment** saying what it holds, its allowed values, and where
  it is written from. Keep it that way when you add a column.
- Primary keys are app-generated opaque ids (`VARCHAR(64)`), not `SERIAL` — assigned in code
  (`backend/app/models.py`), not by the database. Registry tables use a natural composite key
  `(project_id, object_key)` instead.
- `created_at` / `updated_at` come from `TimestampMixin` (`backend/app/db/base.py`); `created_by` /
  `updated_by` store an opaque `users.id`, resolved to a display name at read time
  (`services._actor_name`). A few older columns (`project_settings.updated_by`,
  `environments.updated_by`) store the display name directly — noted inline.
- `*_versions` tables are **append-only** — regeneration inserts a new row, never an update.
- JSON columns are declared `JSON` in SQLAlchemy and shown as `JSONB` here (the Postgres target type);
  `git_catalog_entries` / `git_catalog_changes` are shown as `JSON` because migration `0020` creates them so.
- Secrets are **never** stored — only Key Vault names and secret *names* (rule #6, §25.5).
- Tables and columns tagged **(planned)** are target design (§23.5, §24.17, §25.9, §26.2) and are
  **not** in any migration yet; they are kept here so the eventual migration has one place to start from.
- The live models don't declare a Postgres schema (they land in `public`); `app.` is the target
  namespace — swap it for `public.` to run against the current codebase.

Migrations: [backend/alembic/versions/](../backend/alembic/versions/) `0001_initial_metastore` →
`0021_user_username`. The migration that introduced each table is noted above it.

```sql
CREATE SCHEMA IF NOT EXISTS app;

-- ---------------------------------------------------------------------------
-- Identity
-- ---------------------------------------------------------------------------

-- Organizational unit that owns projects and scopes RBAC (§11, §27.4). Migration 0001.
CREATE TABLE app.teams (
    id          VARCHAR(64)  PRIMARY KEY,                 -- opaque team id, assigned by the API
    name        VARCHAR(200) NOT NULL,                    -- display name shown in the team switcher and admin screens
    description TEXT         NOT NULL DEFAULT '',         -- free-text purpose of the team
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),      -- row creation time (UTC)
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()       -- last modification time (UTC)
);

-- One row per person. Team/role assignment is team_id + role (one home team per user) — there is no
-- membership table; the dead team_members table was dropped in migration 0008. Migration 0001 (+0008, +0021 username).
CREATE TABLE app.users (
    id                     VARCHAR(64)  PRIMARY KEY,                    -- opaque user id; what every created_by/updated_by/actor column stores
    name                   VARCHAR(200) NOT NULL,                       -- display name; the only identity shown in the header (product rule #3)
    email                  VARCHAR(320) NOT NULL UNIQUE,                -- contact address; also accepted at the AUTH_MODE=password login; unique across the platform
    username               VARCHAR(64)  UNIQUE,                         -- login name for AUTH_MODE=password: lowercase, 2-64 chars [a-z0-9._-], never contains '@' (unique index ux_users_username, migration 0021); set by the admin or derived from the email; NULL only for a future SSO-only account
    role                   VARCHAR(32)  NOT NULL DEFAULT 'DEVELOPER',   -- PLATFORM_ADMIN | TEAM_ADMIN | DEVELOPER | VIEWER (§11, app/rbac.py)
    team_id                VARCHAR(64)  REFERENCES app.teams(id),       -- home team for scope checks; NULL only for platform-wide admins
    active                 BOOLEAN      NOT NULL DEFAULT TRUE,          -- FALSE = deactivated; login and API access refused
    last_seen              TIMESTAMPTZ,                                 -- last authenticated request (UTC); NULL = never signed in
    password_hash          VARCHAR(255),                                -- scrypt hash (app/security.py), never plaintext; NULL outside AUTH_MODE=password
    password_updated_at    TIMESTAMPTZ,                                 -- when the password was last set or reset
    must_change_password   BOOLEAN      NOT NULL DEFAULT FALSE,         -- forced on account creation and admin reset; cleared by POST /auth/change-password
    failed_login_attempts  INTEGER      NOT NULL DEFAULT 0,             -- consecutive failures; incremented atomically in SQL, reset on success
    locked_until           TIMESTAMPTZ,                                 -- account locked until this time after auth_max_failed_logins failures
    invited_by             VARCHAR(64)  REFERENCES app.users(id),       -- admin who created the account via POST /admin/users; NULL for the CLI-bootstrapped admin
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),         -- row creation time (UTC)
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now()          -- last modification time (UTC)
);

-- Server-side, revocable session for AUTH_MODE=password (§27.3). Referenced by an opaque
-- httpOnly+Secure+SameSite=Lax cookie, never a stateless JWT. Migration 0008.
CREATE TABLE app.sessions (
    id               VARCHAR(64)  PRIMARY KEY,                    -- opaque session id carried in the session cookie; never exposed by the admin API
    user_id          VARCHAR(64)  NOT NULL REFERENCES app.users(id), -- signed-in user
    issued_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),         -- sign-in time (UTC)
    expires_at       TIMESTAMPTZ  NOT NULL,                       -- absolute timeout; the session ends here regardless of activity
    idle_expires_at  TIMESTAMPTZ  NOT NULL,                       -- idle timeout; slides forward on each authenticated request
    revoked_at       TIMESTAMPTZ,                                 -- set by logout or admin revoke (DELETE /admin/sessions); NULL = still valid
    ip               VARCHAR(64),                                 -- client IP at sign-in, for the Active sessions panel
    user_agent       VARCHAR(512),                                -- client user agent at sign-in, for the Active sessions panel
    csrf_token       VARCHAR(64)  NOT NULL                        -- double-submit CSRF token; echoed back as X-CSRF-Token on writes
);

-- Identity-provider configuration for future OIDC/SAML SSO (§27.5 ID2). Admin-editable metadata only;
-- no login path reads it yet (AUTH_MODE oidc/saml still return 501). Migration 0018.
CREATE TABLE app.sso_configurations (
    id                      VARCHAR(64)  PRIMARY KEY,             -- opaque configuration id
    name                    VARCHAR(128) NOT NULL,                -- admin label, unique (e.g. "Entra ID - corp")
    protocol                VARCHAR(16)  NOT NULL,                -- oidc | saml
    issuer_url              VARCHAR(512),                         -- OIDC issuer / discovery base URL; NULL for SAML
    client_id               VARCHAR(256),                         -- OIDC client (application) id; not a secret
    client_secret_key_vault VARCHAR(128),                         -- Key Vault NAME holding the OIDC client secret (reference only)
    client_secret_name      VARCHAR(128),                         -- secret NAME inside that Key Vault; the value is never stored
    metadata_url            VARCHAR(512),                         -- SAML federation metadata URL; NULL for OIDC
    entity_id               VARCHAR(512),                         -- SAML service-provider entity id; NULL for OIDC
    group_claim             VARCHAR(128) NOT NULL,                -- token claim holding group ids; API default 'groups'
    jit_provisioning        BOOLEAN      NOT NULL,                -- create users on first SSO sign-in; API default TRUE
    enabled                 BOOLEAN      NOT NULL,                -- recorded intent only; AUTH_MODE still decides sign-in. API default FALSE
    created_by              VARCHAR(64),                          -- users.id of the admin who created it
    updated_by              VARCHAR(64),                          -- users.id of the admin who last edited it
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),  -- row creation time (UTC)
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),  -- last modification time (UTC)
    CONSTRAINT uq_sso_configurations_name UNIQUE (name)
);

-- IdP group -> (role, team) mapping used for JIT provisioning; first match by position wins. Migration 0018.
CREATE TABLE app.sso_role_mappings (
    id               VARCHAR(64)  PRIMARY KEY,                                          -- opaque mapping id
    configuration_id VARCHAR(64)  NOT NULL REFERENCES app.sso_configurations(id) ON DELETE CASCADE, -- owning SSO configuration
    position         INTEGER      NOT NULL,                                             -- evaluation order, ascending; first matching group wins
    group_value      VARCHAR(256) NOT NULL,                                             -- group id/name as it appears in group_claim
    role             VARCHAR(32)  NOT NULL,                                             -- PLATFORM_ADMIN | TEAM_ADMIN | DEVELOPER | VIEWER granted on match
    team_id          VARCHAR(64)  REFERENCES app.teams(id)                              -- team assigned on match; required unless role = PLATFORM_ADMIN
);

-- ---------------------------------------------------------------------------
-- Projects
-- ---------------------------------------------------------------------------

-- A team's unit of work: settings, pipelines, registries and Git target all hang off a project. Migration 0001.
CREATE TABLE app.projects (
    id          VARCHAR(64)  PRIMARY KEY,                         -- opaque project id
    team_id     VARCHAR(64)  NOT NULL REFERENCES app.teams(id),   -- owning team; drives RBAC scope
    name        VARCHAR(200) NOT NULL,                            -- display name
    description TEXT         NOT NULL DEFAULT '',                 -- free-text purpose
    created_by  VARCHAR(64),                                      -- users.id of the creator
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),              -- row creation time (UTC)
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()               -- last modification time (UTC)
);

-- Full Project Settings aggregate (§5) as one JSON document per project. Migration 0001.
CREATE TABLE app.project_settings (
    project_id VARCHAR(64) PRIMARY KEY REFERENCES app.projects(id), -- one settings row per project
    document   JSONB       NOT NULL DEFAULT '{}',                 -- ProjectSettings JSON: ADF/source/target defaults, naming convention, layer prefixes, audit columns, gitDefaults, tags, allowedConnectors, controlTable (frontend/types/project-settings.ts)
    updated_by VARCHAR(200),                                      -- display NAME (not id) of the last editor
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),                -- row creation time (UTC)
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()                 -- last save time (UTC)
);

-- Per-project external-service configuration: layer-6 live ARM validation and Purview lineage push.
-- Identifiers + Key Vault secret references only. Migration 0019.
CREATE TABLE app.project_integrations (
    project_id VARCHAR(64) NOT NULL REFERENCES app.projects(id), -- owning project
    kind       VARCHAR(32) NOT NULL,                             -- live_validation (§29.1 layer 6) | purview (lineage push)
    enabled    BOOLEAN     NOT NULL DEFAULT false,               -- integration switched on for this project
    config     JSONB       NOT NULL DEFAULT '{}',                -- tenant/subscription/resource-group/account ids + clientSecretRef (Key Vault secret NAME, never a value)
    updated_by VARCHAR(64),                                      -- users.id of the last editor
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),               -- row creation time (UTC)
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),               -- last modification time (UTC)
    PRIMARY KEY (project_id, kind)
);

-- ADF instances (DEV/QA/PROD or any named factory): identifiers and references only, never a deploy
-- plane — no deployment status, no publish action (§23.7). Migration 0003 (+0004).
CREATE TABLE app.environments (
    id              VARCHAR(64)  PRIMARY KEY,                     -- opaque environment id
    name            VARCHAR(32)  NOT NULL UNIQUE,                 -- instance key picked in Build (e.g. DEV, QA, PROD, EU-PROD); stored on pipeline_versions.target_environment_name
    label           VARCHAR(64)  NOT NULL,                        -- friendly label shown in Administration
    status          VARCHAR(32)  NOT NULL DEFAULT 'not_configured', -- not_configured | configured (informational only)
    description     TEXT         NOT NULL DEFAULT '',             -- free-text notes
    storage_account VARCHAR(256) NOT NULL DEFAULT '',             -- storage account name for this instance (fills globalParameters.json)
    sql_server      VARCHAR(256) NOT NULL DEFAULT '',             -- SQL server host name for this instance
    database        VARCHAR(256) NOT NULL DEFAULT '',             -- database name for this instance
    key_vault       VARCHAR(256) NOT NULL DEFAULT '',             -- Key Vault NAME pipelines resolve secrets from at run time
    factory_name    VARCHAR(256) NOT NULL DEFAULT '',             -- ADF factory name; checked against FACTORY_NAME_RE by the validator
    subscription_id VARCHAR(64)  NOT NULL DEFAULT '',             -- Azure subscription id (identifier only; the app never calls ARM with it)
    resource_group  VARCHAR(256) NOT NULL DEFAULT '',             -- Azure resource group of the factory
    updated_by      VARCHAR(200),                                 -- display NAME (not id) of the last editor
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),          -- row creation time (UTC)
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()           -- last modification time (UTC)
);

-- ---------------------------------------------------------------------------
-- Templates + pipelines
-- ---------------------------------------------------------------------------

-- Seeded pipeline template metadata (§6). The 5 template ids survive as internal generation-strategy
-- keys (§24.12). Migration 0001.
CREATE TABLE app.pipeline_templates (
    id          VARCHAR(64)  PRIMARY KEY,                         -- template id: full_load | incremental_load | initial_load | composite_watermark_load | metadata_driven_load
    name        VARCHAR(200) NOT NULL,                            -- display name
    version     VARCHAR(32)  NOT NULL,                            -- template version; copied to pipeline_versions.template_version on generate
    description TEXT         NOT NULL DEFAULT '',                 -- what the template generates
    active      BOOLEAN      NOT NULL DEFAULT TRUE,               -- FALSE hides it from Build
    document    JSONB        NOT NULL DEFAULT '{}',               -- TemplateMetadata JSON (parameters, defaults, form schema)
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),              -- row creation time (UTC)
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()               -- last modification time (UTC)
);

-- One logical pipeline (Build form result). Holds the current draft + last built spec; each generate
-- creates a pipeline_versions row. Migration 0001 (+0006, +0009 indexes).
CREATE TABLE app.pipeline_definitions (
    id                      VARCHAR(64)  PRIMARY KEY,                        -- opaque pipeline id
    name                    VARCHAR(128) NOT NULL,                           -- logical pipeline name; default artifact names derive from it (PL_{name}_master/child)
    template_id             VARCHAR(64)  NOT NULL REFERENCES app.pipeline_templates(id), -- generation strategy key
    project_id              VARCHAR(64)  NOT NULL REFERENCES app.projects(id), -- owning project
    team_id                 VARCHAR(64)  NOT NULL REFERENCES app.teams(id),  -- owning team, denormalized from the project for scope filtering
    state                   VARCHAR(32)  NOT NULL DEFAULT 'DRAFT',           -- DRAFT | GENERATED | VALIDATED | READY_TO_PUSH | PUSHING | PUSHED | PR_CREATED | GENERATION_FAILED | VALIDATION_FAILED | GIT_PUSH_FAILED | PR_CREATION_FAILED
    spec_version            VARCHAR(32)  NOT NULL DEFAULT '1.0',             -- LogicalPipelineSpec schema version of the stored spec
    latest_artifact_version VARCHAR(32),                                     -- newest pipeline_versions.version; NULL until first generate
    draft                   JSONB        NOT NULL DEFAULT '{}',              -- SpecDraft (Build form state, camelCase); returned to the wizard for edit mode
    spec                    JSONB        NOT NULL DEFAULT '{}',              -- last built LogicalPipelineSpec (§23.3)
    created_by              VARCHAR(64),                                     -- users.id of the creator
    updated_by              VARCHAR(64),                                     -- users.id of the last editor of name/template/draft (migration 0006)
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),             -- row creation time (UTC)
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT now()              -- last edit time (UTC); also the optimistic-concurrency token (expectedUpdatedAt -> 409)
);

-- Immutable generation record: regeneration inserts a new row (§23.5). Migration 0001 (+0007).
CREATE TABLE app.pipeline_versions (
    id                      VARCHAR(64) PRIMARY KEY,                         -- opaque version id
    pipeline_id             VARCHAR(64) NOT NULL REFERENCES app.pipeline_definitions(id), -- parent pipeline
    version                 VARCHAR(32) NOT NULL,                            -- artifact version label (e.g. 1.0.3), unique per pipeline
    template_version        VARCHAR(32) NOT NULL,                            -- pipeline_templates.version used for this generation
    spec                    JSONB       NOT NULL,                            -- LogicalPipelineSpec snapshot this version was generated from
    manifest                JSONB,                                           -- manifest.json: [{name, type, layer}, ...] in §23.9 deployment order
    created_by              VARCHAR(64),                                     -- users.id who triggered the generate
    target_environment_name VARCHAR(32),                                     -- environments.name this version was generated for (migration 0007); not an FK so history survives a rename/delete
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),              -- generation time (UTC)
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),              -- TimestampMixin; never changes after insert (rows are immutable)
    CONSTRAINT uq_pipeline_versions_pipeline_version UNIQUE (pipeline_id, version)
);

-- ---------------------------------------------------------------------------
-- Artifacts
-- ---------------------------------------------------------------------------

-- One generated file per (pipeline, version): pipeline/dataset/linked service/trigger JSON, SQL,
-- manifest. Cached copy for review and diff; Git holds the canonical file. Migration 0001 (+0009 index).
CREATE TABLE app.artifacts (
    id              VARCHAR(64)  PRIMARY KEY,                         -- opaque artifact id; git_push_requests.artifact_ids lists these
    pipeline_id     VARCHAR(64)  NOT NULL REFERENCES app.pipeline_definitions(id), -- generating pipeline
    pipeline_name   VARCHAR(128) NOT NULL,                            -- pipeline name at generation time (Catalog filter/search)
    file_name       VARCHAR(256) NOT NULL,                            -- file name, e.g. PL_orders_master.json, adf_control_table.sql
    file_type       VARCHAR(16)  NOT NULL DEFAULT 'json',             -- json | sql | config
    artifact_type   VARCHAR(32)  NOT NULL,                            -- pipeline | dataset | linkedService | credential | trigger | factory | parameter (SQL, parameters files, manifest.json, lineage.json)
    layer           VARCHAR(32)  NOT NULL,                            -- §23.9 order: infrastructure | connection | data | pipeline | runtime | metadata
    version         VARCHAR(32)  NOT NULL,                            -- pipeline_versions.version this file belongs to
    state           VARCHAR(16)  NOT NULL DEFAULT 'generated',        -- generated | validated | committed
    bytes           INTEGER      NOT NULL DEFAULT 0,                  -- content size in bytes
    content         TEXT         NOT NULL DEFAULT '',                 -- full file text as generated
    repository_path VARCHAR(512),                                     -- path in the Git repo once pushed; NULL before push
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),              -- generation time (UTC)
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),              -- last state change (UTC)
    CONSTRAINT uq_artifacts_pipeline_file_version UNIQUE (pipeline_id, file_name, version)
);

-- ---------------------------------------------------------------------------
-- Connections and identities
-- ---------------------------------------------------------------------------

-- Saved, reusable linked service + its object list + dataset customization (§24.6/§24.17), shared with
-- the whole project (Delivery > Connections). Secrets appear only as Key Vault secret names. Migration 0005.
CREATE TABLE app.connection_library (
    id              VARCHAR(64)  PRIMARY KEY,                         -- opaque connection id
    project_id      VARCHAR(64)  NOT NULL REFERENCES app.projects(id), -- owning project
    team_id         VARCHAR(64)  NOT NULL REFERENCES app.teams(id),   -- owning team, denormalized for scope filtering
    name            VARCHAR(200) NOT NULL,                            -- human label; also the ADF linked service name
    role            VARCHAR(16)  NOT NULL DEFAULT 'source',           -- source | target | both
    connector_type  VARCHAR(64)  NOT NULL,                            -- connector registry key (sql_server, azure_sql_database, adls_gen2, snowflake, sftp, ...)
    description     TEXT         NOT NULL DEFAULT '',                 -- free-text notes
    draft           JSONB        NOT NULL,                            -- LinkedServiceDraft (connector, auth type, IR, Key Vault ref, credential) + source objects + dataset shape
    created_by      VARCHAR(64),                                      -- users.id of the creator
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),              -- row creation time (UTC)
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()               -- last modification time (UTC)
);

-- Admin-curated pick-list of user-assigned managed identities (§32.5/§32.6). The ARM resource id is
-- infrastructure provisioned out-of-band, not a secret. Migration 0013.
CREATE TABLE app.managed_identities (
    id           VARCHAR(64)  PRIMARY KEY,                            -- opaque identity id
    team_id      VARCHAR(64)  NOT NULL REFERENCES app.teams(id),      -- owning team; the picker only lists the caller's team
    name         VARCHAR(200) NOT NULL,                               -- display name; becomes the generated credential artifact name; unique per team
    resource_id  VARCHAR(512) NOT NULL,                               -- ARM resource id of the UAMI (/subscriptions/.../userAssignedIdentities/...)
    client_id    VARCHAR(64),                                         -- UAMI client id, optional, for diagnostics only
    created_by   VARCHAR(64),                                         -- users.id of the registering admin
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),                 -- row creation time (UTC)
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),                 -- last modification time (UTC)
    CONSTRAINT uq_managed_identities_team_name UNIQUE (team_id, name)
);

-- ---------------------------------------------------------------------------
-- Source Onboarding — Bronze object registry
-- ---------------------------------------------------------------------------

-- One onboarded source object per (project, object_key), from CSV/Excel upload, manual grid, or Git
-- import. Feeds Build's object picker and the seed rows of control.adf_control_table (§2). Only the
-- columns without "(planned)" exist today (migration 0011, +0015 data_classification, +0020 provenance);
-- the rest are the §26.2 target catalogue.
CREATE TABLE app.onboarding_objects (
    object_key              VARCHAR(256) NOT NULL,              -- stable registry key; default <source_system>.<source_object>
    project_id              VARCHAR(64)  NOT NULL REFERENCES app.projects(id), -- owning project
    version                 INTEGER      NOT NULL DEFAULT 1,    -- optimistic-concurrency counter, +1 per update
    enabled                 BOOLEAN      NOT NULL DEFAULT TRUE, -- FALSE = excluded from generated control-table seed rows
    task_id                 INTEGER,                            -- (planned) pinned control-table TaskId
    load_order              INTEGER,                            -- (planned) ordering within a batch group
    batch_group             VARCHAR(128),                       -- grouping label for batch/trigger scheduling
    domain                  VARCHAR(128),                       -- (planned) business domain
    priority                INTEGER,                            -- (planned) relative load priority
    owner                   VARCHAR(200),                       -- accountable data owner (free text)
    data_classification     VARCHAR(16),                        -- public | internal | confidential | restricted (migration 0015; feeds the security.pii scan)

    source_system           VARCHAR(128) NOT NULL,              -- source system label (e.g. sap_ecc, crm); used in names and object_key
    source_connection_ref   VARCHAR(64)  REFERENCES app.connection_library(id), -- (planned) saved source connection
    source_type             VARCHAR(16)  NOT NULL DEFAULT 'table', -- table | view | query | file
    source_schema           VARCHAR(128),                       -- source schema/namespace; NULL for schema-less sources
    source_object           VARCHAR(256) NOT NULL,              -- table/view/file name at the source
    source_query            TEXT,                               -- custom SELECT when source_type = query
    source_filter           TEXT,                               -- (planned) WHERE clause appended to the extract
    source_folder_path      VARCHAR(512),                       -- (planned) folder for file sources
    source_file_pattern     VARCHAR(256),                       -- (planned) wildcard for file sources
    source_file_format      VARCHAR(32),                        -- (planned) DelimitedText | Json | Parquet | Avro | Orc | Xml | Excel | Binary
    source_delimiter        VARCHAR(8),                         -- (planned) column delimiter for delimited files
    source_quote_char       VARCHAR(8),                         -- (planned) quote character
    source_escape_char      VARCHAR(8),                         -- (planned) escape character
    source_first_row_as_header BOOLEAN,                         -- (planned) header row present
    source_encoding         VARCHAR(32),                        -- (planned) file encoding
    source_compression      VARCHAR(16),                        -- (planned) gzip | snappy | bzip2 | deflate | ZipDeflate | none
    source_null_value       VARCHAR(32),                        -- (planned) literal treated as NULL
    source_skip_line_count  INTEGER,                            -- (planned) leading lines to skip

    target_connection_ref   VARCHAR(64)  REFERENCES app.connection_library(id), -- (planned) saved target connection
    target_type             VARCHAR(16)  NOT NULL DEFAULT 'table', -- table | file
    target_schema           VARCHAR(128),                       -- target schema when target_type = table
    target_object           VARCHAR(256),                       -- target table when target_type = table
    target_path             VARCHAR(512),                       -- target folder/container path when target_type = file
    target_file_name        VARCHAR(256),                       -- target file name when target_type = file
    target_file_name_prefix VARCHAR(128),                       -- (planned) prefix for generated file names
    target_file_format      VARCHAR(32)  NOT NULL DEFAULT 'Parquet', -- Parquet | DelimitedText | Json | Avro | Orc | ...
    target_compression_codec VARCHAR(16),                       -- (planned) output compression
    target_delimiter        VARCHAR(8),                         -- (planned) output delimiter
    target_quote_char       VARCHAR(8),                         -- (planned) output quote character
    target_first_row_as_header BOOLEAN,                         -- (planned) write a header row
    target_encoding         VARCHAR(32),                        -- (planned) output encoding
    write_behavior          VARCHAR(16)  DEFAULT 'append',      -- append | overwrite | upsert | merge
    upsert_keys             VARCHAR(512),                       -- pipe-separated key columns for upsert/merge, e.g. "id|updated_at"
    business_keys           JSONB        NOT NULL DEFAULT '[]', -- (planned) natural key columns
    partition_by            JSONB        NOT NULL DEFAULT '[]', -- (planned) output partition columns
    max_rows_per_file       INTEGER,                            -- (planned) file split size

    loading_behavior        VARCHAR(16)  NOT NULL DEFAULT 'FullLoad', -- FullLoad | DeltaLoad
    watermark_column        VARCHAR(128),                       -- incremental column when loading_behavior = DeltaLoad
    watermark_column_type   VARCHAR(16),                        -- datetime | int | bigint | string
    watermark_start_value   VARCHAR(256),                       -- (planned) initial watermark
    watermark_is_inclusive_upper BOOLEAN,                       -- (planned) upper bound inclusive
    hard_delete_handling    VARCHAR(32)  DEFAULT 'ignore',      -- (planned) ignore | soft-delete-flag | full-refresh (today set per pipeline via SpecDraft.deleteHandling)
    truncate_before_load    BOOLEAN      NOT NULL DEFAULT FALSE, -- (planned) truncate target first

    columns                 JSONB        NOT NULL DEFAULT '[]', -- (planned) [{name, type, nullable, pii, description}]
    schema_file_ref         VARCHAR(512),                       -- (planned) path to a schema file
    primary_key             JSONB        NOT NULL DEFAULT '[]', -- (planned) primary key columns
    expected_row_count      BIGINT,                             -- (planned) expected volume
    sla_minutes             INTEGER,                            -- (planned) freshness SLA
    notes                   TEXT         NOT NULL DEFAULT '',   -- free-text notes

    provenance              VARCHAR(16),                        -- where the row last came from: manual | upload | git-import (migration 0020); NULL = before tracking
    import_id               VARCHAR(64),                        -- (planned) onboarding_imports.id of the upload that last wrote the row
    last_synced_commit      VARCHAR(64),                        -- (planned) commit sha of the last sync-to-Git (today tracked on onboarding_syncs)
    status                  VARCHAR(16)  NOT NULL DEFAULT 'draft', -- draft | valid | invalid (validation result of the row)

    created_by              VARCHAR(64),                        -- users.id of the creator
    updated_by              VARCHAR(64),                        -- users.id of the last editor
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(), -- row creation time (UTC)
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT now(), -- last modification time (UTC)
    PRIMARY KEY (project_id, object_key)
);

-- Upload template <-> DDL alignment: the onboarding CSV/Excel template's headers
-- (frontend/lib/adf/onboarding.ts ONBOARDING_COLUMNS) are, by construction, a subset of the
-- column names above; onboarding.test.ts's "DDL alignment guardrail" test enforces it.
--
-- Registry column                  -> control.adf_control_table column (§2)
-- enabled                          -> Enabled
-- source_type                      -> SourceType
-- source_schema + source_object    -> Source
-- source_query                     -> SourceQuery
-- target_type                      -> TargetType
-- target_schema + target_object, or target_path -> Target
-- target_file_name                 -> TargetFileName
-- loading_behavior                 -> LoadingBehavior
-- watermark_column                 -> WatermarkColumn
-- watermark_column_type            -> WatermarkType
-- (object_key, batch_group, owner, source_system, write_behavior, upsert_keys, notes stay app-side.)

-- Data-quality rule authored on a Bronze onboarding object (§30.4/§30.5). Build's onboarding picker
-- copies not_null/unique/range/row_count/freshness rules into SpecDraft.dataQualityRules, which the
-- generator emits as pre-copy DQ_* activities. Silver/Gold rules live inline on silver/mart_objects.
-- Migration 0014.
CREATE TABLE app.dq_rules (
    id            VARCHAR(64)  PRIMARY KEY,                           -- opaque rule id
    project_id    VARCHAR(64)  NOT NULL,                              -- with object_key: the onboarding_objects row this rule checks
    object_key    VARCHAR(256) NOT NULL,                              -- onboarding_objects.object_key
    check_type    VARCHAR(64)  NOT NULL,                              -- not_null | unique | range | row_count | freshness
    config        JSONB        NOT NULL DEFAULT '{}',                 -- check parameters, e.g. {"column": "id"}, {"min": 0, "max": 100}, {"hours": 24}
    enforcement   VARCHAR(16)  NOT NULL DEFAULT 'warn',               -- off | warn | block (block emits an IfCondition -> Fail gate)
    created_by    VARCHAR(64),                                        -- users.id of the author
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),                -- row creation time (UTC)
    FOREIGN KEY (project_id, object_key) REFERENCES app.onboarding_objects(project_id, object_key)
);

-- One row per committed Bronze bulk upload (§26.8). The file is parsed in the browser; only the
-- parsed rows reach the server. Migration 0016.
CREATE TABLE app.onboarding_imports (
    id                 VARCHAR(64)  PRIMARY KEY,                      -- opaque import id
    project_id         VARCHAR(64)  NOT NULL REFERENCES app.projects(id), -- owning project
    file_name          VARCHAR(256) NOT NULL,                         -- uploaded file name (.csv / .json / .xlsx)
    file_hash          VARCHAR(128) NOT NULL,                         -- server-computed SHA-256 of the committed rows
    row_total          INTEGER      NOT NULL DEFAULT 0,               -- rows in the upload
    row_valid          INTEGER      NOT NULL DEFAULT 0,               -- rows that passed validation and were committed
    row_error          INTEGER      NOT NULL DEFAULT 0,               -- rows rejected by validation
    mode               VARCHAR(16)  NOT NULL DEFAULT 'upsert',        -- commit mode; upsert-by-object_key (additive, never a destructive replace)
    mapping_profile_id VARCHAR(64),                                   -- onboarding_mapping_profiles.id applied on the Map step; no FK so history survives a profile delete
    committed_by       VARCHAR(64),                                   -- users.id who committed
    committed_at       TIMESTAMPTZ,                                   -- commit time (UTC)
    report             JSONB        NOT NULL DEFAULT '{}',            -- per-row validation result shown in import history
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()            -- row creation time (UTC)
);

-- Named, reusable upload-header -> registry-column map for the §26.3 Map step. Migration 0017.
CREATE TABLE app.onboarding_mapping_profiles (
    id            VARCHAR(64)  PRIMARY KEY,                           -- opaque profile id
    project_id    VARCHAR(64)  NOT NULL REFERENCES app.projects(id),  -- owning project
    name          VARCHAR(128) NOT NULL,                              -- profile name, unique per project (upserted case-insensitively)
    header_map    JSONB        NOT NULL DEFAULT '{}',                 -- {"uploaded header": "onboarding_objects column"}
    updated_by    VARCHAR(64),                                        -- users.id of the last editor
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),                -- row creation time (UTC)
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),                -- last modification time (UTC)
    CONSTRAINT uq_onboarding_mapping_profiles_project_name UNIQUE (project_id, name)
);

-- One "Sync to Git" of a project's Bronze registry (§26.5): control-table SQL + metadata/objects.csv
-- (+ registry lineage) pushed to a feature branch, then a PR. Migration 0019.
CREATE TABLE app.onboarding_syncs (
    id             VARCHAR(64)  PRIMARY KEY,                          -- opaque sync id
    project_id     VARCHAR(64)  NOT NULL REFERENCES app.projects(id), -- owning project
    provider       VARCHAR(32)  NOT NULL,                             -- github | gitlab | azure_repos | custom
    repository_url VARCHAR(512) NOT NULL,                             -- target repository URL
    feature_branch VARCHAR(256) NOT NULL,                             -- branch pushed to; never the protected base branch
    base_branch    VARCHAR(256) NOT NULL,                             -- PR target, normally dev
    commit_message TEXT         NOT NULL DEFAULT '',                  -- commit message used
    file_paths     JSONB        NOT NULL DEFAULT '[]',                -- files written: <root>/metadata/{adf_control_table.sql, objects.csv, registry-lineage.json}
    snapshot       JSONB        NOT NULL DEFAULT '{}',                -- {object_key: row as pushed}; base for the next sync's per-row diff
    status         VARCHAR(32)  NOT NULL DEFAULT 'pending',           -- pending | pushed | pr_created | failed
    commit_sha     VARCHAR(64),                                       -- resulting commit sha
    pr_number      INTEGER,                                           -- provider PR/MR number
    pr_url         VARCHAR(512),                                      -- PR/MR web URL
    simulated      BOOLEAN      NOT NULL DEFAULT false,               -- TRUE = no Git credential resolved; offline SimulatedAdapter used
    error          TEXT,                                              -- failure message when status = failed
    created_by     VARCHAR(64),                                       -- users.id who started the sync
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),               -- row creation time (UTC)
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()                -- last status change (UTC)
);

-- ---------------------------------------------------------------------------
-- STTM Silver/Gold object registry — migration 0010
-- One physical table per layer. transformations / data_quality_rules hold the same shapes the Build
-- wizard produces (TransformationRule[], SpecDraft.dataQualityRules). target_table is a computed cache.
-- ---------------------------------------------------------------------------

-- One uploaded STTM workbook (Silver or Gold), multi-sheet. Created before the registries that FK to it.
CREATE TABLE app.sttm_imports (
    id             VARCHAR(64)  PRIMARY KEY,                          -- opaque import id
    project_id     VARCHAR(64)  NOT NULL REFERENCES app.projects(id), -- owning project
    layer          VARCHAR(16)  NOT NULL,                             -- silver | gold
    file_name      VARCHAR(256) NOT NULL,                             -- uploaded .xlsx name
    file_hash      VARCHAR(128) NOT NULL,                             -- SHA-256 of the uploaded file
    sheet_total    INTEGER      NOT NULL DEFAULT 0,                   -- object sheets found (max 200, else 422 workbook_too_large)
    sheet_valid    INTEGER      NOT NULL DEFAULT 0,                   -- sheets that parsed and validated
    sheet_error    INTEGER      NOT NULL DEFAULT 0,                   -- sheets with blocking errors
    mode           VARCHAR(16)  NOT NULL DEFAULT 'upsert',            -- upsert only (additive)
    committed_by   VARCHAR(64),                                       -- users.id who committed
    committed_at   TIMESTAMPTZ,                                       -- commit time; NULL = previewed, not committed
    report         JSONB        NOT NULL DEFAULT '{}',                -- per-sheet parse/validation result incl. existingVersion for concurrency checks
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()                -- upload time (UTC)
);

-- One curated (Silver) entity, from STTM upload or manual entry.
CREATE TABLE app.silver_objects (
    object_key               VARCHAR(256) NOT NULL,                   -- registry key; defaults to curated_entity
    project_id               VARCHAR(64)  NOT NULL REFERENCES app.projects(id), -- owning project
    version                  INTEGER      NOT NULL DEFAULT 1,         -- optimistic-concurrency counter (expectedVersion -> 409 version_conflict)
    enabled                  BOOLEAN      NOT NULL DEFAULT TRUE,      -- FALSE hides it from the Build picker
    tag                      VARCHAR(128) NOT NULL,                   -- mandatory classification tag; filter in Build/Onboarding
    curated_entity           VARCHAR(128) NOT NULL,                   -- entity name; unique per project; feeds target table name
    source_object_keys       JSONB        NOT NULL DEFAULT '[]',      -- onboarding_objects.object_key values this entity reads from
    owner                    VARCHAR(200),                            -- accountable owner (free text)
    batch_group              VARCHAR(128),                            -- scheduling group label
    domain                   VARCHAR(128),                            -- business domain (filter)
    write_behavior           VARCHAR(16)  NOT NULL DEFAULT 'append',  -- append | overwrite | upsert | merge
    loading_behavior         VARCHAR(16)  NOT NULL DEFAULT 'FullLoad', -- FullLoad | DeltaLoad
    watermark_column         VARCHAR(128),                            -- incremental column for DeltaLoad
    watermark_column_type    VARCHAR(16),                             -- datetime | int | bigint | string
    business_keys            JSONB        NOT NULL DEFAULT '[]',      -- natural key columns
    transformations          JSONB        NOT NULL DEFAULT '[]',      -- TransformationRule[] (max 10): conform_columns | dedupe | cast_schema | scd2 | join
    data_quality_rules       JSONB        NOT NULL DEFAULT '[]',      -- SpecDraft.dataQualityRules shape (max 5)
    target_table             VARCHAR(256) NOT NULL,                   -- computed <silver prefix>.<entity> (default curated.<entity>); recomputed on every write
    data_classification      VARCHAR(16),                             -- public | internal | confidential | restricted
    contains_pii             BOOLEAN,                                 -- PII_Flag (migration 0022; null = not stated)
    contains_phi             BOOLEAN,                                 -- PHI_Flag (migration 0022; null = not stated)
    contains_pci             BOOLEAN,                                 -- PCI_Flag (migration 0022; null = not stated)
    critical_data_element    BOOLEAN,                                 -- Critical_Data_Element (migration 0022)
    audit_required           BOOLEAN,                                 -- Audit_Required (migration 0022)
    retention_period         VARCHAR(64),                             -- Retention_Period, free text e.g. "7y" (migration 0022)
    regulatory_tag           VARCHAR(128),                            -- Regulatory_Tag e.g. GDPR (migration 0022)
    business_criticality     VARCHAR(32),                             -- Business_Criticality (migration 0022)
    notes                    TEXT         NOT NULL DEFAULT '',        -- free-text notes
    provenance_source        VARCHAR(16)  NOT NULL DEFAULT 'manual',  -- upload | manual
    import_id                VARCHAR(64)  REFERENCES app.sttm_imports(id), -- workbook that last wrote this row; NULL for manual
    status                   VARCHAR(16)  NOT NULL DEFAULT 'draft',   -- draft | valid | invalid
    created_by               VARCHAR(64),                             -- users.id of the creator
    updated_by               VARCHAR(64),                             -- users.id of the last editor
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),     -- row creation time (UTC)
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),     -- last modification time (UTC)
    PRIMARY KEY (project_id, object_key),
    CONSTRAINT uq_silver_objects_entity UNIQUE (project_id, curated_entity)
);

-- One business mart (Gold) entity; same shape as silver_objects plus mart_domain, and its sources are Silver keys.
CREATE TABLE app.mart_objects (
    object_key               VARCHAR(256) NOT NULL,                   -- registry key
    project_id               VARCHAR(64)  NOT NULL REFERENCES app.projects(id), -- owning project
    version                  INTEGER      NOT NULL DEFAULT 1,         -- optimistic-concurrency counter
    enabled                  BOOLEAN      NOT NULL DEFAULT TRUE,      -- FALSE hides it from the Build picker
    tag                      VARCHAR(128) NOT NULL,                   -- mandatory classification tag
    mart_domain              VARCHAR(128) NOT NULL,                   -- mart domain; part of the target table name
    curated_entity           VARCHAR(128) NOT NULL,                   -- mart entity name; unique per (project, mart_domain)
    source_object_keys       JSONB        NOT NULL DEFAULT '[]',      -- silver_objects.object_key values this mart reads from
    owner                    VARCHAR(200),                            -- accountable owner (free text)
    batch_group              VARCHAR(128),                            -- scheduling group label
    domain                   VARCHAR(128),                            -- business domain (filter)
    write_behavior           VARCHAR(16)  NOT NULL DEFAULT 'append',  -- append | overwrite | upsert | merge
    loading_behavior         VARCHAR(16)  NOT NULL DEFAULT 'FullLoad', -- FullLoad | DeltaLoad
    watermark_column         VARCHAR(128),                            -- incremental column for DeltaLoad
    watermark_column_type    VARCHAR(16),                             -- datetime | int | bigint | string
    business_keys            JSONB        NOT NULL DEFAULT '[]',      -- natural key columns
    transformations          JSONB        NOT NULL DEFAULT '[]',      -- TransformationRule[] (max 10): join | aggregate | select_columns | scd2
    data_quality_rules       JSONB        NOT NULL DEFAULT '[]',      -- SpecDraft.dataQualityRules shape (max 5)
    target_table             VARCHAR(256) NOT NULL,                   -- computed <gold prefix>.<domain>_<entity> (default mart.<domain>_<entity>)
    data_classification      VARCHAR(16),                             -- public | internal | confidential | restricted
    contains_pii             BOOLEAN,                                 -- PII_Flag (migration 0022; null = not stated)
    contains_phi             BOOLEAN,                                 -- PHI_Flag (migration 0022; null = not stated)
    contains_pci             BOOLEAN,                                 -- PCI_Flag (migration 0022; null = not stated)
    critical_data_element    BOOLEAN,                                 -- Critical_Data_Element (migration 0022)
    audit_required           BOOLEAN,                                 -- Audit_Required (migration 0022)
    retention_period         VARCHAR(64),                             -- Retention_Period, free text e.g. "7y" (migration 0022)
    regulatory_tag           VARCHAR(128),                            -- Regulatory_Tag e.g. GDPR (migration 0022)
    business_criticality     VARCHAR(32),                             -- Business_Criticality (migration 0022)
    notes                    TEXT         NOT NULL DEFAULT '',        -- free-text notes
    provenance_source        VARCHAR(16)  NOT NULL DEFAULT 'manual',  -- upload | manual
    import_id                VARCHAR(64)  REFERENCES app.sttm_imports(id), -- workbook that last wrote this row; NULL for manual
    status                   VARCHAR(16)  NOT NULL DEFAULT 'draft',   -- draft | valid | invalid
    created_by               VARCHAR(64),                             -- users.id of the creator
    updated_by               VARCHAR(64),                             -- users.id of the last editor
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),     -- row creation time (UTC)
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),     -- last modification time (UTC)
    PRIMARY KEY (project_id, object_key),
    CONSTRAINT uq_mart_objects_entity UNIQUE (project_id, mart_domain, curated_entity)
);

-- ---------------------------------------------------------------------------
-- Git
-- ---------------------------------------------------------------------------

-- One push of selected generated artifacts to a feature branch (§8, §9). Migration 0001 (+0002).
CREATE TABLE app.git_push_requests (
    id              VARCHAR(64)  PRIMARY KEY,                         -- opaque push id
    pipeline_id     VARCHAR(64)  NOT NULL REFERENCES app.pipeline_definitions(id), -- pipeline whose artifacts were pushed
    provider        VARCHAR(32)  NOT NULL,                            -- github | gitlab | azure_repos | custom
    repository_url  VARCHAR(512) NOT NULL,                            -- target repository URL
    feature_branch  VARCHAR(256) NOT NULL,                            -- branch pushed to; rejected if equal to base_branch
    base_branch     VARCHAR(256) NOT NULL DEFAULT 'dev',              -- PR target; never pushed to directly
    artifact_ids    JSONB        NOT NULL DEFAULT '[]',               -- artifacts.id values included in the commit
    commit_message  TEXT         NOT NULL DEFAULT '',                 -- commit message used
    commit_sha      VARCHAR(64),                                      -- resulting commit sha; NULL until pushed
    status          VARCHAR(32)  NOT NULL DEFAULT 'pending',          -- pending | pushed | pr_created | failed
    error           TEXT,                                             -- failure message when status = failed
    created_by      VARCHAR(64),                                      -- users.id who requested the push
    simulated       BOOLEAN      NOT NULL DEFAULT FALSE,              -- TRUE = no Git credential resolved; offline SimulatedAdapter used (shown as a badge)
    mode            VARCHAR(16)  NOT NULL DEFAULT 'create',           -- (planned) create | update | rename | delete (catalog edits use git_catalog_changes today)
    old_path        VARCHAR(512),                                     -- (planned) previous path when mode = rename
    artifact_paths  JSONB        NOT NULL DEFAULT '[]',               -- (planned) resolved Git paths for artifact_ids, in push order
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),              -- request time (UTC)
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()               -- last status change (UTC)
);

-- The PR opened from a push's feature branch into the base branch. Migration 0001 (+0002).
CREATE TABLE app.git_pull_requests (
    id              VARCHAR(64)  PRIMARY KEY,                         -- opaque PR record id
    push_request_id VARCHAR(64)  NOT NULL REFERENCES app.git_push_requests(id), -- push this PR was opened from
    number          INTEGER,                                          -- provider PR/MR number
    url             VARCHAR(512),                                     -- PR/MR web URL
    title           TEXT         NOT NULL DEFAULT '',                 -- PR title
    source_branch   VARCHAR(256) NOT NULL,                            -- feature branch
    target_branch   VARCHAR(256) NOT NULL,                            -- base branch (dev)
    simulated       BOOLEAN      NOT NULL DEFAULT FALSE,              -- TRUE = PR step used the offline SimulatedAdapter
    status          VARCHAR(32)  NOT NULL DEFAULT 'open',             -- open (set at creation; merge/close is not tracked back from the provider)
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),              -- row creation time (UTC)
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()               -- last status change (UTC)
);

-- §24.8 Git catalog index: one row per indexed repo file per branch, rewritten wholesale by the worker's
-- catalog_refresh job. Tool-owned vs foreign is resolved at read time, not stored. Migration 0020.
CREATE TABLE app.git_catalog_entries (
    project_id    VARCHAR(64)   NOT NULL REFERENCES app.projects(id), -- project whose Git target was indexed
    branch        VARCHAR(256)  NOT NULL,                             -- branch indexed
    path          VARCHAR(1024) NOT NULL,                             -- repo path, e.g. adf/pipeline/PL_orders_master.json
    artifact_type VARCHAR(32)   NOT NULL,                             -- ADF folder type (pipeline, dataset, linkedService, trigger, ...) or metadata
    name          VARCHAR(260)  NOT NULL,                             -- JSON "name" property, else the file stem
    blob_sha      VARCHAR(64)   NOT NULL DEFAULT '',                  -- Git blob id; optimistic-concurrency base for edits (§24.14)
    content       TEXT          NOT NULL DEFAULT '',                  -- file text (<= 1 MB) for detail view, Open in Build, drift, Git import
    "references"  JSON          NOT NULL DEFAULT '[]',                -- [{type, name}] references this file makes (LinkedServiceReference, DatasetReference, ...)
    parameters    JSON          NOT NULL DEFAULT '[]',                -- names of properties.parameters
    simulated     BOOLEAN       NOT NULL DEFAULT false,               -- TRUE = virtual repo built from simulated pushes (no credential)
    indexed_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),               -- when this file was indexed (UTC)
    PRIMARY KEY (project_id, branch, path)
);

-- §24.9/§24.10 change to files already in Git (raw update, rename, delete), pushed to a feature branch
-- and PR'd. Separate from git_push_requests, which is keyed to one generated pipeline. Migration 0020.
CREATE TABLE app.git_catalog_changes (
    id             VARCHAR(64)   PRIMARY KEY,                         -- opaque change id
    project_id     VARCHAR(64)   NOT NULL REFERENCES app.projects(id), -- owning project
    mode           VARCHAR(16)   NOT NULL,                            -- update | rename | delete
    path           VARCHAR(1024) NOT NULL,                            -- file being changed
    new_path       VARCHAR(1024),                                     -- destination path; rename only
    provider       VARCHAR(32)   NOT NULL,                            -- github | gitlab | azure_repos | custom
    repository_url VARCHAR(512)  NOT NULL,                            -- target repository URL
    feature_branch VARCHAR(256)  NOT NULL,                            -- branch the change is committed to
    base_branch    VARCHAR(256)  NOT NULL,                            -- PR target
    commit_message TEXT          NOT NULL DEFAULT '',                 -- commit message used
    files          JSON          NOT NULL DEFAULT '[]',               -- [{path, content}] the commit writes, incl. rewritten referrers
    delete_paths   JSON          NOT NULL DEFAULT '[]',               -- paths the commit removes
    base_blob_sha  VARCHAR(64),                                       -- blob sha as loaded; mismatch at push time -> status conflict
    status         VARCHAR(32)   NOT NULL DEFAULT 'pending',          -- pending | pushed | pr_created | failed | conflict
    commit_sha     VARCHAR(64),                                       -- resulting commit sha
    pr_number      INTEGER,                                           -- provider PR/MR number
    pr_url         VARCHAR(512),                                      -- PR/MR web URL
    simulated      BOOLEAN       NOT NULL DEFAULT false,              -- TRUE = offline SimulatedAdapter used
    error          TEXT,                                              -- failure message
    created_by     VARCHAR(64),                                       -- users.id who requested the change
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),              -- request time (UTC)
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now()               -- last status change (UTC)
);

-- ---------------------------------------------------------------------------
-- Jobs (worker hand-off)
-- ---------------------------------------------------------------------------

-- Unit of work for the stateless worker, which claims queued rows with FOR UPDATE SKIP LOCKED
-- (worker/app/jobs/loop.py). The payload carries inputs, never mutable UI state. Migration 0001.
CREATE TABLE app.jobs (
    id           VARCHAR(64)  PRIMARY KEY,                            -- opaque job id
    type         VARCHAR(32)  NOT NULL,                               -- generate | validate | git_push | onboarding_sync | live_check | purview_push | catalog_refresh | catalog_change
    status       VARCHAR(16)  NOT NULL DEFAULT 'queued',              -- queued | running | succeeded | failed
    pipeline_id  VARCHAR(64)  REFERENCES app.pipeline_definitions(id), -- related pipeline; NULL for project-level jobs (sync, catalog, purview)
    payload      JSONB        NOT NULL DEFAULT '{}',                  -- job inputs (spec, template id, push target); gitToken is Fernet-encrypted at rest
    result       JSONB,                                               -- handler output (artifacts, validation issues, commit/PR info)
    error        TEXT,                                                -- failure message when status = failed
    attempts     INTEGER      NOT NULL DEFAULT 0,                     -- times the job has been claimed
    claimed_by   VARCHAR(64),                                         -- worker instance id that claimed it
    started_at   TIMESTAMPTZ,                                         -- claim time (UTC)
    finished_at  TIMESTAMPTZ,                                         -- completion time (UTC)
    created_by   VARCHAR(64),                                         -- users.id who enqueued it
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),                 -- enqueue time (UTC)
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()                  -- last status change (UTC)
);

-- ---------------------------------------------------------------------------
-- Audit
-- ---------------------------------------------------------------------------

-- Append-only audit trail (§30). Purged by `python -m app.manage purge-audit-log` (AUDIT_RETENTION_DAYS).
-- Migration 0001 (+0012 team_id).
CREATE TABLE app.audit_log (
    id         VARCHAR(64)  PRIMARY KEY,                              -- opaque entry id
    actor      VARCHAR(200) NOT NULL,                                 -- acting users.id; resolved to actorName at read time
    action     VARCHAR(64)  NOT NULL,                                 -- dotted action, e.g. pipeline.created, pipeline.updated, git.push_requested, user.created
    target     VARCHAR(256) NOT NULL DEFAULT '',                      -- id or name of the affected resource
    details    TEXT         NOT NULL DEFAULT '',                      -- human-readable detail; never contains secrets
    team_id    VARCHAR(64)  REFERENCES app.teams(id),                 -- team of the affected resource, captured at write time; NULL = platform-global (PLATFORM_ADMIN-only)
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),                   -- event time (UTC)
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()                    -- TimestampMixin; entries are never updated
);

-- ---------------------------------------------------------------------------
-- Planned tables (target design, no migration yet)
-- ---------------------------------------------------------------------------

-- (planned) §23.5 template catalogue for generated SQL artifacts.
CREATE TABLE app.sql_templates (                                      -- (planned)
    id          VARCHAR(64)  PRIMARY KEY,                             -- opaque template id
    name        VARCHAR(128) NOT NULL,                                -- template name
    kind        VARCHAR(32)  NOT NULL,                                -- control_table | connection_control_table | watermark_table
    document    JSONB        NOT NULL DEFAULT '{}',                   -- template body/parameters
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),                  -- row creation time (UTC)
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()                   -- last modification time (UTC)
);

-- (planned) §23.5 record that a SQL artifact was generated for a pipeline (today these are plain artifacts rows).
CREATE TABLE app.sql_artifacts (                                      -- (planned)
    id              VARCHAR(64)  PRIMARY KEY,                         -- opaque id
    pipeline_id     VARCHAR(64)  NOT NULL REFERENCES app.pipeline_definitions(id), -- generating pipeline
    sql_template_id VARCHAR(64)  NOT NULL REFERENCES app.sql_templates(id), -- template used
    file_name       VARCHAR(256) NOT NULL,                            -- e.g. adf_control_table.sql
    content         TEXT         NOT NULL DEFAULT '',                 -- generated SQL text
    repository_path VARCHAR(512),                                     -- Git path once pushed
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),              -- generation time (UTC)
    CONSTRAINT uq_sql_artifacts_pipeline_file UNIQUE (pipeline_id, file_name)
);

-- (planned) §24.17 saved-but-not-generated Build form, incl. round-trip edits from the Catalog.
CREATE TABLE app.pipeline_drafts (                                    -- (planned)
    id             VARCHAR(64)  PRIMARY KEY,                          -- opaque draft id
    project_id     VARCHAR(64)  NOT NULL REFERENCES app.projects(id), -- owning project
    user_id        VARCHAR(64)  NOT NULL REFERENCES app.users(id),    -- draft owner
    mode           VARCHAR(16)  NOT NULL DEFAULT 'new',               -- new | edit (round-trip from Catalog)
    repo           VARCHAR(256),                                      -- repository when mode = edit
    branch         VARCHAR(256),                                      -- branch when mode = edit
    path           VARCHAR(512),                                      -- Git path this draft edits
    base_blob_sha  VARCHAR(64),                                       -- optimistic-conflict base (§24.14)
    spec_draft     JSONB        NOT NULL DEFAULT '{}',                -- SpecDraft (Build form state)
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),               -- row creation time (UTC)
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()                -- last save time (UTC)
);

-- (planned) §24.17 edit lock so two users can't round-trip the same Git path at once.
CREATE TABLE app.artifact_locks (                                     -- (planned)
    id          VARCHAR(64)  PRIMARY KEY,                             -- opaque lock id
    project_id  VARCHAR(64)  NOT NULL REFERENCES app.projects(id),    -- owning project
    branch      VARCHAR(256) NOT NULL,                                -- locked branch
    path        VARCHAR(512) NOT NULL,                                -- locked Git path
    user_id     VARCHAR(64)  NOT NULL REFERENCES app.users(id),       -- lock holder
    acquired_at TIMESTAMPTZ  NOT NULL DEFAULT now(),                  -- lock time (UTC)
    expires_at  TIMESTAMPTZ  NOT NULL,                                -- auto-release time (UTC)
    CONSTRAINT uq_artifact_locks_project_branch_path UNIQUE (project_id, branch, path)
);

-- (planned) §25.9 org/team/project-inheritable Standards policy. Today the naming policy lives in
-- project_settings.document and the compliance scan runs in the browser.
CREATE TABLE app.standards_policies (                                 -- (planned)
    id         VARCHAR(64)  PRIMARY KEY,                              -- opaque policy id
    scope      VARCHAR(16)  NOT NULL,                                 -- org | team | project
    scope_id   VARCHAR(64)  NOT NULL,                                 -- id of the org/team/project
    version    VARCHAR(32)  NOT NULL,                                 -- policy version
    rules      JSONB        NOT NULL DEFAULT '{}',                    -- {ruleId: {enforcement: off|warn|block, config}}
    updated_by VARCHAR(200),                                          -- last editor
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),                   -- last modification time (UTC)
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),                   -- row creation time (UTC)
    CONSTRAINT uq_standards_policies_scope UNIQUE (scope, scope_id, version)
);

-- (planned) §25.9 time-boxed exception to a Standards rule.
CREATE TABLE app.standards_exceptions (                               -- (planned)
    id            VARCHAR(64)  PRIMARY KEY,                           -- opaque exception id
    project_id    VARCHAR(64)  NOT NULL REFERENCES app.projects(id),  -- owning project
    rule_id       VARCHAR(128) NOT NULL,                              -- rule being waived, e.g. naming.pipeline
    artifact_path VARCHAR(512),                                       -- specific artifact; NULL = whole project
    reason        TEXT         NOT NULL DEFAULT '',                   -- justification
    granted_by    VARCHAR(64)  NOT NULL,                              -- approver users.id
    expires_at    TIMESTAMPTZ,                                        -- expiry; NULL = no expiry
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()                 -- grant time (UTC)
);

-- (planned) §25.10 persisted result of a policy evaluation.
CREATE TABLE app.policy_evaluations (                                 -- (planned)
    id           VARCHAR(64)  PRIMARY KEY,                            -- opaque evaluation id
    project_id   VARCHAR(64)  NOT NULL REFERENCES app.projects(id),   -- evaluated project
    subject      VARCHAR(16)  NOT NULL,                               -- draft | scan | push
    result       JSONB        NOT NULL DEFAULT '{}',                  -- typed violations
    evaluated_at TIMESTAMPTZ  NOT NULL DEFAULT now()                  -- evaluation time (UTC)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

CREATE INDEX ix_sessions_user_id               ON app.sessions(user_id);                         -- 0008
CREATE INDEX ix_sso_role_mappings_configuration ON app.sso_role_mappings(configuration_id);     -- 0018
CREATE INDEX ix_pipeline_definitions_project   ON app.pipeline_definitions(project_id);          -- 0009
CREATE INDEX ix_pipeline_definitions_team      ON app.pipeline_definitions(team_id);             -- 0009
CREATE INDEX ix_artifacts_pipeline             ON app.artifacts(pipeline_id);                    -- 0009
CREATE INDEX ix_connection_library_project_id  ON app.connection_library(project_id);            -- 0005
CREATE INDEX ix_connection_library_team_id     ON app.connection_library(team_id);               -- 0005
CREATE INDEX ix_managed_identities_team        ON app.managed_identities(team_id);               -- 0013
CREATE INDEX ix_onboarding_objects_project     ON app.onboarding_objects(project_id);            -- 0011
CREATE INDEX ix_dq_rules_object                ON app.dq_rules(project_id, object_key);          -- 0014
CREATE INDEX ix_onboarding_imports_project     ON app.onboarding_imports(project_id);            -- 0016
CREATE INDEX ix_onboarding_syncs_project       ON app.onboarding_syncs(project_id);              -- 0019
CREATE INDEX ix_sttm_imports_project           ON app.sttm_imports(project_id);                  -- 0010
CREATE INDEX ix_silver_objects_project         ON app.silver_objects(project_id);                -- 0010
CREATE INDEX ix_silver_objects_tag             ON app.silver_objects(project_id, tag);           -- 0010
CREATE INDEX ix_mart_objects_project           ON app.mart_objects(project_id);                  -- 0010
CREATE INDEX ix_mart_objects_tag               ON app.mart_objects(project_id, tag);             -- 0010
CREATE INDEX ix_git_catalog_changes_project    ON app.git_catalog_changes(project_id);           -- 0020
CREATE INDEX ix_jobs_status                    ON app.jobs(status);                              -- 0001
CREATE INDEX ix_audit_log_team                 ON app.audit_log(team_id);                        -- 0012
-- (planned) no query filters on these yet; add with the feature that needs them.
CREATE INDEX ix_git_push_requests_pipeline     ON app.git_push_requests(pipeline_id);            -- (planned)
CREATE INDEX ix_audit_log_target               ON app.audit_log(target);                         -- (planned)
CREATE INDEX ix_pipeline_drafts_user           ON app.pipeline_drafts(project_id, user_id);      -- (planned)
CREATE INDEX ix_standards_exceptions_project   ON app.standards_exceptions(project_id, rule_id); -- (planned)
CREATE INDEX ix_policy_evaluations_project     ON app.policy_evaluations(project_id, evaluated_at); -- (planned)
-- git_catalog_entries needs no extra index: its PK (project_id, branch, path) already serves the
-- per-branch listing query.
```

### Verification log

The live tables above are checked column-by-column against `backend/app/models.py` and every migration
each time this file is re-synced. `worker/app/db/models.py` vendors only the subset the worker reads
and writes (no password/session columns, no `connection_library`, no registries); see its module
docstring.

| Date | Range checked | Drift found and fixed in this file |
|---|---|---|
| 2026-09-13 | 0001–0009 | `pipeline_versions.target_environment_name` missing; three live indexes never created by a migration (added `0009`) |
| 2026-09-14 | 0001–0010 | `pipeline_versions.updated_at` missing; `adf_execution_log` / `adf_chunk_plan` only described in prose |
| 2026-09-21 | 0001–0014 | `onboarding_objects.updated_by` missing; `upsert_keys` documented as `JSONB` (really `VARCHAR(512)`) |
| 2026-09-27 | 0001–0020 | `onboarding_objects.import_id` documented as live (it is not on the table; now **(planned)**); `onboarding_imports.mode` default `'upsert'` missing; `ix_onboarding_imports_project` missing; `silver/mart_objects.notes` shown nullable (really `NOT NULL DEFAULT ''`); §2.2 watermark table didn't match the generator. Every column now carries a comment, and `adf_forge_ddl.sql` is generated from this file. |

---

## 2. ADF execution-plane control store — schema `control` by default (Azure SQL / SQL Server, T-SQL)

This is **not** part of this app's database. It is the metadata-driven Copy Data control store a
*generated pipeline* connects to at ADF runtime (via its own linked service) to decide which objects to
copy and how. This app only **generates** this DDL + seed data as a Git
artifact — `adf_control_table.sql`, type `parameter`, layer `metadata` (§23.9) — and pushes it through the
normal feature-branch → PR flow. Reproduced here exactly as
[worker/app/generator/codegen.py](../worker/app/generator/codegen.py) (`_control_table_sql`, in parity with
[frontend/lib/adf/pipeline-codegen.ts](../frontend/lib/adf/pipeline-codegen.ts)'s `controlTableSql`) emits
it, with a comment added to every column. Every create is guarded with `IF OBJECT_ID(N'<table>', N'U') IS
NULL` so the script re-runs cleanly on Azure SQL / SQL Server (T-SQL has no `CREATE TABLE IF NOT EXISTS`;
the generator emitted that invalid form until 2026-09-27). Every seed `INSERT` is guarded with `IF NOT
EXISTS` on its natural key (Source + Target, `ConnectionName`, `ObjectKey`, `TaskId`) since 2026-10-16, so
re-running a script never duplicates a row. The control-table script has no `CREATE SCHEMA` — the
customer's DBA creates the control schema first. (Silver/Gold layer schemas are a separate script, §2.5.)

**Schema/table name is per project, not hardcoded.** The table is whatever `SpecDraft.watermarkTable`
names (Build: "Control / watermark table"). A new draft gets `<schema>.<table>` from Project Settings →
Metadata & parameters → Control-table schema/name when both are set, otherwise `control.watermarks`;
`control.adf_control_table` (used in the examples below) is only the generator's fallback when the field
is blank. The master/single-pipeline `Lookup` reads the `ControlTable` pipeline parameter live, and a
`TaskIdFilter` parameter (default `0` = all enabled rows) re-runs one row without touching the rest of the batch.

### 2.0 Main control table and optional log / chunk tables

```sql
-- Metadata / control table for the metadata-driven Copy Data pattern. One row per object to copy.
-- Add, disable, or re-configure objects here without rebuilding the pipeline.
IF OBJECT_ID(N'control.adf_control_table', N'U') IS NULL
CREATE TABLE control.adf_control_table (
  TaskId            INT IDENTITY(1,1) PRIMARY KEY,                -- row id; passed to the child pipeline and matched by TaskIdFilter for single-row restarts
  SourceType        NVARCHAR(128)  NOT NULL,                      -- source connector type (SpecDraft.sourceType, e.g. sql_server)
  Source            NVARCHAR(512)  NOT NULL,                      -- qualified source object (schema.table, catalog.schema.table, or object name)
  SourceQuery       NVARCHAR(MAX)  NULL,                          -- custom extract query; empty = read the whole object
  TargetType        NVARCHAR(128)  NOT NULL,                      -- target connector type (SpecDraft.targetType)
  Target            NVARCHAR(512)  NOT NULL,                      -- target table or folder path
  TargetFileName    NVARCHAR(512)  NULL,                          -- target file name for file sinks
  LoadingBehavior   NVARCHAR(16)   NOT NULL DEFAULT 'DeltaLoad',  -- FullLoad | DeltaLoad; routes the child pipeline's Switch
  WatermarkColumn   NVARCHAR(128)  NULL,                          -- incremental column(s) for DeltaLoad
  WatermarkType     NVARCHAR(16)   NULL,                          -- single | composite
  WatermarkValue    NVARCHAR(256)  NULL,                          -- last processed watermark; updated by UpdateWatermark after a successful copy
  Enabled           BIT            NOT NULL DEFAULT 1,            -- 0 = skipped by the Lookup
  TaskGroup         NVARCHAR(128)  NULL,                          -- only emitted when the master has childDispatch cases: Switch key choosing the child pipeline; NULL = default child
  ColumnMapping     NVARCHAR(MAX)  NULL,                          -- only emitted when an object has its own columnMappings: per-row Copy TabularTranslator JSON
  LastRunTimestamp  DATETIME2      NULL,                          -- last successful run time
  CreatedAt         DATETIME2      NOT NULL DEFAULT GETDATE()     -- row creation time
);

-- Seed rows: one INSERT per enabled source object, generated from the Build form / Onboarding registry.
-- INSERT INTO control.adf_control_table (
--   SourceType, Source, SourceQuery, TargetType, Target, TargetFileName,
--   LoadingBehavior, WatermarkColumn, WatermarkType, WatermarkValue, Enabled
-- ) VALUES (
--   N'sql_server', N'dbo.Orders', N'',
--   N'adls_gen2', N'raw/orders/', N'',
--   N'DeltaLoad', N'ModifiedDate', N'single', N'1900-01-01', 1
-- );

-- Shared execution log, emitted when enableCopyLog is on. LogRunStart inserts a RUNNING row;
-- LogRunSucceeded / LogRunFailed complete it.
IF OBJECT_ID(N'control.adf_execution_log', N'U') IS NULL
CREATE TABLE control.adf_execution_log (
  LogId          INT IDENTITY(1,1) PRIMARY KEY,                   -- log row id
  RunId          NVARCHAR(64)   NOT NULL,                         -- ADF pipeline run id (@pipeline().RunId)
  PipelineName   NVARCHAR(256)  NOT NULL,                         -- ADF pipeline name
  SourceObject   NVARCHAR(512)  NOT NULL,                         -- source object copied
  TargetObject   NVARCHAR(512)  NULL,                             -- target table/path written
  LoadType       NVARCHAR(16)   NULL,                             -- FullLoad | DeltaLoad | ChunkedLoad
  RowsRead       BIGINT         NULL,                             -- Copy activity rowsRead
  RowsWritten    BIGINT         NULL,                             -- Copy activity rowsCopied
  RowsRejected   BIGINT         NULL,                             -- rows redirected as incompatible
  Status         NVARCHAR(16)   NOT NULL,                         -- RUNNING | SUCCEEDED | FAILED
  ErrorMessage   NVARCHAR(MAX)  NULL,                             -- activity error on failure
  StartedAt      DATETIME2      NOT NULL,                         -- run start (UTC)
  CompletedAt    DATETIME2      NULL                              -- run end (UTC); NULL while RUNNING
);

-- Chunk plan for chunked initial load, emitted when chunkingEnabled + chunkColumn are set.
-- GenerateChunkPlan seeds rows; ForEachChunk updates Status so a re-run only retries PENDING/FAILED chunks.
IF OBJECT_ID(N'control.adf_chunk_plan', N'U') IS NULL
CREATE TABLE control.adf_chunk_plan (
  TaskId         INT            NOT NULL,                         -- adf_control_table.TaskId being chunked
  ChunkId        INT            NOT NULL,                         -- chunk sequence number within the task
  RangeStart     NVARCHAR(128)  NOT NULL,                         -- lower bound on the chunk column
  RangeEnd       NVARCHAR(128)  NOT NULL,                         -- upper bound on the chunk column
  Status         NVARCHAR(16)   NOT NULL DEFAULT 'PENDING',       -- PENDING | RUNNING | SUCCEEDED | FAILED
  RowsCopied     BIGINT         NULL,                             -- rows copied for this chunk
  StartedAt      DATETIME2      NULL,                             -- chunk start (UTC)
  CompletedAt    DATETIME2      NULL,                             -- chunk end (UTC)
  ErrorMessage   NVARCHAR(MAX)  NULL,                             -- error on failure
  PRIMARY KEY (TaskId, ChunkId)
);
```

Both optional tables go into the same `adf_control_table.sql` artifact, after the seed `INSERT`s, and
use the control table's schema.

### 2.1 Connection control table (opt-in)

One row per distinct source/target linked service the pipeline uses, so ops can repoint a connection per
environment by editing this table instead of regenerating artifacts. Turned on by Project Settings →
"Generate a connection control table" or per pipeline (`SpecDraft.connectionControlEnabled` /
`connectionControlTable`); appended to `adf_control_table.sql`. Not yet wired into the main table's
`Source`/`Target` semantics — it is a registry generated alongside it.

```sql
IF OBJECT_ID(N'control.adf_connection_control', N'U') IS NULL
CREATE TABLE control.adf_connection_control (
  ConnectionId      INT IDENTITY(1,1) PRIMARY KEY,                -- row id
  ConnectionName    NVARCHAR(128)  NOT NULL UNIQUE,               -- linked service name (e.g. LS_sql_server_source); dedup key
  ConnectorType     NVARCHAR(64)   NOT NULL,                      -- connector registry key (sql_server, adls_gen2, snowflake, ...)
  LinkedServiceName NVARCHAR(256)  NOT NULL,                      -- ADF linked service this row resolves to
  KeyVaultName      NVARCHAR(256)  NULL,                          -- Key Vault linked service name only; never a secret value
  SecretRef         NVARCHAR(256)  NULL,                          -- Key Vault secret NAME for the selected auth (from the schema's isSecret field)
  Properties        NVARCHAR(MAX)  NULL,                          -- JSON of every other non-secret connector field (server, database, path, ...)
  Enabled           BIT            NOT NULL DEFAULT 1,            -- 0 = connection retired
  CreatedAt         DATETIME2      NOT NULL DEFAULT GETDATE()     -- row creation time
);
```

### 2.2 Watermark table (`independent_watermark` generation strategy)

The single-object incremental pattern (§24.12) has no control table; it tracks its own watermark in a
one-row table (`_watermark_table_sql` / `watermarkTableSql`), emitted as `adf_watermark_table.sql`.
The table name comes from `SpecDraft.watermarkTable` (default `control.watermarks`).

```sql
IF OBJECT_ID(N'control.watermarks', N'U') IS NULL
CREATE TABLE control.watermarks (
  TaskId            INT           NOT NULL PRIMARY KEY,           -- always 1: the single tracked object
  WatermarkValue    NVARCHAR(256) NULL,                           -- last processed watermark; seeded from watermarkInitialValue (default 1900-01-01)
  LastRunTimestamp  DATETIME2     NULL                            -- last successful run time
);
-- IF NOT EXISTS (SELECT 1 FROM control.watermarks WHERE TaskId = 1)
--   INSERT INTO control.watermarks (TaskId, WatermarkValue) VALUES (1, N'1900-01-01');
```

### 2.3 Curated / Mart control tables

Siblings of `adf_control_table`, not extensions of it. Generated only when a pipeline's `medallionLayer`
is silver/gold **and** it was built from a picked registry object (`SpecDraft.silverObjectRef` /
`martObjectRef`), as `adf_curated_control_table.sql` / `adf_mart_control_table.sql`. Values are a
selection-time snapshot, so the generator never queries `app.*`.

```sql
IF OBJECT_ID(N'control.adf_curated_control_table', N'U') IS NULL
CREATE TABLE control.adf_curated_control_table (
  ObjectId              INT IDENTITY(1,1) PRIMARY KEY,            -- row id
  ObjectKey             NVARCHAR(256)  NOT NULL,                  -- app.silver_objects.object_key
  Tag                   NVARCHAR(128)  NOT NULL,                  -- registry classification tag
  CuratedEntity         NVARCHAR(256)  NOT NULL,                  -- curated entity name
  TargetTable           NVARCHAR(512)  NOT NULL,                  -- computed curated.<entity> (or the project's Silver prefix)
  SourceObjectKeys      NVARCHAR(MAX)  NULL,                      -- pipe-separated Bronze object_keys
  TransformationCount   INT            NOT NULL DEFAULT 0,        -- transformation rules applied
  DataQualityRuleCount  INT            NOT NULL DEFAULT 0,        -- DQ rules applied
  WriteBehavior         NVARCHAR(16)   NULL,                      -- append | overwrite | upsert | merge
  LoadingBehavior       NVARCHAR(16)   NULL,                      -- FullLoad | DeltaLoad
  Enabled               BIT            NOT NULL DEFAULT 1,        -- 0 = skipped
  CreatedAt             DATETIME2      NOT NULL DEFAULT GETDATE() -- row creation time
);

IF OBJECT_ID(N'control.adf_mart_control_table', N'U') IS NULL
CREATE TABLE control.adf_mart_control_table (
  ObjectId                  INT IDENTITY(1,1) PRIMARY KEY,        -- row id
  ObjectKey                 NVARCHAR(256) NOT NULL,               -- app.mart_objects.object_key
  Tag                       NVARCHAR(128) NOT NULL,               -- registry classification tag
  MartDomain                NVARCHAR(128) NOT NULL,               -- mart domain
  CuratedEntity             NVARCHAR(256) NOT NULL,               -- mart entity name
  TargetTable               NVARCHAR(512) NOT NULL,               -- computed mart.<domain>_<entity> (or the project's Gold prefix)
  SourceCuratedEntities     NVARCHAR(MAX) NULL,                   -- pipe-separated Silver object_keys
  TransformationCount       INT           NOT NULL DEFAULT 0,     -- transformation rules applied
  DataQualityRuleCount      INT           NOT NULL DEFAULT 0,     -- DQ rules applied
  WriteBehavior             NVARCHAR(16)  NULL,                   -- append | overwrite | upsert | merge
  LoadingBehavior           NVARCHAR(16)  NULL,                   -- FullLoad | DeltaLoad
  Enabled                   BIT           NOT NULL DEFAULT 1,     -- 0 = skipped
  CreatedAt                 DATETIME2     NOT NULL DEFAULT GETDATE() -- row creation time
);
```

### 2.4 Columns generated pipelines add to target tables

These are not control tables, but generated pipelines write them into customer target tables:

| Column(s) | When | Written by |
|---|---|---|
| `_pipeline_run_id`, `_pipeline_name`, `_ingestion_ts_utc`, `_medallion_layer` (+ `_trigger_time` on Bronze) | "Standard audit columns" checked in Build; the list is per layer and overridable in Standards → Audit columns (`ProjectSettings.auditColumns`) | Copy `additionalColumns` (`standardAuditColumns`, `medallion.ts` / `medallion.py`) |
| `_is_deleted BIT NOT NULL DEFAULT 0`, `_deleted_at_utc DATETIME2 NULL` | `SpecDraft.deleteHandling` = `soft_delete` or `cdc`; flag name overridable via `softDeleteColumn` | `ALTER TABLE ... ADD` guarded by `COL_LENGTH`, then a flag/unflag script before `UpdateWatermark` |
| `_is_current`, `_valid_from`, `_valid_to` (names overridable per rule: `currentFlagColumn`/`validFromColumn`/`validToColumn`) | a Silver/Gold `scd2` transformation | the SCD2 `MERGE` script (`medallion.py`) |

### 2.5 Layer schemas and per-run staging tables (Silver/Gold transformation chains)

Not control tables either, but a Silver/Gold pipeline with at least one transformation rule creates or
needs these on the **target** store:

| Object | Shape | Written by |
|---|---|---|
| `adf_layer_schemas.sql` (artifact) | `IF SCHEMA_ID(N'<s>') IS NULL EXEC(N'CREATE SCHEMA [<s>]');` for the Bronze landed schema, `stg`, and the layer target schema. Schemas only — tables are created by the load itself | `_layer_schemas_sql` / `layerSchemasSql`, run once before the first run |
| `stg.<pipeline>_step<N>_<TaskId>_<RunId>` | One table per non-terminal chain step, per run, so overlapping runs never share one | `Transform_*` Script activities; dropped by `DropStagingTables` after a successful chain (kept on failure for diagnosis) |
| `stg.<landed table>_delete_keys` | Source keys staged for soft-delete reconciliation | `deleteHandling = soft_delete` |

> **Never store secrets here.** Every column that identifies a connection holds a *name* or a Key Vault
> *reference* — a linked-service name, a Key Vault name, a secret name. No connection string, password,
> PAT, or client secret is generated into this SQL or written to this app's metastore (§1.6, §23.5).

---

## Why two schemas instead of one

- **Different database, different lifecycle.** Schema 1 lives in this app's own Postgres and is
  redeployed with the app. Schema 2 lives in whatever Azure SQL Database the customer's ADF factory
  points its control-table linked service at; this app never opens a connection to it — it only
  generates SQL text and lets Git + the customer's own deploy process apply it (§23.6, §23.7 — "the
  accelerator's responsibility ends at the PR into `dev`").
- **Different security boundary.** Schema 1 is queried by this app's API/worker under the app's own
  service identity. Schema 2 must be reachable by ADF's managed identity / linked-service credential at
  pipeline runtime — a trust boundary this app must never assume it can reach.
- **Different consumer.** Schema 1 rows are read by `backend/` and rendered in the UI. Schema 2 rows are
  read by a `Lookup` activity inside a running ADF pipeline.
