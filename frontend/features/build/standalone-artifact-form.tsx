"use client";

import { useMemo, useState } from "react";
import { CheckCircle2, Copy, FileCode2, UploadCloud, Wand2 } from "lucide-react";
import type { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ValidationPanel } from "@/components/validation/validation-panel";
import { GitPushDialog } from "@/components/git/git-push-dialog";
import { can } from "@/lib/state";
import { useCatalogNames } from "@/lib/api/use-catalog-names";
import { cn } from "@/lib/utils";
import {
  ALL_CONNECTORS,
  defaultDatasetDraft,
  defaultLinkedServiceDraft,
  type DatasetDraft,
  type LinkedServiceDraft,
} from "@/lib/adf/connections";
import {
  cloneDataset,
  cloneLinkedService,
  newConnection,
  upsertConnection,
} from "@/lib/adf/connection-store";
import {
  buildStandaloneDataset,
  buildStandaloneLinkedService,
  buildStandaloneTrigger,
  emptyTriggerDraft,
  linkedServiceSummary,
  validateStandaloneDataset,
  validateStandaloneLinkedService,
  validateTriggerDraft,
  type StandaloneArtifactKind,
  type StandaloneTriggerDraft,
} from "@/lib/adf/standalone";
import { LinkedServiceEditor, DatasetEditor } from "@/features/build/connection-editors";
import type { ArtifactVersion, ValidationIssue } from "@/types";

interface StandaloneArtifactFormProps extends FeatureProps {
  kind: StandaloneArtifactKind;
}

const KIND_COPY: Record<StandaloneArtifactKind, { title: string; detail: string; eyebrow: string }> = {
  linked_service: {
    eyebrow: "BUILD · LINKED SERVICE",
    title: "New linked service",
    detail:
      "Author a single ADF linked service on its own — connector, authentication, integration runtime and every connector-specific field. Secret fields hold the Key Vault secret name only. Generate the JSON and push it straight to a feature branch.",
  },
  dataset: {
    eyebrow: "BUILD · DATASET",
    title: "New dataset",
    detail:
      "Author a single ADF dataset bound to a linked service. Parameterize the schema/table or folder/file so the metadata-driven pattern can feed it from the control table.",
  },
  trigger: {
    eyebrow: "BUILD · TRIGGER",
    title: "New trigger",
    detail:
      "Author a schedule or tumbling-window trigger for an existing pipeline. No pipeline rebuild — the trigger references the pipeline by name.",
  },
};

const FREQUENCIES: StandaloneTriggerDraft["frequency"][] = ["Minute", "Hour", "Day", "Week", "Month"];

