import type {
  LogicalPipelineSpec,
  PipelineTemplate,
  PipelineTemplateId,
  ValidationIssue,
  SourceTargetType,
  SourceObjectEntry,
  WatermarkColumn,
  ColumnMapping,
} from "@/types";
import {
  buildDatasetProperties,
  buildLinkedServiceProperties,
  datasetName,
  defaultDatasetDraft,
  defaultLinkedServiceDraft,
  isFileConnector,
  linkedServiceAdfType,
  validateDatasetDraft,
  validateLinkedServiceDraft,
  type DatasetDefaults,
  type DatasetDraft,
  type LinkedServiceDraft,
} from "./connections";
import {
  buildSafeQualifierToken,
  checkQualifierCollision,
  checkSapExtractionSupport,
  getQualifierProfile,
  resolveQualifiedName,
} from "./qualifier";
import {
  validateMedallionFields,
  validateTransformationRules,
  type MedallionLayer,
  type TransformationRule,
} from "./medallion";

/**
 * Template registry mirroring AGENTS.md section 6 (ALL metadata-driven templates).
 * The UI only ever shows fields declared by the selected template; users
 * never author raw ADF JSON for normal workflows.
 */
/** UI sink-format choice -> LogicalPipelineSpec formatSettings.type. */
const SINK_FORMAT_MAP: Record<
  "DelimitedText" | "Parquet" | "Json" | "Avro" | "Orc",
  "TextFormat" | "JsonFormat" | "AvroFormat" | "OrcFormat" | "ParquetFormat"
> = {
  DelimitedText: "TextFormat",
  Parquet: "ParquetFormat",
  Json: "JsonFormat",
  Avro: "AvroFormat",
  Orc: "OrcFormat",
};

export const TEMPLATES: Record<PipelineTemplateId, PipelineTemplate> = {
  full_load: {
    id: "full_load",
    name: "Full Load",
    version: "2.0.0",
    description: "Reliable replacement loads. Truncate-and-load or full snapshot of a source object. All settings are metadata-driven.",
    supportedSourceTypes: ["azure_blob_storage", "azure_data_lake_storage_gen2", "azure_sql_database", "sql_server", "oracle", "postgresql", "mysql", "azure_synapse_analytics", "snowflake"],
    supportedTargetTypes: ["azure_blob_storage", "azure_data_lake_storage_gen2", "azure_sql_database", "azure_synapse_analytics", "sql_server"],
    fields: [
      "source.type", "source.object", "target.type", "target.path",
      "loadType.mode", "loadType.truncateTarget", "loadType.preCopyScript", "loadType.postCopyScript",
      "logging.enableCopyLog", "logging.logLevel", "logging.customLogColumns",
      "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
      "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
      "customMetadata"
    ],
    defaults: { sourceType: "sql_server", targetType: "azure_data_lake_storage_gen2" },
  },
  incremental_load: {
    id: "incremental_load",
    name: "Incremental Load",
    version: "2.0.0",
    description: "Watermark-based delta ingestion. Only rows changed since the last run are moved. Supports single watermark column.",
    supportedSourceTypes: ["azure_blob_storage", "azure_data_lake_storage_gen2", "azure_sql_database", "sql_server", "oracle", "postgresql", "mysql", "azure_synapse_analytics"],
    supportedTargetTypes: ["azure_blob_storage", "azure_data_lake_storage_gen2", "azure_sql_database", "azure_synapse_analytics", "sql_server"],
    fields: [
      "source.type", "source.object", "source.filterCondition", "target.type", "target.path",
      "watermark.enabled", "watermark.type", "watermark.columns", "watermark.watermarkTable", "watermark.initialValue",
      "loadType.mode", "logging.enableCopyLog", "logging.customLogColumns",
      "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
      "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
      "customMetadata"
    ],
    defaults: { sourceType: "sql_server", targetType: "azure_data_lake_storage_gen2" },
  },
  initial_load: {
    id: "initial_load",
    name: "Initial Load",
    version: "2.0.0",
    description: "First-time load with initial values. Seeds target with initial watermark values for subsequent incremental loads.",
    supportedSourceTypes: ["azure_blob_storage", "azure_data_lake_storage_gen2", "azure_sql_database", "sql_server", "oracle", "postgresql", "mysql", "azure_synapse_analytics"],
    supportedTargetTypes: ["azure_blob_storage", "azure_data_lake_storage_gen2", "azure_sql_database", "azure_synapse_analytics", "sql_server"],
    fields: [
      "source.type", "source.object", "source.filterCondition", "target.type", "target.path",
      "watermark.enabled", "watermark.type", "watermark.columns", "watermark.watermarkTable", "watermark.initialValue",
      "loadType.mode", "loadType.initialLoad", "logging.enableCopyLog", "logging.customLogColumns",
      "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
      "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
      "customMetadata"
    ],
    defaults: { sourceType: "sql_server", targetType: "azure_data_lake_storage_gen2" },
  },
  composite_watermark_load: {
    id: "composite_watermark_load",
    name: "Composite Watermark Load",
    version: "2.0.0",
    description: "Multiple watermark columns (up to 5) for complex CDC scenarios. Supports multi-column watermark logic for accurate delta detection.",
    supportedSourceTypes: ["azure_blob_storage", "azure_data_lake_storage_gen2", "azure_sql_database", "sql_server", "oracle", "postgresql", "mysql", "azure_synapse_analytics"],
    supportedTargetTypes: ["azure_blob_storage", "azure_data_lake_storage_gen2", "azure_sql_database", "azure_synapse_analytics", "sql_server"],
    fields: [
      "source.type", "source.object", "source.filterCondition", "target.type", "target.path",
      "watermark.enabled", "watermark.type", "watermark.columns", "watermark.watermarkTable", "watermark.initialValue", "watermark.filterCondition",
      "loadType.mode", "logging.enableCopyLog", "logging.customLogColumns",
      "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
      "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
      "validation.rowCountValidation", "validation.dataQualityRules",
      "customMetadata"
    ],
    defaults: { sourceType: "sql_server", targetType: "azure_data_lake_storage_gen2" },
  },
  metadata_driven_load: {
    id: "metadata_driven_load",
    name: "Metadata-Driven Load",
    version: "2.0.0",
    description: "Configuration-table-driven scale-out. Master/child pipeline architecture for multi-table automation with parallel execution.",
    supportedSourceTypes: ["azure_blob_storage", "azure_data_lake_storage_gen2", "azure_sql_database", "sql_server", "oracle", "postgresql", "mysql", "azure_synapse_analytics"],
    supportedTargetTypes: ["azure_blob_storage", "azure_data_lake_storage_gen2", "azure_sql_database", "azure_synapse_analytics", "sql_server"],
    fields: [
      "source.type", "source.object", "source.filterCondition", "target.type", "target.path",
      "watermark.enabled", "watermark.type", "watermark.columns", "watermark.watermarkTable", "watermark.initialValue",
      "loadType.mode", "pipeline.masterChildPattern", "pipeline.parallelSettings.isSequential", "pipeline.parallelSettings.batchCount",
      "logging.enableCopyLog", "logging.customLogColumns", "logging.enableAzureMonitor",
      "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits", "parallelProcessing.enableStaging",
      "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds", "errorHandling.redirectIncompatibleRows",
      "validation.rowCountValidation", "validation.schemaValidation", "validation.dataQualityRules",
      "notifications.onSuccess", "notifications.onFailure",
      "customMetadata"
    ],
    defaults: { sourceType: "sql_server", targetType: "azure_data_lake_storage_gen2" },
  },
};

export const TEMPLATE_LIST: PipelineTemplate[] = Object.values(TEMPLATES);

export function templateById(id: PipelineTemplateId): PipelineTemplate {
  return TEMPLATES[id];
}

/** Lightweight prefill bridge so "Use template" can seed the wizard. */
let prefillTemplate: PipelineTemplateId | null = null;
export function setPrefillTemplate(template: PipelineTemplateId | null): void {
  prefillTemplate = template;
}
export function getPrefillTemplate(): PipelineTemplateId | null {
  return prefillTemplate;
}

