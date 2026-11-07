-- =============================================================================
-- ADF Accelerator — runnable DDL (GENERATED — do not edit by hand)
-- Source: docs/DDL.md  ->  python docs/scripts/build_ddl_sql.py
--
-- Part 1  adf_forge_app      Application metastore (PostgreSQL 15+). Live tables only
--         (migrations 0001–0021); (planned) tables/columns from DDL.md are omitted. Column
--         documentation is stored with COMMENT ON, so `\d+ adf_forge_app.<table>` shows it.
--         In the running app, Alembic owns this schema (tables land in `public`); use this
--         part for review, a fresh reference database, or catalog/lineage tooling.
-- Part 2  adf_forge_control  ADF control store (Azure SQL / SQL Server, T-SQL). Run it in the
--         customer's control database, not the app's Postgres. Column docs are stored as
--         MS_Description extended properties.
-- The two parts use different SQL dialects — run each against its own engine.
-- =============================================================================


-- =============================================================================
-- PART 1 — Application metastore (PostgreSQL 15+)
-- =============================================================================

-- Once, from an admin connection:  CREATE DATABASE adf_forge;  then connect to it.
CREATE SCHEMA IF NOT EXISTS adf_forge_app;

CREATE TABLE adf_forge_app.teams (
    id          VARCHAR(64)  PRIMARY KEY,            -- opaque team id, assigned by the API
    name        VARCHAR(200) NOT NULL,               -- display name shown in the team switcher and admin screens
    description TEXT         NOT NULL DEFAULT '',    -- free-text purpose of the team
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(), -- row creation time (UTC)
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()  -- last modification time (UTC)
);
COMMENT ON TABLE adf_forge_app.teams IS 'Organizational unit that owns projects and scopes RBAC (§11, §27.4). Migration 0001.';
COMMENT ON COLUMN adf_forge_app.teams.id IS 'opaque team id, assigned by the API';
COMMENT ON COLUMN adf_forge_app.teams.name IS 'display name shown in the team switcher and admin screens';
COMMENT ON COLUMN adf_forge_app.teams.description IS 'free-text purpose of the team';
COMMENT ON COLUMN adf_forge_app.teams.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.teams.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.users (
    id                     VARCHAR(64)  PRIMARY KEY,                  -- opaque user id; what every created_by/updated_by/actor column stores
    name                   VARCHAR(200) NOT NULL,                     -- display name; the only identity shown in the header (product rule #3)
    email                  VARCHAR(320) NOT NULL UNIQUE,              -- contact address; also accepted at the AUTH_MODE=password login; unique across the platform
    username               VARCHAR(64)  UNIQUE,                       -- login name for AUTH_MODE=password: lowercase, 2-64 chars [a-z0-9._-], never contains '@' (unique index ux_users_username, migration 0021); set by the admin or derived from the email; NULL only for a future SSO-only account
    role                   VARCHAR(32)  NOT NULL DEFAULT 'DEVELOPER', -- PLATFORM_ADMIN | TEAM_ADMIN | DEVELOPER | VIEWER (§11, app/rbac.py)
    team_id                VARCHAR(64)  REFERENCES adf_forge_app.teams(id), -- home team for scope checks; NULL only for platform-wide admins
    active                 BOOLEAN      NOT NULL DEFAULT TRUE,        -- FALSE = deactivated; login and API access refused
    last_seen              TIMESTAMPTZ,                               -- last authenticated request (UTC); NULL = never signed in
    password_hash          VARCHAR(255),                              -- scrypt hash (app/security.py), never plaintext; NULL outside AUTH_MODE=password
    password_updated_at    TIMESTAMPTZ,                               -- when the password was last set or reset
    must_change_password   BOOLEAN      NOT NULL DEFAULT FALSE,       -- forced on account creation and admin reset; cleared by POST /auth/change-password
    failed_login_attempts  INTEGER      NOT NULL DEFAULT 0,           -- consecutive failures; incremented atomically in SQL, reset on success
    locked_until           TIMESTAMPTZ,                               -- account locked until this time after auth_max_failed_logins failures
    invited_by             VARCHAR(64)  REFERENCES adf_forge_app.users(id), -- admin who created the account via POST /admin/users; NULL for the CLI-bootstrapped admin
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),       -- row creation time (UTC)
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now()        -- last modification time (UTC)
);
COMMENT ON TABLE adf_forge_app.users IS 'One row per person. Team/role assignment is team_id + role (one home team per user) — there is no membership table; the dead team_members table was dropped in migration 0008. Migration 0001 (+0008, +0021 username).';
COMMENT ON COLUMN adf_forge_app.users.id IS 'opaque user id; what every created_by/updated_by/actor column stores';
COMMENT ON COLUMN adf_forge_app.users.name IS 'display name; the only identity shown in the header (product rule #3)';
COMMENT ON COLUMN adf_forge_app.users.email IS 'contact address; also accepted at the AUTH_MODE=password login; unique across the platform';
COMMENT ON COLUMN adf_forge_app.users.username IS 'login name for AUTH_MODE=password: lowercase, 2-64 chars [a-z0-9._-], never contains ''@'' (unique index ux_users_username, migration 0021); set by the admin or derived from the email; NULL only for a future SSO-only account';
COMMENT ON COLUMN adf_forge_app.users.role IS 'PLATFORM_ADMIN | TEAM_ADMIN | DEVELOPER | VIEWER (§11, app/rbac.py)';
COMMENT ON COLUMN adf_forge_app.users.team_id IS 'home team for scope checks; NULL only for platform-wide admins';
COMMENT ON COLUMN adf_forge_app.users.active IS 'FALSE = deactivated; login and API access refused';
COMMENT ON COLUMN adf_forge_app.users.last_seen IS 'last authenticated request (UTC); NULL = never signed in';
COMMENT ON COLUMN adf_forge_app.users.password_hash IS 'scrypt hash (app/security.py), never plaintext; NULL outside AUTH_MODE=password';
COMMENT ON COLUMN adf_forge_app.users.password_updated_at IS 'when the password was last set or reset';
COMMENT ON COLUMN adf_forge_app.users.must_change_password IS 'forced on account creation and admin reset; cleared by POST /auth/change-password';
COMMENT ON COLUMN adf_forge_app.users.failed_login_attempts IS 'consecutive failures; incremented atomically in SQL, reset on success';
COMMENT ON COLUMN adf_forge_app.users.locked_until IS 'account locked until this time after auth_max_failed_logins failures';
COMMENT ON COLUMN adf_forge_app.users.invited_by IS 'admin who created the account via POST /admin/users; NULL for the CLI-bootstrapped admin';
COMMENT ON COLUMN adf_forge_app.users.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.users.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.sessions (
    id               VARCHAR(64)  PRIMARY KEY,                       -- opaque session id carried in the session cookie; never exposed by the admin API
    user_id          VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.users(id), -- signed-in user
    issued_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),            -- sign-in time (UTC)
    expires_at       TIMESTAMPTZ  NOT NULL,                          -- absolute timeout; the session ends here regardless of activity
    idle_expires_at  TIMESTAMPTZ  NOT NULL,                          -- idle timeout; slides forward on each authenticated request
    revoked_at       TIMESTAMPTZ,                                    -- set by logout or admin revoke (DELETE /admin/sessions); NULL = still valid
    ip               VARCHAR(64),                                    -- client IP at sign-in, for the Active sessions panel
    user_agent       VARCHAR(512),                                   -- client user agent at sign-in, for the Active sessions panel
    csrf_token       VARCHAR(64)  NOT NULL                           -- double-submit CSRF token; echoed back as X-CSRF-Token on writes
);
COMMENT ON TABLE adf_forge_app.sessions IS 'Server-side, revocable session for AUTH_MODE=password (§27.3). Referenced by an opaque httpOnly+Secure+SameSite=Lax cookie, never a stateless JWT. Migration 0008.';
COMMENT ON COLUMN adf_forge_app.sessions.id IS 'opaque session id carried in the session cookie; never exposed by the admin API';
COMMENT ON COLUMN adf_forge_app.sessions.user_id IS 'signed-in user';
COMMENT ON COLUMN adf_forge_app.sessions.issued_at IS 'sign-in time (UTC)';
COMMENT ON COLUMN adf_forge_app.sessions.expires_at IS 'absolute timeout; the session ends here regardless of activity';
COMMENT ON COLUMN adf_forge_app.sessions.idle_expires_at IS 'idle timeout; slides forward on each authenticated request';
COMMENT ON COLUMN adf_forge_app.sessions.revoked_at IS 'set by logout or admin revoke (DELETE /admin/sessions); NULL = still valid';
COMMENT ON COLUMN adf_forge_app.sessions.ip IS 'client IP at sign-in, for the Active sessions panel';
COMMENT ON COLUMN adf_forge_app.sessions.user_agent IS 'client user agent at sign-in, for the Active sessions panel';
COMMENT ON COLUMN adf_forge_app.sessions.csrf_token IS 'double-submit CSRF token; echoed back as X-CSRF-Token on writes';

CREATE TABLE adf_forge_app.sso_configurations (
    id                      VARCHAR(64)  PRIMARY KEY,            -- opaque configuration id
    name                    VARCHAR(128) NOT NULL,               -- admin label, unique (e.g. "Entra ID - corp")
    protocol                VARCHAR(16)  NOT NULL,               -- oidc | saml
    issuer_url              VARCHAR(512),                        -- OIDC issuer / discovery base URL; NULL for SAML
    client_id               VARCHAR(256),                        -- OIDC client (application) id; not a secret
    client_secret_key_vault VARCHAR(128),                        -- Key Vault NAME holding the OIDC client secret (reference only)
    client_secret_name      VARCHAR(128),                        -- secret NAME inside that Key Vault; the value is never stored
    metadata_url            VARCHAR(512),                        -- SAML federation metadata URL; NULL for OIDC
    entity_id               VARCHAR(512),                        -- SAML service-provider entity id; NULL for OIDC
    group_claim             VARCHAR(128) NOT NULL,               -- token claim holding group ids; API default 'groups'
    jit_provisioning        BOOLEAN      NOT NULL,               -- create users on first SSO sign-in; API default TRUE
    enabled                 BOOLEAN      NOT NULL,               -- recorded intent only; AUTH_MODE still decides sign-in. API default FALSE
    created_by              VARCHAR(64),                         -- users.id of the admin who created it
    updated_by              VARCHAR(64),                         -- users.id of the admin who last edited it
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(), -- row creation time (UTC)
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT now(), -- last modification time (UTC)
    CONSTRAINT uq_sso_configurations_name UNIQUE (name)
);
COMMENT ON TABLE adf_forge_app.sso_configurations IS 'Identity-provider configuration for future OIDC/SAML SSO (§27.5 ID2). Admin-editable metadata only; no login path reads it yet (AUTH_MODE oidc/saml still return 501). Migration 0018.';
COMMENT ON COLUMN adf_forge_app.sso_configurations.id IS 'opaque configuration id';
COMMENT ON COLUMN adf_forge_app.sso_configurations.name IS 'admin label, unique (e.g. "Entra ID - corp")';
COMMENT ON COLUMN adf_forge_app.sso_configurations.protocol IS 'oidc | saml';
COMMENT ON COLUMN adf_forge_app.sso_configurations.issuer_url IS 'OIDC issuer / discovery base URL; NULL for SAML';
COMMENT ON COLUMN adf_forge_app.sso_configurations.client_id IS 'OIDC client (application) id; not a secret';
COMMENT ON COLUMN adf_forge_app.sso_configurations.client_secret_key_vault IS 'Key Vault NAME holding the OIDC client secret (reference only)';
COMMENT ON COLUMN adf_forge_app.sso_configurations.client_secret_name IS 'secret NAME inside that Key Vault; the value is never stored';
COMMENT ON COLUMN adf_forge_app.sso_configurations.metadata_url IS 'SAML federation metadata URL; NULL for OIDC';
COMMENT ON COLUMN adf_forge_app.sso_configurations.entity_id IS 'SAML service-provider entity id; NULL for OIDC';
COMMENT ON COLUMN adf_forge_app.sso_configurations.group_claim IS 'token claim holding group ids; API default ''groups''';
COMMENT ON COLUMN adf_forge_app.sso_configurations.jit_provisioning IS 'create users on first SSO sign-in; API default TRUE';
COMMENT ON COLUMN adf_forge_app.sso_configurations.enabled IS 'recorded intent only; AUTH_MODE still decides sign-in. API default FALSE';
COMMENT ON COLUMN adf_forge_app.sso_configurations.created_by IS 'users.id of the admin who created it';
COMMENT ON COLUMN adf_forge_app.sso_configurations.updated_by IS 'users.id of the admin who last edited it';
COMMENT ON COLUMN adf_forge_app.sso_configurations.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.sso_configurations.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.sso_role_mappings (
    id               VARCHAR(64)  PRIMARY KEY,                                                      -- opaque mapping id
    configuration_id VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.sso_configurations(id) ON DELETE CASCADE, -- owning SSO configuration
    position         INTEGER      NOT NULL,                                                         -- evaluation order, ascending; first matching group wins
    group_value      VARCHAR(256) NOT NULL,                                                         -- group id/name as it appears in group_claim
    role             VARCHAR(32)  NOT NULL,                                                         -- PLATFORM_ADMIN | TEAM_ADMIN | DEVELOPER | VIEWER granted on match
    team_id          VARCHAR(64)  REFERENCES adf_forge_app.teams(id)                                -- team assigned on match; required unless role = PLATFORM_ADMIN
);
COMMENT ON TABLE adf_forge_app.sso_role_mappings IS 'IdP group -> (role, team) mapping used for JIT provisioning; first match by position wins. Migration 0018.';
COMMENT ON COLUMN adf_forge_app.sso_role_mappings.id IS 'opaque mapping id';
COMMENT ON COLUMN adf_forge_app.sso_role_mappings.configuration_id IS 'owning SSO configuration';
COMMENT ON COLUMN adf_forge_app.sso_role_mappings.position IS 'evaluation order, ascending; first matching group wins';
COMMENT ON COLUMN adf_forge_app.sso_role_mappings.group_value IS 'group id/name as it appears in group_claim';
COMMENT ON COLUMN adf_forge_app.sso_role_mappings.role IS 'PLATFORM_ADMIN | TEAM_ADMIN | DEVELOPER | VIEWER granted on match';
COMMENT ON COLUMN adf_forge_app.sso_role_mappings.team_id IS 'team assigned on match; required unless role = PLATFORM_ADMIN';

