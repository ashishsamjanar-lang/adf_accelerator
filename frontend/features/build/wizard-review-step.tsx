"use client";

import { CheckCircle2, Copy, FileCode2, Wand2 } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { effectiveSourceObjects, sourceDatasetDefaults, targetDatasetDefaults, validateDraft } from "@/lib/adf/spec";
import { datasetName } from "@/lib/adf/connections";
import { ValidationPanel } from "@/components/validation/validation-panel";
import { stepForField } from "@/features/build/wizard-shared";
import type { WizardContext } from "@/features/build/use-pipeline-wizard";

export function ReviewStep({ ctx }: { ctx: WizardContext }) {
  const {
    onNavigate,
    step,
    setStep,
    draft,
    editingPipelineId,
    saveConflict,
    isEditing,
    loadPipelineForEdit,
    generating,
    generated,
    artifacts,
    activeArtifact,
    setActiveArtifact,
    copied,
    setCopied,
    finishing,
    finishResult,
    setFinishResult,
    sourceConnection,
    targetConnection,
    project,
    template,
    loadingBehaviorLabel,
    handleBack,
    handleGenerate,
    handleCopy,
    handleFinish,
  } = ctx;
  return (
    <div className="wizard-step-content animate-fade-in" key={step}>
      <p className="wizard-step-intro">
        Review every field from the previous steps, then generate. Generate produces the full ADF
        artifact set as a new DRAFT — nothing is written to Git yet — and Create pipeline then runs the
        same generation plus validation server-side so you see real results below.
      </p>
      <ValidationPanel
        issues={validateDraft(draft)}
        onSelectField={(field) => setStep(stepForField(field))}
        emptyMessage="Specification is valid. Generate the ADF artifacts to continue."
      />
      <div className="form-card">
        <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-muted">
          <span className="flex items-center gap-1.5"><span className="inline-block h-2 w-2 rounded-full bg-teal" />Pipeline value</span>
          <span className="flex items-center gap-1.5"><span className="inline-block h-2 w-2 rounded-full bg-blue" />Project default</span>
          <span className="flex items-center gap-1.5"><span className="inline-block h-2 w-2 rounded-full bg-amber" />Generated</span>
        </div>
        <h3 className="form-card-title">Specification summary</h3>
        <div className="review-grid">
          <div className="review-item">
            <span>Project</span>
            <strong>{project?.name ?? "—"}</strong>
          </div>
          <div className="review-item">
            <span>Template</span>
            <strong>{template.name} · v{template.version}</strong>
          </div>
          <div className="review-item">
            <span>Pipeline name</span>
            <strong>{draft.name || "—"}</strong>
          </div>
          <div className="review-item">
            <span>Source</span>
            <strong>
              {draft.sourceType} · {effectiveSourceObjects(draft).length} object(s) · {draft.sourceObjectMode === "file" ? "from uploaded file" : "from form"}
            </strong>
          </div>
          <div className="review-item">
            <span>Target</span>
            <strong>{draft.targetType} · {draft.targetPath}</strong>
          </div>
          <div className="review-item">
            <span>Linked services</span>
            <strong>
              {draft.sourceLinkedService.name || "—"} ({draft.sourceType}) → {draft.targetLinkedService.name || "—"} ({draft.targetType})
            </strong>
          </div>
          <div className="review-item">
            <span>Connection source</span>
            <strong>
              {sourceConnection ? `Connections · ${sourceConnection.name}` : "Manual"} →{" "}
              {targetConnection ? `Connections · ${targetConnection.name}` : "Manual"}
            </strong>
          </div>
          <div className="review-item">
            <span>Datasets</span>
            <strong>
              {datasetName(draft.sourceDataset, sourceDatasetDefaults(draft))} · {datasetName(draft.targetDataset, targetDatasetDefaults(draft))}
            </strong>
          </div>
          <div className="review-item">
            <span>Loading behavior</span>
            <strong>{loadingBehaviorLabel}</strong>
          </div>
          <div className="review-item">
            <span>Control / watermark table</span>
            <strong>{draft.watermarkEnabled ? (draft.watermarkTable || "—") : "Disabled (full loads only)"}</strong>
          </div>
          {draft.watermarkEnabled && (
            <div className="review-item">
              <span>Watermark columns</span>
              <strong>{draft.watermarkColumns.map((c) => c.name).join(", ") || "—"}</strong>
            </div>
          )}
          <div className="review-item">
            <span>Batch / parallel copies</span>
            <strong>{draft.batchCount} · {draft.parallelCopies} · {draft.isSequential ? "sequential" : "parallel"}</strong>
          </div>
          <div className="review-item">
            <span>Retry</span>
            <strong>{draft.retryCount}× every {draft.retryIntervalInSeconds}s</strong>
          </div>
          <div className="review-item">
            <span>Logging</span>
            <strong>{draft.logLevel} · azure monitor {draft.enableAzureMonitor ? "on" : "off"}</strong>
          </div>
          <div className="review-item">
            <span>Notifications</span>
            <strong>
              {draft.notifyOnSuccess || draft.notifyOnFailure ? draft.notificationEmails.join(", ") || "—" : "None"}
            </strong>
          </div>
          <div className="review-item">
            <span>Source read</span>
            <strong>
              partition {draft.sourcePartitionOption}
              {draft.sourcePartitionOption === "DynamicRange" && draft.sourcePartitionColumn
                ? ` (${draft.sourcePartitionColumn})`
                : ""}
              {draft.sourceIsolationLevel ? ` · ${draft.sourceIsolationLevel}` : ""}
            </strong>
          </div>
          <div className="review-item">
            <span>Target write</span>
            <strong>
              {draft.sinkFileFormat} · {draft.copyBehavior} · {draft.writeBehavior.toLowerCase()}
            </strong>
          </div>
          <div className="review-item">
            <span>Staged copy</span>
            <strong>{draft.enableStaging ? `${draft.stagingLinkedService || "—"} @ ${draft.stagingPath}` : "Off"}</strong>
          </div>
          <div className="review-item">
            <span>Trigger</span>
            <strong>
              {draft.triggerEnabled
                ? `${draft.triggerType} · every ${draft.triggerInterval} ${draft.triggerFrequency.toLowerCase()}`
                : "Generated but stopped"}
            </strong>
          </div>
        </div>
      </div>

      {!generated ? (
        <div className="wizard-actions">
          <Button variant="ghost" onClick={handleBack}>
            Back
          </Button>
          <Button onClick={handleGenerate} disabled={generating}>
            <Wand2 className="h-3.5 w-3.5" />
            {generating ? "Generating…" : "Generate pipeline code"}
          </Button>
        </div>
      ) : (

<div className="wizard-step-content animate-fade-in">
          <div className="form-card">
            <div className="flex items-center justify-between gap-3">
              <h3 className="form-card-title m-0">Generated pipeline code</h3>
              <Badge variant="green">{artifacts.length} artifacts</Badge>
            </div>
            <p className="mt-2 text-xs text-muted">
              Select an artifact to inspect its ADF code. The control-table seed row, datasets, and linked
              services are all derived from the fields you populated.
            </p>
            <div className="artifact-list mt-3">
              {artifacts.map((artifact) => (
                <button
                  key={artifact.id}
                  type="button"
                  className={cn("artifact-item artifact-select", activeArtifact?.id === artifact.id && "active")}
                  onClick={() => {
                    setActiveArtifact(artifact);
                    setCopied(false);
                  }}
                >
                  <div className="artifact-row">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="artifact-file"><FileCode2 className="h-3.5 w-3.5" /></span>
                      <span className="artifact-name truncate">{artifact.fileName}</span>
                    </span>
                    <Badge variant="blue">{artifact.state}</Badge>
                  </div>
                  <div className="artifact-meta">
                    <span>{artifact.artifactType} · {artifact.layer}</span>
                    <span>{artifact.bytes} B</span>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {activeArtifact ? (
            <div className="form-card">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 min-w-0">
                  <FileCode2 className="h-4 w-4 shrink-0 text-teal" />
                  <strong className="truncate text-xs text-ink">{activeArtifact.fileName}</strong>
                  <Badge variant="teal">{activeArtifact.artifactType}</Badge>
                </div>
                <Button variant="outline" size="sm" onClick={handleCopy}>
                  <Copy className="h-3.5 w-3.5" />
                  {copied ? "Copied!" : "Copy code"}
                </Button>
              </div>
              <pre className="code-viewer">{activeArtifact.content}</pre>
            </div>
          ) : null}

          {finishResult ? (
            <div className="form-card">
              <div className="flex items-center justify-between gap-3">
                <h3 className="form-card-title m-0">Validation results</h3>
                <Badge variant={finishResult.validation?.state === "failed" ? "amber" : "green"}>
                  {finishResult.validation?.state === "failed" ? "Needs attention" : "Validated"}
                </Badge>
              </div>
              <p className="mt-2 text-xs text-muted">
                "{finishResult.pipeline.name}" was generated and validated (syntax, schema,
                naming, and secret checks). Fix any errors below and regenerate from Drafts, or continue to
                Push to Git if everything passed.
              </p>
              <ValidationPanel
                className="mt-3"
                issues={finishResult.validation?.issues ?? []}
                emptyMessage="No issues found — this pipeline is ready for Push to Git."
              />
              <div className="wizard-actions">
                <Button variant="ghost" onClick={() => setFinishResult(null)}>
                  Keep editing this artifact preview
                </Button>
                <Button onClick={() => onNavigate("pipelines.drafts")}>
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Continue to Drafts
                </Button>
              </div>
            </div>
          ) : (
            <div className="wizard-actions">
              {saveConflict ? (
                <Alert
                  variant="error"
                  title="Save conflict"
                  description={saveConflict}
                  className="w-full"
                  action={
                    editingPipelineId ? (
                      <Button size="sm" variant="outline" onClick={() => void loadPipelineForEdit(editingPipelineId)}>
                        Reload pipeline
                      </Button>
                    ) : undefined
                  }
                />
              ) : null}
              <Button variant="ghost" onClick={handleBack}>
                Back to fields
              </Button>
              <Button onClick={handleFinish} disabled={finishing}>
                <CheckCircle2 className="h-3.5 w-3.5" />
                {finishing ? (isEditing ? "Saving…" : "Creating…") : isEditing ? "Save pipeline" : "Create pipeline"}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