/**
 * §24.3 pipeline pattern — independent of load behavior/template. `master_child` (default) is today's
 * only-ever-emitted shape; the other four are the "missing capability" gap (§23.12 gap #5, §24.12):
 *   - `single_pipeline`      one pipeline loops the control table inline (no child, no ExecutePipeline)
 *   - `independent_full`     one source, one target, single Copy — no control table
 *   - `independent_watermark` one source, one target, watermark delta — its own watermark table, no control table
 *   - `child_only`           just the child pipeline (add an object to an existing master)
 *   - `master_only`          just the master pipeline, referencing an existing child by name
 */
export type PipelinePattern = "master_child" | "single_pipeline" | "independent_full" | "independent_watermark" | "child_only" | "master_only";

export type { ColumnMapping } from "@/types";

/** One `Switch` case of the master's child dispatch (gap #30). */
export interface ChildDispatchCase {
  /** Control-table `TaskGroup` value that routes a row to `childPipelineName`. */
  matchValue: string;
  /** Existing child pipeline (by artifact name) to `ExecutePipeline` for matching rows. */
  childPipelineName: string;
}

export interface SpecDraft {
  name: string;
  /** §24.3 pipeline pattern; defaults to `master_child` — today's only behavior, unchanged. */
  pattern: PipelinePattern;
  /**
   * Independent overrides for the generated master/child pipeline artifact names (§24.5), always
   * previewed live and falling back to `PL_{name}_master` / `PL_{name}_child` when unset — see
   * `resolveMasterPipelineName`/`resolveChildPipelineName`. `name` above stays the pipeline record's
   * own identity (project uniqueness, Drafts/Pipelines listing); these two are purely the artifact
   * file names. Used by `master_child`/`child_only`/`master_only`.
   */
  masterPipelineName?: string;
  childPipelineName?: string;
  /**
   * Optional source-system label (gap #29). When set, the master/child defaults become `pl_master` /
   * `pl_<source_system>_child` instead of `PL_{name}_master` / `PL_{name}_child`; unset = unchanged.
   */
  sourceSystem?: string;
  /** Copy activity `policy.timeout` (ADF `d.hh:mm:ss[.fff]`); unset = `COPY_TIMEOUT_DEFAULT`. Prefilled from §5B `defaultTimeout`. */
  copyTimeout?: string;
  /** gaps.md SQL-05: the user's approximate source row count (optional, never guessed) — only drives the Full Load size warning. */
  estimatedSourceRows?: number;
  /**
   * Override for the single generated pipeline's artifact name under the `single_pipeline`/
   * `independent_full`/`independent_watermark` patterns — falls back to `PL_{name}` when unset (see
   * `resolvePipelineName`). Unused by `master_child`/`child_only`/`master_only`.
   */
  pipelineName?: string;
  projectId: string;
  templateId: PipelineTemplateId;
  sourceType: SourceTargetType;
  sourceObject: string;
  sourceQuery?: string;
  sourceFilterCondition?: string;
  targetType: SourceTargetType;
  targetPath: string;
  targetFileName?: string;
  // Source read / partition options (ADF Copy source tuning)
  sourcePartitionOption: "None" | "DynamicRange" | "PhysicalPartitionsOfTable";
  sourcePartitionColumn?: string;
  sourceIsolationLevel: "" | "ReadCommitted" | "ReadUncommitted" | "RepeatableRead" | "Serializable" | "Snapshot";
  queryTimeout: string;
  // Target write options (ADF Copy sink tuning)
  sinkFileFormat: "DelimitedText" | "Parquet" | "Json" | "Avro" | "Orc";
  copyBehavior: "PreserveHierarchy" | "FlattenHierarchy" | "MergeFiles";
  maxRowsPerFile?: number;
  writeBehavior: "Insert" | "Upsert";
  upsertKeyColumns: string;
  /** RUN-14: the Silver/Gold layer target's key columns (target-layer names) for `LoadToLayerTarget`'s delete-by-key, when the chain has no dedupe/scd2/aggregate key. Unset falls back to `upsertKeyColumns`, which stays the Bronze Copy's (raw-column) upsert key. */
  layerKeyColumns?: string;
  writeBatchSize?: number;
  /**
   * How the object list (tables / input files) was populated:
   * "form" = manual rows, "file" = uploaded CSV/TSV/TXT/JSON list.
   */
  sourceObjectMode: "form" | "file";
  /** Full object list; each row becomes a control-table seed row. */
  sourceObjects: SourceObjectEntry[];
  // Connection configuration (linked services + datasets)
  sourceLinkedService: LinkedServiceDraft;
  targetLinkedService: LinkedServiceDraft;
  sourceDataset: DatasetDraft;
  targetDataset: DatasetDraft;
  /**
   * §24.6 "use existing, don't regenerate": set when the user picked a saved Connections-library
   * entry AND confirmed its linked service/dataset are already pushed to the target repo. When
   * true, `buildGeneratedArtifacts` omits `linkedService/{name}.json`/`dataset/{name}.json` for
   * that slot from the push set — the pipeline still references the name via
   * `LinkedServiceReference`/`DatasetReference`, it just isn't regenerated. Defaults to false
   * (today's unchanged behavior: always generate) since this tool has no Git-catalog read (§24.8
   * is still target design) to verify the artifact actually exists on its own.
   */
  sourceConnectionReused?: boolean;
  targetConnectionReused?: boolean;
  /**
   * Gap #28 — per-build opt-outs for the runtime artifacts. Undefined/true = generate (unchanged
   * output); false = omit `TR_{name}.json` (+ fallback) / `globalParameters.json` / `parameters.json`.
   */
  generateTrigger?: boolean;
  generateGlobalParameters?: boolean;
  generateParametersFile?: boolean;
  /**
   * Environment-promotion handoff: opt-in (undefined/false = not generated, unchanged output) ADF
   * custom parameterization template, `arm-template-parameters-definition.json`.
   */
  generateArmParameterDefinition?: boolean;
  /** Opt-in (undefined/false = not generated) static source → target `lineage.json` (OpenLineage naming). */
  generateLineage?: boolean;
  /**
   * Schema-drift slice — opt-in Copy `translator` (TabularTranslator, by-name mapping + type conversion,
   * no truncation). Undefined/false = no translator, unchanged output.
   */
  copyTypeConversion?: boolean;
  /**
   * Schema-drift slice — explicit per-column Copy mappings / renames (TabularTranslator `mappings`).
   * Undefined/empty = by-name auto-mapping, unchanged output. Applies to every Copy in the pipeline.
   */
  columnMappings?: ColumnMapping[];
  /**
   * Gap #33 — ADF `annotations` stamped on every generated pipeline (e.g. a project's `tags[]` as
   * `key:value`). Undefined/empty = `[]`, unchanged output.
   */
  annotations?: string[];
  /**
   * Gap #30 — route control-table rows across several distinct child pipelines from one master: a
   * `Switch` on the row's `TaskGroup` column, one `ExecutePipeline` per case; unmatched/NULL rows fall
   * through to this pipeline's own child. Undefined/empty = today's single-child master, unchanged.
   * Only honored by `master_child`/`master_only`.
   */
  childDispatch?: ChildDispatchCase[];
  /**
   * Hard-delete handling on the incremental (DeltaLoad) branch. Undefined/"none" = unchanged output.
   * `soft_delete`: stage every current source key, then mark landed rows whose key vanished.
   * `cdc`: read SQL Server change-data-capture for the watermark window and apply the latest
   * operation per key. Both mark `softDeleteColumn` (+ `_deleted_at_utc`) on the Bronze landed table
   * instead of physically deleting, keyed on `upsertKeyColumns`.
   */
  deleteHandling?: DeleteHandling;
  /** Soft-delete flag column; blank = `_is_deleted`. */
  softDeleteColumn?: string;
  /** CDC capture instance; blank = SQL Server's default `<schema>_<table>`, resolved per object at run time. */
  cdcCaptureInstance?: string;
  // Watermark configuration
  watermarkEnabled: boolean;
  watermarkType: "single" | "composite";
  watermarkColumns: WatermarkColumn[]; // Up to 5 columns
  watermarkTable: string;
  watermarkInitialValue: string;
  watermarkFilterCondition?: string;
  // Connection control table (AGENTS.md §26.5/§26.6, docs/DDL.md §2.1): opt-in
  // extra artifact that decouples each linked service this pipeline uses from
  // a hardcoded reference in the pipeline JSON, so ops can repoint a
  // connection per environment/team by editing this table instead of
  // regenerating artifacts. Off by default — purely additive.
  connectionControlEnabled: boolean;
  connectionControlTable: string;
  // Load type configuration
  loadTypeMode: "full" | "incremental" | "initial" | "composite_watermark";
  initialLoad: boolean;
  fullLoad: boolean;
  truncateTarget: boolean;
  preCopyScript?: string;
  postCopyScript?: string;
  // Pipeline configuration
  masterChildPattern: boolean;
  isSequential: boolean;
  batchCount: number;
  // Logging configuration
  enableCopyLog: boolean;
  logLevel: "Info" | "Warning" | "Error" | "Verbose" | "None";
  enableAzureMonitor: boolean;
  customLogColumns: Array<{ name: string; value: string }>; // Up to 10
  // Parallel processing
  parallelCopies: number;
  dataIntegrationUnits?: number;
  enableStaging: boolean;
  stagingLinkedService: string;
  stagingPath: string;
  stagingEnableCompression: boolean;
  // Error handling
  retryCount: number;
  retryIntervalInSeconds: number;
  redirectIncompatibleRows: boolean;
  // Rejects destination for redirected incompatible rows (ADF requires this
  // or skipped rows are just discarded, not landed anywhere inspectable).
  rejectsLinkedService: string;
  rejectsPath: string;
  // Chunked historical/initial load: bounded-range fan-out with persisted
  // per-chunk restart status, distinct from parallelCopies/partitionOption
  // (a network blip there restarts the whole extract from scratch).
  chunkingEnabled: boolean;
  chunkColumn?: string;
  chunkSizeRows: number;
  maxParallelChunks: number;
  // Validation
  rowCountValidationEnabled: boolean;
  dataQualityRules: Array<{
    name: string;
    column: string;
    rule: string;
    value?: string;
    enforcement?: "warn" | "block";
    /** DMT-05: breach only when more than this % (0-100) of checked rows violate a row-level rule. */
    maxViolationPct?: string;
  }>;
  // Notifications
  notifyOnSuccess: boolean;
  notifyOnFailure: boolean;
  notificationEmails: string[];
  // Trigger (optional scheduled/tumbling-window/file-arrival trigger artifact)
  triggerEnabled: boolean;
  triggerType: "schedule" | "tumbling" | "blobEvents";
  triggerFrequency: "Minute" | "Hour" | "Day" | "Week" | "Month";
  triggerInterval: number;
  triggerStartTime: string;
  triggerTimeZone: string;
  // File-arrival trigger settings (BlobEventsTrigger) — fires the moment a
  // file lands, with an optional schedule trigger as a missed-event safety net.
  triggerStorageLinkedService?: string;
  triggerContainerName?: string;
  triggerBlobPathBeginsWith?: string;
  triggerBlobPathEndsWith?: string;
  triggerFallbackSchedule: boolean;
  // Custom metadata (up to 10)
  customMetadata: Array<{ name: string; value: string }>;
  /**
   * Which registered ADF instance (`Environment.name`, e.g. "DEV" or a
   * custom instance name — AGENTS.md multi-instance support) this draft is
   * generated for. Optional: when unset, `globalParameters.json` stays fully
   * placeholder-only exactly as before (backward compatible). When set, the
   * caller resolves it against `GET /api/v1/environments` and passes the
   * matched connection into `buildGeneratedArtifacts` — the name alone isn't
   * enough to fill anything in, and is never used by codegen directly.
   */
  targetEnvironmentName?: string;
  /**
   * Bronze/Silver/Gold (medallion) layer this pipeline creates — user
   * request: layer-specific pipeline creation, referencing sample project
   * 2's bronze/silver/gold architecture. Defaults to "bronze", which is
   * exactly today's behavior (plain copy, no transformations) — fully
   * backward compatible. See `lib/adf/medallion.ts`.
   */
  medallionLayer: MedallionLayer;
  /** Silver + Gold: the conformed business entity name -> `curated.<entity>` / `mart.<domain>_<entity>`. */
  curatedEntity?: string;
  /** Gold only: the mart domain -> `mart.<domain>_<entity>`. */
  martDomain?: string;
  /** Silver/Gold transformation steps (up to 10, §33.3), applied after the copy — see `lib/adf/medallion.ts`. */
  transformations: TransformationRule[];
  /**
   * §33.7/§33.8: the `app.silver_objects`/`app.mart_objects` row (its
   * `object_key`) this draft's `curatedEntity`/`martDomain`/`transformations`/
   * `dataQualityRules` were prefilled from, when picked via Build's registry
   * dropdown instead of typed by hand — display/traceability only, and a
   * **selection-time snapshot**: editing the registry object later does not
   * retroactively change an already-picked draft (product rule #7 — pipeline
   * form edits never silently resync from a changed source). Only one of the
   * two is ever set, matching `medallionLayer`. Gates the two additive
   * `curatedControlTableSql`/`martControlTableSql` artifacts (§33.7).
   */
  silverObjectRef?: string;
  martObjectRef?: string;
  /**
   * The picked registry object's `tag`/`sourceObjectKeys` at selection time —
   * needed by `curatedControlTableSql`/`martControlTableSql` (§33.7) to
   * populate the auto-captured control table's `Tag`/`SourceObjectKeys`
   * columns while keeping the generator a pure function of `SpecDraft` (it
   * never queries `app.silver_objects`/`app.mart_objects` directly). Set
   * only alongside `silverObjectRef`/`martObjectRef`; cleared together.
   */
  registryTag?: string;
  registrySourceObjectKeys?: string[];
}

