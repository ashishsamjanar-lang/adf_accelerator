/**
 * The logical pipeline spec and everything it's built from (AGENTS.md
 * section 5, section 10). This mirrors the backend/worker generator's input
 * contract 1:1 — see `lib/adf/spec.ts` (`SpecDraft` -> `buildSpec`) and
 * `worker/app/generator/` (the running port).
 */

import type { ArtifactLayer, ArtifactType, DeploymentState, PipelineTemplateId } from "./core";
import type { AuthenticationType, SourceTargetType } from "./connectors";

// Load types
export type LoadType = "full" | "incremental" | "initial" | "composite_watermark";

// Watermark types
export type WatermarkType = "single" | "composite";

// Watermark column configuration
export interface WatermarkColumn {
  name: string;
  type: "datetime" | "int" | "bigint" | "string";
  order: number;
}

/**
 * One row of the user-provided object list (tables / input files).
 * Populated either by filling the form or by uploading a CSV/TSV/TXT/JSON
 * file; each entry becomes a control-table seed row (metadata-driven).
 */
export interface SourceObjectEntry {
  /**
   * Bare object/entity/file name, or the legacy full dotted string
   * (schema.table, catalog.schema.table, container/folder/file for file
   * sources) when `catalog`/`schema` below aren't set. Parsed connector-
   * aware via `lib/adf/qualifier.ts` `resolveQualifiedName` — never split
   * blindly on "." — so 3-part and schema-less sources resolve correctly.
   */
  object: string;
  /** Optional catalog/database segment — only meaningful for 3-part sources (e.g. Snowflake, SAP HANA with a tenant DB). */
  catalog?: string;
  /** Optional schema segment, kept separate from `object` so schema-less sources (SAP OData, Salesforce, ...) can omit it entirely. */
  schema?: string;
  /** Escape hatch: exact identifier to use when querying the source, bypassing qualifier parsing/case-conformance entirely. */
  qualifierOverride?: string;
  /** Escape hatch: skip automatic case-conformance (e.g. Oracle/SAP HANA upper-casing) for this one object. */
  caseOverride?: "as-typed";
  /** Optional per-object watermark column override. */
  watermarkColumn?: string;
  /** Optional per-object loading behavior override (control table column). */
  loadingBehavior?: "FullLoad" | "DeltaLoad";
  /**
   * Optional per-object Copy column mappings (control-table `ColumnMapping` column). Honored by the
   * `master_child`/`single_pipeline` patterns; replaces the pipeline-level `columnMappings` for this
   * object only. Undefined/empty = the pipeline-level mappings apply.
   */
  columnMappings?: ColumnMapping[];
}

/** One explicit Copy column mapping (schema-drift slice) — source column → sink column, by name. */
export interface ColumnMapping {
  source: string;
  sink: string;
}

// Watermark configuration
export interface WatermarkConfig {
  enabled: boolean;
  type: WatermarkType;
  columns: WatermarkColumn[]; // Up to 5 columns for composite
  watermarkTable: string;
  watermarkColumn: string;
  initialValue: string;
  filterCondition?: string;
}

// Load type configuration
export interface LoadTypeConfig {
  mode: LoadType;
  initialLoad: boolean;
  fullLoad: boolean;
  truncateTarget: boolean;
  preCopyScript?: string;
  postCopyScript?: string;
}

// Linked service configuration
export interface LinkedServiceConfig {
  name: string;
  type: string;
  connectionString?: string;
  serviceEndpoint?: string;
  authenticationType?: AuthenticationType;
  connectVia?: string;
  tenant?: string;
  servicePrincipalId?: string;
  sasUri?: string;
  accountKind?: string;
  /** SourceTargetType the linked service was built from (connector registry key). */
  connectorType?: SourceTargetType;
  /** Linked service that stores secrets referenced by this connection. */
  keyVaultLinkedService?: string;
  /** ADF linked service parameters (parameterized fields + secret names). */
  parameters?: Record<string, { type: string; defaultValue?: unknown }>;
  /** Resolved connector-specific typeProperties (secrets are AKV references). */
  typeProperties?: Record<string, unknown>;
}

// Dataset configuration
export interface DatasetConfig {
  name: string;
  linkedServiceName: string;
  /** ADF dataset type, e.g. AzureSqlTable / DelimitedText. */
  type?: string;
  /** Dataset parameters (SchemaName/TableName or FolderPath/FileName). */
  parameters?: Record<string, { type: string; defaultValue?: unknown }>;
  /** Resolved dataset properties (schema/table or location + format). */
  properties?: Record<string, unknown>;
}

// Logging configuration
export interface LoggingConfig {
  enableCopyLog: boolean;
  logLevel: "Info" | "Warning" | "Error" | "Verbose" | "None";
  logStorageSettings: {
    linkedServiceName: string;
    path: string;
    enableTimestampsInPath: boolean;
  };
  enableMetricCapture: boolean;
  enableAzureMonitor: boolean;
  customLogColumns: Array<{ name: string; value: string }>; // Up to 10
}

// Parallel processing settings
export interface ParallelProcessingConfig {
  dataIntegrationUnits?: number;
  parallelCopies?: number;
  cloudDataMovementUnits?: number;
  timeToLive?: string;
  retryCount: number;
  retryIntervalInSeconds: number;
  timeout: string;
  enableStaging: boolean;
  stagingSettings?: {
    linkedServiceName: string;
    path: string;
    enableCompression: boolean;
  };
}

