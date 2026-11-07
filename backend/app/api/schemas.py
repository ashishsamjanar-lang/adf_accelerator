"""Boundary DTOs (Pydantic v2).

camelCase on the wire to match ``frontend/types/index.ts``; snake_case in
Python. Every collection/detail endpoint returns one of these — routes never
serialize ORM objects directly.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


# --- Identity ------------------------------------------------------------


class UserOut(_Camel):
    id: str
    name: str
    email: str
    username: str | None = None
    role: str
    team_id: str | None = None
    active: bool = True
    last_seen: str | None = None
    # AUTH_MODE=password only (§27.2 extension) — always false under mock/SSO.
    must_change_password: bool = False


class UserCreateIn(_Camel):
    """POST /admin/users body — onboarding a new account (PLATFORM_ADMIN only,
    §11/§27 extension). No password field: the server always generates a
    one-time temp password (no email sender exists in this app to deliver a
    user-chosen one securely)."""

    name: str
    email: str
    # Login name. Omitted → derived from the email's local part (app/security.py).
    username: str | None = None
    # Optional admin-chosen initial password; omitted → a generated one-time
    # password. Either way the account must change it on first login.
    password: str | None = None
    role: str = "DEVELOPER"
    team_id: str | None = None


class UserCreateOut(UserOut):
    """Same as UserOut, plus the one-time temp password — returned exactly
    once, on creation. Never retrievable again; only its hash is stored. Null
    when the admin chose the initial password themselves."""

    temp_password: str | None = None


class UserUpdateIn(_Camel):
    """PUT /admin/users/{id} body. Every field optional — only supplied
    fields change. TEAM_ADMIN callers are further restricted server-side
    (own team only, never role=PLATFORM_ADMIN, never team_id) — see
    app/api/users.py."""

    name: str | None = None
    role: str | None = None
    team_id: str | None = None
    active: bool | None = None


class ResetPasswordOut(_Camel):
    temp_password: str


class AuthConfigOut(_Camel):
    """Public, unauthenticated — tells the frontend which login UI to render."""

    auth_mode: str
    idle_timeout_minutes: int | None = None


class LoginIn(_Camel):
    """``username`` takes a username *or* an email. ``email`` is the pre-username
    field name, still accepted so older clients keep working."""

    username: str = ""
    email: str = ""
    password: str


class ChangePasswordIn(_Camel):
    current_password: str
    new_password: str


class TeamOut(_Camel):
    id: str
    name: str
    description: str = ""
    member_count: int = 0
    project_count: int = 0


class ProjectOut(_Camel):
    id: str
    team_id: str
    name: str
    description: str = ""
    pipeline_count: int = 0
    settings: dict[str, Any] | None = None


class ProjectCreateIn(_Camel):
    team_id: str
    name: str
    description: str = ""


class TeamCreateIn(_Camel):
    name: str
    description: str = ""


class DevSwitchUserIn(_Camel):
    """AUTH_MODE=mock only (§27.2/§27.6) — the dev user-switcher's request body."""

    user_id: str


# --- Templates ----------------------------------------------------------


class TemplateOut(_Camel):
    id: str
    name: str
    version: str
    description: str = ""
    supported_source_types: list[str] = []
    supported_target_types: list[str] = []
    fields: list[str] = []
    defaults: dict[str, str] = {}


# --- Pipelines ---------------------------------------------------------


class PipelineOut(_Camel):
    id: str
    name: str
    type: str
    project_id: str
    project_name: str
    team_id: str
    team_name: str
    spec_version: str
    state: str
    latest_artifact_version: str | None = None
    created_at: str
    updated_at: str
    created_by: str | None = None
    created_by_name: str | None = None
    updated_by: str | None = None
    updated_by_name: str | None = None
    spec: dict[str, Any] = {}
    # Raw SpecDraft payload (camelCase) as last saved — needed so the Build wizard can reopen an
    # existing pipeline and hydrate its form state (§24.1 "open anything already in Git and edit it",
    # scoped down here to "open anything already created and edit it"). `spec` above is the reduced
    # LogicalPipelineSpec and cannot round-trip back into a wizard draft.
    draft: dict[str, Any] = {}


