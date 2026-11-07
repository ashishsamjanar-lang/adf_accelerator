"use client";

import { ChildDispatchEditor } from "@/features/build/child-dispatch-editor";
import { useCatalogNames } from "@/lib/api/use-catalog-names";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import {
  emptySpecDraft,
  resolveChildPipelineName,
  resolveMasterPipelineName,
  resolvePipelineName,
  type PipelinePattern,
  type SpecDraft,
} from "@/lib/adf/spec";
import { RegistryObjectPicker, registryLoadSettings } from "@/features/build/registry-object-picker";
import { buildExpectedName, describeRule } from "@/lib/adf/naming";
import { layerTargetTableName } from "@/lib/adf/medallion";
import { LayerPicker, NamingHint } from "@/features/build/wizard-shared";
import type { WizardContext } from "@/features/build/use-pipeline-wizard";

export function BasicsStep({ ctx }: { ctx: WizardContext }) {
  const {
    data,
    onNavigate,
    step,
    draft,
    setDraft,
    errors,
    editingPipelineId,
    isEditing,
    loadPipelineForEdit,
    resetToCreateMode,
    project,
    namingConvention,
    pipelineNamingRule,
    pipelineNameViolation,
    updateDraft,
    handleNext,
  } = ctx;
  const repoPipelines = useCatalogNames(draft.projectId, "pipeline");
  return (
    <div className="wizard-step-content animate-fade-in" key={step}>
      <p className="wizard-step-intro">
        Name the pipeline, choose its project and (optionally) target ADF instance. Every other field on
        this step is metadata — nothing here is hand-written per connector.
      </p>
      <div className="form-card">
        <Alert
          variant="info"
          title="Metadata-driven only"
          description="This accelerator only creates metadata-driven pipelines. The Metadata-Driven Load template is always used: a master pipeline reads the control table, batches objects, and delegates to a child pipeline that routes each object by LoadingBehavior (FullLoad or DeltaLoad)."
        />
      </div>

      {isEditing ? (
        <div className="form-card">
          <Alert
            variant="info"
            title={`Editing "${draft.name}"`}
            description="Add more onboarded objects on the next step, or rename the master/child artifacts below — Save updates this pipeline instead of creating a new one."
            action={
              <Button size="sm" variant="ghost" onClick={resetToCreateMode}>
                Start a new pipeline instead
              </Button>
            }
          />
        </div>
      ) : (
        <div className="form-card">
          <h3 className="form-card-title">Add objects to an existing pipeline</h3>
          <p className="editor-hint">
            Already have a master/child pipeline for this project? Pick it here to append more onboarded
            objects to it instead of a separate pipeline. Leave this on “Create a new pipeline” — you can
            create as many master pipelines as you need.
          </p>
          <Select value="" onChange={(e) => { if (e.target.value) void loadPipelineForEdit(e.target.value); }}>
            <option value="">— Create a new pipeline —</option>
            {data.pipelines.data
              .filter((p) => p.projectId === draft.projectId)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </Select>
        </div>
      )}

      <div className="form-card">
        <h3 className="form-card-title">Project</h3>
        <div className="form-fields">
          <label>
            <span>Select project</span>
            <Select value={draft.projectId} onChange={(e) => updateDraft("projectId", e.target.value)}>
              {data.projects.data.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({data.teams.data.find((t) => t.id === p.teamId)?.name ?? "Unassigned"})
                </option>
              ))}
            </Select>
            {errors.projectId?.map((msg) => (
              <small key={msg} className="text-red">{msg}</small>
            ))}
                        </label>
          <label>
            <span>ADF instance (optional)</span>
            <Select
              value={draft.targetEnvironmentName ?? ""}
              onChange={(e) => updateDraft("targetEnvironmentName", e.target.value || undefined)}
            >
              <option value="">Not set — globalParameters.json stays placeholder-only</option>
              {data.environments.data.map((env) => (
                <option key={env.name} value={env.name}>
                  {env.label || env.name}
                </option>
              ))}
            </Select>
            <small>
              Which registered ADF instance (Administration → Environments) this pipeline targets.
              Resolves the real factory/instance name into <code>globalParameters.json</code> at
              Generate time — every other generated artifact is unaffected.
            </small>
          </label>
        </div>
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Layer</h3>
        <p className="editor-hint">
          Which medallion layer is this pipeline for? Bronze lands the raw source unchanged (today's
          default). Silver and Gold add transformations and write into a layer-standardized table name.
        </p>
        <LayerPicker value={draft.medallionLayer} onChange={(next) => setDraft((prev) => ({ ...prev, medallionLayer: next }))} />
        {draft.medallionLayer !== "bronze" && (
          <div className="form-fields">
            <RegistryObjectPicker
              layer={draft.medallionLayer}
              projectId={draft.projectId}
              pickedObjectKey={draft.medallionLayer === "gold" ? draft.martObjectRef : draft.silverObjectRef}
              onPick={(obj) =>
                setDraft((prev) => ({
                  ...prev,
                  ...registryLoadSettings(obj, prev),
                  curatedEntity: obj.curatedEntity,
                  martDomain: obj.martDomain ?? prev.martDomain,
                  transformations: obj.transformations,
                  dataQualityRules: obj.dataQualityRules,
                  silverObjectRef: draft.medallionLayer === "silver" ? obj.objectKey : undefined,
                  martObjectRef: draft.medallionLayer === "gold" ? obj.objectKey : undefined,
                  registryTag: obj.tag,
                  registrySourceObjectKeys: obj.sourceObjectKeys,
                }))
              }
              onClear={() =>
                setDraft((prev) => ({
                  ...prev,
                  silverObjectRef: undefined,
                  martObjectRef: undefined,
                  registryTag: undefined,
                  registrySourceObjectKeys: undefined,
                }))
              }
              onNavigate={onNavigate}
            />
            {!(draft.medallionLayer === "gold" ? draft.martObjectRef : draft.silverObjectRef) && (
              <>
                <label>
                  <span>Curated entity name</span>
                  <Input
                    value={draft.curatedEntity ?? ""}
                    onChange={(e) => updateDraft("curatedEntity", e.target.value || undefined)}
                    placeholder="e.g. gl_transaction"
                  />
                  {errors.curatedEntity?.map((msg) => (
                    <small key={msg} className="text-red">{msg}</small>
                  ))}
                </label>
                {draft.medallionLayer === "gold" && (
                  <label>
                    <span>Mart domain</span>
                    <Input
                      value={draft.martDomain ?? ""}
                      onChange={(e) => updateDraft("martDomain", e.target.value || undefined)}
                      placeholder="e.g. finance"
                    />
                    {errors.martDomain?.map((msg) => (
                      <small key={msg} className="text-red">{msg}</small>
                    ))}
                  </label>
                )}
              </>
            )}
            <small className="text-muted">
              Target table: <code>{layerTargetTableName(draft.medallionLayer, { entity: draft.curatedEntity, domain: draft.martDomain })}</code>
              {" "}— computed from the layer's naming standard, not freely typed.
            </small>
          </div>
        )}
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Pipeline details</h3>
        <div className="form-fields">
          <label>
            <span>Pipeline name</span>
            <Input
              value={draft.name}
              onChange={(e) => updateDraft("name", e.target.value)}
              placeholder={pipelineNamingRule?.prefix || pipelineNamingRule?.suffix ? buildExpectedName("customer_metadata_load", pipelineNamingRule) : "e.g. customer_metadata_load"}
            />
            <small>Lowercase letters, digits and underscores (3–64 chars).</small>
            {namingConvention?.enforcement !== "off" && pipelineNamingRule && (
              <small className={pipelineNameViolation?.level === "block" ? "text-red" : pipelineNameViolation ? "amber-text" : "text-muted"}>
                {pipelineNameViolation
                  ? pipelineNameViolation.message
                  : `Project naming standard: ${describeRule(pipelineNamingRule)}`}
              </small>
            )}
            {errors.name?.map((msg) => (
              <small key={msg} className="text-red">{msg}</small>
            ))}
          </label>
          <label>
            <span>Pipeline pattern</span>
            <Select
              value={draft.pattern}
              onChange={(e) => updateDraft("pattern", e.target.value as PipelinePattern)}
            >
              <option value="master_child">Master + child (metadata-driven) — N objects, scale-out</option>
              <option value="single_pipeline">Single pipeline (metadata-driven) — N objects, no nested ExecutePipeline</option>
              <option value="independent_full">Independent — full load (one object, no control table)</option>
              <option value="independent_watermark">Independent — watermark delta (one object, own watermark table)</option>
              <option value="child_only">Child only — attach an object to an existing master</option>
              <option value="master_only">Master only — wrap an existing child</option>
            </Select>
            <small>
              {draft.pattern === "master_child" && "Generates PL_{name}_master + PL_{name}_child + a control table. Today's default shape."}
              {draft.pattern === "single_pipeline" && "Generates one pipeline that loops the control table inline — no child pipeline, no nested ExecutePipeline."}
              {draft.pattern === "independent_full" && "Generates one pipeline with a single Copy activity. No control table — one source, one target."}
              {draft.pattern === "independent_watermark" && "Generates one pipeline with a watermark-delta chain and its own single-row watermark table. No control table."}
              {draft.pattern === "child_only" && "Generates just the child pipeline, for adding an object to an already-existing master via its own ExecutePipeline reference."}
              {draft.pattern === "master_only" && "Generates just the master pipeline, referencing an existing child pipeline by name."}
            </small>
          </label>
          <label>
            <span>Source system (optional)</span>
            <Input
              value={draft.sourceSystem ?? ""}
              onChange={(e) => updateDraft("sourceSystem", e.target.value || undefined)}
              placeholder="e.g. sap"
            />
            <small>
              Names the Bronze landed table <code>raw.&lt;source_system&gt;_&lt;object&gt;</code> (else the connector type)
              {draft.pattern === "master_child" || draft.pattern === "master_only" || draft.pattern === "child_only" ? (
                <>, and default pipeline names become <code>pl_master</code> / <code>pl_&lt;source_system&gt;_child</code></>
              ) : null}
              .
            </small>
          </label>
          {(draft.pattern === "master_child" || draft.pattern === "master_only") && (
            <label>
              <span>Master pipeline name (optional override)</span>
              <Input
                value={draft.masterPipelineName ?? ""}
                onChange={(e) => updateDraft("masterPipelineName", e.target.value || undefined)}
                placeholder={resolveMasterPipelineName(draft)}
              />
              <small>
                Generated as <code>{resolveMasterPipelineName(draft)}.json</code>
                {isEditing ? " — renaming here renames the master pipeline artifact on the next Generate." : "."}
              </small>
              <NamingHint name={resolveMasterPipelineName(draft)} convention={namingConvention} />
              {errors.masterPipelineName?.map((msg) => (
                <small key={msg} className="text-red">{msg}</small>
              ))}
            </label>
          )}
          {(draft.pattern === "master_child" || draft.pattern === "child_only" || draft.pattern === "master_only") && (
            <label>
              <span>
                Child pipeline name
                {draft.pattern === "master_child" ? " (optional override)" : " — the existing child this references"}
              </span>
              <Input
                value={draft.childPipelineName ?? ""}
                onChange={(e) => updateDraft("childPipelineName", e.target.value || undefined)}
                placeholder={draft.pattern === "master_child" ? resolveChildPipelineName(draft) : "e.g. PL_customer_child"}
              />
              <small>
                {draft.pattern === "master_child"
                  ? <>Generated as <code>{resolveChildPipelineName(draft)}.json</code>.</>
                  : "Required — must match the child pipeline's actual name in Git."}
              </small>
              {draft.pattern === "master_child" && (
                <NamingHint name={resolveChildPipelineName(draft)} convention={namingConvention} />
              )}
              {errors.childPipelineName?.map((msg) => (
                <small key={msg} className="text-red">{msg}</small>
              ))}
            </label>
          )}
          {(draft.pattern === "master_child" || draft.pattern === "master_only") && (
            <ChildDispatchEditor
              value={draft.childDispatch ?? []}
              defaultChildName={resolveChildPipelineName(draft)}
              suggestions={[
                ...data.pipelines.data
                  .filter((p) => p.projectId === draft.projectId && p.id !== editingPipelineId)
                  .map((p) => resolveChildPipelineName({ ...emptySpecDraft(), name: p.name, ...(p.draft as Partial<SpecDraft> | undefined) })),
                ...repoPipelines.names,
              ].filter((n) => n !== resolveChildPipelineName(draft) && n !== resolveMasterPipelineName(draft))}
              errors={errors.childDispatch}
              onChange={(next) => updateDraft("childDispatch", next)}
            />
          )}
          {(draft.pattern === "single_pipeline" || draft.pattern === "independent_full" || draft.pattern === "independent_watermark") && (
            <label>
              <span>Pipeline name (optional override)</span>
              <Input
                value={draft.pipelineName ?? ""}
                onChange={(e) => updateDraft("pipelineName", e.target.value || undefined)}
                placeholder={resolvePipelineName(draft)}
              />
              <small>Generated as <code>{resolvePipelineName(draft)}.json</code>.</small>
              <NamingHint name={resolvePipelineName(draft)} convention={namingConvention} />
              {errors.pipelineName?.map((msg) => (
                <small key={msg} className="text-red">{msg}</small>
              ))}
            </label>
          )}
          <label>
            <span>Template</span>
            <Input value="Metadata-Driven Load" disabled />
          </label>
          <label>
            <span>Project</span>
            <Input value={project?.name ?? "—"} disabled />
          </label>
          <label>
            <span>Pipeline type</span>
            <Input value="Metadata-driven (control table + master/child)" disabled />
          </label>
        </div>
      </div>

      <div className="wizard-actions">
        <Button variant="ghost" onClick={() => onNavigate("pipelines")}>
          Cancel
        </Button>
        <Button onClick={handleNext} disabled={!draft.projectId}>
          Continue
        </Button>
      </div>
    </div>
  );
}
