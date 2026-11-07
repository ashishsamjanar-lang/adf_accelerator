"use client";

import { Pencil, RotateCcw } from "lucide-react";
import type { FeatureProps, OnboardingPrefill } from "@/features/types";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { type SpecDraft } from "@/lib/adf/spec";
import { connectorSchema } from "@/lib/adf/connections";
import type { DatasetDraft, LinkedServiceDraft } from "@/lib/adf/connections";
import { type SavedConnection } from "@/lib/adf/connection-store";
import { useCatalogNames } from "@/lib/api/use-catalog-names";
import { describeRule, evaluateName, isConfigured, ruleFor } from "@/lib/adf/naming";
import type { NamingConvention } from "@/types";
import { MEDALLION_LAYERS, MEDALLION_LAYER_INFO, type MedallionLayer } from "@/lib/adf/medallion";
import type { PipelineDefinition, PipelineTemplateId, WatermarkColumn } from "@/types";

/**
 * Create Pipeline uses exactly one template: Metadata-Driven Load.
 * Every pipeline created here is generated from this template plus a
 * metadata/control table; there is no free-form pipeline authoring.
 */
export const METADATA_TEMPLATE_ID: PipelineTemplateId = "metadata_driven_load";

export interface WizardProps extends FeatureProps {
  /**
   * `persisted` is the real backend `PipelineDefinition` when
   * `POST /api/v1/pipelines` succeeded — callers should prefer it (it carries
   * a pushable server-tracked id) and fall back to building a client-only
   * entry from `draft` only when the backend was unreachable (demo mode / API
   * down), exactly as before this pipeline gained real persistence.
   */
  onCreated: (draft: SpecDraft, persisted?: PipelineDefinition) => void;
  /** Set when opened from Pipelines' "Edit", Drafts' "Continue setup", or the Basics step's own
   * "Add objects to an existing pipeline" picker — hydrates the wizard from that pipeline's saved
   * draft and switches Save from create to update (§24.1's round-trip edit, scoped to "reopen and
   * edit a pipeline this app already created", not full Git round-trip). */
  editPipelineId?: string | null;
  /** Called instead of `onCreated` once an edit-mode Save (`PUT /pipelines/{id}`) succeeds. */
  onUpdated?: (pipeline: PipelineDefinition) => void;
  /** Onboarding's "Build pipeline" — create mode on that project, those objects preselected in Step 2's picker. */
  onboardingPrefill?: OnboardingPrefill | null;
}

/** Connector types ADF accepts as a staged-copy store. */
export const STAGING_CONNECTOR_TYPES = new Set(["azure_blob_storage", "azure_data_lake_storage_gen2"]);

export const WATERMARK_TYPES: Array<{ value: WatermarkColumn["type"]; label: string }> = [
  { value: "datetime", label: "datetime" },
  { value: "int", label: "int" },
  { value: "bigint", label: "bigint" },
  { value: "string", label: "string" },
];

export const WIZARD_STEP_LABELS = ["Basics", "Source", "Target", "Pipeline Settings", "Review & Generate"];

/** Which draft fields live on each step, so validation errors gate the right "Continue" and
 * clicking an issue in the review step's ValidationPanel jumps back to where it can be fixed. */
export const STEP_FIELDS: Record<number, string[]> = {
  1: ["name", "pattern", "masterPipelineName", "childPipelineName", "sourceSystem", "childDispatch", "pipelineName", "projectId", "curatedEntity", "martDomain"],
  2: ["sourceObjects", "sourceLinkedService", "sourceDataset", "sourcePartitionColumn"],
  3: ["targetLinkedService", "targetDataset", "targetPath", "upsertKeyColumns"],
  4: [
    "watermarkColumns", "watermarkTable", "connectionControlTable", "customLogColumns", "customMetadata", "dataQualityRules", "batchCount", "parallelCopies",
    "stagingLinkedService", "triggerInterval", "rejectsLinkedService", "chunkColumn", "chunkSizeRows",
    "maxParallelChunks", "triggerStorageLinkedService", "triggerContainerName", "transformations",
    "deleteHandling", "softDeleteColumn", "cdcCaptureInstance",
  ],
};

