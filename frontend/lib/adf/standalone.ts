/**
 * Standalone ADF artifact generation.
 *
 * The Build surface can author a single artifact — a linked service, a
 * dataset, or a trigger — independently of a pipeline (AGENTS.md §24.2:
 * "Artifact-type switch at top: Pipeline / Linked service / Dataset /
 * Trigger"). Every value here comes from the user-populated draft; no
 * business values are fabricated and secrets stay as Key Vault names.
 *
 * The pipeline generator (`pipeline-codegen.ts`) still emits the full
 * 9-artifact set for a pipeline; this module is the single-artifact path.
 */
import type { ArtifactVersion, ValidationIssue } from "@/types";
import {
  buildDatasetProperties,
  buildLinkedServiceProperties,
  datasetName,
  connectorSchema,
  validateDatasetDraft,
  validateLinkedServiceDraft,
  type DatasetDefaults,
  type DatasetDraft,
  type LinkedServiceDraft,
} from "./connections";

export type StandaloneArtifactKind = "linked_service" | "dataset" | "trigger";

export const STANDALONE_KINDS: Array<{ kind: StandaloneArtifactKind; label: string; noun: string }> = [
  { kind: "linked_service", label: "Linked service", noun: "linked service" },
  { kind: "dataset", label: "Dataset", noun: "dataset" },
  { kind: "trigger", label: "Trigger", noun: "trigger" },
];

// ---------------------------------------------------------------------------
// Trigger draft
// ---------------------------------------------------------------------------

export interface StandaloneTriggerDraft {
  name: string;
  /** Pipeline the trigger starts (an existing ADF pipeline name). */
  pipelineName: string;
  triggerType: "schedule" | "tumbling";
  frequency: "Minute" | "Hour" | "Day" | "Week" | "Month";
  interval: number;
  startTime: string;
  timeZone: string;
  /** Start the trigger on deploy, or leave it stopped. */
  started: boolean;
}

export function emptyTriggerDraft(): StandaloneTriggerDraft {
  return {
    name: "",
    pipelineName: "",
    triggerType: "schedule",
    frequency: "Day",
    interval: 1,
    startTime: "",
    timeZone: "UTC",
    started: false,
  };
}

const ADF_NAME_RE = /^[A-Za-z][A-Za-z0-9_]{1,63}$/;

export function validateTriggerDraft(t: StandaloneTriggerDraft): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!t.name.trim()) {
    issues.push({ severity: "error", message: "A trigger name is required.", field: "name" });
  } else if (!ADF_NAME_RE.test(t.name.trim())) {
    issues.push({
      severity: "error",
      message: `Trigger name "${t.name}" must start with a letter and contain only letters, digits and underscores (2–64 chars).`,
      field: "name",
    });
  }
  if (!t.pipelineName.trim()) {
    issues.push({ severity: "error", message: "The trigger must reference a pipeline to start.", field: "pipelineName" });
  }
  if (!Number.isFinite(t.interval) || t.interval < 1) {
    issues.push({ severity: "error", message: "Recurrence interval must be 1 or greater.", field: "interval" });
  }
  return issues;
}

// ---------------------------------------------------------------------------
// Artifact builders — one ArtifactVersion each
// ---------------------------------------------------------------------------

function toArtifact(
  fileName: string,
  artifactType: ArtifactVersion["artifactType"],
  layer: ArtifactVersion["layer"],
  fileType: ArtifactVersion["fileType"],
  value: unknown,
): ArtifactVersion {
  const content = (typeof value === "string" ? value : JSON.stringify(value, null, 2)) + "\n";
  const name = fileName.replace(/\.(json|sql|config)$/, "");
  return {
    id: `art-${fileName}-${Date.now()}`,
    pipelineId: `standalone-${name}`,
    pipelineName: name,
    fileName,
    fileType,
    artifactType,
    layer,
    version: "v1.0.0",
    state: "generated",
    bytes: content.length,
    content,
  };
}