CREATE TABLE adf_forge_app.projects (
    id          VARCHAR(64)  PRIMARY KEY,                       -- opaque project id
    team_id     VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.teams(id), -- owning team; drives RBAC scope
    name        VARCHAR(200) NOT NULL,                          -- display name
    description TEXT         NOT NULL DEFAULT '',               -- free-text purpose
    created_by  VARCHAR(64),                                    -- users.id of the creator
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),            -- row creation time (UTC)
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()             -- last modification time (UTC)
);
COMMENT ON TABLE adf_forge_app.projects IS 'A team''s unit of work: settings, pipelines, registries and Git target all hang off a project. Migration 0001.';
COMMENT ON COLUMN adf_forge_app.projects.id IS 'opaque project id';
COMMENT ON COLUMN adf_forge_app.projects.team_id IS 'owning team; drives RBAC scope';
COMMENT ON COLUMN adf_forge_app.projects.name IS 'display name';
COMMENT ON COLUMN adf_forge_app.projects.description IS 'free-text purpose';
COMMENT ON COLUMN adf_forge_app.projects.created_by IS 'users.id of the creator';
COMMENT ON COLUMN adf_forge_app.projects.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.projects.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.project_settings (
    project_id VARCHAR(64) PRIMARY KEY REFERENCES adf_forge_app.projects(id), -- one settings row per project
    document   JSONB       NOT NULL DEFAULT '{}',                   -- ProjectSettings JSON: ADF/source/target defaults, naming convention, layer prefixes, audit columns, gitDefaults, tags, allowedConnectors, controlTable (frontend/types/project-settings.ts)
    updated_by VARCHAR(200),                                        -- display NAME (not id) of the last editor
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),                  -- row creation time (UTC)
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()                   -- last save time (UTC)
);
COMMENT ON TABLE adf_forge_app.project_settings IS 'Full Project Settings aggregate (§5) as one JSON document per project. Migration 0001.';
COMMENT ON COLUMN adf_forge_app.project_settings.project_id IS 'one settings row per project';
COMMENT ON COLUMN adf_forge_app.project_settings.document IS 'ProjectSettings JSON: ADF/source/target defaults, naming convention, layer prefixes, audit columns, gitDefaults, tags, allowedConnectors, controlTable (frontend/types/project-settings.ts)';
COMMENT ON COLUMN adf_forge_app.project_settings.updated_by IS 'display NAME (not id) of the last editor';
COMMENT ON COLUMN adf_forge_app.project_settings.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.project_settings.updated_at IS 'last save time (UTC)';

CREATE TABLE adf_forge_app.project_integrations (
    project_id VARCHAR(64) NOT NULL REFERENCES adf_forge_app.projects(id), -- owning project
    kind       VARCHAR(32) NOT NULL,                             -- live_validation (§29.1 layer 6) | purview (lineage push)
    enabled    BOOLEAN     NOT NULL DEFAULT false,               -- integration switched on for this project
    config     JSONB       NOT NULL DEFAULT '{}',                -- tenant/subscription/resource-group/account ids + clientSecretRef (Key Vault secret NAME, never a value)
    updated_by VARCHAR(64),                                      -- users.id of the last editor
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),               -- row creation time (UTC)
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),               -- last modification time (UTC)
    PRIMARY KEY (project_id, kind)
);
COMMENT ON TABLE adf_forge_app.project_integrations IS 'Per-project external-service configuration: layer-6 live ARM validation and Purview lineage push. Identifiers + Key Vault secret references only. Migration 0019.';
COMMENT ON COLUMN adf_forge_app.project_integrations.project_id IS 'owning project';
COMMENT ON COLUMN adf_forge_app.project_integrations.kind IS 'live_validation (§29.1 layer 6) | purview (lineage push)';
COMMENT ON COLUMN adf_forge_app.project_integrations.enabled IS 'integration switched on for this project';
COMMENT ON COLUMN adf_forge_app.project_integrations.config IS 'tenant/subscription/resource-group/account ids + clientSecretRef (Key Vault secret NAME, never a value)';
COMMENT ON COLUMN adf_forge_app.project_integrations.updated_by IS 'users.id of the last editor';
COMMENT ON COLUMN adf_forge_app.project_integrations.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.project_integrations.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.environments (
    id              VARCHAR(64)  PRIMARY KEY,                       -- opaque environment id
    name            VARCHAR(32)  NOT NULL UNIQUE,                   -- instance key picked in Build (e.g. DEV, QA, PROD, EU-PROD); stored on pipeline_versions.target_environment_name
    label           VARCHAR(64)  NOT NULL,                          -- friendly label shown in Administration
    status          VARCHAR(32)  NOT NULL DEFAULT 'not_configured', -- not_configured | configured (informational only)
    description     TEXT         NOT NULL DEFAULT '',               -- free-text notes
    storage_account VARCHAR(256) NOT NULL DEFAULT '',               -- storage account name for this instance (fills globalParameters.json)
    sql_server      VARCHAR(256) NOT NULL DEFAULT '',               -- SQL server host name for this instance
    database        VARCHAR(256) NOT NULL DEFAULT '',               -- database name for this instance
    key_vault       VARCHAR(256) NOT NULL DEFAULT '',               -- Key Vault NAME pipelines resolve secrets from at run time
    factory_name    VARCHAR(256) NOT NULL DEFAULT '',               -- ADF factory name; checked against FACTORY_NAME_RE by the validator
    subscription_id VARCHAR(64)  NOT NULL DEFAULT '',               -- Azure subscription id (identifier only; the app never calls ARM with it)
    resource_group  VARCHAR(256) NOT NULL DEFAULT '',               -- Azure resource group of the factory
    updated_by      VARCHAR(200),                                   -- display NAME (not id) of the last editor
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),            -- row creation time (UTC)
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()             -- last modification time (UTC)
);
COMMENT ON TABLE adf_forge_app.environments IS 'ADF instances (DEV/QA/PROD or any named factory): identifiers and references only, never a deploy plane — no deployment status, no publish action (§23.7). Migration 0003 (+0004).';
COMMENT ON COLUMN adf_forge_app.environments.id IS 'opaque environment id';
COMMENT ON COLUMN adf_forge_app.environments.name IS 'instance key picked in Build (e.g. DEV, QA, PROD, EU-PROD); stored on pipeline_versions.target_environment_name';
COMMENT ON COLUMN adf_forge_app.environments.label IS 'friendly label shown in Administration';
COMMENT ON COLUMN adf_forge_app.environments.status IS 'not_configured | configured (informational only)';
COMMENT ON COLUMN adf_forge_app.environments.description IS 'free-text notes';
COMMENT ON COLUMN adf_forge_app.environments.storage_account IS 'storage account name for this instance (fills globalParameters.json)';
COMMENT ON COLUMN adf_forge_app.environments.sql_server IS 'SQL server host name for this instance';
COMMENT ON COLUMN adf_forge_app.environments.database IS 'database name for this instance';
COMMENT ON COLUMN adf_forge_app.environments.key_vault IS 'Key Vault NAME pipelines resolve secrets from at run time';
COMMENT ON COLUMN adf_forge_app.environments.factory_name IS 'ADF factory name; checked against FACTORY_NAME_RE by the validator';
COMMENT ON COLUMN adf_forge_app.environments.subscription_id IS 'Azure subscription id (identifier only; the app never calls ARM with it)';
COMMENT ON COLUMN adf_forge_app.environments.resource_group IS 'Azure resource group of the factory';
COMMENT ON COLUMN adf_forge_app.environments.updated_by IS 'display NAME (not id) of the last editor';
COMMENT ON COLUMN adf_forge_app.environments.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.environments.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.pipeline_templates (
    id          VARCHAR(64)  PRIMARY KEY,            -- template id: full_load | incremental_load | initial_load | composite_watermark_load | metadata_driven_load
    name        VARCHAR(200) NOT NULL,               -- display name
    version     VARCHAR(32)  NOT NULL,               -- template version; copied to pipeline_versions.template_version on generate
    description TEXT         NOT NULL DEFAULT '',    -- what the template generates
    active      BOOLEAN      NOT NULL DEFAULT TRUE,  -- FALSE hides it from Build
    document    JSONB        NOT NULL DEFAULT '{}',  -- TemplateMetadata JSON (parameters, defaults, form schema)
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(), -- row creation time (UTC)
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()  -- last modification time (UTC)
);
COMMENT ON TABLE adf_forge_app.pipeline_templates IS 'Seeded pipeline template metadata (§6). The 5 template ids survive as internal generation-strategy keys (§24.12). Migration 0001.';
COMMENT ON COLUMN adf_forge_app.pipeline_templates.id IS 'template id: full_load | incremental_load | initial_load | composite_watermark_load | metadata_driven_load';
COMMENT ON COLUMN adf_forge_app.pipeline_templates.name IS 'display name';
COMMENT ON COLUMN adf_forge_app.pipeline_templates.version IS 'template version; copied to pipeline_versions.template_version on generate';
COMMENT ON COLUMN adf_forge_app.pipeline_templates.description IS 'what the template generates';
COMMENT ON COLUMN adf_forge_app.pipeline_templates.active IS 'FALSE hides it from Build';
COMMENT ON COLUMN adf_forge_app.pipeline_templates.document IS 'TemplateMetadata JSON (parameters, defaults, form schema)';
COMMENT ON COLUMN adf_forge_app.pipeline_templates.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.pipeline_templates.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.pipeline_definitions (
    id                      VARCHAR(64)  PRIMARY KEY,                                    -- opaque pipeline id
    name                    VARCHAR(128) NOT NULL,                                       -- logical pipeline name; default artifact names derive from it (PL_{name}_master/child)
    template_id             VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.pipeline_templates(id), -- generation strategy key
    project_id              VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.projects(id), -- owning project
    team_id                 VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.teams(id),    -- owning team, denormalized from the project for scope filtering
    state                   VARCHAR(32)  NOT NULL DEFAULT 'DRAFT',                       -- DRAFT | GENERATED | VALIDATED | READY_TO_PUSH | PUSHING | PUSHED | PR_CREATED | GENERATION_FAILED | VALIDATION_FAILED | GIT_PUSH_FAILED | PR_CREATION_FAILED
    spec_version            VARCHAR(32)  NOT NULL DEFAULT '1.0',                         -- LogicalPipelineSpec schema version of the stored spec
    latest_artifact_version VARCHAR(32),                                                 -- newest pipeline_versions.version; NULL until first generate
    draft                   JSONB        NOT NULL DEFAULT '{}',                          -- SpecDraft (Build form state, camelCase); returned to the wizard for edit mode
    spec                    JSONB        NOT NULL DEFAULT '{}',                          -- last built LogicalPipelineSpec (§23.3)
    created_by              VARCHAR(64),                                                 -- users.id of the creator
    updated_by              VARCHAR(64),                                                 -- users.id of the last editor of name/template/draft (migration 0006)
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),                         -- row creation time (UTC)
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT now()                          -- last edit time (UTC); also the optimistic-concurrency token (expectedUpdatedAt -> 409)
);
COMMENT ON TABLE adf_forge_app.pipeline_definitions IS 'One logical pipeline (Build form result). Holds the current draft + last built spec; each generate creates a pipeline_versions row. Migration 0001 (+0006, +0009 indexes).';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.id IS 'opaque pipeline id';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.name IS 'logical pipeline name; default artifact names derive from it (PL_{name}_master/child)';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.template_id IS 'generation strategy key';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.project_id IS 'owning project';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.team_id IS 'owning team, denormalized from the project for scope filtering';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.state IS 'DRAFT | GENERATED | VALIDATED | READY_TO_PUSH | PUSHING | PUSHED | PR_CREATED | GENERATION_FAILED | VALIDATION_FAILED | GIT_PUSH_FAILED | PR_CREATION_FAILED';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.spec_version IS 'LogicalPipelineSpec schema version of the stored spec';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.latest_artifact_version IS 'newest pipeline_versions.version; NULL until first generate';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.draft IS 'SpecDraft (Build form state, camelCase); returned to the wizard for edit mode';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.spec IS 'last built LogicalPipelineSpec (§23.3)';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.created_by IS 'users.id of the creator';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.updated_by IS 'users.id of the last editor of name/template/draft (migration 0006)';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.pipeline_definitions.updated_at IS 'last edit time (UTC); also the optimistic-concurrency token (expectedUpdatedAt -> 409)';