export function StandaloneArtifactForm({ kind, data, user, onNavigate }: StandaloneArtifactFormProps) {
  const copy = KIND_COPY[kind];
  const canCreate = can(user.role, "pipeline:create");
  const canPush = can(user.role, "pipeline:push");

  const [ls, setLs] = useState<LinkedServiceDraft>(() => {
    const draft = defaultLinkedServiceDraft("source", "azure_sql_database");
    draft.name = "";
    return draft;
  });
  const [ds, setDs] = useState<DatasetDraft>(() => defaultDatasetDraft("source", "azure_sql_database"));
  const [trigger, setTrigger] = useState<StandaloneTriggerDraft>(emptyTriggerDraft);

  const [generated, setGenerated] = useState<ArtifactVersion | null>(null);
  const [generating, setGenerating] = useState(false);
  const [copied, setCopied] = useState(false);
  const [pushOpen, setPushOpen] = useState(false);
  const [savedConnection, setSavedConnection] = useState(false);

  const dsWithLink: DatasetDraft = useMemo(
    () => ({ ...ds, linkedServiceName: ds.linkedServiceName || ls.name }),
    [ds, ls.name],
  );

  const issues: ValidationIssue[] = useMemo(() => {
    if (kind === "linked_service") return validateStandaloneLinkedService(ls);
    if (kind === "dataset") return validateStandaloneDataset(dsWithLink, ls);
    return validateTriggerDraft(trigger);
  }, [kind, ls, dsWithLink, trigger]);

  const blocked = issues.some((i) => i.severity === "error");
  // §24.6 trigger→pipeline pick: pipelines already in the repo (real ADF names, from the §24.8 catalog)
  // plus this app's own pipeline definitions. Free text stays allowed — a trigger may target a
  // pipeline that isn't indexed yet.
  const catalogPipelines = useCatalogNames(data.projects.data[0]?.id, "pipeline");
  const pipelineNames = useMemo(
    () => [...new Set([...catalogPipelines.names, ...data.pipelines.data.map((p) => p.name)])].filter(Boolean).sort(),
    [catalogPipelines.names, data.pipelines.data],
  );

  function resetGenerated() {
    setGenerated(null);
    setCopied(false);
    setSavedConnection(false);
  }

  async function handleGenerate() {
    if (blocked) return;
    setGenerating(true);
    await new Promise((r) => setTimeout(r, 700));
    const artifact =
      kind === "linked_service"
        ? buildStandaloneLinkedService(ls)
        : kind === "dataset"
          ? buildStandaloneDataset(dsWithLink, ls)
          : buildStandaloneTrigger(trigger);
    setGenerated(artifact);
    setCopied(false);
    setGenerating(false);
  }

  async function handleCopy() {
    if (!generated?.content) return;
    try {
      await navigator.clipboard.writeText(generated.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }

  /** Promote a generated linked service / dataset into the reusable connection library. */
  function handleSaveConnection() {
    const base = newConnection("both");
    upsertConnection({
      ...base,
      name: ls.name || `LS_${ls.connectorType}`,
      linkedService: { ...cloneLinkedService(ls), name: ls.name || base.name },
      dataset: cloneDataset(dsWithLink),
    });
    setSavedConnection(true);
  }

  if (!canCreate) {
    return (
      <div className="animate-fade-in">
        <PageHeader eyebrow={copy.eyebrow} title={copy.title} detail={copy.detail} />
        <div className="form-card">
          <Alert
            variant="warning"
            title="You do not have permission to author artifacts"
            description="Ask a team or platform admin to upgrade your role."
          />
        </div>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <PageHeader eyebrow={copy.eyebrow} title={copy.title} detail={copy.detail} />

      {(kind === "linked_service" || kind === "dataset") && (
        <div className="form-card">
          <h3 className="form-card-title">Linked service</h3>
          <LinkedServiceEditor
            title={kind === "dataset" ? "Bound linked service" : "Connection parameters"}
            ls={ls}
            connectors={ALL_CONNECTORS}
            onChange={(next) => {
              setLs(next);
              resetGenerated();
            }}
            errors={[]}
          />
        </div>
      )}

      {kind === "dataset" && (
        <div className="form-card">
          <h3 className="form-card-title">Dataset</h3>
          <DatasetEditor
            title="Dataset"
            ds={dsWithLink}
            ls={ls}
            fallbackName={`DS_${ls.name || "dataset"}`}
            onChange={(next) => {
              setDs(next);
              resetGenerated();
            }}
            errors={[]}
          />
        </div>
      )}

      {kind === "trigger" && (
        <div className="form-card">
          <h3 className="form-card-title">Trigger</h3>
          <div className="form-fields">
            <label>
              <span>Trigger name</span>
              <Input
                value={trigger.name}
                placeholder="e.g. TR_customer_daily"
                onChange={(e) => {
                  setTrigger((t) => ({ ...t, name: e.target.value }));
                  resetGenerated();
                }}
              />
              <small>Starts with a letter; letters, digits and underscores only (2–64 chars).</small>
            </label>
            <label>
              <span>Pipeline to start</span>
              <Input
                list="trigger-pipeline-options"
                value={trigger.pipelineName}
                placeholder="e.g. PL_customer_master"
                onChange={(e) => {
                  setTrigger((t) => ({ ...t, pipelineName: e.target.value }));
                  resetGenerated();
                }}
              />
              <datalist id="trigger-pipeline-options">
                {pipelineNames.map((name) => <option key={name} value={name} />)}
              </datalist>
              <small>
                The ADF pipeline this trigger invokes. It is referenced by name — no pipeline changes.
                {trigger.pipelineName && catalogPipelines.indexed && !catalogPipelines.names.includes(trigger.pipelineName)
                  ? ` Not found in ${catalogPipelines.branch} as last indexed from Git.`
                  : ""}
              </small>
            </label>
            <label>
              <span>Trigger type</span>
              <Select
                value={trigger.triggerType}
                onChange={(e) => {
                  setTrigger((t) => ({ ...t, triggerType: e.target.value as StandaloneTriggerDraft["triggerType"] }));
                  resetGenerated();
                }}
              >
                <option value="schedule">Schedule</option>
                <option value="tumbling">Tumbling window</option>
              </Select>
            </label>
            <label>
              <span>Recurrence frequency</span>
              <Select
                value={trigger.frequency}
                onChange={(e) => {
                  setTrigger((t) => ({ ...t, frequency: e.target.value as StandaloneTriggerDraft["frequency"] }));
                  resetGenerated();
                }}
              >
                {FREQUENCIES.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </Select>
              {trigger.triggerType === "tumbling" ? (
                <small>Tumbling-window triggers support Minute or Hour; other values fall back to Hour.</small>
              ) : null}
            </label>
            <label>
              <span>Interval</span>
              <Input
                type="number"
                min={1}
                value={trigger.interval}
                onChange={(e) => {
                  setTrigger((t) => ({ ...t, interval: Number(e.target.value) }));
                  resetGenerated();
                }}
              />
            </label>
            <label>
              <span>Start time (ISO 8601, optional)</span>
              <Input
                value={trigger.startTime}
                placeholder="e.g. 2026-01-01T00:00:00Z"
                onChange={(e) => {
                  setTrigger((t) => ({ ...t, startTime: e.target.value }));
                  resetGenerated();
                }}
              />
            </label>
            <label>
              <span>Time zone</span>
              <Input
                value={trigger.timeZone}
                placeholder="UTC"
                onChange={(e) => {
                  setTrigger((t) => ({ ...t, timeZone: e.target.value }));
                  resetGenerated();
                }}
              />
            </label>
            <label className="form-check">
              <input
                type="checkbox"
                checked={trigger.started}
                onChange={(e) => {
                  setTrigger((t) => ({ ...t, started: e.target.checked }));
                  resetGenerated();
                }}
              />
              <span className="form-check-body">
                <span className="form-check-label">Start the trigger on deploy</span>
                <small>Left off, the trigger JSON is generated with runtimeState &quot;Stopped&quot;.</small>
              </span>
            </label>
          </div>
        </div>
      )}

      <ValidationPanel
        issues={issues}
        emptyMessage={`The ${kind.replace("_", " ")} specification is valid. Generate the artifact to continue.`}
      />

      {kind === "linked_service" && (
        <div className="form-card">
          <h3 className="form-card-title">Summary</h3>
          <div className="review-grid">
            {linkedServiceSummary(ls).map((row) => (
              <div className="review-item" key={row.label}>
                <span>{row.label}</span>
                <strong>{row.value}</strong>
              </div>
            ))}
          </div>
        </div>
      )}

      {!generated ? (
        <div className="wizard-actions">
          <Button variant="ghost" onClick={() => onNavigate("delivery.catalog")}>
            Cancel
          </Button>
          <Button onClick={handleGenerate} disabled={blocked || generating}>
            <Wand2 className="h-3.5 w-3.5" />
            {generating ? "Generating…" : "Generate artifact"}
          </Button>
        </div>
      ) : (
        <>
          <div className="form-card">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 min-w-0">
                <FileCode2 className="h-4 w-4 shrink-0 text-teal" />
                <strong className="truncate text-xs text-ink">{generated.fileName}</strong>
                <Badge variant="teal">{generated.artifactType.replace("_", " ")}</Badge>
              </div>
              <Button variant="outline" size="sm" onClick={handleCopy}>
                <Copy className="h-3.5 w-3.5" />
                {copied ? "Copied!" : "Copy code"}
              </Button>
            </div>
            <pre className="code-viewer">{generated.content}</pre>
          </div>

          {savedConnection ? (
            <Alert
              variant="success"
              title="Saved to the connection library"
              description="This linked service and dataset are now offered as pick-or-create options in the pipeline form."
              className="mb-4"
            />
          ) : null}

          <div className="wizard-actions">
            <Button variant="ghost" onClick={resetGenerated}>
              Back to fields
            </Button>
            <div className="flex gap-2">
              {kind !== "trigger" ? (
                <Button variant="outline" onClick={handleSaveConnection} disabled={savedConnection}>
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Save to connection library
                </Button>
              ) : null}
              <Button
                onClick={() => setPushOpen(true)}
                disabled={!canPush}
                title={!canPush ? "Requires push permission" : undefined}
                className={cn(!canPush && "opacity-70")}
              >
                <UploadCloud className="h-3.5 w-3.5" />
                Push to Git
              </Button>
            </div>
          </div>
        </>
      )}

      <GitPushDialog
        open={pushOpen}
        onClose={() => setPushOpen(false)}
        artifacts={generated ? [generated] : []}
      />
    </div>
  );
}