export function emptySpecDraft(): SpecDraft {
  return {
    name: "",
    pattern: "master_child",
    masterPipelineName: undefined,
    childPipelineName: undefined,
    sourceSystem: undefined,
    copyTimeout: undefined,
    pipelineName: undefined,
    projectId: "",
    templateId: "incremental_load",
    sourceType: "sql_server",
    sourceObject: "dbo.customer",
    sourceQuery: undefined,
    sourceFilterCondition: undefined,
    targetType: "azure_data_lake_storage_gen2",
    targetPath: "/customer/",
    targetFileName: undefined,
    // Source read / partition options
    sourcePartitionOption: "None",
    sourcePartitionColumn: undefined,
    sourceIsolationLevel: "",
    queryTimeout: "02:00:00",
    // Target write options
    sinkFileFormat: "Parquet",
    copyBehavior: "PreserveHierarchy",
    maxRowsPerFile: undefined,
    writeBehavior: "Insert",
    upsertKeyColumns: "",
    writeBatchSize: undefined,
    // Object list (tables / input files) — filled by form or file upload
    sourceObjectMode: "form",
    sourceObjects: [{ object: "dbo.customer" }],
    // Connections (linked services + datasets)
    sourceLinkedService: defaultLinkedServiceDraft("source", "sql_server"),
    targetLinkedService: defaultLinkedServiceDraft("target", "azure_data_lake_storage_gen2"),
    sourceDataset: defaultDatasetDraft("source", "sql_server"),
    targetDataset: defaultDatasetDraft("target", "azure_data_lake_storage_gen2"),
    // Watermark configuration
    watermarkEnabled: true,
    watermarkType: "single",
    watermarkColumns: [{ name: "modified_date", type: "datetime", order: 1 }],
    watermarkTable: "control.watermarks",
    watermarkInitialValue: "1900-01-01",
    watermarkFilterCondition: undefined,
    connectionControlEnabled: false,
    connectionControlTable: "control.adf_connection_control",
    // Load type configuration
    loadTypeMode: "incremental",
    initialLoad: false,
    fullLoad: false,
    truncateTarget: false,
    preCopyScript: undefined,
    postCopyScript: undefined,
    // Pipeline configuration
    masterChildPattern: false,
    isSequential: false,
    batchCount: 20,
    // Logging configuration
    enableCopyLog: true,
    logLevel: "Info",
    enableAzureMonitor: true,
    customLogColumns: [
      { name: "PipelineRunId", value: "@pipeline().RunId" },
      { name: "ExecutionDate", value: "@utcnow()" },
    ],
    // Parallel processing
    parallelCopies: 10,
    dataIntegrationUnits: undefined,
    enableStaging: false,
    stagingLinkedService: "",
    stagingPath: "staging/",
    stagingEnableCompression: false,
    // Error handling
    retryCount: 3,
    retryIntervalInSeconds: 30,
    redirectIncompatibleRows: false,
    rejectsLinkedService: "",
    rejectsPath: "rejects/",
    chunkingEnabled: false,
    chunkColumn: undefined,
    chunkSizeRows: 1000000,
    maxParallelChunks: 4,
    // Validation
    rowCountValidationEnabled: false,
    dataQualityRules: [],
    // Notifications
    notifyOnSuccess: false,
    notifyOnFailure: false,
    notificationEmails: [],
    // Trigger
    triggerEnabled: false,
    triggerType: "schedule",
    triggerFrequency: "Day",
    triggerInterval: 1,
    triggerStartTime: "",
    triggerTimeZone: "UTC",
    triggerStorageLinkedService: undefined,
    triggerContainerName: undefined,
    triggerBlobPathBeginsWith: undefined,
    triggerBlobPathEndsWith: undefined,
    triggerFallbackSchedule: true,
    // Custom metadata
    customMetadata: [],
    targetEnvironmentName: undefined,
    // Medallion layer — bronze is the default, matching today's behavior exactly.
    medallionLayer: "bronze",
    curatedEntity: undefined,
    martDomain: undefined,
    transformations: [],
    silverObjectRef: undefined,
    martObjectRef: undefined,
    registryTag: undefined,
    registrySourceObjectKeys: undefined,
  };
}