CREATE TABLE adf_forge_app.pipeline_versions (
    id                      VARCHAR(64) PRIMARY KEY,                                      -- opaque version id
    pipeline_id             VARCHAR(64) NOT NULL REFERENCES adf_forge_app.pipeline_definitions(id), -- parent pipeline
    version                 VARCHAR(32) NOT NULL,                                         -- artifact version label (e.g. 1.0.3), unique per pipeline
    template_version        VARCHAR(32) NOT NULL,                                         -- pipeline_templates.version used for this generation
    spec                    JSONB       NOT NULL,                                         -- LogicalPipelineSpec snapshot this version was generated from
    manifest                JSONB,                                                        -- manifest.json: [{name, type, layer}, ...] in §23.9 deployment order
    created_by              VARCHAR(64),                                                  -- users.id who triggered the generate
    target_environment_name VARCHAR(32),                                                  -- environments.name this version was generated for (migration 0007); not an FK so history survives a rename/delete
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),                           -- generation time (UTC)
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),                           -- TimestampMixin; never changes after insert (rows are immutable)
    CONSTRAINT uq_pipeline_versions_pipeline_version UNIQUE (pipeline_id, version)
);
COMMENT ON TABLE adf_forge_app.pipeline_versions IS 'Immutable generation record: regeneration inserts a new row (§23.5). Migration 0001 (+0007).';
COMMENT ON COLUMN adf_forge_app.pipeline_versions.id IS 'opaque version id';
COMMENT ON COLUMN adf_forge_app.pipeline_versions.pipeline_id IS 'parent pipeline';
COMMENT ON COLUMN adf_forge_app.pipeline_versions.version IS 'artifact version label (e.g. 1.0.3), unique per pipeline';
COMMENT ON COLUMN adf_forge_app.pipeline_versions.template_version IS 'pipeline_templates.version used for this generation';
COMMENT ON COLUMN adf_forge_app.pipeline_versions.spec IS 'LogicalPipelineSpec snapshot this version was generated from';
COMMENT ON COLUMN adf_forge_app.pipeline_versions.manifest IS 'manifest.json: [{name, type, layer}, ...] in §23.9 deployment order';
COMMENT ON COLUMN adf_forge_app.pipeline_versions.created_by IS 'users.id who triggered the generate';
COMMENT ON COLUMN adf_forge_app.pipeline_versions.target_environment_name IS 'environments.name this version was generated for (migration 0007); not an FK so history survives a rename/delete';
COMMENT ON COLUMN adf_forge_app.pipeline_versions.created_at IS 'generation time (UTC)';
COMMENT ON COLUMN adf_forge_app.pipeline_versions.updated_at IS 'TimestampMixin; never changes after insert (rows are immutable)';

CREATE TABLE adf_forge_app.artifacts (
    id              VARCHAR(64)  PRIMARY KEY,                                               -- opaque artifact id; git_push_requests.artifact_ids lists these
    pipeline_id     VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.pipeline_definitions(id), -- generating pipeline
    pipeline_name   VARCHAR(128) NOT NULL,                                                  -- pipeline name at generation time (Catalog filter/search)
    file_name       VARCHAR(256) NOT NULL,                                                  -- file name, e.g. PL_orders_master.json, adf_control_table.sql
    file_type       VARCHAR(16)  NOT NULL DEFAULT 'json',                                   -- json | sql | config
    artifact_type   VARCHAR(32)  NOT NULL,                                                  -- pipeline | dataset | linkedService | credential | trigger | factory | parameter (SQL, parameters files, manifest.json, lineage.json)
    layer           VARCHAR(32)  NOT NULL,                                                  -- §23.9 order: infrastructure | connection | data | pipeline | runtime | metadata
    version         VARCHAR(32)  NOT NULL,                                                  -- pipeline_versions.version this file belongs to
    state           VARCHAR(16)  NOT NULL DEFAULT 'generated',                              -- generated | validated | committed
    bytes           INTEGER      NOT NULL DEFAULT 0,                                        -- content size in bytes
    content         TEXT         NOT NULL DEFAULT '',                                       -- full file text as generated
    repository_path VARCHAR(512),                                                           -- path in the Git repo once pushed; NULL before push
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),                                    -- generation time (UTC)
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),                                    -- last state change (UTC)
    CONSTRAINT uq_artifacts_pipeline_file_version UNIQUE (pipeline_id, file_name, version)
);
COMMENT ON TABLE adf_forge_app.artifacts IS 'One generated file per (pipeline, version): pipeline/dataset/linked service/trigger JSON, SQL, manifest. Cached copy for review and diff; Git holds the canonical file. Migration 0001 (+0009 index).';
COMMENT ON COLUMN adf_forge_app.artifacts.id IS 'opaque artifact id; git_push_requests.artifact_ids lists these';
COMMENT ON COLUMN adf_forge_app.artifacts.pipeline_id IS 'generating pipeline';
COMMENT ON COLUMN adf_forge_app.artifacts.pipeline_name IS 'pipeline name at generation time (Catalog filter/search)';
COMMENT ON COLUMN adf_forge_app.artifacts.file_name IS 'file name, e.g. PL_orders_master.json, adf_control_table.sql';
COMMENT ON COLUMN adf_forge_app.artifacts.file_type IS 'json | sql | config';
COMMENT ON COLUMN adf_forge_app.artifacts.artifact_type IS 'pipeline | dataset | linkedService | credential | trigger | factory | parameter (SQL, parameters files, manifest.json, lineage.json)';
COMMENT ON COLUMN adf_forge_app.artifacts.layer IS '§23.9 order: infrastructure | connection | data | pipeline | runtime | metadata';
COMMENT ON COLUMN adf_forge_app.artifacts.version IS 'pipeline_versions.version this file belongs to';
COMMENT ON COLUMN adf_forge_app.artifacts.state IS 'generated | validated | committed';
COMMENT ON COLUMN adf_forge_app.artifacts.bytes IS 'content size in bytes';
COMMENT ON COLUMN adf_forge_app.artifacts.content IS 'full file text as generated';
COMMENT ON COLUMN adf_forge_app.artifacts.repository_path IS 'path in the Git repo once pushed; NULL before push';
COMMENT ON COLUMN adf_forge_app.artifacts.created_at IS 'generation time (UTC)';
COMMENT ON COLUMN adf_forge_app.artifacts.updated_at IS 'last state change (UTC)';

CREATE TABLE adf_forge_app.connection_library (
    id              VARCHAR(64)  PRIMARY KEY,                          -- opaque connection id
    project_id      VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.projects(id), -- owning project
    team_id         VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.teams(id), -- owning team, denormalized for scope filtering
    name            VARCHAR(200) NOT NULL,                             -- human label; also the ADF linked service name
    role            VARCHAR(16)  NOT NULL DEFAULT 'source',            -- source | target | both
    connector_type  VARCHAR(64)  NOT NULL,                             -- connector registry key (sql_server, azure_sql_database, adls_gen2, snowflake, sftp, ...)
    description     TEXT         NOT NULL DEFAULT '',                  -- free-text notes
    draft           JSONB        NOT NULL,                             -- LinkedServiceDraft (connector, auth type, IR, Key Vault ref, credential) + source objects + dataset shape
    created_by      VARCHAR(64),                                       -- users.id of the creator
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),               -- row creation time (UTC)
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()                -- last modification time (UTC)
);
COMMENT ON TABLE adf_forge_app.connection_library IS 'Saved, reusable linked service + its object list + dataset customization (§24.6/§24.17), shared with the whole project (Delivery > Connections). Secrets appear only as Key Vault secret names. Migration 0005.';
COMMENT ON COLUMN adf_forge_app.connection_library.id IS 'opaque connection id';
COMMENT ON COLUMN adf_forge_app.connection_library.project_id IS 'owning project';
COMMENT ON COLUMN adf_forge_app.connection_library.team_id IS 'owning team, denormalized for scope filtering';
COMMENT ON COLUMN adf_forge_app.connection_library.name IS 'human label; also the ADF linked service name';
COMMENT ON COLUMN adf_forge_app.connection_library.role IS 'source | target | both';
COMMENT ON COLUMN adf_forge_app.connection_library.connector_type IS 'connector registry key (sql_server, azure_sql_database, adls_gen2, snowflake, sftp, ...)';
COMMENT ON COLUMN adf_forge_app.connection_library.description IS 'free-text notes';
COMMENT ON COLUMN adf_forge_app.connection_library.draft IS 'LinkedServiceDraft (connector, auth type, IR, Key Vault ref, credential) + source objects + dataset shape';
COMMENT ON COLUMN adf_forge_app.connection_library.created_by IS 'users.id of the creator';
COMMENT ON COLUMN adf_forge_app.connection_library.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.connection_library.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.managed_identities (
    id           VARCHAR(64)  PRIMARY KEY,                             -- opaque identity id
    team_id      VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.teams(id), -- owning team; the picker only lists the caller's team
    name         VARCHAR(200) NOT NULL,                                -- display name; becomes the generated credential artifact name; unique per team
    resource_id  VARCHAR(512) NOT NULL,                                -- ARM resource id of the UAMI (/subscriptions/.../userAssignedIdentities/...)
    client_id    VARCHAR(64),                                          -- UAMI client id, optional, for diagnostics only
    created_by   VARCHAR(64),                                          -- users.id of the registering admin
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),                  -- row creation time (UTC)
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),                  -- last modification time (UTC)
    CONSTRAINT uq_managed_identities_team_name UNIQUE (team_id, name)
);
COMMENT ON TABLE adf_forge_app.managed_identities IS 'Admin-curated pick-list of user-assigned managed identities (§32.5/§32.6). The ARM resource id is infrastructure provisioned out-of-band, not a secret. Migration 0013.';
COMMENT ON COLUMN adf_forge_app.managed_identities.id IS 'opaque identity id';
COMMENT ON COLUMN adf_forge_app.managed_identities.team_id IS 'owning team; the picker only lists the caller''s team';
COMMENT ON COLUMN adf_forge_app.managed_identities.name IS 'display name; becomes the generated credential artifact name; unique per team';
COMMENT ON COLUMN adf_forge_app.managed_identities.resource_id IS 'ARM resource id of the UAMI (/subscriptions/.../userAssignedIdentities/...)';
COMMENT ON COLUMN adf_forge_app.managed_identities.client_id IS 'UAMI client id, optional, for diagnostics only';
COMMENT ON COLUMN adf_forge_app.managed_identities.created_by IS 'users.id of the registering admin';
COMMENT ON COLUMN adf_forge_app.managed_identities.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.managed_identities.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.onboarding_objects (
    object_key              VARCHAR(256) NOT NULL,                             -- stable registry key; default <source_system>.<source_object>
    project_id              VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.projects(id), -- owning project
    version                 INTEGER      NOT NULL DEFAULT 1,                   -- optimistic-concurrency counter, +1 per update
    enabled                 BOOLEAN      NOT NULL DEFAULT TRUE,                -- FALSE = excluded from generated control-table seed rows
    batch_group             VARCHAR(128),                                      -- grouping label for batch/trigger scheduling
    owner                   VARCHAR(200),                                      -- accountable data owner (free text)
    data_classification     VARCHAR(16),                                       -- public | internal | confidential | restricted (migration 0015; feeds the security.pii scan)
    source_system           VARCHAR(128) NOT NULL,                             -- source system label (e.g. sap_ecc, crm); used in names and object_key
    source_type             VARCHAR(16)  NOT NULL DEFAULT 'table',             -- table | view | query | file
    source_schema           VARCHAR(128),                                      -- source schema/namespace; NULL for schema-less sources
    source_object           VARCHAR(256) NOT NULL,                             -- table/view/file name at the source
    source_query            TEXT,                                              -- custom SELECT when source_type = query
    target_type             VARCHAR(16)  NOT NULL DEFAULT 'table',             -- table | file
    target_schema           VARCHAR(128),                                      -- target schema when target_type = table
    target_object           VARCHAR(256),                                      -- target table when target_type = table
    target_path             VARCHAR(512),                                      -- target folder/container path when target_type = file
    target_file_name        VARCHAR(256),                                      -- target file name when target_type = file
    target_file_format      VARCHAR(32)  NOT NULL DEFAULT 'Parquet',           -- Parquet | DelimitedText | Json | Avro | Orc | ...
    write_behavior          VARCHAR(16)  DEFAULT 'append',                     -- append | overwrite | upsert | merge
    upsert_keys             VARCHAR(512),                                      -- pipe-separated key columns for upsert/merge, e.g. "id|updated_at"
    loading_behavior        VARCHAR(16)  NOT NULL DEFAULT 'FullLoad',          -- FullLoad | DeltaLoad
    watermark_column        VARCHAR(128),                                      -- incremental column when loading_behavior = DeltaLoad
    watermark_column_type   VARCHAR(16),                                       -- datetime | int | bigint | string
    notes                   TEXT         NOT NULL DEFAULT '',                  -- free-text notes
    provenance              VARCHAR(16),                                       -- where the row last came from: manual | upload | git-import (migration 0020); NULL = before tracking
    status                  VARCHAR(16)  NOT NULL DEFAULT 'draft',             -- draft | valid | invalid (validation result of the row)
    created_by              VARCHAR(64),                                       -- users.id of the creator
    updated_by              VARCHAR(64),                                       -- users.id of the last editor
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),               -- row creation time (UTC)
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),               -- last modification time (UTC)
    PRIMARY KEY (project_id, object_key)
);
COMMENT ON TABLE adf_forge_app.onboarding_objects IS 'One onboarded source object per (project, object_key), from CSV/Excel upload, manual grid, or Git import. Feeds Build''s object picker and the seed rows of control.adf_control_table (§2). Only the columns without "(planned)" exist today (migration 0011, +0015 data_classification, +0020 provenance); the rest are the §26.2 target catalogue.';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.object_key IS 'stable registry key; default <source_system>.<source_object>';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.project_id IS 'owning project';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.version IS 'optimistic-concurrency counter, +1 per update';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.enabled IS 'FALSE = excluded from generated control-table seed rows';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.batch_group IS 'grouping label for batch/trigger scheduling';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.owner IS 'accountable data owner (free text)';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.data_classification IS 'public | internal | confidential | restricted (migration 0015; feeds the security.pii scan)';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.source_system IS 'source system label (e.g. sap_ecc, crm); used in names and object_key';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.source_type IS 'table | view | query | file';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.source_schema IS 'source schema/namespace; NULL for schema-less sources';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.source_object IS 'table/view/file name at the source';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.source_query IS 'custom SELECT when source_type = query';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.target_type IS 'table | file';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.target_schema IS 'target schema when target_type = table';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.target_object IS 'target table when target_type = table';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.target_path IS 'target folder/container path when target_type = file';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.target_file_name IS 'target file name when target_type = file';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.target_file_format IS 'Parquet | DelimitedText | Json | Avro | Orc | ...';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.write_behavior IS 'append | overwrite | upsert | merge';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.upsert_keys IS 'pipe-separated key columns for upsert/merge, e.g. "id|updated_at"';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.loading_behavior IS 'FullLoad | DeltaLoad';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.watermark_column IS 'incremental column when loading_behavior = DeltaLoad';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.watermark_column_type IS 'datetime | int | bigint | string';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.notes IS 'free-text notes';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.provenance IS 'where the row last came from: manual | upload | git-import (migration 0020); NULL = before tracking';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.status IS 'draft | valid | invalid (validation result of the row)';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.created_by IS 'users.id of the creator';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.updated_by IS 'users.id of the last editor';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.onboarding_objects.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.dq_rules (
    id            VARCHAR(64)  PRIMARY KEY,                                                         -- opaque rule id
    project_id    VARCHAR(64)  NOT NULL,                                                            -- with object_key: the onboarding_objects row this rule checks
    object_key    VARCHAR(256) NOT NULL,                                                            -- onboarding_objects.object_key
    check_type    VARCHAR(64)  NOT NULL,                                                            -- not_null | unique | range | row_count | freshness
    config        JSONB        NOT NULL DEFAULT '{}',                                               -- check parameters, e.g. {"column": "id"}, {"min": 0, "max": 100}, {"hours": 24}
    enforcement   VARCHAR(16)  NOT NULL DEFAULT 'warn',                                             -- off | warn | block (block emits an IfCondition -> Fail gate)
    created_by    VARCHAR(64),                                                                      -- users.id of the author
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),                                              -- row creation time (UTC)
    FOREIGN KEY (project_id, object_key) REFERENCES adf_forge_app.onboarding_objects(project_id, object_key)
);
COMMENT ON TABLE adf_forge_app.dq_rules IS 'Data-quality rule authored on a Bronze onboarding object (§30.4/§30.5). Build''s onboarding picker copies not_null/unique/range/row_count/freshness rules into SpecDraft.dataQualityRules, which the generator emits as pre-copy DQ_* activities. Silver/Gold rules live inline on silver/mart_objects. Migration 0014.';
COMMENT ON COLUMN adf_forge_app.dq_rules.id IS 'opaque rule id';
COMMENT ON COLUMN adf_forge_app.dq_rules.project_id IS 'with object_key: the onboarding_objects row this rule checks';
COMMENT ON COLUMN adf_forge_app.dq_rules.object_key IS 'onboarding_objects.object_key';
COMMENT ON COLUMN adf_forge_app.dq_rules.check_type IS 'not_null | unique | range | row_count | freshness';
COMMENT ON COLUMN adf_forge_app.dq_rules.config IS 'check parameters, e.g. {"column": "id"}, {"min": 0, "max": 100}, {"hours": 24}';
COMMENT ON COLUMN adf_forge_app.dq_rules.enforcement IS 'off | warn | block (block emits an IfCondition -> Fail gate)';
COMMENT ON COLUMN adf_forge_app.dq_rules.created_by IS 'users.id of the author';
COMMENT ON COLUMN adf_forge_app.dq_rules.created_at IS 'row creation time (UTC)';

