"use client";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { targetDatasetDefaults, type SpecDraft } from "@/lib/adf/spec";
import { LinkedServiceEditor, DatasetEditor } from "@/features/build/connection-editors";
import { SavedConnectionPicker, ConnectionReference } from "@/features/build/wizard-shared";
import type { WizardContext } from "@/features/build/use-pipeline-wizard";

export function TargetStep({ ctx }: { ctx: WizardContext }) {
  const {
    onNavigate,
    step,
    draft,
    errors,
    savedTargetConnections,
    targetConnectionId,
    targetConnection,
    template,
    updateDraft,
    applySavedConnection,
    updateTargetLinkedService,
    updateTargetDataset,
    handleNext,
    handleBack,
  } = ctx;
  return (
    <div className="wizard-step-content animate-fade-in" key={step}>
      <p className="wizard-step-intro">
        Configure where data lands — pick or create a connection, the target path/table, and how the
        dataset is written (file format, copy behavior).
      </p>
      <div className="form-card">
        <h3 className="form-card-title">Target configuration</h3>
        <div className="form-fields">
          <label>
            <span>Target path</span>
            <Input
              value={draft.targetPath}
              onChange={(e) => updateDraft("targetPath", e.target.value)}
              placeholder="e.g. /landing/customer/"
            />
            {errors.targetPath?.map((msg) => (
              <small key={msg} className="text-red">{msg}</small>
            ))}
          </label>
          <label>
            <span>Target file name (optional)</span>
            <Input
              value={draft.targetFileName ?? ""}
              onChange={(e) => updateDraft("targetFileName", e.target.value || undefined)}
              placeholder="e.g. customer_delta.csv"
            />
          </label>
        </div>
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Target linked service &amp; dataset</h3>
        <SavedConnectionPicker
          role="target"
          connections={savedTargetConnections}
          value={targetConnectionId}
          onSelect={(conn) => applySavedConnection("target", conn)}
          onNavigate={onNavigate}
        />
        {targetConnection ? (
          <ConnectionReference
            connection={targetConnection}
            ls={draft.targetLinkedService}
            ds={draft.targetDataset}
            onOverride={() => applySavedConnection("target", undefined)}
            reused={draft.targetConnectionReused ?? false}
            onReusedChange={(next) => updateDraft("targetConnectionReused", next)}
            projectId={draft.projectId}
          />
        ) : (
          <>
            {draft.targetConnectionReused && (
              <Alert
                variant="warning"
                title="This pipeline still skips regenerating its target linked service/dataset"
                description="The saved Connection it was picked from can't be found in this session (renamed, deleted, or this is a fresh page load). Uncheck below or the artifact stays skipped on Generate."
                className="mb-3"
                action={
                  <Button size="sm" variant="outline" onClick={() => updateDraft("targetConnectionReused", false)}>
                    Generate it again
                  </Button>
                }
              />
            )}
            <LinkedServiceEditor
              title="Target connection"
              ls={draft.targetLinkedService}
              connectors={template.supportedTargetTypes}
              onChange={updateTargetLinkedService}
              errors={errors.targetLinkedService ?? []}
            />
          </>
        )}
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Target dataset</h3>
        <div className="form-fields">
          {targetConnection ? (
            <div className="editor-block">
              <div className="editor-head">
                <strong>Target dataset</strong>
                <Badge variant="teal">{draft.targetDataset.name || `DS_${targetConnection.name}`}</Badge>
              </div>
              <p className="editor-hint">
                Referenced from connection <code>{targetConnection.name}</code>. Override the target linked
                service above to edit it here.
              </p>
            </div>
          ) : (
            <DatasetEditor
              title="Target dataset"
              ds={draft.targetDataset}
              ls={draft.targetLinkedService}
              fallbackName={targetDatasetDefaults(draft).fallbackName}
              onChange={updateTargetDataset}
              errors={errors.targetDataset ?? []}
            />
          )}
        </div>
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Target write options</h3>
        <p className="form-section-hint">
          Copy-activity sink tuning. File formats and copy behavior apply to file targets; write behavior and
          batch size apply to relational targets.
        </p>
        <div className="form-fields">
          <label>
            <span>Sink file format</span>
            <Select
              value={draft.sinkFileFormat}
              onChange={(e) => updateDraft("sinkFileFormat", e.target.value as SpecDraft["sinkFileFormat"])}
            >
              <option value="Parquet">Parquet</option>
              <option value="DelimitedText">DelimitedText (CSV)</option>
              <option value="Json">JSON</option>
              <option value="Avro">Avro</option>
              <option value="Orc">ORC</option>
            </Select>
          </label>
          <label>
            <span>Copy behavior</span>
            <Select
              value={draft.copyBehavior}
              onChange={(e) => updateDraft("copyBehavior", e.target.value as SpecDraft["copyBehavior"])}
            >
              <option value="PreserveHierarchy">PreserveHierarchy</option>
              <option value="FlattenHierarchy">FlattenHierarchy</option>
              <option value="MergeFiles">MergeFiles</option>
            </Select>
          </label>
          <label>
            <span>Max rows per file (optional)</span>
            <Input
              type="number"
              min={0}
              value={draft.maxRowsPerFile ?? ""}
              onChange={(e) => updateDraft("maxRowsPerFile", e.target.value ? Number(e.target.value) : undefined)}
              placeholder="e.g. 1000000"
            />
          </label>
          <label>
            <span>Write behavior (relational target)</span>
            <Select
              value={draft.writeBehavior}
              onChange={(e) => updateDraft("writeBehavior", e.target.value as SpecDraft["writeBehavior"])}
            >
              <option value="Insert">Bulk insert</option>
              <option value="Upsert">Upsert</option>
            </Select>
          </label>
          <label>
            <span>Upsert key column(s)</span>
            <small>Also the match key for hard-delete handling (Pipeline Settings → Load settings).</small>
            <Input
              value={draft.upsertKeyColumns}
              onChange={(e) => updateDraft("upsertKeyColumns", e.target.value)}
              placeholder="e.g. customer_id, region"
              disabled={draft.writeBehavior !== "Upsert" && (draft.deleteHandling ?? "none") === "none"}
            />
            {errors.upsertKeyColumns?.map((msg) => (
              <small key={msg} className="text-red">{msg}</small>
            ))}
          </label>
          <label>
            <span>Write batch size (optional)</span>
            <Input
              type="number"
              min={0}
              value={draft.writeBatchSize ?? ""}
              onChange={(e) => updateDraft("writeBatchSize", e.target.value ? Number(e.target.value) : undefined)}
              placeholder="e.g. 10000"
            />
          </label>
        </div>
      </div>

      <div className="wizard-actions">
        <Button variant="ghost" onClick={handleBack}>
          Back
        </Button>
        <Button onClick={handleNext}>
          Continue
        </Button>
      </div>
    </div>
  );
}
