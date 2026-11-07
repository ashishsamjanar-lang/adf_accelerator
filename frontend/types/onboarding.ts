/**
 * §26 Source Onboarding — the object registry (source object → target +
 * file formats + load behaviour). One entry per row of
 * `lib/adf/onboarding.ts`'s bulk-upload template; the field names here are
 * the CSV/TSV header names verbatim and, per that module's DDL-alignment
 * comment block, also the `app.onboarding_objects` column names
 * (`docs/DDL.md`) — kept in lockstep on purpose so a template column, a
 * stored row, and a database column are always the same name.
 */

export type OnboardingSourceType = "table" | "view" | "query" | "file";
export type OnboardingTargetType = "table" | "file";
export type OnboardingWriteBehavior = "append" | "overwrite" | "upsert" | "merge";
export type OnboardingLoadingBehavior = "FullLoad" | "DeltaLoad";
export type OnboardingWatermarkColumnType = "datetime" | "int" | "bigint" | "string";
export type OnboardingDataClassification = "public" | "internal" | "confidential" | "restricted";
export type OnboardingStatus = "draft" | "valid" | "invalid";

export interface OnboardingObjectEntry {
  /** Unique per project; auto-derived as `source_system.source_object` when left blank. */
  object_key: string;
  enabled: boolean;
  batch_group?: string;
  owner?: string;
  /** Sensitivity label — same vocabulary as the Silver/Gold registries' `dataClassification`. */
  data_classification?: OnboardingDataClassification;

  source_system: string;
  source_type: OnboardingSourceType;
  source_schema?: string;
  source_object: string;
  source_query?: string;

  target_type: OnboardingTargetType;
  target_schema?: string;
  target_object?: string;
  target_path?: string;
  target_file_name?: string;
  target_file_format: string;

  write_behavior?: OnboardingWriteBehavior;
  /** Pipe-separated column list, e.g. "id|updated_at" — kept as a plain string to round-trip through one CSV cell. */
  upsert_keys?: string;

  loading_behavior: OnboardingLoadingBehavior;
  watermark_column?: string;
  watermark_column_type?: OnboardingWatermarkColumnType;

  notes?: string;

  /** Client-only bookkeeping — not a template column. */
  status?: OnboardingStatus;
  /** §26.6 where the row last came from — set server-side; not a template column. */
  provenance?: "manual" | "upload" | "git-import" | null;
}

/** §30.4/§30.5 — a data-quality rule authored on a Bronze onboarding object (`app.dq_rules`). */
export type OnboardingDqCheckType = "not_null" | "unique" | "range" | "row_count" | "freshness";

export interface OnboardingDqRule {
  id: string;
  projectId: string;
  objectKey: string;
  checkType: OnboardingDqCheckType;
  /** `column` for not_null/unique/range, plus `min`/`max` for range; `threshold` for row_count/freshness. */
  config: Record<string, string>;
  enforcement: "off" | "warn" | "block";
  createdBy?: string | null;
  createdAt?: string | null;
}

/** §26 `app.onboarding_imports` — audit row for one bulk-upload commit. */
export interface OnboardingImportRecord {
  id: string;
  projectId: string;
  fileName: string;
  /** Server-computed SHA-256 of the committed rows. */
  fileHash: string;
  rowTotal: number;
  rowValid: number;
  rowError: number;
  mode: string;
  added: number;
  updated: number;
  /** §26.3 mapping profile applied on the Map step; name is null once the profile is deleted. */
  mappingProfileId?: string | null;
  mappingProfileName?: string | null;
  committedBy?: string | null;
  committedByName?: string | null;
  committedAt?: string | null;
  createdAt?: string | null;
}

/** §26.3/§26.7 saved Map-step profile — `{uploaded header: registry column}`, per project, server-side. */
export interface OnboardingMappingProfile {
  id: string;
  projectId: string;
  name: string;
  headerMap: Record<string, string>;
  updatedBy?: string | null;
  updatedAt?: string | null;
}

/** §26.5 "Sync to Git" — one push of the registry files to a feature branch + PR into the base. */
export interface OnboardingSync {
  id: string;
  projectId: string;
  provider: string;
  repositoryUrl: string;
  featureBranch: string;
  baseBranch: string;
  commitMessage: string;
  filePaths: string[];
  objectCount: number;
  status: "pending" | "pushed" | "pr_created" | "failed";
  commitSha: string | null;
  prNumber: number | null;
  prUrl: string | null;
  simulated: boolean;
  error: string | null;
  createdBy: string | null;
  createdAt: string | null;
}

/** Per-row diff of the current registry vs. the last successful sync's snapshot. */
export interface OnboardingSyncDiff {
  baseSyncId: string | null;
  baseCommitSha: string | null;
  basePrUrl: string | null;
  added: string[];
  removed: string[];
  changed: { objectKey: string; fields: string[] }[];
  unchanged: number;
}

export interface OnboardingSyncRequest {
  files: { name: "adf_control_table.sql" | "objects.csv" | "registry-lineage.json"; content: string }[];
  objectKeys: string[];
  provider?: string;
  repositoryUrl?: string;
  featureBranch: string;
  baseBranch?: string;
  commitMessage?: string;
  prTitle?: string;
  prBody?: string;
  reviewers?: string[];
  /** Per-sync credential — forwarded encrypted to the worker, never stored. */
  gitToken?: string;
}

/** Per-project external integrations: §29.1 layer-6 live ARM check, Purview lineage push. */
export type IntegrationKind = "live_validation" | "purview";

export interface ProjectIntegration {
  projectId: string;
  kind: IntegrationKind;
  enabled: boolean;
  /** Identifiers + a Key Vault secret *reference* (`clientSecretRef`) — never a secret value. */
  config: Record<string, string>;
  updatedBy: string | null;
  updatedAt: string | null;
}

/** A queued sync / live-check / Purview / catalog job, polled via `GET /projects/{id}/jobs/{jobId}`. */
export interface IntegrationJob {
  id: string;
  type: "onboarding_sync" | "live_check" | "purview_push" | "catalog_refresh" | "catalog_change";
  status: "queued" | "running" | "succeeded" | "failed";
  result: Record<string, unknown> | null;
  error: string | null;
  createdAt: string | null;
  finishedAt: string | null;
}