CREATE TABLE adf_forge_app.onboarding_imports (
    id                 VARCHAR(64)  PRIMARY KEY,                          -- opaque import id
    project_id         VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.projects(id), -- owning project
    file_name          VARCHAR(256) NOT NULL,                             -- uploaded file name (.csv / .json / .xlsx)
    file_hash          VARCHAR(128) NOT NULL,                             -- server-computed SHA-256 of the committed rows
    row_total          INTEGER      NOT NULL DEFAULT 0,                   -- rows in the upload
    row_valid          INTEGER      NOT NULL DEFAULT 0,                   -- rows that passed validation and were committed
    row_error          INTEGER      NOT NULL DEFAULT 0,                   -- rows rejected by validation
    mode               VARCHAR(16)  NOT NULL DEFAULT 'upsert',            -- commit mode; upsert-by-object_key (additive, never a destructive replace)
    mapping_profile_id VARCHAR(64),                                       -- onboarding_mapping_profiles.id applied on the Map step; no FK so history survives a profile delete
    committed_by       VARCHAR(64),                                       -- users.id who committed
    committed_at       TIMESTAMPTZ,                                       -- commit time (UTC)
    report             JSONB        NOT NULL DEFAULT '{}',                -- per-row validation result shown in import history
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()                -- row creation time (UTC)
);
COMMENT ON TABLE adf_forge_app.onboarding_imports IS 'One row per committed Bronze bulk upload (§26.8). The file is parsed in the browser; only the parsed rows reach the server. Migration 0016.';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.id IS 'opaque import id';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.project_id IS 'owning project';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.file_name IS 'uploaded file name (.csv / .json / .xlsx)';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.file_hash IS 'server-computed SHA-256 of the committed rows';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.row_total IS 'rows in the upload';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.row_valid IS 'rows that passed validation and were committed';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.row_error IS 'rows rejected by validation';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.mode IS 'commit mode; upsert-by-object_key (additive, never a destructive replace)';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.mapping_profile_id IS 'onboarding_mapping_profiles.id applied on the Map step; no FK so history survives a profile delete';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.committed_by IS 'users.id who committed';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.committed_at IS 'commit time (UTC)';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.report IS 'per-row validation result shown in import history';
COMMENT ON COLUMN adf_forge_app.onboarding_imports.created_at IS 'row creation time (UTC)';