export function stepForField(field: string): number {
  for (const [step, fields] of Object.entries(STEP_FIELDS)) {
    if (fields.includes(field)) return Number(step);
  }
  return 1;
}

export function mapValue<T>(list: T[], index: number, patch: Partial<T>): T[] {
  return list.map((item, i) => (i === index ? { ...item, ...patch } : item));
}

export function removeAt<T>(list: T[], index: number): T[] {
  return list.filter((_, i) => i !== index);
}

export function SavedConnectionPicker({
  role,
  connections,
  value,
  onSelect,
  onNavigate,
}: {
  role: "source" | "target";
  connections: SavedConnection[];
  value: string;
  onSelect: (conn: SavedConnection | undefined) => void;
  onNavigate: (view: "pipelines.connections") => void;
}) {
  if (connections.length === 0) {
    return (
      <Alert
        variant="info"
        title={`No saved ${role} connections yet`}
        description="Create a linked service and dataset once on Delivery → Connections, then pick it here instead of filling the form."
        className="mb-3"
        action={
          <Button size="sm" variant="outline" onClick={() => onNavigate("pipelines.connections")}>
            Open Connections
          </Button>
        }
      />
    );
  }
  return (
    <div className="form-fields">
      <label>
        <span>Linked service &amp; dataset</span>
        <Select value={value} onChange={(e) => onSelect(connections.find((c) => c.id === e.target.value))}>
          <option value="">Configure manually…</option>
          {connections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} · {connectorSchema(c.linkedService.connectorType).displayName}
            </option>
          ))}
        </Select>
        <small>
          Choose a linked service and dataset you saved on Delivery → Connections, or leave this on
          “Configure manually…” to fill the form below.
        </small>
      </label>
    </div>
  );
}

/** Read-only view of the linked service + dataset a pipeline slot references. */
export function ConnectionReference({
  connection,
  ls,
  ds,
  onOverride,
  reused,
  onReusedChange,
  projectId,
}: {
  connection: SavedConnection;
  ls: LinkedServiceDraft;
  ds: DatasetDraft;
  onOverride: () => void;
  /** §24.6 "use existing, don't regenerate" — see `SpecDraft.sourceConnectionReused`. */
  reused: boolean;
  onReusedChange: (next: boolean) => void;
  /** Checks the reused names against the project's §24.8 Git catalog index. */
  projectId?: string;
}) {
  const repoLinkedServices = useCatalogNames(reused ? projectId : undefined, "linked_service");
  const repoDatasets = useCatalogNames(reused ? projectId : undefined, "dataset");
  const datasetName = ds.name || `DS_${connection.name}`;
  const schema = connectorSchema(ls.connectorType);
  const objectRef =
    schema.kind === "file"
      ? [ds.folderPath, ds.fileName].filter(Boolean).join("") || "—"
      : [ds.schema, ds.table].filter(Boolean).join(".") || "—";
  const rows: Array<{ label: string; value: string }> = [
    { label: "Linked service", value: ls.name || "—" },
    { label: "Connector", value: `${schema.displayName} · ${schema.adfType}` },
    { label: "Authentication", value: ls.authType.replace(/_/g, " ") },
    { label: "Integration runtime", value: ls.integrationRuntime || "—" },
    { label: "Dataset", value: ds.name || `DS_${connection.name}` },
    { label: schema.kind === "file" ? "Path / file" : "Schema / table", value: objectRef },
  ];
  return (
    <div className="editor-block">
      <div className="editor-head">
        <strong>Referenced from Connections</strong>
        <Badge variant="teal">{connection.name}</Badge>
      </div>
      <p className="editor-hint">
        The linked service and dataset are generated from this saved connection. Edit it on Delivery →
        Connections, or override it here for this pipeline only.
      </p>
      <div className="review-grid">
        {rows.map((row) => (
          <div className="review-item" key={row.label}>
            <span>{row.label}</span>
            <strong>{row.value}</strong>
          </div>
        ))}
      </div>
      <Checkbox
        checked={reused}
        onChange={onReusedChange}
        label="Already pushed to the target repo — reference it, don't regenerate"
        hint={
          reused
            ? `linkedService/${ls.name || "…"}.json and dataset/${ds.name || `DS_${connection.name}`}.json will be skipped on Generate/Push. The pipeline still references them by name — make sure they really are already there.`
            : "Leave unchecked the first time you use this connection, so its linked service/dataset files get created too."
        }
      />
      {reused && repoLinkedServices.indexed ? (
        <ReusedInGitStatus
          branch={repoLinkedServices.branch}
          missing={[
            ...(repoLinkedServices.names.includes(ls.name) ? [] : [`linkedService/${ls.name}.json`]),
            ...(repoDatasets.names.includes(datasetName) ? [] : [`dataset/${datasetName}.json`]),
          ]}
          onRegenerate={() => onReusedChange(false)}
        />
      ) : null}
      <button type="button" className="editor-add" onClick={onOverride}>
        Override / configure manually for this pipeline
      </button>
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <label className="form-check">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="form-check-body">
        <span className="form-check-label">{label}</span>
        {hint ? <small>{hint}</small> : null}
      </span>
    </label>
  );
}