const PL_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,259}$/;

/**
 * Resolve the master/child pipeline artifact names (§24.5): the user's explicit override when set,
 * else the `PL_{name}_master`/`PL_{name}_child` convention. Single source of truth for the generator
 * (`pipeline-codegen.ts`) and the Build wizard's live preview — never re-derive the fallback inline.
 */
export const COPY_TIMEOUT_DEFAULT = "0.02:00:00.000";
export const ADF_TIMESPAN_RE = /^\d{1,2}\.\d{2}:\d{2}:\d{2}(\.\d{1,3})?$/;
export function resolveCopyTimeout(draft: SpecDraft): string {
  return draft.copyTimeout?.trim() || COPY_TIMEOUT_DEFAULT;
}
export function sourceSystemToken(draft: SpecDraft): string {
  return (draft.sourceSystem ?? "").trim().toLowerCase().replace(/[^a-z0-9_]/g, "_");
}
/**
 * The `<source>` token of the Bronze landed table (`raw.<source>_<object>`): the source-system label
 * when set, so two systems on the same connector (e.g. two SQL Servers) don't share one landing
 * table; otherwise the connector type, unchanged.
 */
export function bronzeSourceToken(draft: SpecDraft): string {
  return sourceSystemToken(draft) || draft.sourceType;
}
export function resolveMasterPipelineName(draft: SpecDraft): string {
  return draft.masterPipelineName?.trim() || (sourceSystemToken(draft) ? "pl_master" : `PL_${draft.name}_master`);
}
export function resolveChildPipelineName(draft: SpecDraft): string {
  const token = sourceSystemToken(draft);
  return draft.childPipelineName?.trim() || (token ? `pl_${token}_child` : `PL_${draft.name}_child`);
}
/** Resolve the single generated pipeline's name for the `single_pipeline`/`independent_*` patterns (§24.5/§24.12). */
export function resolvePipelineName(draft: SpecDraft): string {
  return draft.pipelineName?.trim() || `PL_${draft.name}`;
}
/** Which pipeline artifact a generated trigger should reference — the master for `master_child`, the
 *  single generated pipeline for `single_pipeline`/`independent_*`. `child_only`/`master_only` never emit
 *  a trigger (§24.12 — "just the one pipeline"). */
export function resolveTriggerPipelineName(draft: SpecDraft): string {
  const pattern = draft.pattern ?? "master_child";
  if (pattern === "single_pipeline" || pattern === "independent_full" || pattern === "independent_watermark") {
    return resolvePipelineName(draft);
  }
  return resolveMasterPipelineName(draft);
}

/** Complete (both sides non-blank), trimmed column mappings — the ones that generate a `mappings` entry. */
export function completeColumnMappings(mappings: ColumnMapping[] | undefined): ColumnMapping[] {
  return (mappings ?? [])
    .map((m) => ({ source: m.source.trim(), sink: m.sink.trim() }))
    .filter((m) => m.source && m.sink);
}

/**
 * Per-object column mappings are live: a control-table pattern (`master_child`/`single_pipeline`) and at
 * least one object carries its own complete mappings. Only then does the control table gain its
 * `ColumnMapping` column and the Copy translator read it per row — otherwise output is unchanged.
 */
export function perObjectColumnMappingsActive(draft: SpecDraft): boolean {
  const pattern = draft.pattern ?? "master_child";
  if (pattern !== "master_child" && pattern !== "single_pipeline") return false;
  return effectiveSourceObjects(draft).some((o) => completeColumnMappings(o.columnMappings).length > 0);
}

/** All non-empty object rows; falls back to the single sourceObject field. */
export function effectiveSourceObjects(draft: SpecDraft): SourceObjectEntry[] {
  const rows = draft.sourceObjects.filter((row) => row.object.trim());
  if (rows.length) return rows;
  const single = draft.sourceObject.trim();
  return single ? [{ object: single }] : [];
}

/** Dataset parameter defaults for the source side. */
export function sourceDatasetDefaults(draft: SpecDraft): DatasetDefaults {
  const raw = draft.sourceObject.trim() || "source_table";
  const profile = getQualifierProfile(draft.sourceType);
  const resolved = resolveQualifiedName({ object: raw }, profile);
  const schema = resolved.schema ?? "dbo";
  const table = resolved.object || "source_table";
  // File-kind connectors (sftp/ftp/blob/s3/sharepoint/...) put the actual file
  // name or wildcard pattern straight into `sourceObject` (the qualifier is a
  // no-op for them, so `table` above is that same raw string) — appending
  // ".csv" unconditionally, as the table-based default below does, doubles up
  // any extension the author already typed (e.g. "invoice_*.csv.csv"). Only
  // synthesize a ".csv" default for schema/table-shaped sources, where `table`
  // is a bare object name with no extension of its own.
  const fileName = isFileConnector(draft.sourceType) ? raw.split("/").pop() || raw : `${table}.csv`;
  return {
    schema,
    table,
    folderPath: raw,
    fileName,
    fallbackName: `DS_${draft.name.trim() || "pipeline"}_source`,
  };
}

/** Dataset parameter defaults for the target side. */
export function targetDatasetDefaults(draft: SpecDraft): DatasetDefaults {
  const folder = draft.targetPath.trim() || "target";
  const file = draft.targetFileName?.trim() || "data.csv";
  return {
    schema: "dbo",
    table: file.replace(/\.[^.]+$/, "") || folder,
    folderPath: folder,
    fileName: file,
    fallbackName: `DS_${draft.name.trim() || "pipeline"}_target`,
  };
}

/**
 * Build the stable logical pipeline specification (AGENTS.md section 10)
 * from form state. This is the developer-facing contract; ADF artifacts
 * are generated from it server-side.
 */