CREATE TABLE adf_forge_app.onboarding_mapping_profiles (
    id            VARCHAR(64)  PRIMARY KEY,                                           -- opaque profile id
    project_id    VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.projects(id),        -- owning project
    name          VARCHAR(128) NOT NULL,                                              -- profile name, unique per project (upserted case-insensitively)
    header_map    JSONB        NOT NULL DEFAULT '{}',                                 -- {"uploaded header": "onboarding_objects column"}
    updated_by    VARCHAR(64),                                                        -- users.id of the last editor
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),                                -- row creation time (UTC)
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),                                -- last modification time (UTC)
    CONSTRAINT uq_onboarding_mapping_profiles_project_name UNIQUE (project_id, name)
);
COMMENT ON TABLE adf_forge_app.onboarding_mapping_profiles IS 'Named, reusable upload-header -> registry-column map for the §26.3 Map step. Migration 0017.';
COMMENT ON COLUMN adf_forge_app.onboarding_mapping_profiles.id IS 'opaque profile id';
COMMENT ON COLUMN adf_forge_app.onboarding_mapping_profiles.project_id IS 'owning project';
COMMENT ON COLUMN adf_forge_app.onboarding_mapping_profiles.name IS 'profile name, unique per project (upserted case-insensitively)';
COMMENT ON COLUMN adf_forge_app.onboarding_mapping_profiles.header_map IS '{"uploaded header": "onboarding_objects column"}';
COMMENT ON COLUMN adf_forge_app.onboarding_mapping_profiles.updated_by IS 'users.id of the last editor';
COMMENT ON COLUMN adf_forge_app.onboarding_mapping_profiles.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.onboarding_mapping_profiles.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.onboarding_syncs (
    id             VARCHAR(64)  PRIMARY KEY,                          -- opaque sync id
    project_id     VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.projects(id), -- owning project
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
COMMENT ON TABLE adf_forge_app.onboarding_syncs IS 'One "Sync to Git" of a project''s Bronze registry (§26.5): control-table SQL + metadata/objects.csv (+ registry lineage) pushed to a feature branch, then a PR. Migration 0019.';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.id IS 'opaque sync id';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.project_id IS 'owning project';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.provider IS 'github | gitlab | azure_repos | custom';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.repository_url IS 'target repository URL';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.feature_branch IS 'branch pushed to; never the protected base branch';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.base_branch IS 'PR target, normally dev';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.commit_message IS 'commit message used';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.file_paths IS 'files written: <root>/metadata/{adf_control_table.sql, objects.csv, registry-lineage.json}';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.snapshot IS '{object_key: row as pushed}; base for the next sync''s per-row diff';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.status IS 'pending | pushed | pr_created | failed';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.commit_sha IS 'resulting commit sha';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.pr_number IS 'provider PR/MR number';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.pr_url IS 'PR/MR web URL';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.simulated IS 'TRUE = no Git credential resolved; offline SimulatedAdapter used';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.error IS 'failure message when status = failed';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.created_by IS 'users.id who started the sync';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.onboarding_syncs.updated_at IS 'last status change (UTC)';

CREATE TABLE adf_forge_app.sttm_imports (
    id             VARCHAR(64)  PRIMARY KEY,                          -- opaque import id
    project_id     VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.projects(id), -- owning project
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
COMMENT ON TABLE adf_forge_app.sttm_imports IS 'One uploaded STTM workbook (Silver or Gold), multi-sheet. Created before the registries that FK to it.';
COMMENT ON COLUMN adf_forge_app.sttm_imports.id IS 'opaque import id';
COMMENT ON COLUMN adf_forge_app.sttm_imports.project_id IS 'owning project';
COMMENT ON COLUMN adf_forge_app.sttm_imports.layer IS 'silver | gold';
COMMENT ON COLUMN adf_forge_app.sttm_imports.file_name IS 'uploaded .xlsx name';
COMMENT ON COLUMN adf_forge_app.sttm_imports.file_hash IS 'SHA-256 of the uploaded file';
COMMENT ON COLUMN adf_forge_app.sttm_imports.sheet_total IS 'object sheets found (max 200, else 422 workbook_too_large)';
COMMENT ON COLUMN adf_forge_app.sttm_imports.sheet_valid IS 'sheets that parsed and validated';
COMMENT ON COLUMN adf_forge_app.sttm_imports.sheet_error IS 'sheets with blocking errors';
COMMENT ON COLUMN adf_forge_app.sttm_imports.mode IS 'upsert only (additive)';
COMMENT ON COLUMN adf_forge_app.sttm_imports.committed_by IS 'users.id who committed';
COMMENT ON COLUMN adf_forge_app.sttm_imports.committed_at IS 'commit time; NULL = previewed, not committed';
COMMENT ON COLUMN adf_forge_app.sttm_imports.report IS 'per-sheet parse/validation result incl. existingVersion for concurrency checks';
COMMENT ON COLUMN adf_forge_app.sttm_imports.created_at IS 'upload time (UTC)';

CREATE TABLE adf_forge_app.silver_objects (
    object_key               VARCHAR(256) NOT NULL,                             -- registry key; defaults to curated_entity
    project_id               VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.projects(id), -- owning project
    version                  INTEGER      NOT NULL DEFAULT 1,                   -- optimistic-concurrency counter (expectedVersion -> 409 version_conflict)
    enabled                  BOOLEAN      NOT NULL DEFAULT TRUE,                -- FALSE hides it from the Build picker
    tag                      VARCHAR(128) NOT NULL,                             -- mandatory classification tag; filter in Build/Onboarding
    curated_entity           VARCHAR(128) NOT NULL,                             -- entity name; unique per project; feeds target table name
    source_object_keys       JSONB        NOT NULL DEFAULT '[]',                -- onboarding_objects.object_key values this entity reads from
    owner                    VARCHAR(200),                                      -- accountable owner (free text)
    batch_group              VARCHAR(128),                                      -- scheduling group label
    domain                   VARCHAR(128),                                      -- business domain (filter)
    write_behavior           VARCHAR(16)  NOT NULL DEFAULT 'append',            -- append | overwrite | upsert | merge
    loading_behavior         VARCHAR(16)  NOT NULL DEFAULT 'FullLoad',          -- FullLoad | DeltaLoad
    watermark_column         VARCHAR(128),                                      -- incremental column for DeltaLoad
    watermark_column_type    VARCHAR(16),                                       -- datetime | int | bigint | string
    business_keys            JSONB        NOT NULL DEFAULT '[]',                -- natural key columns
    transformations          JSONB        NOT NULL DEFAULT '[]',                -- TransformationRule[] (max 10): conform_columns | dedupe | cast_schema | scd2 | join
    data_quality_rules       JSONB        NOT NULL DEFAULT '[]',                -- SpecDraft.dataQualityRules shape (max 5)
    target_table             VARCHAR(256) NOT NULL,                             -- computed <silver prefix>.<entity> (default curated.<entity>); recomputed on every write
    data_classification      VARCHAR(16),                                       -- public | internal | confidential | restricted
    contains_pii             BOOLEAN,                                           -- PII_Flag (migration 0022; null = not stated)
    contains_phi             BOOLEAN,                                           -- PHI_Flag (migration 0022; null = not stated)
    contains_pci             BOOLEAN,                                           -- PCI_Flag (migration 0022; null = not stated)
    critical_data_element    BOOLEAN,                                           -- Critical_Data_Element (migration 0022)
    audit_required           BOOLEAN,                                           -- Audit_Required (migration 0022)
    retention_period         VARCHAR(64),                                       -- Retention_Period, free text e.g. "7y" (migration 0022)
    regulatory_tag           VARCHAR(128),                                      -- Regulatory_Tag e.g. GDPR (migration 0022)
    business_criticality     VARCHAR(32),                                       -- Business_Criticality (migration 0022)
    notes                    TEXT         NOT NULL DEFAULT '',                  -- free-text notes
    provenance_source        VARCHAR(16)  NOT NULL DEFAULT 'manual',            -- upload | manual
    import_id                VARCHAR(64)  REFERENCES adf_forge_app.sttm_imports(id), -- workbook that last wrote this row; NULL for manual
    status                   VARCHAR(16)  NOT NULL DEFAULT 'draft',             -- draft | valid | invalid
    created_by               VARCHAR(64),                                       -- users.id of the creator
    updated_by               VARCHAR(64),                                       -- users.id of the last editor
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),               -- row creation time (UTC)
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),               -- last modification time (UTC)
    PRIMARY KEY (project_id, object_key),
    CONSTRAINT uq_silver_objects_entity UNIQUE (project_id, curated_entity)
);
COMMENT ON TABLE adf_forge_app.silver_objects IS 'One curated (Silver) entity, from STTM upload or manual entry.';
COMMENT ON COLUMN adf_forge_app.silver_objects.object_key IS 'registry key; defaults to curated_entity';
COMMENT ON COLUMN adf_forge_app.silver_objects.project_id IS 'owning project';
COMMENT ON COLUMN adf_forge_app.silver_objects.version IS 'optimistic-concurrency counter (expectedVersion -> 409 version_conflict)';
COMMENT ON COLUMN adf_forge_app.silver_objects.enabled IS 'FALSE hides it from the Build picker';
COMMENT ON COLUMN adf_forge_app.silver_objects.tag IS 'mandatory classification tag; filter in Build/Onboarding';
COMMENT ON COLUMN adf_forge_app.silver_objects.curated_entity IS 'entity name; unique per project; feeds target table name';
COMMENT ON COLUMN adf_forge_app.silver_objects.source_object_keys IS 'onboarding_objects.object_key values this entity reads from';
COMMENT ON COLUMN adf_forge_app.silver_objects.owner IS 'accountable owner (free text)';
COMMENT ON COLUMN adf_forge_app.silver_objects.batch_group IS 'scheduling group label';
COMMENT ON COLUMN adf_forge_app.silver_objects.domain IS 'business domain (filter)';
COMMENT ON COLUMN adf_forge_app.silver_objects.write_behavior IS 'append | overwrite | upsert | merge';
COMMENT ON COLUMN adf_forge_app.silver_objects.loading_behavior IS 'FullLoad | DeltaLoad';
COMMENT ON COLUMN adf_forge_app.silver_objects.watermark_column IS 'incremental column for DeltaLoad';
COMMENT ON COLUMN adf_forge_app.silver_objects.watermark_column_type IS 'datetime | int | bigint | string';
COMMENT ON COLUMN adf_forge_app.silver_objects.business_keys IS 'natural key columns';
COMMENT ON COLUMN adf_forge_app.silver_objects.transformations IS 'TransformationRule[] (max 10): conform_columns | dedupe | cast_schema | scd2 | join';
COMMENT ON COLUMN adf_forge_app.silver_objects.data_quality_rules IS 'SpecDraft.dataQualityRules shape (max 5)';
COMMENT ON COLUMN adf_forge_app.silver_objects.target_table IS 'computed <silver prefix>.<entity> (default curated.<entity>); recomputed on every write';
COMMENT ON COLUMN adf_forge_app.silver_objects.data_classification IS 'public | internal | confidential | restricted';
COMMENT ON COLUMN adf_forge_app.silver_objects.contains_pii IS 'PII_Flag (migration 0022; null = not stated)';
COMMENT ON COLUMN adf_forge_app.silver_objects.contains_phi IS 'PHI_Flag (migration 0022; null = not stated)';
COMMENT ON COLUMN adf_forge_app.silver_objects.contains_pci IS 'PCI_Flag (migration 0022; null = not stated)';
COMMENT ON COLUMN adf_forge_app.silver_objects.critical_data_element IS 'Critical_Data_Element (migration 0022)';
COMMENT ON COLUMN adf_forge_app.silver_objects.audit_required IS 'Audit_Required (migration 0022)';
COMMENT ON COLUMN adf_forge_app.silver_objects.retention_period IS 'Retention_Period, free text e.g. "7y" (migration 0022)';
COMMENT ON COLUMN adf_forge_app.silver_objects.regulatory_tag IS 'Regulatory_Tag e.g. GDPR (migration 0022)';
COMMENT ON COLUMN adf_forge_app.silver_objects.business_criticality IS 'Business_Criticality (migration 0022)';
COMMENT ON COLUMN adf_forge_app.silver_objects.notes IS 'free-text notes';
COMMENT ON COLUMN adf_forge_app.silver_objects.provenance_source IS 'upload | manual';
COMMENT ON COLUMN adf_forge_app.silver_objects.import_id IS 'workbook that last wrote this row; NULL for manual';
COMMENT ON COLUMN adf_forge_app.silver_objects.status IS 'draft | valid | invalid';
COMMENT ON COLUMN adf_forge_app.silver_objects.created_by IS 'users.id of the creator';
COMMENT ON COLUMN adf_forge_app.silver_objects.updated_by IS 'users.id of the last editor';
COMMENT ON COLUMN adf_forge_app.silver_objects.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.silver_objects.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.mart_objects (
    object_key               VARCHAR(256) NOT NULL,                                     -- registry key
    project_id               VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.projects(id), -- owning project
    version                  INTEGER      NOT NULL DEFAULT 1,                           -- optimistic-concurrency counter
    enabled                  BOOLEAN      NOT NULL DEFAULT TRUE,                        -- FALSE hides it from the Build picker
    tag                      VARCHAR(128) NOT NULL,                                     -- mandatory classification tag
    mart_domain              VARCHAR(128) NOT NULL,                                     -- mart domain; part of the target table name
    curated_entity           VARCHAR(128) NOT NULL,                                     -- mart entity name; unique per (project, mart_domain)
    source_object_keys       JSONB        NOT NULL DEFAULT '[]',                        -- silver_objects.object_key values this mart reads from
    owner                    VARCHAR(200),                                              -- accountable owner (free text)
    batch_group              VARCHAR(128),                                              -- scheduling group label
    domain                   VARCHAR(128),                                              -- business domain (filter)
    write_behavior           VARCHAR(16)  NOT NULL DEFAULT 'append',                    -- append | overwrite | upsert | merge
    loading_behavior         VARCHAR(16)  NOT NULL DEFAULT 'FullLoad',                  -- FullLoad | DeltaLoad
    watermark_column         VARCHAR(128),                                              -- incremental column for DeltaLoad
    watermark_column_type    VARCHAR(16),                                               -- datetime | int | bigint | string
    business_keys            JSONB        NOT NULL DEFAULT '[]',                        -- natural key columns
    transformations          JSONB        NOT NULL DEFAULT '[]',                        -- TransformationRule[] (max 10): join | aggregate | select_columns | scd2
    data_quality_rules       JSONB        NOT NULL DEFAULT '[]',                        -- SpecDraft.dataQualityRules shape (max 5)
    target_table             VARCHAR(256) NOT NULL,                                     -- computed <gold prefix>.<domain>_<entity> (default mart.<domain>_<entity>)
    data_classification      VARCHAR(16),                                               -- public | internal | confidential | restricted
    contains_pii             BOOLEAN,                                                   -- PII_Flag (migration 0022; null = not stated)
    contains_phi             BOOLEAN,                                                   -- PHI_Flag (migration 0022; null = not stated)
    contains_pci             BOOLEAN,                                                   -- PCI_Flag (migration 0022; null = not stated)
    critical_data_element    BOOLEAN,                                                   -- Critical_Data_Element (migration 0022)
    audit_required           BOOLEAN,                                                   -- Audit_Required (migration 0022)
    retention_period         VARCHAR(64),                                               -- Retention_Period, free text e.g. "7y" (migration 0022)
    regulatory_tag           VARCHAR(128),                                              -- Regulatory_Tag e.g. GDPR (migration 0022)
    business_criticality     VARCHAR(32),                                               -- Business_Criticality (migration 0022)
    notes                    TEXT         NOT NULL DEFAULT '',                          -- free-text notes
    provenance_source        VARCHAR(16)  NOT NULL DEFAULT 'manual',                    -- upload | manual
    import_id                VARCHAR(64)  REFERENCES adf_forge_app.sttm_imports(id),    -- workbook that last wrote this row; NULL for manual
    status                   VARCHAR(16)  NOT NULL DEFAULT 'draft',                     -- draft | valid | invalid
    created_by               VARCHAR(64),                                               -- users.id of the creator
    updated_by               VARCHAR(64),                                               -- users.id of the last editor
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),                       -- row creation time (UTC)
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),                       -- last modification time (UTC)
    PRIMARY KEY (project_id, object_key),
    CONSTRAINT uq_mart_objects_entity UNIQUE (project_id, mart_domain, curated_entity)
);
COMMENT ON TABLE adf_forge_app.mart_objects IS 'One business mart (Gold) entity; same shape as silver_objects plus mart_domain, and its sources are Silver keys.';
COMMENT ON COLUMN adf_forge_app.mart_objects.object_key IS 'registry key';
COMMENT ON COLUMN adf_forge_app.mart_objects.project_id IS 'owning project';
COMMENT ON COLUMN adf_forge_app.mart_objects.version IS 'optimistic-concurrency counter';
COMMENT ON COLUMN adf_forge_app.mart_objects.enabled IS 'FALSE hides it from the Build picker';
COMMENT ON COLUMN adf_forge_app.mart_objects.tag IS 'mandatory classification tag';
COMMENT ON COLUMN adf_forge_app.mart_objects.mart_domain IS 'mart domain; part of the target table name';
COMMENT ON COLUMN adf_forge_app.mart_objects.curated_entity IS 'mart entity name; unique per (project, mart_domain)';
COMMENT ON COLUMN adf_forge_app.mart_objects.source_object_keys IS 'silver_objects.object_key values this mart reads from';
COMMENT ON COLUMN adf_forge_app.mart_objects.owner IS 'accountable owner (free text)';
COMMENT ON COLUMN adf_forge_app.mart_objects.batch_group IS 'scheduling group label';
COMMENT ON COLUMN adf_forge_app.mart_objects.domain IS 'business domain (filter)';
COMMENT ON COLUMN adf_forge_app.mart_objects.write_behavior IS 'append | overwrite | upsert | merge';
COMMENT ON COLUMN adf_forge_app.mart_objects.loading_behavior IS 'FullLoad | DeltaLoad';
COMMENT ON COLUMN adf_forge_app.mart_objects.watermark_column IS 'incremental column for DeltaLoad';
COMMENT ON COLUMN adf_forge_app.mart_objects.watermark_column_type IS 'datetime | int | bigint | string';
COMMENT ON COLUMN adf_forge_app.mart_objects.business_keys IS 'natural key columns';
COMMENT ON COLUMN adf_forge_app.mart_objects.transformations IS 'TransformationRule[] (max 10): join | aggregate | select_columns | scd2';
COMMENT ON COLUMN adf_forge_app.mart_objects.data_quality_rules IS 'SpecDraft.dataQualityRules shape (max 5)';
COMMENT ON COLUMN adf_forge_app.mart_objects.target_table IS 'computed <gold prefix>.<domain>_<entity> (default mart.<domain>_<entity>)';
COMMENT ON COLUMN adf_forge_app.mart_objects.data_classification IS 'public | internal | confidential | restricted';
COMMENT ON COLUMN adf_forge_app.mart_objects.contains_pii IS 'PII_Flag (migration 0022; null = not stated)';
COMMENT ON COLUMN adf_forge_app.mart_objects.contains_phi IS 'PHI_Flag (migration 0022; null = not stated)';
COMMENT ON COLUMN adf_forge_app.mart_objects.contains_pci IS 'PCI_Flag (migration 0022; null = not stated)';
COMMENT ON COLUMN adf_forge_app.mart_objects.critical_data_element IS 'Critical_Data_Element (migration 0022)';
COMMENT ON COLUMN adf_forge_app.mart_objects.audit_required IS 'Audit_Required (migration 0022)';
COMMENT ON COLUMN adf_forge_app.mart_objects.retention_period IS 'Retention_Period, free text e.g. "7y" (migration 0022)';
COMMENT ON COLUMN adf_forge_app.mart_objects.regulatory_tag IS 'Regulatory_Tag e.g. GDPR (migration 0022)';
COMMENT ON COLUMN adf_forge_app.mart_objects.business_criticality IS 'Business_Criticality (migration 0022)';
COMMENT ON COLUMN adf_forge_app.mart_objects.notes IS 'free-text notes';
COMMENT ON COLUMN adf_forge_app.mart_objects.provenance_source IS 'upload | manual';
COMMENT ON COLUMN adf_forge_app.mart_objects.import_id IS 'workbook that last wrote this row; NULL for manual';
COMMENT ON COLUMN adf_forge_app.mart_objects.status IS 'draft | valid | invalid';
COMMENT ON COLUMN adf_forge_app.mart_objects.created_by IS 'users.id of the creator';
COMMENT ON COLUMN adf_forge_app.mart_objects.updated_by IS 'users.id of the last editor';
COMMENT ON COLUMN adf_forge_app.mart_objects.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.mart_objects.updated_at IS 'last modification time (UTC)';

