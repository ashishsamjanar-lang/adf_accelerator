/**
 * Project Settings aggregate: naming/standards, ADF + template defaults,
 * tags, and the Git defaults they compose with (AGENTS.md section 5, §25).
 */

import type { PipelineTemplateId } from "./core";
import type { SourceTargetType } from "./connectors";
import type { GitSettings } from "./git";

/**
 * Enforceable prefix/suffix/case rule for one artifact type (AGENTS.md §25.3/§25.4).
 * This is the machine-checked half of a naming convention — the freeform
 * `*Pattern` strings on `NamingConvention` stay documentation-only.
 */
export interface NamingRule {
  prefix?: string;
  suffix?: string;
  case?: "snake" | "pascal" | "camel" | "upper" | "lower" | "none";
  maxLength?: number;
}

export type NamingArtifactType = "pipeline" | "dataset" | "linkedService" | "trigger";

/** Medallion zone/layer this project's Build wizard and STTM registry support (`lib/adf/medallion.ts`'s `MedallionLayer`, duplicated here rather than imported to avoid a `types` <-> `lib/adf` import cycle). */
export type MedallionLayerType = "bronze" | "silver" | "gold";

/** Per-layer standard audit columns a project overrides (gap #32). */
export type AuditColumnOverrides = Partial<Record<MedallionLayerType, Array<{ name: string; value: string }>>>;

/**
 * Naming convention configuration for a project (AGENTS.md section 5A, §25).
 */
export interface NamingConvention {
  /** e.g. {project}_{source}_{target}_{suffix} — documentation only, not enforced. */
  pipelinePattern: string;
  datasetPattern: string;
  linkedServicePattern: string;
  parameterPattern: string;
  triggerPattern: string;
  description?: string;
  /**
   * Opt-in enforcement level for the `rules` below (§25.1 — default "off",
   * nothing is checked until an admin turns this on). Evaluated inline on
   * the Build form, at pre-validate, and as a push-blocking gate when "block".
   */
  enforcement?: "off" | "warn" | "block";
  /** Per-artifact-type prefix/suffix/case/maxLength rule, keyed by NamingArtifactType. */
  rules?: Partial<Record<NamingArtifactType, NamingRule>>;
  /**
   * Per-medallion-layer custom zone prefix (extends §25.3's naming category to
   * the medallion layers feature's "layer-wise target table naming standard" —
   * `lib/adf/medallion.ts`'s `MEDALLION_LAYER_INFO[layer].zonePrefix`). When a
   * layer has an entry here, `layerTargetTableName()` uses it in place of the
   * built-in default (`bronze` "raw", `silver` "curated", `gold` "mart") when
   * computing that layer's target table name, e.g. `{ bronze: "lnd" }` turns
   * `raw.sql_server_gl_transaction` into `lnd.sql_server_gl_transaction`.
   * Unlike `rules`, this isn't a separate warn/block check — the custom
   * prefix is compiled straight into the computed name (enforced by
   * construction, matching how the default raw/curated/mart prefixes already
   * work), so no `enforcement` gate applies to it.
   */
  layerPrefixes?: Partial<Record<MedallionLayerType, string>>;
}

/**
 * Control-table / metadata-table settings (AGENTS.md section 5C).
 *
 * `schemaName`/`tableName` let a project (and, since a project belongs to one
 * team, effectively a team) run its metadata-driven pipelines against its own
 * control-table schema/table instead of sharing one global default — the
 * Build wizard prefills a new pipeline's control table from these (see
 * `create-pipeline-wizard.tsx`), and the generator (`pipeline-codegen.ts` /
 * `worker/app/generator/codegen.py`) always creates/seeds the *same*
 * reference the generated pipeline reads from (AGENTS.md §26.5/§26.6).
 */