export function buildSpec(draft: SpecDraft): LogicalPipelineSpec {
  const spec: LogicalPipelineSpec = {
    schemaVersion: "2.1",
    pipeline: {
      name: draft.name,
      type: draft.templateId,
      description: `${draft.templateId} pipeline for ${draft.sourceObject}`,
      masterChildPattern: draft.masterChildPattern,
      parallelSettings: {
        isSequential: draft.isSequential,
        batchCount: draft.batchCount,
      },
    },
    source: {
      type: draft.sourceType,
      object: effectiveSourceObjects(draft)[0]?.object ?? draft.sourceObject,
      query: draft.sourceQuery,
      filterCondition: draft.sourceFilterCondition,
      partitionOption: draft.sourcePartitionOption,
      partitionSettings:
        draft.sourcePartitionOption === "DynamicRange" && draft.sourcePartitionColumn?.trim()
          ? { partitionColumnName: draft.sourcePartitionColumn.trim() }
          : undefined,
      isolationLevel: draft.sourceIsolationLevel || undefined,
      queryTimeout: draft.queryTimeout || undefined,
      // Standard metadata/lineage columns (§ sample-guide parity gap 1). A value
      // starting with "@" is emitted as ADF dynamic content; everything else is
      // a literal, same convention already used by customLogColumns' defaults.
      additionalColumns: draft.customMetadata.length ? draft.customMetadata.slice(0, 10) : undefined,
    },
    target: {
      type: draft.targetType,
      path: draft.targetPath,
      fileName: draft.targetFileName,
      copyBehavior: draft.copyBehavior,
      formatSettings: {
        type: SINK_FORMAT_MAP[draft.sinkFileFormat],
        maxRowsPerFile: draft.maxRowsPerFile,
      },
    },
    watermark: {
      enabled: draft.watermarkEnabled,
      type: draft.watermarkType,
      columns: draft.watermarkColumns.slice(0, 5), // Max 5 columns
      watermarkTable: draft.watermarkTable || "control.watermarks",
      watermarkColumn: "watermark_value",
      initialValue: draft.watermarkInitialValue || "1900-01-01",
      filterCondition: draft.watermarkFilterCondition,
    },
    loadType: {
      mode: draft.loadTypeMode,
      initialLoad: draft.initialLoad,
      fullLoad: draft.fullLoad,
      truncateTarget: draft.truncateTarget,
      preCopyScript: draft.preCopyScript,
      postCopyScript: draft.postCopyScript,
    },
    linkedServices: (() => {
      const sourceProps = buildLinkedServiceProperties(draft.sourceLinkedService);
      const targetProps = buildLinkedServiceProperties(draft.targetLinkedService);
      return {
        source: {
          name: draft.sourceLinkedService.name,
          type: linkedServiceAdfType(draft.sourceLinkedService.connectorType),
          connectorType: draft.sourceLinkedService.connectorType,
          authenticationType: draft.sourceLinkedService.authType,
          connectVia: draft.sourceLinkedService.integrationRuntime,
          keyVaultLinkedService: draft.sourceLinkedService.keyVaultLinkedService,
          parameters: sourceProps.parameters as Record<string, { type: string; defaultValue?: unknown }>,
          typeProperties: sourceProps.typeProperties as Record<string, unknown>,
        },
        target: {
          name: draft.targetLinkedService.name,
          type: linkedServiceAdfType(draft.targetLinkedService.connectorType),
          connectorType: draft.targetLinkedService.connectorType,
          authenticationType: draft.targetLinkedService.authType,
          connectVia: draft.targetLinkedService.integrationRuntime,
          keyVaultLinkedService: draft.targetLinkedService.keyVaultLinkedService,
          parameters: targetProps.parameters as Record<string, { type: string; defaultValue?: unknown }>,
          typeProperties: targetProps.typeProperties as Record<string, unknown>,
        },
      };
    })(),
    datasets: (() => {
      const sourceDefaults = sourceDatasetDefaults(draft);
      const targetDefaults = targetDatasetDefaults(draft);
      const sourceProps = buildDatasetProperties(draft.sourceDataset, draft.sourceLinkedService, sourceDefaults);
      const targetProps = buildDatasetProperties(draft.targetDataset, draft.targetLinkedService, targetDefaults);
      return {
        source: {
          name: datasetName(draft.sourceDataset, sourceDefaults),
          linkedServiceName: draft.sourceLinkedService.name,
          type: sourceProps.type as string,
          parameters: sourceProps.parameters as Record<string, { type: string; defaultValue?: unknown }>,
          properties: sourceProps,
        },
        target: {
          name: datasetName(draft.targetDataset, targetDefaults),
          linkedServiceName: draft.targetLinkedService.name,
          type: targetProps.type as string,
          parameters: targetProps.parameters as Record<string, { type: string; defaultValue?: unknown }>,
          properties: targetProps,
        },
      };
    })(),
    sourceObjects: effectiveSourceObjects(draft),
    logging: {
      enableCopyLog: draft.enableCopyLog,
      logLevel: draft.logLevel,
      logStorageSettings: {
        linkedServiceName: draft.targetLinkedService.name,
        path: "logs/copy/",
        enableTimestampsInPath: true,
      },
      enableMetricCapture: true,
      enableAzureMonitor: draft.enableAzureMonitor,
      customLogColumns: draft.customLogColumns.slice(0, 10), // Max 10 columns
    },
    parallelProcessing: {
      dataIntegrationUnits: draft.dataIntegrationUnits,
      parallelCopies: draft.parallelCopies,
      retryCount: draft.retryCount,
      retryIntervalInSeconds: draft.retryIntervalInSeconds,
      timeout: "7.00:00:00",
      enableStaging: draft.enableStaging,
      stagingSettings: draft.enableStaging
        ? {
            linkedServiceName: draft.stagingLinkedService,
            path: draft.stagingPath,
            enableCompression: draft.stagingEnableCompression,
          }
        : undefined,
    },
    errorHandling: {
      onFailure: "Stop",
      skipErrorFile: {
        fileMissing: true,
        fileConflict: false,
        dataInconsistency: true,
      },
      enableFaultInjection: false,
      redirectIncompatibleRows: draft.redirectIncompatibleRows,
      // Sample-guide parity gap: enableSkipIncompatibleRow alone just discards
      // rejected rows. ADF also needs a destination to actually land them.
      redirectRowSettings:
        draft.redirectIncompatibleRows && draft.rejectsLinkedService.trim()
          ? { linkedServiceName: draft.rejectsLinkedService.trim(), path: draft.rejectsPath || "rejects/" }
          : undefined,
    },
    customMetadata: draft.customMetadata.slice(0, 10), // Max 10 columns
    validation: {
      rowCountValidation: draft.rowCountValidationEnabled
        ? {
            enabled: true,
            sourceQuery: `SELECT COUNT(*) FROM ${draft.sourceObject}`,
            targetQuery: `SELECT COUNT(*) FROM ${draft.targetPath}`,
            tolerancePercent: 5,
          }
        : undefined,
      dataQualityRules: draft.dataQualityRules,
    },
    notifications: {
      onSuccess:
        draft.notifyOnSuccess && draft.notificationEmails.length > 0
          ? { email: draft.notificationEmails }
          : undefined,
      onFailure:
        draft.notifyOnFailure && draft.notificationEmails.length > 0
          ? { email: draft.notificationEmails }
          : undefined,
    },
  };

  return spec;
}

/** Client-side pre-validation mirroring the ticket-level validation rules. */
/** RUN-17: regex-only syntax (anchors, quantifiers, groups, escapes, alternation) that LIKE/PATINDEX doesn't have. Keep in parity with spec.py/sttm.py. */
export function looksLikeRegex(pattern: string): boolean {
  const p = pattern.trim();
  return /^\^|\$$|[+\\(){}|*?]/.test(p);
}

const DQ_RULE_TYPES = new Set(["IS_NOT_NULL", "UNIQUE", "LENGTH_LE", "RANGE_BETWEEN", "REGEX_MATCH", "ROW_COUNT_MIN", "FRESHNESS_HOURS"]);

/** ROW_COUNT_MIN checks the whole object; every other rule type targets one column. */
export function dqRuleNeedsColumn(rule: string): boolean {
  return rule !== "ROW_COUNT_MIN";
}

/** Validates the §30.4 data quality rules — kept in parity with worker/app/generator/spec.py's
 *  validate_data_quality_rules, since both sides gate Generate on the same rule shape. */
export type DeleteHandling = "none" | "soft_delete" | "cdc";
export const SOFT_DELETE_COLUMN_DEFAULT = "_is_deleted";
/** Sources with SQL Server change-data-capture (`cdc.fn_cdc_get_all_changes_<instance>`). */
export const CDC_SOURCE_TYPES: SourceTargetType[] = ["sql_server", "azure_sql_database", "azure_sql_managed_instance", "amazon_rds_sql_server"];
const SQL_IDENTIFIER_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