CREATE TABLE adf_forge_app.git_push_requests (
    id              VARCHAR(64)  PRIMARY KEY,                                      -- opaque push id
    pipeline_id     VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.pipeline_definitions(id), -- pipeline whose artifacts were pushed
    provider        VARCHAR(32)  NOT NULL,                                         -- github | gitlab | azure_repos | custom
    repository_url  VARCHAR(512) NOT NULL,                                         -- target repository URL
    feature_branch  VARCHAR(256) NOT NULL,                                         -- branch pushed to; rejected if equal to base_branch
    base_branch     VARCHAR(256) NOT NULL DEFAULT 'dev',                           -- PR target; never pushed to directly
    artifact_ids    JSONB        NOT NULL DEFAULT '[]',                            -- artifacts.id values included in the commit
    commit_message  TEXT         NOT NULL DEFAULT '',                              -- commit message used
    commit_sha      VARCHAR(64),                                                   -- resulting commit sha; NULL until pushed
    status          VARCHAR(32)  NOT NULL DEFAULT 'pending',                       -- pending | pushed | pr_created | failed
    error           TEXT,                                                          -- failure message when status = failed
    created_by      VARCHAR(64),                                                   -- users.id who requested the push
    simulated       BOOLEAN      NOT NULL DEFAULT FALSE,                           -- TRUE = no Git credential resolved; offline SimulatedAdapter used (shown as a badge)
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),                           -- request time (UTC)
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()                            -- last status change (UTC)
);
COMMENT ON TABLE adf_forge_app.git_push_requests IS 'One push of selected generated artifacts to a feature branch (§8, §9). Migration 0001 (+0002).';
COMMENT ON COLUMN adf_forge_app.git_push_requests.id IS 'opaque push id';
COMMENT ON COLUMN adf_forge_app.git_push_requests.pipeline_id IS 'pipeline whose artifacts were pushed';
COMMENT ON COLUMN adf_forge_app.git_push_requests.provider IS 'github | gitlab | azure_repos | custom';
COMMENT ON COLUMN adf_forge_app.git_push_requests.repository_url IS 'target repository URL';
COMMENT ON COLUMN adf_forge_app.git_push_requests.feature_branch IS 'branch pushed to; rejected if equal to base_branch';
COMMENT ON COLUMN adf_forge_app.git_push_requests.base_branch IS 'PR target; never pushed to directly';
COMMENT ON COLUMN adf_forge_app.git_push_requests.artifact_ids IS 'artifacts.id values included in the commit';
COMMENT ON COLUMN adf_forge_app.git_push_requests.commit_message IS 'commit message used';
COMMENT ON COLUMN adf_forge_app.git_push_requests.commit_sha IS 'resulting commit sha; NULL until pushed';
COMMENT ON COLUMN adf_forge_app.git_push_requests.status IS 'pending | pushed | pr_created | failed';
COMMENT ON COLUMN adf_forge_app.git_push_requests.error IS 'failure message when status = failed';
COMMENT ON COLUMN adf_forge_app.git_push_requests.created_by IS 'users.id who requested the push';
COMMENT ON COLUMN adf_forge_app.git_push_requests.simulated IS 'TRUE = no Git credential resolved; offline SimulatedAdapter used (shown as a badge)';
COMMENT ON COLUMN adf_forge_app.git_push_requests.created_at IS 'request time (UTC)';
COMMENT ON COLUMN adf_forge_app.git_push_requests.updated_at IS 'last status change (UTC)';

CREATE TABLE adf_forge_app.git_pull_requests (
    id              VARCHAR(64)  PRIMARY KEY,                                   -- opaque PR record id
    push_request_id VARCHAR(64)  NOT NULL REFERENCES adf_forge_app.git_push_requests(id), -- push this PR was opened from
    number          INTEGER,                                                    -- provider PR/MR number
    url             VARCHAR(512),                                               -- PR/MR web URL
    title           TEXT         NOT NULL DEFAULT '',                           -- PR title
    source_branch   VARCHAR(256) NOT NULL,                                      -- feature branch
    target_branch   VARCHAR(256) NOT NULL,                                      -- base branch (dev)
    simulated       BOOLEAN      NOT NULL DEFAULT FALSE,                        -- TRUE = PR step used the offline SimulatedAdapter
    status          VARCHAR(32)  NOT NULL DEFAULT 'open',                       -- open (set at creation; merge/close is not tracked back from the provider)
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),                        -- row creation time (UTC)
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()                         -- last status change (UTC)
);
COMMENT ON TABLE adf_forge_app.git_pull_requests IS 'The PR opened from a push''s feature branch into the base branch. Migration 0001 (+0002).';
COMMENT ON COLUMN adf_forge_app.git_pull_requests.id IS 'opaque PR record id';
COMMENT ON COLUMN adf_forge_app.git_pull_requests.push_request_id IS 'push this PR was opened from';
COMMENT ON COLUMN adf_forge_app.git_pull_requests.number IS 'provider PR/MR number';
COMMENT ON COLUMN adf_forge_app.git_pull_requests.url IS 'PR/MR web URL';
COMMENT ON COLUMN adf_forge_app.git_pull_requests.title IS 'PR title';
COMMENT ON COLUMN adf_forge_app.git_pull_requests.source_branch IS 'feature branch';
COMMENT ON COLUMN adf_forge_app.git_pull_requests.target_branch IS 'base branch (dev)';
COMMENT ON COLUMN adf_forge_app.git_pull_requests.simulated IS 'TRUE = PR step used the offline SimulatedAdapter';
COMMENT ON COLUMN adf_forge_app.git_pull_requests.status IS 'open (set at creation; merge/close is not tracked back from the provider)';
COMMENT ON COLUMN adf_forge_app.git_pull_requests.created_at IS 'row creation time (UTC)';
COMMENT ON COLUMN adf_forge_app.git_pull_requests.updated_at IS 'last status change (UTC)';

CREATE TABLE adf_forge_app.git_catalog_entries (
    project_id    VARCHAR(64)   NOT NULL REFERENCES adf_forge_app.projects(id), -- project whose Git target was indexed
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
COMMENT ON TABLE adf_forge_app.git_catalog_entries IS '§24.8 Git catalog index: one row per indexed repo file per branch, rewritten wholesale by the worker''s catalog_refresh job. Tool-owned vs foreign is resolved at read time, not stored. Migration 0020.';
COMMENT ON COLUMN adf_forge_app.git_catalog_entries.project_id IS 'project whose Git target was indexed';
COMMENT ON COLUMN adf_forge_app.git_catalog_entries.branch IS 'branch indexed';
COMMENT ON COLUMN adf_forge_app.git_catalog_entries.path IS 'repo path, e.g. adf/pipeline/PL_orders_master.json';
COMMENT ON COLUMN adf_forge_app.git_catalog_entries.artifact_type IS 'ADF folder type (pipeline, dataset, linkedService, trigger, ...) or metadata';
COMMENT ON COLUMN adf_forge_app.git_catalog_entries.name IS 'JSON "name" property, else the file stem';
COMMENT ON COLUMN adf_forge_app.git_catalog_entries.blob_sha IS 'Git blob id; optimistic-concurrency base for edits (§24.14)';
COMMENT ON COLUMN adf_forge_app.git_catalog_entries.content IS 'file text (<= 1 MB) for detail view, Open in Build, drift, Git import';
COMMENT ON COLUMN adf_forge_app.git_catalog_entries."references" IS '[{type, name}] references this file makes (LinkedServiceReference, DatasetReference, ...)';
COMMENT ON COLUMN adf_forge_app.git_catalog_entries.parameters IS 'names of properties.parameters';
COMMENT ON COLUMN adf_forge_app.git_catalog_entries.simulated IS 'TRUE = virtual repo built from simulated pushes (no credential)';
COMMENT ON COLUMN adf_forge_app.git_catalog_entries.indexed_at IS 'when this file was indexed (UTC)';

CREATE TABLE adf_forge_app.git_catalog_changes (
    id             VARCHAR(64)   PRIMARY KEY,                          -- opaque change id
    project_id     VARCHAR(64)   NOT NULL REFERENCES adf_forge_app.projects(id), -- owning project
    mode           VARCHAR(16)   NOT NULL,                             -- update | rename | delete
    path           VARCHAR(1024) NOT NULL,                             -- file being changed
    new_path       VARCHAR(1024),                                      -- destination path; rename only
    provider       VARCHAR(32)   NOT NULL,                             -- github | gitlab | azure_repos | custom
    repository_url VARCHAR(512)  NOT NULL,                             -- target repository URL
    feature_branch VARCHAR(256)  NOT NULL,                             -- branch the change is committed to
    base_branch    VARCHAR(256)  NOT NULL,                             -- PR target
    commit_message TEXT          NOT NULL DEFAULT '',                  -- commit message used
    files          JSON          NOT NULL DEFAULT '[]',                -- [{path, content}] the commit writes, incl. rewritten referrers
    delete_paths   JSON          NOT NULL DEFAULT '[]',                -- paths the commit removes
    base_blob_sha  VARCHAR(64),                                        -- blob sha as loaded; mismatch at push time -> status conflict
    status         VARCHAR(32)   NOT NULL DEFAULT 'pending',           -- pending | pushed | pr_created | failed | conflict
    commit_sha     VARCHAR(64),                                        -- resulting commit sha
    pr_number      INTEGER,                                            -- provider PR/MR number
    pr_url         VARCHAR(512),                                       -- PR/MR web URL
    simulated      BOOLEAN       NOT NULL DEFAULT false,               -- TRUE = offline SimulatedAdapter used
    error          TEXT,                                               -- failure message
    created_by     VARCHAR(64),                                        -- users.id who requested the change
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),               -- request time (UTC)
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now()                -- last status change (UTC)
);
COMMENT ON TABLE adf_forge_app.git_catalog_changes IS '§24.9/§24.10 change to files already in Git (raw update, rename, delete), pushed to a feature branch and PR''d. Separate from git_push_requests, which is keyed to one generated pipeline. Migration 0020.';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.id IS 'opaque change id';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.project_id IS 'owning project';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.mode IS 'update | rename | delete';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.path IS 'file being changed';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.new_path IS 'destination path; rename only';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.provider IS 'github | gitlab | azure_repos | custom';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.repository_url IS 'target repository URL';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.feature_branch IS 'branch the change is committed to';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.base_branch IS 'PR target';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.commit_message IS 'commit message used';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.files IS '[{path, content}] the commit writes, incl. rewritten referrers';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.delete_paths IS 'paths the commit removes';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.base_blob_sha IS 'blob sha as loaded; mismatch at push time -> status conflict';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.status IS 'pending | pushed | pr_created | failed | conflict';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.commit_sha IS 'resulting commit sha';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.pr_number IS 'provider PR/MR number';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.pr_url IS 'PR/MR web URL';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.simulated IS 'TRUE = offline SimulatedAdapter used';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.error IS 'failure message';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.created_by IS 'users.id who requested the change';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.created_at IS 'request time (UTC)';
COMMENT ON COLUMN adf_forge_app.git_catalog_changes.updated_at IS 'last status change (UTC)';