/** Dataset defaults derived from the standalone draft itself (no pipeline context). */
export function standaloneDatasetDefaults(ds: DatasetDraft, ls: LinkedServiceDraft): DatasetDefaults {
  return {
    schema: ds.schema.trim() || "dbo",
    table: ds.table.trim() || "source_table",
    folderPath: ds.folderPath.trim() || "/",
    fileName: ds.fileName.trim() || "data.csv",
    fallbackName: `DS_${ls.name.trim() || "dataset"}`,
  };
}

export function buildStandaloneLinkedService(ls: LinkedServiceDraft): ArtifactVersion {
  const value = {
    name: ls.name,
    type: "Microsoft.DataFactory/factories/linkedServices",
    properties: buildLinkedServiceProperties(ls),
  };
  // `ls.name` already carries the `LS_` convention prefix — don't double it.
  return toArtifact(`${ls.name}.json`, "linked_service", "connection", "json", value);
}

export function buildStandaloneDataset(ds: DatasetDraft, ls: LinkedServiceDraft): ArtifactVersion {
  const defaults = standaloneDatasetDefaults(ds, ls);
  const value = {
    name: datasetName(ds, defaults),
    type: "Microsoft.DataFactory/factories/datasets",
    properties: buildDatasetProperties(ds, ls, defaults),
  };
  return toArtifact(`${value.name}.json`, "dataset", "data", "json", value);
}

export function buildStandaloneTrigger(t: StandaloneTriggerDraft): ArtifactVersion {
  const startTime = t.startTime.trim() || "2026-01-01T00:00:00Z";
  const timeZone = t.timeZone.trim() || "UTC";
  const interval = Number.isFinite(t.interval) && t.interval > 0 ? t.interval : 1;
  const pipelineRef = {
    pipelineReference: { referenceName: t.pipelineName, type: "PipelineReference" },
    parameters: {},
  };
  const value =
    t.triggerType === "tumbling"
      ? {
          name: t.name,
          type: "Microsoft.DataFactory/factories/triggers",
          properties: {
            annotations: [],
            runtimeState: t.started ? "Started" : "Stopped",
            type: "TumblingWindowTrigger",
            typeProperties: {
              frequency: t.frequency === "Minute" ? "Minute" : "Hour",
              interval,
              startTime,
              delay: "00:00:00",
              maxConcurrency: 1,
            },
            pipeline: pipelineRef,
          },
        }
      : {
          name: t.name,
          type: "Microsoft.DataFactory/factories/triggers",
          properties: {
            annotations: [],
            runtimeState: t.started ? "Started" : "Stopped",
            type: "ScheduleTrigger",
            typeProperties: {
              recurrence: { frequency: t.frequency, interval, startTime, timeZone },
            },
            pipelines: [pipelineRef],
          },
        };
  return toArtifact(`TR_${t.name}.json`, "trigger", "runtime", "json", value);
}

// ---------------------------------------------------------------------------
// Validation dispatch
// ---------------------------------------------------------------------------

export function validateStandaloneLinkedService(ls: LinkedServiceDraft): ValidationIssue[] {
  return validateLinkedServiceDraft(ls, "linkedService");
}

export function validateStandaloneDataset(ds: DatasetDraft, ls: LinkedServiceDraft): ValidationIssue[] {
  const issues = validateLinkedServiceDraft(ls, "linkedService").length
    ? [{ severity: "warning" as const, message: "Complete the linked service before the dataset can be generated cleanly.", field: "linkedService" }]
    : [];
  return [...issues, ...validateDatasetDraft(ds, ls, standaloneDatasetDefaults(ds, ls), "dataset")];
}

/** Human summary of a linked service draft for the review panel. */
export function linkedServiceSummary(ls: LinkedServiceDraft): Array<{ label: string; value: string }> {
  const schema = connectorSchema(ls.connectorType);
  return [
    { label: "Name", value: ls.name || "—" },
    { label: "Connector", value: `${schema.displayName} · ${schema.adfType}` },
    { label: "Authentication", value: ls.authType.replace(/_/g, " ") },
    { label: "Integration runtime", value: ls.integrationRuntime || "—" },
    { label: "Key Vault linked service", value: ls.keyVaultLinkedService || "—" },
  ];
}