export interface ControlTableSettings {
  schemaName: string;
  tableName: string;
  connectionReference: string;
  watermarkColumn: string;
  loadingBehaviorColumn: string;
  sourceObjectColumn: string;
  targetPathColumn: string;
  enabledColumn: string;
  /**
   * Connection control table (docs/DDL.md §2.1) — opt-in per project; decouples
   * each linked service a pipeline uses from a hardcoded reference in the
   * pipeline JSON so ops can repoint a connection per environment/team.
   */
  enableConnectionControlTable: boolean;
  connectionTableName: string;
}

/**
 * ADF infrastructure defaults (AGENTS.md section 5B).
 */
export interface AdfDefaults {
  subscriptionId?: string;
  resourceGroup?: string;
  factoryName?: string;
  integrationRuntime?: string;
  defaultSourceConnector?: SourceTargetType;
  defaultTargetConnector?: SourceTargetType;
  defaultSourceLinkedService?: string;
  defaultTargetLinkedService?: string;
  logLevel?: "Info" | "Warning" | "Error" | "Verbose";
  enableAzureMonitor?: boolean;
  defaultParallelCopies?: number;
  defaultRetryCount?: number;
  defaultRetryIntervalInSeconds?: number;
  defaultTimeout?: string;
}

/**
 * Metadata-driven template defaults (AGENTS.md section 5C).
 */
export interface TemplateDefaults {
  defaultTemplateId?: PipelineTemplateId;
  controlTable?: ControlTableSettings;
  defaultWatermarkType?: "datetime" | "int" | "bigint" | "string";
  defaultFilterCondition?: string;
  customMetadataColumns?: Array<{ name: string; value: string }>;
}

/**
 * Tag/annotation entry for project organization (AGENTS.md section 5A).
 */
export interface ProjectTag {
  key: string;
  value: string;
}

/**
 * Custom environment variable exposed to this project's generation/execution
 * context (AGENTS.md §22.3.H) — e.g. a service endpoint, feature flag, or
 * connector option that isn't already covered by ADF/Git defaults above.
 * Not an ADF pipeline parameter.
 *
 * `isSecret: true` means `value` must be a Key Vault secret name (or `@`/
 * `SecretRef:` reference), never the raw secret — enforced server-side by
 * `_reject_secret_values` (§1.6, §23.5), same as `gitDefaults.credentialVault`.
 */
export interface EnvironmentVariable {
  key: string;
  value: string;
  isSecret: boolean;
  description?: string;
}

/**
 * Full project settings aggregate (AGENTS.md section 5 and section 15).
 * This is the payload for GET/PUT /api/v1/projects/{id}/settings.
 */
export interface ProjectSettings {
  projectId: string;
  // A. Project identity
  name: string;
  description: string;
  teamId: string;
  namingConvention?: NamingConvention;
  tags: ProjectTag[];
  // B. ADF defaults
  adfDefaults?: AdfDefaults;
  // C. Metadata/template defaults
  templateDefaults?: TemplateDefaults;
  // D. Git defaults
  gitDefaults?: GitSettings;
  // E. Custom environment variables (AGENTS.md §22.3.H)
  environmentVariables?: EnvironmentVariable[];
  // Custom metadata
  customMetadata?: Array<{ name: string; value: string }>;
  // Gap #32 — project-configured standard audit columns per medallion layer (Standards page). A layer
  // with no (non-blank) entry falls back to the built-in list in `lib/adf/medallion.ts`.
  auditColumns?: AuditColumnOverrides;
  // §25.3 connection.connectors — connector keys (`lib/adf/connections.ts`) pipelines may use. Empty/unset
  // = no allow-list; the Standards compliance scan reports anything outside it (never blocks).
  allowedConnectors?: string[];
  // §25.3 tagging.required (gaps.md GOV-05/WD-06) — tag keys every pipeline must carry as an annotation
  // (`key` or `key:value`). `block` rejects Push to Git; `warn` is reported by the compliance scan only.
  requiredTags?: { enforcement: "off" | "warn" | "block"; keys: string[] };
  // Audit
  updatedAt?: string;
  updatedBy?: string;
}