class PipelineCreateIn(_Camel):
    name: str
    project_id: str
    template_id: str
    # Raw SpecDraft payload (camelCase) — the wizard form state.
    draft: dict[str, Any] = {}


class PipelineUpdateIn(_Camel):
    name: str | None = None
    template_id: str | None = None
    draft: dict[str, Any] | None = None
    # §24.14 optimistic concurrency — the `updatedAt` the editor loaded; omitted = last write wins.
    expected_updated_at: str | None = None


# --- Artifacts -------------------------------------------------------


class ArtifactOut(_Camel):
    id: str
    pipeline_id: str
    pipeline_name: str
    file_name: str
    file_type: str
    artifact_type: str
    layer: str
    version: str
    state: str
    bytes: int
    content: str | None = None


class ArtifactDiffOut(_Camel):
    additions: int
    deletions: int


# --- Validation -----------------------------------------------------


class ValidationIssueOut(_Camel):
    severity: str
    message: str
    source: str | None = None
    field: str | None = None
    # §29.1/§29.3 — which pyramid layer produced this issue (syntax|semantic|security|policy
    # today; schema/live are still target design, §29.5). Optional/defaulted so an older
    # worker's `job.result` (written before this field existed) still deserializes cleanly.
    layer: str = "semantic"
    # §29.3 — stable rule id + one-line remediation (worker/app/validator/rules.py FIX_HINTS).
    rule_id: str | None = None
    fix_hint: str | None = None


class ValidationResultOut(_Camel):
    pipeline_id: str
    state: str  # passed | failed | pending
    issues: list[ValidationIssueOut] = []
    validated_at: str | None = None
    correlation_id: str


# --- Git ----------------------------------------------------------


class GitPushIn(_Camel):
    pipeline_id: str
    artifact_ids: list[str] = []
    provider: str | None = None
    repository_url: str | None = None
    feature_branch: str
    base_branch: str = "dev"
    commit_message: str | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    reviewers: list[str] = []
    # Per-push credential: forwarded to the worker for this provider call only.
    # Never written to the metastore, never audited, never logged (§25.5).
    git_token: str | None = None


class PullRequestIn(_Camel):
    push_request_id: str
    title: str | None = None
    body: str | None = None


class GitPullRequestOut(_Camel):
    id: str
    number: int | None = None
    url: str | None = None
    title: str = ""
    source_branch: str
    target_branch: str
    status: str
    # True when no Git credential resolved and the offline SimulatedAdapter
    # opened this "PR" instead of a real provider call (§23.12 gap #14).
    simulated: bool = False


class GitPushOut(_Camel):
    id: str
    pipeline_id: str
    provider: str
    repository_url: str
    feature_branch: str
    base_branch: str
    artifact_ids: list[str]
    commit_message: str
    commit_sha: str | None = None
    status: str
    error: str | None = None
    pull_request: GitPullRequestOut | None = None
    # True when no Git credential resolved and the offline SimulatedAdapter
    # produced this commit instead of a real provider call (§23.12 gap #14).
    # The frontend must render this as a visible "not a real push" notice,
    # never silently show it the same as a real success.
    simulated: bool = False


# --- Environments -------------------------------------------------------


class EnvironmentConfigurationOut(_Camel):
    name: str
    storage_account: str = ""
    sql_server: str = ""
    database: str = ""
    key_vault: str = ""
    # ADF factory this environment's global parameters (A-2) resolve against.
    factory_name: str = ""
    subscription_id: str = ""
    resource_group: str = ""


class EnvironmentOut(_Camel):
    name: str
    label: str
    status: str = "not_configured"
    description: str = ""
    configuration: EnvironmentConfigurationOut