CREATE TABLE adf_forge_app.jobs (
    id           VARCHAR(64)  PRIMARY KEY,                             -- opaque job id
    type         VARCHAR(32)  NOT NULL,                                -- generate | validate | git_push | onboarding_sync | live_check | purview_push | catalog_refresh | catalog_change
    status       VARCHAR(16)  NOT NULL DEFAULT 'queued',               -- queued | running | succeeded | failed
    pipeline_id  VARCHAR(64)  REFERENCES adf_forge_app.pipeline_definitions(id), -- related pipeline; NULL for project-level jobs (sync, catalog, purview)
    payload      JSONB        NOT NULL DEFAULT '{}',                   -- job inputs (spec, template id, push target); gitToken is Fernet-encrypted at rest
    result       JSONB,                                                -- handler output (artifacts, validation issues, commit/PR info)
    error        TEXT,                                                 -- failure message when status = failed
    attempts     INTEGER      NOT NULL DEFAULT 0,                      -- times the job has been claimed
    claimed_by   VARCHAR(64),                                          -- worker instance id that claimed it
    started_at   TIMESTAMPTZ,                                          -- claim time (UTC)
    finished_at  TIMESTAMPTZ,                                          -- completion time (UTC)
    created_by   VARCHAR(64),                                          -- users.id who enqueued it
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),                  -- enqueue time (UTC)
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()                   -- last status change (UTC)
);
COMMENT ON TABLE adf_forge_app.jobs IS 'Unit of work for the stateless worker, which claims queued rows with FOR UPDATE SKIP LOCKED (worker/app/jobs/loop.py). The payload carries inputs, never mutable UI state. Migration 0001.';
COMMENT ON COLUMN adf_forge_app.jobs.id IS 'opaque job id';
COMMENT ON COLUMN adf_forge_app.jobs.type IS 'generate | validate | git_push | onboarding_sync | live_check | purview_push | catalog_refresh | catalog_change';
COMMENT ON COLUMN adf_forge_app.jobs.status IS 'queued | running | succeeded | failed';
COMMENT ON COLUMN adf_forge_app.jobs.pipeline_id IS 'related pipeline; NULL for project-level jobs (sync, catalog, purview)';
COMMENT ON COLUMN adf_forge_app.jobs.payload IS 'job inputs (spec, template id, push target); gitToken is Fernet-encrypted at rest';
COMMENT ON COLUMN adf_forge_app.jobs.result IS 'handler output (artifacts, validation issues, commit/PR info)';
COMMENT ON COLUMN adf_forge_app.jobs.error IS 'failure message when status = failed';
COMMENT ON COLUMN adf_forge_app.jobs.attempts IS 'times the job has been claimed';
COMMENT ON COLUMN adf_forge_app.jobs.claimed_by IS 'worker instance id that claimed it';
COMMENT ON COLUMN adf_forge_app.jobs.started_at IS 'claim time (UTC)';
COMMENT ON COLUMN adf_forge_app.jobs.finished_at IS 'completion time (UTC)';
COMMENT ON COLUMN adf_forge_app.jobs.created_by IS 'users.id who enqueued it';
COMMENT ON COLUMN adf_forge_app.jobs.created_at IS 'enqueue time (UTC)';
COMMENT ON COLUMN adf_forge_app.jobs.updated_at IS 'last status change (UTC)';

CREATE TABLE adf_forge_app.audit_log (
    id         VARCHAR(64)  PRIMARY KEY,              -- opaque entry id
    actor      VARCHAR(200) NOT NULL,                 -- acting users.id; resolved to actorName at read time
    action     VARCHAR(64)  NOT NULL,                 -- dotted action, e.g. pipeline.created, pipeline.updated, git.push_requested, user.created
    target     VARCHAR(256) NOT NULL DEFAULT '',      -- id or name of the affected resource
    details    TEXT         NOT NULL DEFAULT '',      -- human-readable detail; never contains secrets
    team_id    VARCHAR(64)  REFERENCES adf_forge_app.teams(id), -- team of the affected resource, captured at write time; NULL = platform-global (PLATFORM_ADMIN-only)
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),   -- event time (UTC)
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()    -- TimestampMixin; entries are never updated
);
COMMENT ON TABLE adf_forge_app.audit_log IS 'Append-only audit trail (§30). Purged by `python -m app.manage purge-audit-log` (AUDIT_RETENTION_DAYS). Migration 0001 (+0012 team_id).';
COMMENT ON COLUMN adf_forge_app.audit_log.id IS 'opaque entry id';
COMMENT ON COLUMN adf_forge_app.audit_log.actor IS 'acting users.id; resolved to actorName at read time';
COMMENT ON COLUMN adf_forge_app.audit_log.action IS 'dotted action, e.g. pipeline.created, pipeline.updated, git.push_requested, user.created';
COMMENT ON COLUMN adf_forge_app.audit_log.target IS 'id or name of the affected resource';
COMMENT ON COLUMN adf_forge_app.audit_log.details IS 'human-readable detail; never contains secrets';
COMMENT ON COLUMN adf_forge_app.audit_log.team_id IS 'team of the affected resource, captured at write time; NULL = platform-global (PLATFORM_ADMIN-only)';
COMMENT ON COLUMN adf_forge_app.audit_log.created_at IS 'event time (UTC)';
COMMENT ON COLUMN adf_forge_app.audit_log.updated_at IS 'TimestampMixin; entries are never updated';

-- Indexes
CREATE INDEX ix_sessions_user_id               ON adf_forge_app.sessions(user_id);  -- migration 0008
CREATE INDEX ix_sso_role_mappings_configuration ON adf_forge_app.sso_role_mappings(configuration_id);  -- migration 0018
CREATE INDEX ix_pipeline_definitions_project   ON adf_forge_app.pipeline_definitions(project_id);  -- migration 0009
CREATE INDEX ix_pipeline_definitions_team      ON adf_forge_app.pipeline_definitions(team_id);  -- migration 0009
CREATE INDEX ix_artifacts_pipeline             ON adf_forge_app.artifacts(pipeline_id);  -- migration 0009
CREATE INDEX ix_connection_library_project_id  ON adf_forge_app.connection_library(project_id);  -- migration 0005
CREATE INDEX ix_connection_library_team_id     ON adf_forge_app.connection_library(team_id);  -- migration 0005
CREATE INDEX ix_managed_identities_team        ON adf_forge_app.managed_identities(team_id);  -- migration 0013
CREATE INDEX ix_onboarding_objects_project     ON adf_forge_app.onboarding_objects(project_id);  -- migration 0011
CREATE INDEX ix_dq_rules_object                ON adf_forge_app.dq_rules(project_id, object_key);  -- migration 0014
CREATE INDEX ix_onboarding_imports_project     ON adf_forge_app.onboarding_imports(project_id);  -- migration 0016
CREATE INDEX ix_onboarding_syncs_project       ON adf_forge_app.onboarding_syncs(project_id);  -- migration 0019
CREATE INDEX ix_sttm_imports_project           ON adf_forge_app.sttm_imports(project_id);  -- migration 0010
CREATE INDEX ix_silver_objects_project         ON adf_forge_app.silver_objects(project_id);  -- migration 0010
CREATE INDEX ix_silver_objects_tag             ON adf_forge_app.silver_objects(project_id, tag);  -- migration 0010
CREATE INDEX ix_mart_objects_project           ON adf_forge_app.mart_objects(project_id);  -- migration 0010
CREATE INDEX ix_mart_objects_tag               ON adf_forge_app.mart_objects(project_id, tag);  -- migration 0010
CREATE INDEX ix_git_catalog_changes_project    ON adf_forge_app.git_catalog_changes(project_id);  -- migration 0020
CREATE INDEX ix_jobs_status                    ON adf_forge_app.jobs(status);  -- migration 0001
CREATE INDEX ix_audit_log_team                 ON adf_forge_app.audit_log(team_id);  -- migration 0012


-- =============================================================================
-- PART 2 — ADF control store (Azure SQL / SQL Server, T-SQL)
-- =============================================================================

IF SCHEMA_ID(N'adf_forge_control') IS NULL EXEC(N'CREATE SCHEMA adf_forge_control');
GO

IF OBJECT_ID(N'adf_forge_control.adf_control_table', N'U') IS NULL
CREATE TABLE adf_forge_control.adf_control_table (
    TaskId            INT IDENTITY(1,1) PRIMARY KEY,               -- row id; passed to the child pipeline and matched by TaskIdFilter for single-row restarts
    SourceType        NVARCHAR(128)  NOT NULL,                     -- source connector type (SpecDraft.sourceType, e.g. sql_server)
    Source            NVARCHAR(512)  NOT NULL,                     -- qualified source object (schema.table, catalog.schema.table, or object name)
    SourceQuery       NVARCHAR(MAX)  NULL,                         -- custom extract query; empty = read the whole object
    TargetType        NVARCHAR(128)  NOT NULL,                     -- target connector type (SpecDraft.targetType)
    Target            NVARCHAR(512)  NOT NULL,                     -- target table or folder path
    TargetFileName    NVARCHAR(512)  NULL,                         -- target file name for file sinks
    LoadingBehavior   NVARCHAR(16)   NOT NULL DEFAULT 'DeltaLoad', -- FullLoad | DeltaLoad; routes the child pipeline's Switch
    WatermarkColumn   NVARCHAR(128)  NULL,                         -- incremental column(s) for DeltaLoad
    WatermarkType     NVARCHAR(16)   NULL,                         -- single | composite
    WatermarkValue    NVARCHAR(256)  NULL,                         -- last processed watermark; updated by UpdateWatermark after a successful copy
    Enabled           BIT            NOT NULL DEFAULT 1,           -- 0 = skipped by the Lookup
    TaskGroup         NVARCHAR(128)  NULL,                         -- only emitted when the master has childDispatch cases: Switch key choosing the child pipeline; NULL = default child
    ColumnMapping     NVARCHAR(MAX)  NULL,                         -- only emitted when an object has its own columnMappings: per-row Copy TabularTranslator JSON
    LastRunTimestamp  DATETIME2      NULL,                         -- last successful run time
    CreatedAt         DATETIME2      NOT NULL DEFAULT GETDATE()    -- row creation time
);
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'TaskId', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'row id; passed to the child pipeline and matched by TaskIdFilter for single-row restarts', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'TaskId';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'SourceType', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'source connector type (SpecDraft.sourceType, e.g. sql_server)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'SourceType';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'Source', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'qualified source object (schema.table, catalog.schema.table, or object name)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'Source';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'SourceQuery', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'custom extract query; empty = read the whole object', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'SourceQuery';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'TargetType', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'target connector type (SpecDraft.targetType)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'TargetType';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'Target', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'target table or folder path', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'Target';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'TargetFileName', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'target file name for file sinks', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'TargetFileName';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'LoadingBehavior', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'FullLoad | DeltaLoad; routes the child pipeline''s Switch', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'LoadingBehavior';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'WatermarkColumn', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'incremental column(s) for DeltaLoad', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'WatermarkColumn';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'WatermarkType', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'single | composite', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'WatermarkType';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'WatermarkValue', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'last processed watermark; updated by UpdateWatermark after a successful copy', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'WatermarkValue';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'Enabled', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'0 = skipped by the Lookup', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'Enabled';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'TaskGroup', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'only emitted when the master has childDispatch cases: Switch key choosing the child pipeline; NULL = default child', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'TaskGroup';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'ColumnMapping', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'only emitted when an object has its own columnMappings: per-row Copy TabularTranslator JSON', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'ColumnMapping';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'LastRunTimestamp', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'last successful run time', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'LastRunTimestamp';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_control_table'), N'CreatedAt', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'row creation time', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_control_table', N'COLUMN', N'CreatedAt';
GO