// Error handling configuration
export interface ErrorHandlingConfig {
  onFailure: "Stop" | "Continue";
  skipErrorFile: {
    fileMissing: boolean;
    fileConflict: boolean;
    dataInconsistency: boolean;
  };
  enableFaultInjection: boolean;
  redirectIncompatibleRows: boolean;
  redirectRowSettings?: {
    linkedServiceName: string;
    path: string;
  };
}

// Validation rules
export interface ValidationConfig {
  preValidationScripts?: string[];
  postValidationScripts?: string[];
  rowCountValidation?: {
    enabled: boolean;
    sourceQuery: string;
    targetQuery: string;
    tolerancePercent: number;
  };
  schemaValidation?: {
    enabled: boolean;
    sourceSchema: string;
    targetSchema: string;
  };
  dataQualityRules?: Array<{
    name: string;
    column: string;
    rule: string;
    /** Rule-specific threshold/pattern: max length (LENGTH_LE), "min,max" (RANGE_BETWEEN),
     *  or a pattern (REGEX_MATCH). Unused by IS_NOT_NULL/UNIQUE. */
    value?: string;
    /** "warn" (default) logs the violation count in ADF run history; "block" fails the
     *  pipeline run via a generated IfCondition/Fail gate (AGENTS.md §30.4). */
    enforcement?: "warn" | "block";
    /** DMT-05: breach only when more than this % (0-100) of checked rows violate a row-level rule. */
    maxViolationPct?: string;
  }>;
}

// Notification configuration
export interface NotificationConfig {
  onSuccess?: {
    email?: string[];
    webhook?: string;
  };
  onFailure?: {
    email?: string[];
    webhook?: string;
  };
}

/**
 * Logical, execution-engine-neutral pipeline spec (AGENTS.md section 5, §23.3).
 * Bump `schemaVersion` (here, `buildSpec` in lib/adf/spec.ts, and the worker's
 * `spec.py` twin, all three together) whenever a field is added to or removed
 * from this shape — it must reflect the actual contract, not stay frozen while
 * the shape grows underneath it (AGENTS.md §23.13).
 */
export interface LogicalPipelineSpec {
  schemaVersion: "2.1";
  pipeline: {
    name: string;
    type: PipelineTemplateId;
    description?: string;
    masterChildPattern: boolean;
    parallelSettings: {
      isSequential: boolean;
      batchCount: number;
      degreeOfParallelism?: number;
    };
  };
  source: {
    type: SourceTargetType;
    object: string;
    query?: string;
    filterCondition?: string;
    partitionOption?: "None" | "DynamicRange" | "PhysicalPartitionsOfTable";
    partitionSettings?: {
      partitionColumnName?: string;
      partitionUpperBound?: number;
      partitionLowerBound?: number;
    };
    additionalColumns?: Array<{ name: string; value: string }>;
    isolationLevel?: "ReadCommitted" | "ReadUncommitted" | "RepeatableRead" | "Serializable" | "Snapshot";
    commandTimeout?: string;
    statisticsEnabled?: boolean;
    queryTimeout?: string;
  };
  target: {
    type: SourceTargetType;
    path: string;
    fileName?: string;
    copyBehavior?: "PreserveHierarchy" | "FlattenHierarchy" | "MergeFiles";
    formatSettings?: {
      type: "TextFormat" | "JsonFormat" | "AvroFormat" | "OrcFormat" | "ParquetFormat" | "BinaryFormat" | "XmlFormat" | "DeltaFormat";
      compressionCodec?: string;
      maxRowsPerFile?: number;
      fileGroupName?: string;
    };
  };
  watermark: WatermarkConfig;
  loadType: LoadTypeConfig;
  linkedServices: {
    source: LinkedServiceConfig;
    target: LinkedServiceConfig;
  };
  /** Generated datasets referenced by the pipeline copy activities. */
  datasets: {
    source: DatasetConfig;
    target: DatasetConfig;
  };
  /** Full object list (tables / input files) feeding the control table. */
  sourceObjects?: SourceObjectEntry[];
  logging: LoggingConfig;
  parallelProcessing: ParallelProcessingConfig;
  errorHandling: ErrorHandlingConfig;
  customMetadata: Array<{ name: string; value: string }>; // Up to 10
  validation: ValidationConfig;
  notifications: NotificationConfig;
}

export interface PipelineTemplate {
  id: PipelineTemplateId;
  name: string;
  version: string;
  description: string;
  supportedSourceTypes: string[];
  supportedTargetTypes: string[];
  fields: string[];
  defaults: Record<string, string>;
}

export interface PipelineDefinition {
  id: string;
  name: string;
  type: PipelineTemplateId;
  projectId: string;
  projectName: string;
  teamId: string;
  teamName: string;
  specVersion: string;
  state: DeploymentState;
  latestArtifactVersion?: string;
  createdAt: string;
  updatedAt: string;
  createdBy: string;
  createdByName?: string;
  updatedBy?: string;
  updatedByName?: string;
  spec: LogicalPipelineSpec;
  /**
   * Raw `SpecDraft` payload (camelCase) as last saved — used to reopen this pipeline in the Build
   * wizard (edit mode: rename master/child, append objects) since `spec` above is the reduced,
   * one-way `LogicalPipelineSpec` and can't hydrate a wizard draft. Omitted by mock fixtures — demo
   * mode degrades edit-mode to a fresh draft with just name/type carried over.
   */
  draft?: Record<string, unknown>;
}

export interface ArtifactVersion {
  id: string;
  pipelineId: string;
  pipelineName: string;
  fileName: string;
  fileType: "json" | "sql" | "config";
  artifactType: ArtifactType;
  layer: ArtifactLayer;
  version: string; // immutable version identifier
  state: "generated" | "validated" | "committed";
  bytes: number;
  content?: string;
}