class EnvironmentIn(_Camel):
    """Create/update body — ``name`` is the natural key (DEV | QA | PROD)."""

    name: str
    label: str
    status: str = "not_configured"
    description: str = ""
    storage_account: str = ""
    sql_server: str = ""
    database: str = ""
    key_vault: str = ""
    factory_name: str = ""
    subscription_id: str = ""
    resource_group: str = ""


# --- Connection library (§24.17) ---------------------------------------


class ConnectionOut(_Camel):
    id: str
    project_id: str
    team_id: str
    name: str
    role: str = "source"
    connector_type: str
    description: str = ""
    # Linked service + source objects + dataset — see `ConnectionLibraryEntry.draft`.
    draft: dict[str, Any] = {}
    created_at: str | None = None
    updated_at: str | None = None
    created_by: str | None = None
    created_by_name: str | None = None


class ConnectionCreateIn(_Camel):
    project_id: str
    name: str
    role: str = "source"
    connector_type: str
    description: str = ""
    draft: dict[str, Any] = {}


class ConnectionUpdateIn(_Camel):
    name: str | None = None
    role: str | None = None
    connector_type: str | None = None
    description: str | None = None
    draft: dict[str, Any] | None = None


# --- Managed identities (§32.6, gap #21 phase MI3) ------------------------


class ManagedIdentityOut(_Camel):
    id: str
    team_id: str
    name: str
    resource_id: str
    client_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    created_by: str | None = None
    created_by_name: str | None = None


class ManagedIdentityCreateIn(_Camel):
    team_id: str | None = None  # PLATFORM_ADMIN may register on another team's behalf; else forced to caller's own
    name: str
    resource_id: str
    client_id: str | None = None


class ManagedIdentityUpdateIn(_Camel):
    name: str | None = None
    resource_id: str | None = None
    client_id: str | None = None


# --- Bronze Source Onboarding Registry (§26) ------------------------------
#
# Deliberately snake_case on the wire, not this file's usual camelCase
# convention: `frontend/types/onboarding.ts`'s `OnboardingObjectEntry` is
# already snake_case throughout (its field names double as CSV template
# headers, §26.3), and matching it 1:1 avoids a translation layer for a type
# whose whole point is to round-trip through a CSV cell-for-cell.


class OnboardingObjectOut(BaseModel):
    object_key: str
    project_id: str
    version: int = 1
    enabled: bool = True
    batch_group: str | None = None
    owner: str | None = None
    data_classification: str | None = None
    source_system: str
    source_type: str = "table"
    source_schema: str | None = None
    source_object: str
    source_query: str | None = None
    target_type: str = "table"
    target_schema: str | None = None
    target_object: str | None = None
    target_path: str | None = None
    target_file_name: str | None = None
    target_file_format: str = "Parquet"
    write_behavior: str | None = "append"
    upsert_keys: str | None = None
    loading_behavior: str = "FullLoad"
    watermark_column: str | None = None
    watermark_column_type: str | None = None
    notes: str = ""
    status: str = "draft"
    provenance: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class OnboardingObjectIn(BaseModel):
    """Create body. `object_key` is optional — defaults to
    `source_system.source_object` (§26.2) when left blank."""

    object_key: str | None = None
    enabled: bool = True
    batch_group: str | None = None
    owner: str | None = None
    data_classification: str | None = None
    source_system: str
    source_type: str = "table"
    source_schema: str | None = None
    source_object: str
    source_query: str | None = None
    target_type: str = "table"
    target_schema: str | None = None
    target_object: str | None = None
    target_path: str | None = None
    target_file_name: str | None = None
    target_file_format: str = "Parquet"
    write_behavior: str | None = "append"
    upsert_keys: str | None = None
    loading_behavior: str = "FullLoad"
    watermark_column: str | None = None
    watermark_column_type: str | None = None
    notes: str = ""
    status: str = "draft"
    # §26.6 manual | upload | git-import — defaulted server-side when omitted.
    provenance: str | None = None