export function resolveSoftDeleteColumn(draft: SpecDraft): string {
  return draft.softDeleteColumn?.trim() || SOFT_DELETE_COLUMN_DEFAULT;
}

/** Hard-delete handling checks; nothing when `deleteHandling` is unset/"none". */
export function validateDeleteHandling(draft: SpecDraft): ValidationIssue[] {
  const mode = draft.deleteHandling ?? "none";
  if (mode === "none") return [];
  const issues: ValidationIssue[] = [];
  const field = "deleteHandling";
  if (!draft.upsertKeyColumns.split(",").some((k) => k.trim())) {
    issues.push({ severity: "error", message: "Delete handling needs key column(s) — set the upsert key columns.", field: "upsertKeyColumns" });
  }
  if (!SQL_IDENTIFIER_RE.test(resolveSoftDeleteColumn(draft))) {
    issues.push({ severity: "error", message: "Soft-delete column must be a plain SQL identifier.", field: "softDeleteColumn" });
  }
  if (draft.loadTypeMode === "full" || draft.fullLoad) {
    issues.push({ severity: "warning", message: "Delete handling only runs on the DeltaLoad branch — a full load already replaces the target.", field });
  }
  if (effectiveSourceObjects(draft).length > 1) {
    issues.push({ severity: "warning", message: "Delete handling marks the pipeline's own landed table using pipeline-level keys; with several source objects every row applies it to that one table.", field });
  }
  if (mode === "cdc") {
    if (!CDC_SOURCE_TYPES.includes(draft.sourceType)) {
      issues.push({ severity: "error", message: "CDC delete detection needs a SQL Server-family source (SQL Server, Azure SQL Database/Managed Instance, RDS SQL Server).", field });
    }
    if (draft.watermarkType === "composite" || draft.watermarkColumns.length !== 1 || draft.watermarkColumns[0]?.type !== "datetime") {
      issues.push({ severity: "error", message: "CDC delete detection maps the watermark window to LSNs — use one datetime watermark column.", field: "watermarkColumns" });
    }
    const ci = draft.cdcCaptureInstance?.trim();
    if (ci && !SQL_IDENTIFIER_RE.test(ci)) {
      issues.push({ severity: "error", message: "CDC capture instance must be a plain SQL identifier.", field: "cdcCaptureInstance" });
    }
  }
  return issues;
}

/** DMT-05: rules whose ViolationCount counts rows, so a percentage of the checked rows is meaningful. */
export const DQ_PCT_RULE_TYPES = new Set(["IS_NOT_NULL", "UNIQUE", "LENGTH_LE", "RANGE_BETWEEN", "REGEX_MATCH"]);

export function validateDataQualityRules(rules: SpecDraft["dataQualityRules"]): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (rules.length > 5) {
    issues.push({ severity: "error", message: "Maximum 5 data quality rules allowed.", field: "dataQualityRules" });
  }
  rules.forEach((rule, i) => {
    const label = rule.name.trim() || `Rule ${i + 1}`;
    if (!rule.name.trim()) {
      issues.push({ severity: "error", message: `Data quality rule ${i + 1} needs a name.`, field: "dataQualityRules" });
    }
    if (!rule.column.trim() && dqRuleNeedsColumn(rule.rule)) {
      issues.push({ severity: "error", message: `Data quality rule "${label}" needs a column.`, field: "dataQualityRules" });
    }
    if (!DQ_RULE_TYPES.has(rule.rule)) {
      issues.push({ severity: "error", message: `Data quality rule "${label}" has an unknown rule type.`, field: "dataQualityRules" });
    }
    if (rule.enforcement && rule.enforcement !== "warn" && rule.enforcement !== "block") {
      issues.push({ severity: "error", message: `Data quality rule "${label}" enforcement must be "warn" or "block".`, field: "dataQualityRules" });
    }
    if (rule.rule === "LENGTH_LE" && !/^\d+$/.test((rule.value ?? "").trim())) {
      issues.push({
        severity: "error",
        message: `Data quality rule "${label}" (LENGTH_LE) needs a whole-number max length in Value.`,
        field: "dataQualityRules",
      });
    }
    if ((rule.rule === "ROW_COUNT_MIN" || rule.rule === "FRESHNESS_HOURS") && !/^\d+$/.test((rule.value ?? "").trim())) {
      issues.push({
        severity: "error",
        message: `Data quality rule "${label}" (${rule.rule}) needs a whole-number ${rule.rule === "ROW_COUNT_MIN" ? "minimum row count" : "maximum age in hours"} in Value.`,
        field: "dataQualityRules",
      });
    }
    if (rule.rule === "RANGE_BETWEEN") {
      const parts = (rule.value ?? "").split(",").map((v) => v.trim());
      const [min, max] = parts;
      if (parts.length !== 2 || !min || !max || Number.isNaN(Number(min)) || Number.isNaN(Number(max)) || Number(min) > Number(max)) {
        issues.push({
          severity: "error",
          message: `Data quality rule "${label}" (RANGE_BETWEEN) needs "min,max" in Value (e.g. "0,100").`,
          field: "dataQualityRules",
        });
      }
    }
    const pct = (rule.maxViolationPct ?? "").trim();
    if (pct && !DQ_PCT_RULE_TYPES.has(rule.rule)) {
      issues.push({
        severity: "error",
        message: `Data quality rule "${label}" (${rule.rule}) is a whole-table check; a max violation % applies only to row-level rules.`,
        field: "dataQualityRules",
      });
    } else if (pct && !(/^\d+(?:\.\d+)?$/.test(pct) && Number(pct) <= 100)) {
      issues.push({ severity: "error", message: `Data quality rule "${label}" max violation % must be a number from 0 to 100.`, field: "dataQualityRules" });
    }
    if (rule.rule === "REGEX_MATCH" && !(rule.value ?? "").trim()) {
      issues.push({
        severity: "error",
        message: `Data quality rule "${label}" (REGEX_MATCH) needs a pattern in Value.`,
        field: "dataQualityRules",
      });
    } else if (rule.rule === "REGEX_MATCH" && looksLikeRegex(rule.value ?? "")) {
      // RUN-17: the check is PATINDEX, so a real regex never matches and every row is a violation.
      issues.push({
        severity: "warning",
        message: `Data quality rule "${label}" (REGEX_MATCH) is generated as T-SQL PATINDEX (LIKE wildcards %, _, [ ]), not a regex; "${(rule.value ?? "").trim()}" looks like a regex and would flag every row. Rewrite it as a LIKE pattern (e.g. %_@_%._%).`,
        field: "dataQualityRules",
      });
    }
  });
  // Two names that sanitize to the same activity token would emit duplicate DQ_<name>_Check activities (DQ-06).
  const seen = new Map<string, string>();
  rules.forEach((rule) => {
    const name = rule.name.trim();
    if (!name) return;
    const token = name.replace(/[^A-Za-z0-9_]/g, "_").toLowerCase();
    const prior = seen.get(token);
    if (prior !== undefined) {
      issues.push({
        severity: "error",
        message: `Data quality rules "${prior}" and "${name}" produce the same activity name — rename one.`,
        field: "dataQualityRules",
      });
    } else {
      seen.set(token, name);
    }
  });
  return issues;
}

/** gaps.md SQL-05 — the sample guides' Full Load rule of thumb. */
export const FULL_LOAD_ROW_WARNING = 1_000_000;

