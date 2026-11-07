"use client";

import { ColumnMappingsEditor } from "@/features/build/column-mappings-editor";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { COPY_TIMEOUT_DEFAULT, FULL_LOAD_ROW_WARNING, SOFT_DELETE_COLUMN_DEFAULT, type DeleteHandling, type SpecDraft } from "@/lib/adf/spec";
import { DataQualityEditor, TransformationsEditor } from "@/components/rules/rule-editors";
import { hasStandardAuditColumns, standardAuditColumns, withStandardAuditColumns } from "@/lib/adf/medallion";
import {
  Checkbox,
  PairEditor,
  InheritedField,
  UseProjectDefaultLink,
  WatermarkColumnEditor,
} from "@/features/build/wizard-shared";
import type { WizardContext } from "@/features/build/use-pipeline-wizard";

export function SettingsStep({ ctx }: { ctx: WizardContext }) {
  const {
    step,
    draft,
    setDraft,
    errors,
    stagingCandidates,
    adfDefaults,
    controlTableSettings,
    hasControlTableDefault,
    hasConnectionControlDefault,
    hasParallelCopiesDefault,
    hasRetryCountDefault,
    hasRetryIntervalDefault,
    hasLogLevelDefault,
    hasAzureMonitorDefault,
    isRevealed,
    revealField,
    hideField,
    loadingBehaviorLabel,
    updateDraft,
    handleNext,
    handleBack,
    projectSettings,
  } = ctx;
  const auditOverrides = projectSettings.data.auditColumns;
  return (
    <div className="wizard-step-content animate-fade-in" key={step}>
      <p className="wizard-step-intro">
        Runtime and observability behaviour: watermark/control table for incremental loads, parallelism
        and retries, logging and data quality checks, notifications, and the trigger that starts this
        pipeline.
      </p>
      <div className="form-card">
        <h3 className="form-card-title">Watermark & control table</h3>
        <Checkbox
          checked={draft.watermarkEnabled}
          onChange={(next) => updateDraft("watermarkEnabled", next)}
          label="Enable watermark / delta tracking"
          hint="When disabled, every object loads as a FullLoad snapshot."
        />
        <div className="form-fields">
          {draft.watermarkEnabled && (
            <>
              <label>
                <span>Watermark strategy</span>
                <Select
                  value={draft.watermarkType}
                  onChange={(e) => updateDraft("watermarkType", e.target.value as SpecDraft["watermarkType"])}
                >
                  <option value="single">Single column</option>
                  <option value="composite">Composite (multiple columns)</option>
                </Select>
              </label>
              {hasControlTableDefault && !isRevealed("watermarkTable") ? (
                <InheritedField
                  label="Control / watermark table"
                  value={draft.watermarkTable}
                  hint="Set in Project Settings → Metadata & parameters."
                  onUnhide={() => revealField("watermarkTable")}
                />
              ) : (
                <label>
                  <span>Control / watermark table</span>
                  <Input
                    value={draft.watermarkTable}
                    onChange={(e) => updateDraft("watermarkTable", e.target.value)}
                    placeholder="e.g. control.adf_control_table"
                  />
                  <small>
                    One row per object to copy; the generated SQL (adf_control_table.sql) creates and seeds it.
                  </small>
                  {errors.watermarkTable?.map((msg) => (
                    <small key={msg} className="text-red">{msg}</small>
                  ))}
                  {hasControlTableDefault && (
                    <UseProjectDefaultLink
                      onClick={() =>
                        hideField("watermarkTable", () =>
                          updateDraft("watermarkTable", `${controlTableSettings!.schemaName}.${controlTableSettings!.tableName}`),
                        )
                      }
                    />
                  )}
                </label>
              )}
              <WatermarkColumnEditor rows={draft.watermarkColumns} onChange={(next) => updateDraft("watermarkColumns", next)} />
              {errors.watermarkColumns?.map((msg) => (
                <small key={msg} className="text-red">{msg}</small>
              ))}
              <label>
                <span>Initial watermark value</span>
                <Input
                  value={draft.watermarkInitialValue}
                  onChange={(e) => updateDraft("watermarkInitialValue", e.target.value)}
                  placeholder="e.g. 1900-01-01"
                />
              </label>
              <label>
                <span>Watermark filter condition (optional)</span>
                <Input
                  value={draft.watermarkFilterCondition ?? ""}
                  onChange={(e) => updateDraft("watermarkFilterCondition", e.target.value || undefined)}
                  placeholder="e.g. source_system = 'ERP'"
                />
              </label>
            </>
          )}
          {hasConnectionControlDefault && !isRevealed("connectionControl") ? (
            <InheritedField
              label="Connection control table"
              value={
                draft.connectionControlEnabled
                  ? `Enabled — ${draft.connectionControlTable}`
                  : "Disabled"
              }
              hint="Set in Project Settings → Metadata & parameters."
              onUnhide={() => revealField("connectionControl")}
            />
          ) : (
            <>
              <Checkbox
                checked={draft.connectionControlEnabled}
                onChange={(next) => updateDraft("connectionControlEnabled", next)}
                label="Generate a connection control table"
                hint="Opt-in extra artifact (docs/DDL.md §2.1): one row per linked service this pipeline uses, so ops can repoint a connection per environment/team without regenerating artifacts."
              />
              {draft.connectionControlEnabled && (
                <label>
                  <span>Connection control table</span>
                  <Input
                    value={draft.connectionControlTable}
                    onChange={(e) => updateDraft("connectionControlTable", e.target.value)}
                    placeholder="e.g. control.adf_connection_control"
                  />
                  {errors.connectionControlTable?.map((msg) => (
                    <small key={msg} className="text-red">{msg}</small>
                  ))}
                </label>
              )}
              {hasConnectionControlDefault && (
                <UseProjectDefaultLink
                  onClick={() =>
                    hideField("connectionControl", () =>
                      setDraft((prev) => ({
                        ...prev,
                        connectionControlEnabled: controlTableSettings!.enableConnectionControlTable,
                        connectionControlTable: `${controlTableSettings!.schemaName}.${controlTableSettings!.connectionTableName || "adf_connection_control"}`,
                      })),
                    )
                  }
                />
              )}
            </>
          )}
        </div>
      </div>

<div className="form-card">
        <h3 className="form-card-title">Load settings</h3>
        <div className="form-fields">
          <label>
            <span>Loading behavior</span>
            <Select
              value={draft.loadTypeMode}
              onChange={(e) => {
                const mode = e.target.value as SpecDraft["loadTypeMode"];
                updateDraft("loadTypeMode", mode);
                updateDraft("watermarkEnabled", mode !== "full");
                updateDraft("fullLoad", mode === "full");
              }}
            >
              <option value="incremental">Incremental load (delta via watermark)</option>
              <option value="full">Full load (snapshot / truncate-and-load)</option>
              <option value="initial">Initial load (seed + watermark start)</option>
              <option value="composite_watermark">Composite watermark load</option>
            </Select>
            <small>
              {loadingBehaviorLabel}. Maps to LoadingBehavior in the control table (FullLoad / DeltaLoad).
            </small>
          </label>
          <label>
            <span>Approx. source rows (optional)</span>
            <Input
              type="number"
              min={0}
              value={draft.estimatedSourceRows ?? ""}
              placeholder="e.g. 12000000"
              onChange={(e) => updateDraft("estimatedSourceRows", e.target.value === "" ? undefined : Math.max(0, Math.floor(Number(e.target.value))))}
            />
            <small>Only used to warn when a large table is set to a Full Load (over {FULL_LOAD_ROW_WARNING.toLocaleString("en-US")} rows).</small>
          </label>
          <div className="form-checks">
            <Checkbox
              checked={draft.fullLoad}
              onChange={(next) => updateDraft("fullLoad", next)}
              label="Full load snapshot"
              hint="Truncate-and-load or full snapshot of the source object."
            />
            <Checkbox
              checked={draft.truncateTarget}
              onChange={(next) => updateDraft("truncateTarget", next)}
              label="Truncate target before load"
              hint="Applies to relational targets in FullLoad copies."
            />
          </div>
          <label>
            <span>Pre-copy script (optional)</span>
            <Input
              value={draft.preCopyScript ?? ""}
              onChange={(e) => updateDraft("preCopyScript", e.target.value || undefined)}
              placeholder="e.g. TRUNCATE TABLE target.customer"
            />
          </label>
          <label>
            <span>Post-copy script (optional)</span>
            <Input
              value={draft.postCopyScript ?? ""}
              onChange={(e) => updateDraft("postCopyScript", e.target.value || undefined)}
              placeholder="e.g. EXEC control.dbo.update_stats 'customer'"
            />
          </label>
          <label>
            <span>Hard-delete handling</span>
            <Select
              value={draft.deleteHandling ?? "none"}
              onChange={(e) => updateDraft("deleteHandling", e.target.value as DeleteHandling)}
            >
              <option value="none">None — deletes aren&apos;t detected</option>
              <option value="soft_delete">Soft delete — reconcile source keys each run</option>
              <option value="cdc">CDC — SQL Server change data capture</option>
            </Select>
            <small>
              DeltaLoad runs only. Rows deleted at the source are flagged on the landed table (never physically
              removed), matched on the target step&apos;s key column(s).
            </small>
            {errors.deleteHandling?.map((msg) => (
              <small key={msg} className="text-red">{msg}</small>
            ))}
          </label>
          {(draft.deleteHandling ?? "none") !== "none" && (
            <label>
              <span>Soft-delete column</span>
              <Input
                value={draft.softDeleteColumn ?? ""}
                onChange={(e) => updateDraft("softDeleteColumn", e.target.value || undefined)}
                placeholder={SOFT_DELETE_COLUMN_DEFAULT}
              />
              <small>Added to the landed table if missing, alongside _deleted_at_utc.</small>
              {errors.softDeleteColumn?.map((msg) => (
                <small key={msg} className="text-red">{msg}</small>
              ))}
            </label>
          )}
          {draft.deleteHandling === "cdc" && (
            <label>
              <span>CDC capture instance (optional)</span>
              <Input
                value={draft.cdcCaptureInstance ?? ""}
                onChange={(e) => updateDraft("cdcCaptureInstance", e.target.value || undefined)}
                placeholder="<schema>_<table> per object"
              />
              <small>Blank uses SQL Server&apos;s default instance name for each object. CDC must be enabled on the source table.</small>
              {errors.cdcCaptureInstance?.map((msg) => (
                <small key={msg} className="text-red">{msg}</small>
              ))}
            </label>
          )}
        </div>
      </div>

<div className="form-card">
        <h3 className="form-card-title">Pipeline control (master/child)</h3>
        <div className="form-fields">
          <div className="form-checks">
            <Checkbox
              checked
              onChange={() => undefined}
              disabled
              label="Master/child pattern"
              hint="Metadata-driven templates always use a master pipeline that reads the control table and delegates objects to the child pipeline."
            />
            <Checkbox
              checked={draft.isSequential}
              onChange={(next) => updateDraft("isSequential", next)}
              label="Run objects sequentially"
              hint="When off, the master ForEach runs batches in parallel (degree of parallelism = parallel copies)."
            />
          </div>
          <label>
            <span>Batch count (objects per ForEach batch)</span>
            <Input
              type="number"
              min={1}
              max={50}
              value={draft.batchCount}
              onChange={(e) => updateDraft("batchCount", Number(e.target.value))}
            />
            <small>Between 1 and 50. {errors.batchCount?.[0]}</small>
          </label>
        </div>
      </div>

<div className="form-card">
        <h3 className="form-card-title">Runtime artifacts</h3>
        <p className="form-section-hint">
          Turn one off when the repo already holds a shared version you don&apos;t want this build to regenerate.
        </p>
        <div className="form-fields">
          <div className="form-checks">
            <Checkbox
              checked={draft.generateTrigger !== false}
              onChange={(next) => updateDraft("generateTrigger", next)}
              label="Generate trigger"
              hint="TR_{name}.json (plus its fallback trigger, when configured)."
            />
            <Checkbox
              checked={draft.generateGlobalParameters !== false}
              onChange={(next) => updateDraft("generateGlobalParameters", next)}
              label="Generate globalParameters.json"
              hint="Factory-level global parameters (filled from the selected ADF instance, if any)."
            />
            <Checkbox
              checked={draft.generateParametersFile !== false}
              onChange={(next) => updateDraft("generateParametersFile", next)}
              label="Generate parameters.json"
              hint="Pipeline parameter values file."
            />
            <Checkbox
              checked={draft.generateArmParameterDefinition === true}
              onChange={(next) => updateDraft("generateArmParameterDefinition", next)}
              label="Generate arm-template-parameters-definition.json"
              hint="ADF custom parameterization, so your own dev→QA→PROD release can override linked-service/dataset properties per environment."
            />
            <Checkbox
              checked={draft.generateLineage === true}
              onChange={(next) => updateDraft("generateLineage", next)}
              label="Generate lineage.json"
              hint="Static source → target lineage (OpenLineage dataset naming) for a data catalog such as Purview."
            />
            <Checkbox
              checked={draft.copyTypeConversion === true}
              onChange={(next) => updateDraft("copyTypeConversion", next)}
              label="Tolerate source schema drift (type conversion)"
              hint="Adds a Copy translator that maps columns by name and converts changed types instead of failing the copy. Never truncates."
            />
          </div>
          <div className="form-fields">
            <label>
              <span>Pipeline annotations</span>
              <Input
                value={(draft.annotations ?? []).join(", ")}
                onChange={(e) =>
                  updateDraft(
                    "annotations",
                    e.target.value.split(",").map((a) => a.trimStart()),
                  )
                }
                placeholder="e.g. domain:finance, owner:data-eng"
              />
              <small>
                Comma-separated ADF annotations stamped on every generated pipeline. Prefilled from this
                project&apos;s tags on a new pipeline.
              </small>
            </label>
            <ColumnMappingsEditor
              value={draft.columnMappings ?? []}
              errors={errors.columnMappings}
              onChange={(next) => updateDraft("columnMappings", next)}
            />
          </div>
        </div>
      </div>

<div className="form-card">
        <h3 className="form-card-title">Partition & chunking</h3>
        <p className="form-section-hint">
          High-volume historical/initial seeds: bounded-range chunks with persisted per-chunk restart status
          — a re-run only retries chunks that failed, instead of restarting the whole extract.
        </p>
        <div className="form-fields">
          <Checkbox
            checked={draft.chunkingEnabled}
            onChange={(next) => updateDraft("chunkingEnabled", next)}
            label="Chunk this load"
            hint="Fan out over row-range chunks of this one object, tracked in a chunk-plan control table."
          />
          {draft.chunkingEnabled && (
            <>
              <label>
                <span>Chunk column</span>
                <Input
                  value={draft.chunkColumn ?? ""}
                  onChange={(e) => updateDraft("chunkColumn", e.target.value || undefined)}
                  placeholder="e.g. claim_id"
                />
                <small>A numeric or date column to bound each chunk's range.</small>
                {errors.chunkColumn?.map((msg) => (
                  <small key={msg} className="text-red">{msg}</small>
                ))}
              </label>
              <label>
                <span>Target chunk size (rows)</span>
                <Input
                  type="number"
                  min={1000}
                  value={draft.chunkSizeRows}
                  onChange={(e) => updateDraft("chunkSizeRows", Number(e.target.value))}
                />
                {errors.chunkSizeRows?.map((msg) => (
                  <small key={msg} className="text-red">{msg}</small>
                ))}
              </label>
              <label>
                <span>Max parallel chunks</span>
                <Input
                  type="number"
                  min={1}
                  max={50}
                  value={draft.maxParallelChunks}
                  onChange={(e) => updateDraft("maxParallelChunks", Number(e.target.value))}
                />
                {errors.maxParallelChunks?.map((msg) => (
                  <small key={msg} className="text-red">{msg}</small>
                ))}
              </label>
            </>
          )}
        </div>
      </div>

<div className="form-card">
        <h3 className="form-card-title">Parallel processing</h3>
        <div className="form-fields">
          {hasParallelCopiesDefault && !isRevealed("parallelCopies") ? (
            <InheritedField
              label="Parallel copies"
              value={String(draft.parallelCopies)}
              hint="Set in Project Settings → ADF defaults."
              onUnhide={() => revealField("parallelCopies")}
            />
          ) : (
            <label>
              <span>Parallel copies</span>
              <Input
                type="number"
                min={1}
                max={50}
                value={draft.parallelCopies}
                onChange={(e) => updateDraft("parallelCopies", Number(e.target.value))}
              />
              <small>Between 1 and 50. {errors.parallelCopies?.[0]}</small>
              {hasParallelCopiesDefault && (
                <UseProjectDefaultLink
                  onClick={() => hideField("parallelCopies", () => updateDraft("parallelCopies", adfDefaults!.defaultParallelCopies!))}
                />
              )}
            </label>
          )}
          <label>
            <span>Data integration units (DIU)</span>
            <Input
              type="number"
              min={2}
              max={256}
              value={draft.dataIntegrationUnits ?? 2}
              onChange={(e) => updateDraft("dataIntegrationUnits", Number(e.target.value))}
            />
            <small>Power of 2 between 2 and 256.</small>
          </label>
          <Checkbox
            checked={draft.enableStaging}
            onChange={(next) => updateDraft("enableStaging", next)}
            label="Enable staged copy"
            hint="Copy via a staging blob container for cross-connector moves / PolyBase / COPY."
          />
          {draft.enableStaging && (
            <>
              <label>
                <span>Staging linked service</span>
                <Input
                  value={draft.stagingLinkedService}
                  onChange={(e) => updateDraft("stagingLinkedService", e.target.value)}
                  placeholder="e.g. LS_staging_blob"
                  list={stagingCandidates.length ? "staging-ls-options" : undefined}
                />
                {stagingCandidates.length ? (
                  <>
                    <datalist id="staging-ls-options">
                      {stagingCandidates.map((name) => (
                        <option key={name} value={name} />
                      ))}
                    </datalist>
                    <small>Suggestions are your saved Blob / ADLS Gen2 connections (Delivery → Connections).</small>
                  </>
                ) : null}
                {errors.stagingLinkedService?.map((msg) => (
                  <small key={msg} className="text-red">{msg}</small>
                ))}
              </label>
              <label>
                <span>Staging path</span>
                <Input
                  value={draft.stagingPath}
                  onChange={(e) => updateDraft("stagingPath", e.target.value)}
                  placeholder="e.g. staging/"
                />
              </label>
              <Checkbox
                checked={draft.stagingEnableCompression}
                onChange={(next) => updateDraft("stagingEnableCompression", next)}
                label="Compress staged data"
                hint="Gzip interim files in the staging area."
              />
            </>
          )}
        </div>
      </div>

<div className="form-card">
        <h3 className="form-card-title">Error handling</h3>
        <div className="form-fields">
          {hasRetryCountDefault && !isRevealed("retryCount") ? (
            <InheritedField
              label="Retry count"
              value={String(draft.retryCount)}
              hint="Set in Project Settings → ADF defaults."
              onUnhide={() => revealField("retryCount")}
            />
          ) : (
            <label>
              <span>Retry count</span>
              <Input
                type="number"
                min={0}
                max={10}
                value={draft.retryCount}
                onChange={(e) => updateDraft("retryCount", Number(e.target.value))}
              />
              {hasRetryCountDefault && (
                <UseProjectDefaultLink
                  onClick={() => hideField("retryCount", () => updateDraft("retryCount", adfDefaults!.defaultRetryCount!))}
                />
              )}
            </label>
          )}
          {hasRetryIntervalDefault && !isRevealed("retryIntervalInSeconds") ? (
            <InheritedField
              label="Retry interval (seconds)"
              value={String(draft.retryIntervalInSeconds)}
              hint="Set in Project Settings → ADF defaults."
              onUnhide={() => revealField("retryIntervalInSeconds")}
            />
          ) : (
            <label>
              <span>Retry interval (seconds)</span>
              <Input
                type="number"
                min={1}
                max={600}
                value={draft.retryIntervalInSeconds}
                onChange={(e) => updateDraft("retryIntervalInSeconds", Number(e.target.value))}
              />
              {hasRetryIntervalDefault && (
                <UseProjectDefaultLink
                  onClick={() =>
                    hideField("retryIntervalInSeconds", () =>
                      updateDraft("retryIntervalInSeconds", adfDefaults!.defaultRetryIntervalInSeconds!),
                    )
                  }
                />
              )}
            </label>
          )}
          <label>
            <span>Copy activity timeout (optional)</span>
            <Input
              value={draft.copyTimeout ?? ""}
              onChange={(e) => updateDraft("copyTimeout", e.target.value || undefined)}
              placeholder={COPY_TIMEOUT_DEFAULT}
            />
            <small>ADF d.hh:mm:ss format. Blank = {COPY_TIMEOUT_DEFAULT}.</small>
            {errors.copyTimeout?.map((msg) => (
              <small key={msg} className="text-red">{msg}</small>
            ))}
          </label>
          <Checkbox
            checked={draft.redirectIncompatibleRows}
            onChange={(next) => updateDraft("redirectIncompatibleRows", next)}
            label="Redirect incompatible rows"
            hint="Route rows that fail type conversion to a reject file/table instead of failing the run."
          />
          {draft.redirectIncompatibleRows && (
            <>
              <label>
                <span>Rejects linked service</span>
                <Input
                  value={draft.rejectsLinkedService}
                  onChange={(e) => updateDraft("rejectsLinkedService", e.target.value)}
                  placeholder="e.g. LS_rejects_blob"
                />
                <small>Required — without a destination, redirected rows are just discarded.</small>
                {errors.rejectsLinkedService?.map((msg) => (
                  <small key={msg} className="text-red">{msg}</small>
                ))}
              </label>
              <label>
                <span>Rejects path</span>
                <Input
                  value={draft.rejectsPath}
                  onChange={(e) => updateDraft("rejectsPath", e.target.value)}
                  placeholder="e.g. rejects/"
                />
              </label>
            </>
          )}
        </div>
      </div>

<div className="form-card">
        <h3 className="form-card-title">Logging & monitoring</h3>
        <div className="form-fields">
          <div className="form-checks">
            <Checkbox
              checked={draft.enableCopyLog}
              onChange={(next) => updateDraft("enableCopyLog", next)}
              label="Enable copy activity log"
              hint="Record a row per activity run for the Copy Pipeline log."
            />
            {!hasAzureMonitorDefault || isRevealed("enableAzureMonitor") ? (
              <Checkbox
                checked={draft.enableAzureMonitor}
                onChange={(next) => updateDraft("enableAzureMonitor", next)}
                label="Send metrics to Azure Monitor"
                hint="Emit pipeline/activity metrics to the configured Azure Monitor sink."
              />
            ) : null}
          </div>
          {hasAzureMonitorDefault && !isRevealed("enableAzureMonitor") && (
            <InheritedField
              label="Send metrics to Azure Monitor"
              value={draft.enableAzureMonitor ? "On" : "Off"}
              hint="Set in Project Settings → ADF defaults."
              onUnhide={() => revealField("enableAzureMonitor")}
            />
          )}
          {hasAzureMonitorDefault && isRevealed("enableAzureMonitor") && (
            <UseProjectDefaultLink
              onClick={() => hideField("enableAzureMonitor", () => updateDraft("enableAzureMonitor", adfDefaults!.enableAzureMonitor!))}
            />
          )}
          {hasLogLevelDefault && !isRevealed("logLevel") ? (
            <InheritedField
              label="Log level"
              value={draft.logLevel}
              hint="Set in Project Settings → ADF defaults."
              onUnhide={() => revealField("logLevel")}
            />
          ) : (
            <label>
              <span>Log level</span>
              <Select
                value={draft.logLevel}
                onChange={(e) => updateDraft("logLevel", e.target.value as SpecDraft["logLevel"])}
              >
                <option value="Info">Info</option>
                <option value="Warning">Warning</option>
                <option value="Error">Error</option>
                <option value="Verbose">Verbose</option>
                <option value="None">None</option>
              </Select>
              {hasLogLevelDefault && (
                <UseProjectDefaultLink
                  onClick={() => hideField("logLevel", () => updateDraft("logLevel", adfDefaults!.logLevel as SpecDraft["logLevel"]))}
                />
              )}
            </label>
          )}
          <PairEditor
            title="Custom log columns"
            hint="Extra key/value pairs written with every copy-log row (ADF expressions allowed), up to 10."
            rows={draft.customLogColumns}
            onChange={(next) => updateDraft("customLogColumns", next)}
            max={10}
            nameLabel="Column"
            valueLabel="Value"
            namePlaceholder="e.g. PipelineRunId"
            valuePlaceholder="e.g. @pipeline().RunId"
          />
          {errors.customLogColumns?.map((msg) => (
            <small key={msg} className="text-red">{msg}</small>
          ))}
        </div>
      </div>

      {draft.medallionLayer !== "bronze" && (
        <div className="form-card">
          <h3 className="form-card-title">Transformations</h3>
          <TransformationsEditor
            layer={draft.medallionLayer}
            rows={draft.transformations}
            onChange={(next) => updateDraft("transformations", next)}
          />
          {errors.transformations?.map((msg) => (
            <small key={msg} className="text-red">{msg}</small>
          ))}
        </div>
      )}

<div className="form-card">
        <h3 className="form-card-title">Validation & data quality</h3>
        <div className="form-fields">
          <div className="form-checks">
            <Checkbox
              checked={draft.rowCountValidationEnabled}
              onChange={(next) => updateDraft("rowCountValidationEnabled", next)}
              label="Row-count validation"
              hint="Compare source vs target row count after each copy and fail on mismatch."
            />
          </div>
          <DataQualityEditor rows={draft.dataQualityRules} onChange={(next) => updateDraft("dataQualityRules", next)} />
          {errors.dataQualityRules?.map((msg) => (
            <small key={msg} className="text-red">{msg}</small>
          ))}
          <Checkbox
            checked={hasStandardAuditColumns(draft.medallionLayer, draft.customMetadata, auditOverrides)}
            onChange={(next) => {
              const rows = withStandardAuditColumns(draft.medallionLayer, draft.customMetadata, next, auditOverrides);
              if (rows) updateDraft("customMetadata", rows);
            }}
            disabled={
              !hasStandardAuditColumns(draft.medallionLayer, draft.customMetadata, auditOverrides) &&
              withStandardAuditColumns(draft.medallionLayer, draft.customMetadata, true, auditOverrides) === null
            }
            label={`Apply standard audit columns for this layer (${draft.medallionLayer})`}
            hint={`Adds ${standardAuditColumns(draft.medallionLayer, auditOverrides)
              .map((c) => c.name)
              .join(", ")} to the custom metadata below (editable; counts toward the 10-column cap)${
              auditOverrides?.[draft.medallionLayer]?.some((c) => c.name.trim()) ? " — this project's list from Standards." : "."
            }`}
          />
          <PairEditor
            title="Custom metadata"
            hint="Object metadata carried into the output (up to 10)."
            rows={draft.customMetadata}
            onChange={(next) => updateDraft("customMetadata", next)}
            max={10}
            namePlaceholder="e.g. domain"
            valuePlaceholder="e.g. commerce"
          />
          {errors.customMetadata?.map((msg) => (
            <small key={msg} className="text-red">{msg}</small>
          ))}
        </div>
      </div>

<div className="form-card">
        <h3 className="form-card-title">Notifications</h3>
        <div className="form-fields">
          <div className="form-checks">
            <Checkbox
              checked={draft.notifyOnSuccess}
              onChange={(next) => updateDraft("notifyOnSuccess", next)}
              label="Notify on success"
            />
            <Checkbox
              checked={draft.notifyOnFailure}
              onChange={(next) => updateDraft("notifyOnFailure", next)}
              label="Notify on failure"
            />
          </div>
          <label>
            <span>Notification emails</span>
            <Input
              value={draft.notificationEmails.join(", ")}
              onChange={(e) =>
                updateDraft(
                  "notificationEmails",
                  e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                )}
              placeholder="e.g. analyst@example.com, devops@example.com"
            />
            <small>Comma-separated recipient list.</small>
          </label>
        </div>
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Trigger</h3>
        <Checkbox
          checked={draft.triggerEnabled}
          onChange={(next) => updateDraft("triggerEnabled", next)}
          label="Generate a trigger for this pipeline"
          hint="Emits a trigger artifact bound to the master pipeline. Left off, the trigger JSON is still generated but starts Stopped."
        />
        <div className="form-fields">
          <label>
            <span>Trigger type</span>
            <Select
              value={draft.triggerType}
              onChange={(e) => updateDraft("triggerType", e.target.value as SpecDraft["triggerType"])}
            >
              <option value="schedule">Schedule</option>
              <option value="tumbling">Tumbling window</option>
              <option value="blobEvents">File arrival (blob events)</option>
            </Select>
          </label>
          {draft.triggerType === "blobEvents" ? (
            <>
              <label>
                <span>Storage linked service</span>
                <Input
                  value={draft.triggerStorageLinkedService ?? ""}
                  onChange={(e) => updateDraft("triggerStorageLinkedService", e.target.value || undefined)}
                  placeholder="e.g. LS_landing_blob"
                />
                {errors.triggerStorageLinkedService?.map((msg) => (
                  <small key={msg} className="text-red">{msg}</small>
                ))}
              </label>
              <label>
                <span>Container name</span>
                <Input
                  value={draft.triggerContainerName ?? ""}
                  onChange={(e) => updateDraft("triggerContainerName", e.target.value || undefined)}
                  placeholder="e.g. landing"
                />
                {errors.triggerContainerName?.map((msg) => (
                  <small key={msg} className="text-red">{msg}</small>
                ))}
              </label>
              <label>
                <span>Blob path begins with (optional)</span>
                <Input
                  value={draft.triggerBlobPathBeginsWith ?? ""}
                  onChange={(e) => updateDraft("triggerBlobPathBeginsWith", e.target.value || undefined)}
                  placeholder="e.g. invoices/"
                />
              </label>
              <label>
                <span>Blob path ends with (optional)</span>
                <Input
                  value={draft.triggerBlobPathEndsWith ?? ""}
                  onChange={(e) => updateDraft("triggerBlobPathEndsWith", e.target.value || undefined)}
                  placeholder="e.g. .csv"
                />
              </label>
              <Checkbox
                checked={draft.triggerFallbackSchedule}
                onChange={(next) => updateDraft("triggerFallbackSchedule", next)}
                label="Add a fallback schedule trigger"
                hint="Safety net in case a blob-arrival event is missed."
              />
            </>
          ) : (
            <label>
              <span>Recurrence frequency</span>
              <Select
                value={draft.triggerFrequency}
                onChange={(e) => updateDraft("triggerFrequency", e.target.value as SpecDraft["triggerFrequency"])}
              >
                <option value="Minute">Minute</option>
                <option value="Hour">Hour</option>
                <option value="Day">Day</option>
                <option value="Week">Week</option>
                <option value="Month">Month</option>
              </Select>
            </label>
          )}
          {draft.triggerType !== "blobEvents" && (
            <label>
              <span>Interval</span>
              <Input
                type="number"
                min={1}
                value={draft.triggerInterval}
                onChange={(e) => updateDraft("triggerInterval", Number(e.target.value))}
              />
              {errors.triggerInterval?.map((msg) => (
                <small key={msg} className="text-red">{msg}</small>
              ))}
            </label>
          )}
          <label>
            <span>Start time (ISO 8601, optional)</span>
            <Input
              value={draft.triggerStartTime}
              onChange={(e) => updateDraft("triggerStartTime", e.target.value)}
              placeholder="e.g. 2026-01-01T00:00:00Z"
            />
          </label>
          <label>
            <span>Time zone</span>
            <Input
              value={draft.triggerTimeZone}
              onChange={(e) => updateDraft("triggerTimeZone", e.target.value)}
              placeholder="UTC"
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