class OnboardingObjectUpdateIn(BaseModel):
    """PUT body — every field optional; only supplied fields change."""

    enabled: bool | None = None
    batch_group: str | None = None
    owner: str | None = None
    data_classification: str | None = None
    source_system: str | None = None
    source_type: str | None = None
    source_schema: str | None = None
    source_object: str | None = None
    source_query: str | None = None
    target_type: str | None = None
    target_schema: str | None = None
    target_object: str | None = None
    target_path: str | None = None
    target_file_name: str | None = None
    target_file_format: str | None = None
    write_behavior: str | None = None
    upsert_keys: str | None = None
    loading_behavior: str | None = None
    watermark_column: str | None = None
    watermark_column_type: str | None = None
    notes: str | None = None
    status: str | None = None
    provenance: str | None = None


class OnboardingBulkUpsertIn(BaseModel):
    """POST .../onboarding/objects/bulk body — always additive (matches
    `bulkUpsertOnboardingObjects`'s contract): a row whose `object_key`
    matches an existing one is updated in place, every other row is
    inserted. Nothing already in the registry is ever removed by this call."""

    objects: list[OnboardingObjectIn]
    # Name of the uploaded file the rows came from, recorded on the §26.8 import audit row.
    file_name: str | None = None
    # §26.3 saved mapping profile applied on the Map step, if any — recorded on the import row.
    mapping_profile_id: str | None = None


class OnboardingImportOut(_Camel):
    id: str
    project_id: str
    file_name: str
    file_hash: str
    row_total: int
    row_valid: int
    row_error: int
    mode: str = "upsert"
    added: int = 0
    updated: int = 0
    mapping_profile_id: str | None = None
    mapping_profile_name: str | None = None
    committed_by: str | None = None
    committed_by_name: str | None = None
    committed_at: str | None = None
    created_at: str | None = None


class OnboardingMappingProfileIn(_Camel):
    name: str
    header_map: dict[str, str] = {}


class OnboardingMappingProfileOut(_Camel):
    id: str
    project_id: str
    name: str
    header_map: dict[str, str] = {}
    updated_by: str | None = None
    updated_at: str | None = None


class SessionOut(_Camel):
    # A one-way handle derived from the session id — the raw id is the cookie secret
    # (§27.3), so it is never returned, even to an admin.
    handle: str
    user_id: str
    user_name: str | None = None
    user_email: str | None = None
    team_id: str | None = None
    issued_at: str | None = None
    idle_expires_at: str | None = None
    expires_at: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    current: bool = False


# --- Data Quality rules (§30.4/§30.5) — camelCase, this file's usual convention (unlike
# OnboardingObject* just above, which is deliberately snake_case for its own CSV-header reason).


class DqRuleOut(_Camel):
    id: str
    project_id: str
    object_key: str
    check_type: str
    config: dict[str, Any] = {}
    enforcement: str = "warn"
    created_by: str | None = None
    created_at: str | None = None


class DqRuleCreateIn(_Camel):
    check_type: str
    config: dict[str, Any] = {}
    enforcement: str = "warn"


# --- STTM Silver/Gold Object Registry (§33) -----------------------------