export function validateDraft(draft: SpecDraft): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const name = draft.name.trim();
  if (!name) {
    issues.push({
      severity: "error",
      message: "A pipeline name is required.",
      field: "name",
    });
  } else if (!/^[a-z][a-z0-9_]{2,63}$/.test(name)) {
    issues.push({
      severity: "error",
      message:
        "Pipeline name must start with a lowercase letter and contain only lowercase letters, digits, and underscores (3–64 characters).",
      field: "name",
    });
  }
  if (!draft.projectId) {
    issues.push({ severity: "error", message: "Select a project.", field: "projectId" });
  }
  // SQL-05: the sample guides' rule of thumb is Full Load only below ~1M rows; a larger table set to an
  // unchunked Full Load is warned about, never blocked. Keep in parity with spec.py.
  const rows = draft.estimatedSourceRows;
  const isFull = draft.loadTypeMode === "full" || draft.fullLoad;
  if (rows !== undefined && rows > FULL_LOAD_ROW_WARNING && isFull && !(draft.chunkingEnabled && draft.chunkColumn?.trim())) {
    issues.push({
      severity: "warning",
      message: `About ${rows.toLocaleString("en-US")} source rows on a Full Load: every run re-copies the whole table. Consider a delta (watermark) load or chunking for tables over ${FULL_LOAD_ROW_WARNING.toLocaleString("en-US")} rows.`,
      field: "estimatedSourceRows",
    });
  }
  if (draft.copyTimeout?.trim() && !ADF_TIMESPAN_RE.test(draft.copyTimeout.trim())) {
    issues.push({ severity: "error", message: "Copy activity timeout must use ADF's d.hh:mm:ss format (e.g. 0.02:00:00).", field: "copyTimeout" });
  }
  // Master/child artifact-name overrides (§24.5) — optional, but must be ADF-legal when set, and
  // distinct from each other (both would otherwise resolve to the exact same fallback pattern gone
  // wrong, which is a real correctness bug, not a style preference).
  {
    const master = draft.masterPipelineName?.trim();
    const child = draft.childPipelineName?.trim();
    if (master && !PL_NAME_RE.test(master)) {
      issues.push({
        severity: "error",
        message: "Master pipeline name must start with a letter or underscore and contain only letters, digits, and underscores (max 260 characters).",
        field: "masterPipelineName",
      });
    }
    if (child && !PL_NAME_RE.test(child)) {
      issues.push({
        severity: "error",
        message: "Child pipeline name must start with a letter or underscore and contain only letters, digits, and underscores (max 260 characters).",
        field: "childPipelineName",
      });
    }
    if (master && child && master === child) {
      issues.push({
        severity: "error",
        message: "Master and child pipeline names must be different.",
        field: "childPipelineName",
      });
    }
    const single = draft.pipelineName?.trim();
    if (single && !PL_NAME_RE.test(single)) {
      issues.push({
        severity: "error",
        message: "Pipeline name must start with a letter or underscore and contain only letters, digits, and underscores (max 260 characters).",
        field: "pipelineName",
      });
    }
  }
  // §24.3 pattern-specific requirements. `child_only` needs an existing master to attach to (by name);
  // `master_only` needs an existing child to delegate to (by name) — both are just the override fields
  // above, required only for these two patterns since master_child/single/independent don't reference
  // an artifact outside their own generated set.
  if (draft.pattern === "child_only" && !draft.childPipelineName?.trim()) {
    issues.push({
      severity: "error",
      message: "Child pipeline name is required for the \"child only\" pattern (it must match the existing master's ExecutePipeline reference).",
      field: "childPipelineName",
    });
  }
  if (draft.pattern === "master_only" && !draft.childPipelineName?.trim()) {
    issues.push({
      severity: "error",
      message: "Child pipeline name is required for the \"master only\" pattern (the existing child this master delegates to).",
      field: "childPipelineName",
    });
  }
  // Schema-drift slice — explicit column mappings: complete pairs, one mapping per sink column.
  const mappings = draft.columnMappings ?? [];
  const sinks = new Set<string>();
  for (const [i, m] of mappings.entries()) {
    const source = m.source.trim();
    const sink = m.sink.trim();
    if (!source || !sink) {
      issues.push({ severity: "error", message: `Column mapping ${i + 1} needs both a source and a sink column.`, field: "columnMappings" });
      continue;
    }
    if (sinks.has(sink.toLowerCase())) {
      issues.push({ severity: "error", message: `Column mapping ${i + 1}: sink column "${sink}" is mapped more than once.`, field: "columnMappings" });
    }
    sinks.add(sink.toLowerCase());
  }
  const mappedObjects = effectiveSourceObjects(draft);
  for (const obj of mappedObjects) {
    const objSinks = new Set<string>();
    for (const [i, m] of (obj.columnMappings ?? []).entries()) {
      const source = m.source.trim();
      const sink = m.sink.trim();
      const label = `Object "${obj.object}" column mapping ${i + 1}`;
      if (!source || !sink) {
        issues.push({ severity: "error", message: `${label} needs both a source and a sink column.`, field: "sourceObjects" });
        continue;
      }
      if (objSinks.has(sink.toLowerCase())) {
        issues.push({ severity: "error", message: `${label}: sink column "${sink}" is mapped more than once.`, field: "sourceObjects" });
      }
      objSinks.add(sink.toLowerCase());
    }
  }
  const pattern = draft.pattern ?? "master_child";
  const objectsWithOwn = mappedObjects.filter((o) => completeColumnMappings(o.columnMappings).length > 0);
  if (pattern !== "master_child" && pattern !== "single_pipeline" && objectsWithOwn.length) {
    issues.push({
      severity: "warning",
      message: "Per-object column mappings are only applied by the master/child and single-pipeline patterns — they are ignored here.",
      field: "sourceObjects",
    });
  }
  if (mappings.length && mappedObjects.length > 1 && !(perObjectColumnMappingsActive(draft) && objectsWithOwn.length === mappedObjects.length)) {
    issues.push({
      severity: "warning",
      message: "Column mappings apply to every source object's Copy — objects without these columns will fail to copy.",
      field: "columnMappings",
    });
  }
  // Gap #30 — child dispatch cases: complete, unique match values, ADF-legal names, never the master.
  if (draft.pattern === "master_child" || draft.pattern === "master_only") {
    const seen = new Set<string>();
    const master = resolveMasterPipelineName(draft);
    for (const [i, c] of (draft.childDispatch ?? []).entries()) {
      const match = c.matchValue.trim();
      const child = c.childPipelineName.trim();
      const label = `Child dispatch case ${i + 1}`;
      if (!match || !child) {
        issues.push({ severity: "error", message: `${label} needs both a TaskGroup value and a child pipeline name.`, field: "childDispatch" });
        continue;
      }
      if (seen.has(match)) {
        issues.push({ severity: "error", message: `${label}: TaskGroup value "${match}" is used by more than one case.`, field: "childDispatch" });
      }
      seen.add(match);
      if (!PL_NAME_RE.test(child)) {
        issues.push({ severity: "error", message: `${label}: child pipeline name must start with a letter or underscore and contain only letters, digits, and underscores (max 260 characters).`, field: "childDispatch" });
      } else if (child === master) {
        issues.push({ severity: "error", message: `${label}: a master can't dispatch to itself.`, field: "childDispatch" });
      }
    }
  }
  // Source object list (tables / input files) — form or uploaded file
  const objects = effectiveSourceObjects(draft);
  if (!objects.length) {
    issues.push({
      severity: "error",
      message: "Add at least one source object (table or input file). Upload a list file or fill the form.",
      field: "sourceObjects",
    });
  }
  if (objects.length > 200) {
    issues.push({
      severity: "error",
      message: "Maximum 200 source objects per pipeline.",
      field: "sourceObjects",
    });
  }
  // Qualifier resolution (naming-convention edge cases): connector-aware,
  // never a blind ".".split on the raw object string (lib/adf/qualifier.ts).
  {
    const profile = getQualifierProfile(draft.sourceType);
    const resolvedObjects = objects.map((row) => ({ row, resolved: resolveQualifiedName(row, profile) }));
    // Always-on safety rule (§25.5): two objects that would collide on the
    // same generated target-name token are a correctness bug, not a naming
    // style preference — never silently generate one artifact from two.
    const collisions = checkQualifierCollision(
      resolvedObjects.map(({ row, resolved }) => ({ key: row.object, token: buildSafeQualifierToken(resolved) })),
    );
    for (const collision of collisions) {
      issues.push({
        severity: "error",
        message: `These source objects all generate the same artifact name "${collision.token}" once sanitized: ${collision.keys.join(", ")}. Give them distinct names or an explicit override.`,
        field: "sourceObjects",
      });
    }
    // SAP flag-only check: some objects need a different extraction
    // mechanism entirely (RFC/Open Hub), which no naming fix can solve.
    for (const { row, resolved } of resolvedObjects) {
      const sapIssue = checkSapExtractionSupport(draft.sourceType, resolved.object);
      if (sapIssue) {
        issues.push({
          severity: sapIssue.level === "error" ? "error" : "warning",
          message: sapIssue.message,
          field: "sourceObjects",
          source: row.object,
        });
      }
    }
  }
  // Linked services — connector-driven required fields
  issues.push(...validateLinkedServiceDraft(draft.sourceLinkedService, "sourceLinkedService"));
  issues.push(...validateLinkedServiceDraft(draft.targetLinkedService, "targetLinkedService"));
  // Datasets
  issues.push(...validateDatasetDraft(draft.sourceDataset, draft.sourceLinkedService, sourceDatasetDefaults(draft), "sourceDataset"));
  issues.push(...validateDatasetDraft(draft.targetDataset, draft.targetLinkedService, targetDatasetDefaults(draft), "targetDataset"));
  if (!draft.targetPath.trim()) {
    issues.push({ severity: "error", message: "Target path is required.", field: "targetPath" });
  }
  // Watermark validation for incremental/composite loads
  if (draft.watermarkEnabled && draft.loadTypeMode !== "full") {
    if (!draft.watermarkColumns.length) {
      issues.push({
        severity: "error",
        message: "At least one watermark column is required for incremental loads.",
        field: "watermarkColumns",
      });
    }
    if (draft.watermarkColumns.length > 5) {
      issues.push({
        severity: "error",
        message: "Maximum 5 watermark columns allowed for composite watermark.",
        field: "watermarkColumns",
      });
    }
    if (!draft.watermarkTable.trim()) {
      issues.push({
        severity: "error",
        message: "Watermark table is required for incremental loads.",
        field: "watermarkTable",
      });
    }
  }
  // Custom log columns validation
  if (draft.customLogColumns.length > 10) {
    issues.push({
      severity: "error",
      message: "Maximum 10 custom log columns allowed.",
      field: "customLogColumns",
    });
  }
  // Custom metadata validation
  if (draft.customMetadata.length > 10) {
    issues.push({
      severity: "error",
      message: "Maximum 10 custom metadata columns allowed.",
      field: "customMetadata",
    });
  }
  // Data quality rules validation (§30.4 — wired into generated pre-copy check
  // activities by pipeline-codegen.ts's dataQualityActivities; a rule that fails
  // these checks would either silently no-op or generate a broken query).
  issues.push(...validateDataQualityRules(draft.dataQualityRules));
  // Batch count validation for parallel execution
  if (draft.batchCount < 1 || draft.batchCount > 50) {
    issues.push({
      severity: "error",
      message: "Batch count must be between 1 and 50.",
      field: "batchCount",
    });
  }
  // Parallel copies validation
  if (draft.parallelCopies < 1 || draft.parallelCopies > 50) {
    issues.push({
      severity: "error",
      message: "Parallel copies must be between 1 and 50.",
      field: "parallelCopies",
    });
  }
  // Staging
  if (draft.enableStaging && !draft.stagingLinkedService.trim()) {
    issues.push({
      severity: "error",
      message: "A staging linked service is required when staged copy is enabled.",
      field: "stagingLinkedService",
    });
  }
  // Dynamic-range partitioning needs a partition column
  if (draft.sourcePartitionOption === "DynamicRange" && !draft.sourcePartitionColumn?.trim()) {
    issues.push({
      severity: "error",
      message: "A partition column is required for dynamic-range partitioned reads.",
      field: "sourcePartitionColumn",
    });
  }
  // Upsert needs key columns
  if (draft.writeBehavior === "Upsert" && !draft.upsertKeyColumns.trim()) {
    issues.push({
      severity: "error",
      message: "Upsert key column(s) are required when the write behavior is Upsert.",
      field: "upsertKeyColumns",
    });
  }
  issues.push(...validateDeleteHandling(draft));
  // Trigger
  if (
    draft.triggerEnabled &&
    draft.triggerType !== "blobEvents" &&
    (!Number.isFinite(draft.triggerInterval) || draft.triggerInterval < 1)
  ) {
    issues.push({
      severity: "error",
      message: "Trigger interval must be a positive whole number.",
      field: "triggerInterval",
    });
  }
  if (draft.triggerEnabled && draft.triggerType === "blobEvents") {
    if (!draft.triggerStorageLinkedService?.trim()) {
      issues.push({
        severity: "error",
        message: "A storage linked service is required for a file-arrival trigger.",
        field: "triggerStorageLinkedService",
      });
    }
    if (!draft.triggerContainerName?.trim()) {
      issues.push({
        severity: "error",
        message: "A container name is required for a file-arrival trigger.",
        field: "triggerContainerName",
      });
    }
  }
  // Redirect-incompatible-rows needs a destination or rejected rows are just discarded.
  if (draft.redirectIncompatibleRows && !draft.rejectsLinkedService.trim()) {
    issues.push({
      severity: "error",
      message: "A rejects destination linked service is required when redirecting incompatible rows.",
      field: "rejectsLinkedService",
    });
  }
  // Chunked historical/initial load
  if (draft.chunkingEnabled) {
    if (!draft.chunkColumn?.trim()) {
      issues.push({
        severity: "error",
        message: "A chunk column (numeric or date range) is required when chunking is enabled.",
        field: "chunkColumn",
      });
    }
    if (draft.chunkSizeRows < 1000) {
      issues.push({
        severity: "error",
        message: "Chunk size must be at least 1,000 rows.",
        field: "chunkSizeRows",
      });
    }
    if (draft.maxParallelChunks < 1 || draft.maxParallelChunks > 50) {
      issues.push({
        severity: "error",
        message: "Max parallel chunks must be between 1 and 50.",
        field: "maxParallelChunks",
      });
    }
  }
  // Medallion layer (Bronze/Silver/Gold) — Silver/Gold need a resolvable target
  // entity/domain (layer-wise table naming standard) and at least one valid
  // transformation; Bronze is a no-op here (unchanged existing behavior).
  issues.push(...validateMedallionFields(draft.medallionLayer, { curatedEntity: draft.curatedEntity, martDomain: draft.martDomain }));
  issues.push(...validateTransformationRules(draft.medallionLayer, draft.transformations));
  // G2: Silver/Gold transformations run as Script (SQL) activities on the target store.
  if (draft.medallionLayer !== "bronze" && isFileConnector(draft.targetType)) {
    issues.push({
      severity: "warning",
      message: `The ${draft.medallionLayer} layer runs SQL transformations on the target, so the target must be a SQL-capable store, not a file store.`,
      field: "targetType",
    });
  }
  // Connection control table
  if (draft.connectionControlEnabled && !draft.connectionControlTable.trim()) {
    issues.push({
      severity: "error",
      message: "A connection control table name is required when it's enabled.",
      field: "connectionControlTable",
    });
  }
  return issues;
}

/** Field-level error lookup for inline validation rendering. */
export function fieldErrors(issues: ValidationIssue[], field: string): string[] {
  return issues
    .filter((issue) => issue.field === field)
    .map((issue) => issue.message);
}
/** Gap #30 — trimmed, complete dispatch cases; `[]` (single-child master) unless the pattern has a master. */
export function childDispatchCases(draft: Pick<SpecDraft, "childDispatch" | "pattern">): ChildDispatchCase[] {
  if (draft.pattern !== "master_child" && draft.pattern !== "master_only") return [];
  return (draft.childDispatch ?? [])
    .map((c) => ({ matchValue: c.matchValue.trim(), childPipelineName: c.childPipelineName.trim() }))
    .filter((c) => c.matchValue && c.childPipelineName);
}
/** Trimmed, de-duplicated pipeline annotations (gap #33) — `[]` when none. */
export function pipelineAnnotations(draft: Pick<SpecDraft, "annotations">): string[] {
  return [...new Set((draft.annotations ?? []).map((a) => a.trim()).filter(Boolean))];
}