IF OBJECT_ID(N'adf_forge_control.adf_execution_log', N'U') IS NULL
CREATE TABLE adf_forge_control.adf_execution_log (
    LogId          INT IDENTITY(1,1) PRIMARY KEY, -- log row id
    RunId          NVARCHAR(64)   NOT NULL,       -- ADF pipeline run id (@pipeline().RunId)
    PipelineName   NVARCHAR(256)  NOT NULL,       -- ADF pipeline name
    SourceObject   NVARCHAR(512)  NOT NULL,       -- source object copied
    TargetObject   NVARCHAR(512)  NULL,           -- target table/path written
    LoadType       NVARCHAR(16)   NULL,           -- FullLoad | DeltaLoad | ChunkedLoad
    RowsRead       BIGINT         NULL,           -- Copy activity rowsRead
    RowsWritten    BIGINT         NULL,           -- Copy activity rowsCopied
    RowsRejected   BIGINT         NULL,           -- rows redirected as incompatible
    Status         NVARCHAR(16)   NOT NULL,       -- RUNNING | SUCCEEDED | FAILED
    ErrorMessage   NVARCHAR(MAX)  NULL,           -- activity error on failure
    StartedAt      DATETIME2      NOT NULL,       -- run start (UTC)
    CompletedAt    DATETIME2      NULL            -- run end (UTC); NULL while RUNNING
);
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'LogId', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'log row id', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'LogId';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'RunId', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'ADF pipeline run id (@pipeline().RunId)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'RunId';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'PipelineName', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'ADF pipeline name', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'PipelineName';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'SourceObject', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'source object copied', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'SourceObject';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'TargetObject', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'target table/path written', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'TargetObject';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'LoadType', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'FullLoad | DeltaLoad | ChunkedLoad', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'LoadType';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'RowsRead', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'Copy activity rowsRead', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'RowsRead';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'RowsWritten', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'Copy activity rowsCopied', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'RowsWritten';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'RowsRejected', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'rows redirected as incompatible', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'RowsRejected';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'Status', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'RUNNING | SUCCEEDED | FAILED', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'Status';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'ErrorMessage', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'activity error on failure', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'ErrorMessage';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'StartedAt', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'run start (UTC)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'StartedAt';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_execution_log') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_execution_log'), N'CompletedAt', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'run end (UTC); NULL while RUNNING', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_execution_log', N'COLUMN', N'CompletedAt';
GO

IF OBJECT_ID(N'adf_forge_control.adf_chunk_plan', N'U') IS NULL
CREATE TABLE adf_forge_control.adf_chunk_plan (
    TaskId         INT            NOT NULL,                   -- adf_control_table.TaskId being chunked
    ChunkId        INT            NOT NULL,                   -- chunk sequence number within the task
    RangeStart     NVARCHAR(128)  NOT NULL,                   -- lower bound on the chunk column
    RangeEnd       NVARCHAR(128)  NOT NULL,                   -- upper bound on the chunk column
    Status         NVARCHAR(16)   NOT NULL DEFAULT 'PENDING', -- PENDING | RUNNING | SUCCEEDED | FAILED
    RowsCopied     BIGINT         NULL,                       -- rows copied for this chunk
    StartedAt      DATETIME2      NULL,                       -- chunk start (UTC)
    CompletedAt    DATETIME2      NULL,                       -- chunk end (UTC)
    ErrorMessage   NVARCHAR(MAX)  NULL,                       -- error on failure
    PRIMARY KEY (TaskId, ChunkId)
);
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_chunk_plan') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_chunk_plan'), N'TaskId', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'adf_control_table.TaskId being chunked', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_chunk_plan', N'COLUMN', N'TaskId';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_chunk_plan') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_chunk_plan'), N'ChunkId', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'chunk sequence number within the task', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_chunk_plan', N'COLUMN', N'ChunkId';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_chunk_plan') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_chunk_plan'), N'RangeStart', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'lower bound on the chunk column', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_chunk_plan', N'COLUMN', N'RangeStart';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_chunk_plan') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_chunk_plan'), N'RangeEnd', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'upper bound on the chunk column', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_chunk_plan', N'COLUMN', N'RangeEnd';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_chunk_plan') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_chunk_plan'), N'Status', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'PENDING | RUNNING | SUCCEEDED | FAILED', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_chunk_plan', N'COLUMN', N'Status';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_chunk_plan') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_chunk_plan'), N'RowsCopied', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'rows copied for this chunk', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_chunk_plan', N'COLUMN', N'RowsCopied';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_chunk_plan') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_chunk_plan'), N'StartedAt', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'chunk start (UTC)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_chunk_plan', N'COLUMN', N'StartedAt';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_chunk_plan') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_chunk_plan'), N'CompletedAt', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'chunk end (UTC)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_chunk_plan', N'COLUMN', N'CompletedAt';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_chunk_plan') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_chunk_plan'), N'ErrorMessage', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'error on failure', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_chunk_plan', N'COLUMN', N'ErrorMessage';
GO

IF OBJECT_ID(N'adf_forge_control.adf_connection_control', N'U') IS NULL
CREATE TABLE adf_forge_control.adf_connection_control (
    ConnectionId      INT IDENTITY(1,1) PRIMARY KEY,             -- row id
    ConnectionName    NVARCHAR(128)  NOT NULL UNIQUE,            -- linked service name (e.g. LS_sql_server_source); dedup key
    ConnectorType     NVARCHAR(64)   NOT NULL,                   -- connector registry key (sql_server, adls_gen2, snowflake, ...)
    LinkedServiceName NVARCHAR(256)  NOT NULL,                   -- ADF linked service this row resolves to
    KeyVaultName      NVARCHAR(256)  NULL,                       -- Key Vault linked service name only; never a secret value
    SecretRef         NVARCHAR(256)  NULL,                       -- Key Vault secret NAME for the selected auth (from the schema's isSecret field)
    Properties        NVARCHAR(MAX)  NULL,                       -- JSON of every other non-secret connector field (server, database, path, ...)
    Enabled           BIT            NOT NULL DEFAULT 1,         -- 0 = connection retired
    CreatedAt         DATETIME2      NOT NULL DEFAULT GETDATE()  -- row creation time
);
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_connection_control') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_connection_control'), N'ConnectionId', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'row id', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_connection_control', N'COLUMN', N'ConnectionId';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_connection_control') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_connection_control'), N'ConnectionName', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'linked service name (e.g. LS_sql_server_source); dedup key', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_connection_control', N'COLUMN', N'ConnectionName';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_connection_control') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_connection_control'), N'ConnectorType', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'connector registry key (sql_server, adls_gen2, snowflake, ...)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_connection_control', N'COLUMN', N'ConnectorType';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_connection_control') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_connection_control'), N'LinkedServiceName', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'ADF linked service this row resolves to', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_connection_control', N'COLUMN', N'LinkedServiceName';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_connection_control') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_connection_control'), N'KeyVaultName', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'Key Vault linked service name only; never a secret value', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_connection_control', N'COLUMN', N'KeyVaultName';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_connection_control') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_connection_control'), N'SecretRef', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'Key Vault secret NAME for the selected auth (from the schema''s isSecret field)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_connection_control', N'COLUMN', N'SecretRef';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_connection_control') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_connection_control'), N'Properties', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'JSON of every other non-secret connector field (server, database, path, ...)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_connection_control', N'COLUMN', N'Properties';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_connection_control') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_connection_control'), N'Enabled', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'0 = connection retired', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_connection_control', N'COLUMN', N'Enabled';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_connection_control') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_connection_control'), N'CreatedAt', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'row creation time', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_connection_control', N'COLUMN', N'CreatedAt';
GO

IF OBJECT_ID(N'adf_forge_control.watermarks', N'U') IS NULL
CREATE TABLE adf_forge_control.watermarks (
    TaskId            INT           NOT NULL PRIMARY KEY, -- always 1: the single tracked object
    WatermarkValue    NVARCHAR(256) NULL,                 -- last processed watermark; seeded from watermarkInitialValue (default 1900-01-01)
    LastRunTimestamp  DATETIME2     NULL                  -- last successful run time
);
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.watermarks') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.watermarks'), N'TaskId', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'always 1: the single tracked object', N'SCHEMA', N'adf_forge_control', N'TABLE', N'watermarks', N'COLUMN', N'TaskId';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.watermarks') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.watermarks'), N'WatermarkValue', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'last processed watermark; seeded from watermarkInitialValue (default 1900-01-01)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'watermarks', N'COLUMN', N'WatermarkValue';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.watermarks') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.watermarks'), N'LastRunTimestamp', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'last successful run time', N'SCHEMA', N'adf_forge_control', N'TABLE', N'watermarks', N'COLUMN', N'LastRunTimestamp';
GO

IF OBJECT_ID(N'adf_forge_control.adf_curated_control_table', N'U') IS NULL
CREATE TABLE adf_forge_control.adf_curated_control_table (
    ObjectId              INT IDENTITY(1,1) PRIMARY KEY,             -- row id
    ObjectKey             NVARCHAR(256)  NOT NULL,                   -- app.silver_objects.object_key
    Tag                   NVARCHAR(128)  NOT NULL,                   -- registry classification tag
    CuratedEntity         NVARCHAR(256)  NOT NULL,                   -- curated entity name
    TargetTable           NVARCHAR(512)  NOT NULL,                   -- computed curated.<entity> (or the project's Silver prefix)
    SourceObjectKeys      NVARCHAR(MAX)  NULL,                       -- pipe-separated Bronze object_keys
    TransformationCount   INT            NOT NULL DEFAULT 0,         -- transformation rules applied
    DataQualityRuleCount  INT            NOT NULL DEFAULT 0,         -- DQ rules applied
    WriteBehavior         NVARCHAR(16)   NULL,                       -- append | overwrite | upsert | merge
    LoadingBehavior       NVARCHAR(16)   NULL,                       -- FullLoad | DeltaLoad
    Enabled               BIT            NOT NULL DEFAULT 1,         -- 0 = skipped
    CreatedAt             DATETIME2      NOT NULL DEFAULT GETDATE()  -- row creation time
);
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'ObjectId', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'row id', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'ObjectId';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'ObjectKey', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'app.silver_objects.object_key', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'ObjectKey';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'Tag', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'registry classification tag', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'Tag';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'CuratedEntity', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'curated entity name', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'CuratedEntity';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'TargetTable', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'computed curated.<entity> (or the project''s Silver prefix)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'TargetTable';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'SourceObjectKeys', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'pipe-separated Bronze object_keys', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'SourceObjectKeys';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'TransformationCount', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'transformation rules applied', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'TransformationCount';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'DataQualityRuleCount', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'DQ rules applied', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'DataQualityRuleCount';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'WriteBehavior', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'append | overwrite | upsert | merge', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'WriteBehavior';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'LoadingBehavior', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'FullLoad | DeltaLoad', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'LoadingBehavior';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'Enabled', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'0 = skipped', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'Enabled';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_curated_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_curated_control_table'), N'CreatedAt', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'row creation time', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_curated_control_table', N'COLUMN', N'CreatedAt';
GO

IF OBJECT_ID(N'adf_forge_control.adf_mart_control_table', N'U') IS NULL
CREATE TABLE adf_forge_control.adf_mart_control_table (
    ObjectId                  INT IDENTITY(1,1) PRIMARY KEY,            -- row id
    ObjectKey                 NVARCHAR(256) NOT NULL,                   -- app.mart_objects.object_key
    Tag                       NVARCHAR(128) NOT NULL,                   -- registry classification tag
    MartDomain                NVARCHAR(128) NOT NULL,                   -- mart domain
    CuratedEntity             NVARCHAR(256) NOT NULL,                   -- mart entity name
    TargetTable               NVARCHAR(512) NOT NULL,                   -- computed mart.<domain>_<entity> (or the project's Gold prefix)
    SourceCuratedEntities     NVARCHAR(MAX) NULL,                       -- pipe-separated Silver object_keys
    TransformationCount       INT           NOT NULL DEFAULT 0,         -- transformation rules applied
    DataQualityRuleCount      INT           NOT NULL DEFAULT 0,         -- DQ rules applied
    WriteBehavior             NVARCHAR(16)  NULL,                       -- append | overwrite | upsert | merge
    LoadingBehavior           NVARCHAR(16)  NULL,                       -- FullLoad | DeltaLoad
    Enabled                   BIT           NOT NULL DEFAULT 1,         -- 0 = skipped
    CreatedAt                 DATETIME2     NOT NULL DEFAULT GETDATE()  -- row creation time
);
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'ObjectId', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'row id', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'ObjectId';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'ObjectKey', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'app.mart_objects.object_key', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'ObjectKey';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'Tag', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'registry classification tag', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'Tag';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'MartDomain', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'mart domain', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'MartDomain';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'CuratedEntity', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'mart entity name', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'CuratedEntity';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'TargetTable', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'computed mart.<domain>_<entity> (or the project''s Gold prefix)', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'TargetTable';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'SourceCuratedEntities', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'pipe-separated Silver object_keys', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'SourceCuratedEntities';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'TransformationCount', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'transformation rules applied', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'TransformationCount';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'DataQualityRuleCount', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'DQ rules applied', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'DataQualityRuleCount';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'WriteBehavior', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'append | overwrite | upsert | merge', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'WriteBehavior';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'LoadingBehavior', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'FullLoad | DeltaLoad', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'LoadingBehavior';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'Enabled', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'0 = skipped', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'Enabled';
IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' AND major_id = OBJECT_ID(N'adf_forge_control.adf_mart_control_table') AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'adf_forge_control.adf_mart_control_table'), N'CreatedAt', 'ColumnId'))
  EXEC sp_addextendedproperty N'MS_Description', N'row creation time', N'SCHEMA', N'adf_forge_control', N'TABLE', N'adf_mart_control_table', N'COLUMN', N'CreatedAt';
GO