class RegistryObjectOut(_Camel):
    """Shared shape for both `GET .../silver-objects/{key}` and
    `GET .../mart-objects/{key}` — identical fields per §33.9; `mart_domain`
    is simply always `None` on a silver row."""

    object_key: str
    project_id: str
    version: int
    enabled: bool = True
    tag: str
    curated_entity: str
    mart_domain: str | None = None
    source_object_keys: list[str] = []
    owner: str | None = None
    batch_group: str | None = None
    domain: str | None = None
    write_behavior: str = "append"
    loading_behavior: str = "FullLoad"
    watermark_column: str | None = None
    watermark_column_type: str | None = None
    business_keys: list[str] = []
    transformations: list[dict[str, Any]] = []
    data_quality_rules: list[dict[str, Any]] = []
    target_table: str
    data_classification: str | None = None
    contains_pii: bool | None = None
    contains_phi: bool | None = None
    contains_pci: bool | None = None
    critical_data_element: bool | None = None
    audit_required: bool | None = None
    retention_period: str | None = None
    regulatory_tag: str | None = None
    business_criticality: str | None = None
    notes: str = ""
    provenance_source: str = "manual"
    import_id: str | None = None
    status: str = "draft"
    created_by: str | None = None
    created_by_name: str | None = None
    updated_by: str | None = None
    updated_by_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # §33.6 rule 6 (soft, non-blocking) — a DQ rule whose `column` doesn't
    # appear among this object's own transformations' output columns. Never
    # populated with anything that could block a create/update/commit.
    warnings: list[dict[str, Any]] = []


class RegistryObjectIn(_Camel):
    """Create/update body — shared by silver and mart objects. `object_key`
    is optional on create (defaults from `curated_entity`); every field is
    optional on update (only supplied fields change, matching
    `ConnectionUpdateIn`'s convention) via the separate Update model below."""

    object_key: str | None = None
    enabled: bool = True
    tag: str
    curated_entity: str
    mart_domain: str | None = None  # required (validated) for mart objects only
    source_object_keys: list[str] = []
    owner: str | None = None
    batch_group: str | None = None
    domain: str | None = None
    write_behavior: str = "append"
    loading_behavior: str = "FullLoad"
    watermark_column: str | None = None
    watermark_column_type: str | None = None
    business_keys: list[str] = []
    transformations: list[dict[str, Any]] = []
    data_quality_rules: list[dict[str, Any]] = []
    data_classification: str | None = None
    contains_pii: bool | None = None
    contains_phi: bool | None = None
    contains_pci: bool | None = None
    critical_data_element: bool | None = None
    audit_required: bool | None = None
    retention_period: str | None = None
    regulatory_tag: str | None = None
    business_criticality: str | None = None
    notes: str = ""


class RegistryObjectUpdateIn(_Camel):
    """PUT body — every field optional; `expected_version` required for the
    §33.11 optimistic-concurrency guarantee (409 on mismatch)."""

    expected_version: int
    enabled: bool | None = None
    tag: str | None = None
    curated_entity: str | None = None
    mart_domain: str | None = None
    source_object_keys: list[str] | None = None
    owner: str | None = None
    batch_group: str | None = None
    domain: str | None = None
    write_behavior: str | None = None
    loading_behavior: str | None = None
    watermark_column: str | None = None
    watermark_column_type: str | None = None
    business_keys: list[str] | None = None
    transformations: list[dict[str, Any]] | None = None
    data_quality_rules: list[dict[str, Any]] | None = None
    data_classification: str | None = None
    contains_pii: bool | None = None
    contains_phi: bool | None = None
    contains_pci: bool | None = None
    critical_data_element: bool | None = None
    audit_required: bool | None = None
    retention_period: str | None = None
    regulatory_tag: str | None = None
    business_criticality: str | None = None
    notes: str | None = None


class SttmImportSheetOut(_Camel):
    sheet_name: str
    object_key: str
    curated_entity: str | None = None
    valid: bool
    issues: list[dict[str, Any]] = []
    # The parsed row, ready to submit as a `RegistryObjectIn` at commit time.
    parsed: dict[str, Any] | None = None
    # Version of the already-existing registry row this sheet's `object_key`
    # matched at upload time (None for a brand-new object). A commit request
    # that echoes this back as `expectedVersions[objectKey]` (§33.9/§33.11)
    # lets the commit endpoint detect a genuinely concurrent edit that landed
    # between this preview and the commit, instead of only ever comparing
    # against whatever the row's version happens to be *at commit time* (which
    # can never disagree with itself).
    existing_version: int | None = None