export function PairEditor({
  title,
  hint,
  rows,
  onChange,
  max = 10,
  nameLabel = "Name",
  valueLabel = "Value",
  namePlaceholder = "e.g. key",
  valuePlaceholder = "e.g. value",
}: {
  title: string;
  hint?: string;
  rows: Array<{ name: string; value: string }>;
  onChange: (next: Array<{ name: string; value: string }>) => void;
  max?: number;
  nameLabel?: string;
  valueLabel?: string;
  namePlaceholder?: string;
  valuePlaceholder?: string;
}) {
  const atLimit = rows.length >= max;
  return (
    <div className="editor-block">
      <div className="editor-head">
        <strong>{title}</strong>
        <small>
          {rows.length}/{max}
        </small>
      </div>
      {hint ? <p className="editor-hint">{hint}</p> : null}
      {rows.length === 0 ? (
        <p className="editor-empty">No entries yet.</p>
      ) : (
        <div className="editor-rows">
          {rows.map((row, i) => (
            <div className="editor-row" key={i}>
              <Input
                value={row.name}
                placeholder={namePlaceholder}
                onChange={(e) => onChange(mapValue(rows, i, { name: e.target.value }))}
              />
              <Input
                value={row.value}
                placeholder={valuePlaceholder}
                onChange={(e) => onChange(mapValue(rows, i, { value: e.target.value }))}
              />
              <button
                type="button"
                className="editor-remove"
                title={`Remove ${title} row`}
                onClick={() => onChange(removeAt(rows, i))}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        className="editor-add"
        disabled={atLimit}
        onClick={() => onChange([...rows, { name: "", value: "" }])}
      >
        + Add {nameLabel.toLowerCase()} / {valueLabel.toLowerCase()}
      </button>
    </div>
  );
}

/**
 * Collapses one Build field to a read-only summary when Project Settings
 * already defines a value for it (AGENTS.md section 5B/5C ADF + control-
 * table defaults) — the Build form shouldn't ask again for something an
 * admin already configured project-wide. "Override" reveals the normal
 * editable control (see `CreatePipelineWizard`'s `revealField`/`hideField`),
 * still prefilled with that project value, for a one-off change on this
 * pipeline. Fields the project hasn't configured a default for are never
 * wrapped in this — there's nothing to hide.
 */
export function InheritedField({
  label,
  value,
  hint,
  onUnhide,
}: {
  label: string;
  value: string;
  hint?: string;
  onUnhide: () => void;
}) {
  return (
    <div className="inherited-field">
      <span className="inherited-field-label">{label}</span>
      <div className="inherited-field-row">
        <span className="status-pill gray"><i />{value}</span>
        <Button type="button" variant="ghost" size="sm" onClick={onUnhide}>
          <Pencil className="h-3.5 w-3.5" />
          Override
        </Button>
      </div>
      <small>{hint ?? "Set in Project Settings."}</small>
    </div>
  );
}

/** Pairs with `InheritedField` — collapses a field the user unhid back to its project default. */
export function UseProjectDefaultLink({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="inherited-field-collapse" onClick={onClick}>
      <RotateCcw className="h-3 w-3" />
      Use project default
    </button>
  );
}

export function WatermarkColumnEditor({
  rows,
  onChange,
  max = 5,
}: {
  rows: WatermarkColumn[];
  onChange: (next: WatermarkColumn[]) => void;
  max?: number;
}) {
  const atLimit = rows.length >= max;
  return (
    <div className="editor-block">
      <div className="editor-head">
        <strong>Watermark columns</strong>
        <small>
          {rows.length}/{max}
        </small>
      </div>
      {rows.length === 0 ? (
        <p className="editor-empty">Add at least one watermark column for delta loads.</p>
      ) : (
        <div className="editor-rows">
          {rows.map((col, i) => (
            <div className="editor-row" key={i}>
              <Input
                value={col.name}
                placeholder="e.g. modified_date"
                onChange={(e) => onChange(mapValue(rows, i, { name: e.target.value }))}
              />
              <Select
                value={col.type}
                onChange={(e) => onChange(mapValue(rows, i, { type: e.target.value as WatermarkColumn["type"] }))}
              >
                {WATERMARK_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </Select>
              <button
                type="button"
                className="editor-remove"
                title="Remove watermark column"
                onClick={() => onChange(removeAt(rows, i))}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        className="editor-add"
        disabled={atLimit}
        onClick={() => onChange([...rows, { name: "", type: "datetime", order: rows.length + 1 }])}
      >
        + Add watermark column
      </button>
    </div>
  );
}

/** Bronze/Silver/Gold layer picker — user request: pick which layer's pipeline to create. */
export function LayerPicker({ value, onChange }: { value: MedallionLayer; onChange: (next: MedallionLayer) => void }) {
  return (
    <div className="layer-picker">
      {MEDALLION_LAYERS.map((layer) => {
        const info = MEDALLION_LAYER_INFO[layer];
        return (
          <button
            type="button"
            key={layer}
            className={cn("layer-card", value === layer && "layer-card-active")}
            onClick={() => onChange(layer)}
          >
            <strong>{info.label}</strong>
            <span>{info.description}</span>
            <small>Target table pattern: {info.targetPatternHint}</small>
          </button>
        );
      })}
    </div>
  );
}

/**
 * Inline §25 naming hint for a generated artifact name (gap #33): shows the active project rule
 * next to the field it governs, and the violation against the name that will actually be generated
 * — instead of only a post-hoc validate/push error. Hint only; the Block gate stays server-side.
 */
export function NamingHint({ name, convention }: { name: string; convention: NamingConvention | undefined }) {
  const rule = ruleFor(convention, "pipeline");
  if ((convention?.enforcement ?? "off") === "off" || !isConfigured(rule)) return null;
  const violation = evaluateName(name, "pipeline", convention);
  return (
    <small className={violation?.level === "block" ? "text-red" : violation ? "amber-text" : "text-muted"}>
      {violation ? `${name}: ${violation.message}` : `Project naming standard: ${describeRule(rule)}`}
    </small>
  );
}

/** §24.6 — is a "reference it, don't regenerate" linked service/dataset really in the repo? */
function ReusedInGitStatus({ branch, missing, onRegenerate }: { branch: string | null; missing: string[]; onRegenerate: () => void }) {
  if (!missing.length) {
    return <Alert variant="success" className="mt-3" title={`Both files are in ${branch} as last indexed from Git.`} />;
  }
  return (
    <Alert
      variant="warning"
      className="mt-3"
      title={`Not found in ${branch}: ${missing.join(", ")}`}
      description="Skipping it would leave the pipeline referencing a missing artifact. Refresh the Catalog if it was pushed since, or generate it."
      action={<Button size="sm" variant="outline" onClick={onRegenerate}>Generate it again</Button>}
    />
  );
}
