"use client";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { sourceDatasetDefaults, type SpecDraft } from "@/lib/adf/spec";
import { SourceObjectsEditor, LinkedServiceEditor, DatasetEditor } from "@/features/build/connection-editors";
import { OnboardingObjectPicker } from "@/features/build/onboarding-object-picker";
import { SavedConnectionPicker, ConnectionReference } from "@/features/build/wizard-shared";
import type { WizardContext } from "@/features/build/use-pipeline-wizard";

export function SourceStep({ ctx }: { ctx: WizardContext }) {
  const {
    onNavigate,
    onboardingPrefill,
    step,
    draft,
    errors,
    isEditing,
    savedSourceConnections,
    sourceConnectionId,
    sourceConnection,
    template,
    updateDraft,
    updateSourceObjects,
    importOnboardingObjects,
    applySavedConnection,
    updateSourceLinkedService,
    updateSourceDataset,
    handleNext,
    handleBack,
  } = ctx;
  return (
    <div className="wizard-step-content animate-fade-in" key={step}>
      <p className="wizard-step-intro">
        Configure where data comes from — pick or create a connection, then list the source object(s)
        this pipeline copies. Objects already onboarded for this connection are pre-filled.
      </p>
      <div className="form-card">
        <h3 className="form-card-title">Source configuration</h3>
        <div className="form-fields">
          <label>
            <span>Source query (optional)</span>
            <Input
              value={draft.sourceQuery ?? ""}
              onChange={(e) => updateDraft("sourceQuery", e.target.value || undefined)}
              placeholder="e.g. SELECT * FROM dbo.customer WHERE active = 1"
            />
          </label>
          <label>
            <span>Source filter condition (optional)</span>
            <Input
              value={draft.sourceFilterCondition ?? ""}
              onChange={(e) => updateDraft("sourceFilterCondition", e.target.value || undefined)}
              placeholder="e.g. TRUNCATED = 'N'"
            />
          </label>
        </div>
      </div>

      <OnboardingObjectPicker
        projectId={draft.projectId}
        existingObjects={draft.sourceObjects.map((r) => r.object)}
        existingDqRuleCount={draft.dataQualityRules.length}
        onImport={importOnboardingObjects}
        preselectKeys={!isEditing && onboardingPrefill?.projectId === draft.projectId ? onboardingPrefill.objectKeys : undefined}
      />

      <div className="form-card">
        <h3 className="form-card-title">Source objects — input files / table names</h3>
        <SourceObjectsEditor
          mode={draft.sourceObjectMode}
          rows={draft.sourceObjects}
          errors={errors.sourceObjects ?? []}
          onChange={updateSourceObjects}
          showColumnMappings
        />
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Source linked service &amp; dataset</h3>
        <SavedConnectionPicker
          role="source"
          connections={savedSourceConnections}
          value={sourceConnectionId}
          onSelect={(conn) => applySavedConnection("source", conn)}
          onNavigate={onNavigate}
        />
        {sourceConnection ? (
          <ConnectionReference
            connection={sourceConnection}
            ls={draft.sourceLinkedService}
            ds={draft.sourceDataset}
            onOverride={() => applySavedConnection("source", undefined)}
            reused={draft.sourceConnectionReused ?? false}
            onReusedChange={(next) => updateDraft("sourceConnectionReused", next)}
            projectId={draft.projectId}
          />
        ) : (
          <>
            {draft.sourceConnectionReused && (
              <Alert
                variant="warning"
                title="This pipeline still skips regenerating its source linked service/dataset"
                description="The saved Connection it was picked from can't be found in this session (renamed, deleted, or this is a fresh page load). Uncheck below or the artifact stays skipped on Generate."
                className="mb-3"
                action={
                  <Button size="sm" variant="outline" onClick={() => updateDraft("sourceConnectionReused", false)}>
                    Generate it again
                  </Button>
                }
              />
            )}
            <LinkedServiceEditor
              title="Source connection"
              ls={draft.sourceLinkedService}
              connectors={template.supportedSourceTypes}
              onChange={updateSourceLinkedService}
              errors={errors.sourceLinkedService ?? []}
            />
          </>
        )}
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Source dataset</h3>
        <div className="form-fields">
          {sourceConnection ? (
            <div className="editor-block">
              <div className="editor-head">
                <strong>Source dataset</strong>
                <Badge variant="teal">{draft.sourceDataset.name || `DS_${sourceConnection.name}`}</Badge>
              </div>
              <p className="editor-hint">
                Referenced from connection <code>{sourceConnection.name}</code>. Override the source linked
                service above to edit it here.
              </p>
            </div>
          ) : (
            <DatasetEditor
              title="Source dataset"
              ds={draft.sourceDataset}
              ls={draft.sourceLinkedService}
              fallbackName={sourceDatasetDefaults(draft).fallbackName}
              onChange={updateSourceDataset}
              errors={errors.sourceDataset ?? []}
            />
          )}
        </div>
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Source read options</h3>
        <p className="form-section-hint">
          Copy-activity source tuning. Partitioned reads parallelize extraction of large tables.
        </p>
        <div className="form-fields">
          <label>
            <span>Partition option</span>
            <Select
              value={draft.sourcePartitionOption}
              onChange={(e) => updateDraft("sourcePartitionOption", e.target.value as SpecDraft["sourcePartitionOption"])}
            >
              <option value="None">None</option>
              <option value="DynamicRange">Dynamic range</option>
              <option value="PhysicalPartitionsOfTable">Physical partitions of table</option>
            </Select>
          </label>
          <label>
            <span>Partition column</span>
            <Input
              value={draft.sourcePartitionColumn ?? ""}
              onChange={(e) => updateDraft("sourcePartitionColumn", e.target.value || undefined)}
              placeholder="e.g. id"
              disabled={draft.sourcePartitionOption !== "DynamicRange"}
            />
            {errors.sourcePartitionColumn?.map((msg) => (
              <small key={msg} className="text-red">{msg}</small>
            ))}
          </label>
          <label>
            <span>Isolation level</span>
            <Select
              value={draft.sourceIsolationLevel}
              onChange={(e) => updateDraft("sourceIsolationLevel", e.target.value as SpecDraft["sourceIsolationLevel"])}
            >
              <option value="">Default</option>
              <option value="ReadCommitted">ReadCommitted</option>
              <option value="ReadUncommitted">ReadUncommitted</option>
              <option value="RepeatableRead">RepeatableRead</option>
              <option value="Serializable">Serializable</option>
              <option value="Snapshot">Snapshot</option>
            </Select>
          </label>
          <label>
            <span>Query timeout (hh:mm:ss)</span>
            <Input
              value={draft.queryTimeout}
              onChange={(e) => updateDraft("queryTimeout", e.target.value)}
              placeholder="02:00:00"
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