class SttmImportOut(_Camel):
    id: str
    project_id: str
    layer: str
    file_name: str
    file_hash: str
    sheet_total: int
    sheet_valid: int
    sheet_error: int
    mode: str = "upsert"
    committed_by: str | None = None
    committed_at: str | None = None
    sheets: list[SttmImportSheetOut] = []
    created_at: str | None = None


class SttmImportCommitIn(_Camel):
    """POST .../sttm-import/{importId}/commit body — only the listed sheet
    names are committed (lets a user exclude a still-invalid sheet rather
    than block the whole batch); `confirm_overwrite` acknowledges §33.6 rule
    8 when a sheet's object_key collides with an existing manually-created
    row."""

    sheet_names: list[str] | None = None  # None = commit every valid sheet
    confirm_overwrite: bool = False
    # object_key -> the `existingVersion` this client saw at preview time
    # (from `SttmImportSheetOut.existingVersion`). §33.9/§33.11: when set for
    # a key, the commit is checked against it instead of the row's live
    # version, so a genuinely concurrent edit 409s instead of silently
    # winning-by-request-order. Omitted (or missing a given key) falls back
    # to the pre-existing always-succeeds behavior, for older clients.
    expected_versions: dict[str, int] | None = None


# --- Audit ------------------------------------------------------


class AuditOut(_Camel):
    id: str
    timestamp: str
    actor: str
    actor_name: str | None = None
    action: str
    target: str
    details: str
    team_id: str | None = None


# --- §27.5 SSO configuration (ID2) -------------------------------------------


class SsoRoleMappingIO(_Camel):
    group_value: str
    role: str
    team_id: str | None = None


class SsoConfigurationIn(_Camel):
    name: str
    protocol: str  # oidc | saml
    issuer_url: str | None = None
    client_id: str | None = None
    client_secret_key_vault: str | None = None
    client_secret_name: str | None = None
    metadata_url: str | None = None
    entity_id: str | None = None
    group_claim: str = "groups"
    jit_provisioning: bool = True
    enabled: bool = False
    role_mappings: list[SsoRoleMappingIO] = []


class SsoConfigurationOut(SsoConfigurationIn):
    id: str
    created_at: str | None = None
    updated_at: str | None = None
    updated_by_name: str | None = None


# --- §26.5 onboarding sync-to-Git · §29.1 layer 6 · Purview push -------------


class OnboardingSyncFileIn(_Camel):
    name: str
    content: str


class OnboardingSyncIn(_Camel):
    files: list[OnboardingSyncFileIn]
    # The registry rows the client built `files` from — must match the server's.
    object_keys: list[str]
    provider: str | None = None
    repository_url: str | None = None
    feature_branch: str
    base_branch: str | None = None
    commit_message: str | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    reviewers: list[str] = []
    # Per-sync credential, same handling as GitPushIn.git_token (never stored).
    git_token: str | None = None


class CatalogRefreshIn(_Camel):
    branch: str | None = None
    # Per-refresh credential, same handling as GitPushIn.git_token (never stored).
    git_token: str | None = None


class CatalogChangeIn(_Camel):
    """§24.9/§24.10 — one change to a file already in Git, always against the base branch."""

    mode: str  # update | rename | delete
    path: str
    content: str | None = None  # update
    new_name: str | None = None  # rename
    rewrite_references: bool = False  # rename: also rewrite inbound references in the same PR
    force: bool = False  # rename/delete despite inbound references
    feature_branch: str
    commit_message: str | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    git_token: str | None = None


class IntegrationIn(_Camel):
    enabled: bool = False
    config: dict[str, Any] = {}


class LiveCheckIn(_Camel):
    pipeline_id: str


class PurviewPushIn(_Camel):
    lineage: dict[str, Any]
