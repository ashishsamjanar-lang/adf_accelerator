import { useEffect, useRef, useState } from "react";
import { can } from "@/lib/state";
import { emptySpecDraft, TEMPLATES, validateDraft, type SpecDraft } from "@/lib/adf/spec";
import { defaultDatasetDraft, defaultLinkedServiceDraft } from "@/lib/adf/connections";
import { buildGeneratedArtifacts } from "@/lib/adf/pipeline-codegen";
import type { DatasetDraft, LinkedServiceDraft } from "@/lib/adf/connections";
import {
  cloneDataset,
  cloneLinkedService,
  useConnections,
  useConnectionsForRole,
  type SavedConnection,
} from "@/lib/adf/connection-store";
import { MAX_PIPELINE_DQ_RULES, onboardingPipelineSettings } from "@/lib/adf/onboarding";
import { useProjectSettings } from "@/lib/api/hooks";
import { evaluateName } from "@/lib/adf/naming";
import { EMPTY_PROJECT_SETTINGS } from "@/lib/api/empty-values";
import { api, ApiError } from "@/lib/api/client";
import type { ArtifactVersion, OnboardingObjectEntry, PipelineDefinition, PipelineTemplateId, SourceObjectEntry, ValidationResult } from "@/types";
import {
  METADATA_TEMPLATE_ID,
  STAGING_CONNECTOR_TYPES,
  STEP_FIELDS,
} from "@/features/build/wizard-shared";
import type { WizardProps } from "@/features/build/wizard-shared";

/**
 * Build wizard state + handlers (split out of create-pipeline-wizard.tsx so each step can live in its
 * own file). Every step component receives this hook's return value as `ctx`.
 */
/** §5C `defaultTemplateId` → the Loading behavior it implies; `metadata_driven_load` is the wizard's own shape. */
const LOAD_MODE_BY_TEMPLATE: Partial<Record<PipelineTemplateId, SpecDraft["loadTypeMode"]>> = {
  full_load: "full",
  incremental_load: "incremental",
  initial_load: "initial",
  composite_watermark_load: "composite_watermark",
};

export function usePipelineWizard({ data, user, onNavigate, onCreated, editPipelineId, onUpdated, onboardingPrefill }: WizardProps) {
  const [step, setStep] = useState(1);
  const [draft, setDraft] = useState(() => {
    const base = emptySpecDraft();
    base.templateId = METADATA_TEMPLATE_ID;
    base.masterChildPattern = true;
    base.watermarkEnabled = true;
    base.loadTypeMode = "incremental";
    base.projectId = (!editPipelineId && onboardingPrefill?.projectId) || (data.projects.data[0]?.id ?? "");
    return base;
  });
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  // Edit mode (§3 of the plan): reopening a pipeline this app already created, to rename its
  // master/child artifact names or append more objects — never a full Git round-trip (§24 target
  // design stays out of scope). `editingPipelineId` is the one actually saved-against on update;
  // it can differ from the `editPipelineId` prop once the user picks a *different* existing
  // pipeline from the Basics step's own "Add objects to an existing pipeline" selector.
  const [editingPipelineId, setEditingPipelineId] = useState<string | null>(null);
  // §24.14 — the loaded pipeline's `updatedAt`, echoed on save so a concurrent edit 409s instead of being overwritten.
  const [editingUpdatedAt, setEditingUpdatedAt] = useState<string | undefined>(undefined);
  const [saveConflict, setSaveConflict] = useState<string | null>(null);
  const [hydrating, setHydrating] = useState(false);
  const isEditing = editingPipelineId != null;

  /** Fetch a pipeline's saved draft and hydrate the wizard from it — shared by the mount-time
   * `editPipelineId` prop and the Basics step's "Add objects to an existing pipeline" picker. */
  async function loadPipelineForEdit(id: string) {
    setHydrating(true);
    try {
      const pipeline = await api.pipelines.details(id);
      const hydrated: SpecDraft = { ...emptySpecDraft(), ...(pipeline.draft as Partial<SpecDraft> | undefined) };
      // The saved draft may predate a field (forward-compat) or, in demo/offline mode, be entirely
      // absent — `name`/`projectId` always come from the pipeline record itself either way, never
      // silently reset to the empty-draft default.
      hydrated.name = pipeline.name;
      hydrated.projectId = pipeline.projectId;
      hydrated.templateId = METADATA_TEMPLATE_ID;
      setDraft(hydrated);
      setEditingPipelineId(pipeline.id);
      setEditingUpdatedAt(pipeline.updatedAt || undefined);
      setSaveConflict(null);
      setErrors({});
      setStep(2); // the user is here to add objects, not re-answer Basics
    } catch {
      // Backend unreachable — degrade to a fresh create-mode draft rather than a dead end.
      setEditingPipelineId(null);
    } finally {
      setHydrating(false);
    }
  }

  useEffect(() => {
    if (editPipelineId) void loadPipelineForEdit(editPipelineId);
  }, [editPipelineId]);

  /** Basics step's "Start a new pipeline instead" — leaves the currently-selected project in
   * place but otherwise returns to a fresh create-mode draft. */
  function resetToCreateMode() {
    setEditingPipelineId(null);
    setDraft((prev) => {
      const base = emptySpecDraft();
      base.templateId = METADATA_TEMPLATE_ID;
      base.masterChildPattern = true;
      base.watermarkEnabled = true;
      base.loadTypeMode = "incremental";
      base.projectId = prev.projectId;
      return base;
    });
    setErrors({});
    setStep(1);
  }
  const [generating, setGenerating] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [artifacts, setArtifacts] = useState<ArtifactVersion[]>([]);
  const [activeArtifact, setActiveArtifact] = useState<ArtifactVersion | null>(null);
  const [copied, setCopiedState] = useState(false);
  // The "Copied!" reset timer; cleared on every copy/reset so a stale one can't blank a fresh label.
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function setCopied(value: boolean) {
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = null;
    setCopiedState(value);
  }
  useEffect(() => () => {
    if (copyTimer.current) clearTimeout(copyTimer.current);
  }, []);
  const [finishing, setFinishing] = useState(false);
  /** Set once "Create pipeline" has persisted + generated + (best-effort)
   * validated a real server-tracked pipeline (AGENTS.md §29) — drives the
   * post-generate results screen below instead of navigating away silently. */
  const [finishResult, setFinishResult] = useState<{ pipeline: PipelineDefinition; validation: ValidationResult | null } | null>(null);

  const savedSourceConnections = useConnectionsForRole("source");
  const savedTargetConnections = useConnectionsForRole("target");
  // §24.6 pick-or-reference for staging: saved Blob/ADLS Gen2 linked services, offered as suggestions.
  const stagingCandidates = Array.from(
    new Set(
      useConnections()
        .filter((c) => STAGING_CONNECTOR_TYPES.has(c.linkedService.connectorType))
        .map((c) => c.linkedService.name)
        .filter(Boolean),
    ),
  );
  const [sourceConnectionId, setSourceConnectionId] = useState("");
  const [targetConnectionId, setTargetConnectionId] = useState("");
  const sourceConnection = savedSourceConnections.find((c) => c.id === sourceConnectionId);
  const targetConnection = savedTargetConnections.find((c) => c.id === targetConnectionId);

  const project = data.projects.data.find((p) => p.id === draft.projectId);
  const template = TEMPLATES[METADATA_TEMPLATE_ID];
  const canCreate = can(user.role, "pipeline:create");

  // §25 opt-in naming standard for the active project — inline hint only;
  // the hard Block gate lives server-side (validate + Push to Git) so this
  // never silently rewrites what the user typed (§25.6 "pre-generate").
  const projectSettings = useProjectSettings(draft.projectId, EMPTY_PROJECT_SETTINGS);
  const namingConvention = projectSettings.data.namingConvention;
  const pipelineNamingRule = namingConvention?.rules?.pipeline;
  const pipelineNameViolation = evaluateName(draft.name, "pipeline", namingConvention);

  // Fields below that Project Settings already defines a value for (ADF
  // defaults §5B, control table §5C) are collapsed to a summary via
  // `InheritedField` instead of repeating an editable box the user would
  // otherwise have to fill in again — see `revealField`/`hideField` and each
  // field's `has*Default` flag below. Fields the project hasn't configured
  // stay as plain editable inputs; there's nothing to hide.
  const adfDefaults = projectSettings.data.adfDefaults;
  const templateDefaults = projectSettings.data.templateDefaults;
  const projectTags = (projectSettings.data.tags ?? []).filter((t) => t.key?.trim());
  const controlTableSettings = templateDefaults?.controlTable;
  const hasControlTableDefault = !!(controlTableSettings?.schemaName && controlTableSettings?.tableName);
  const hasConnectionControlDefault = !!controlTableSettings;
  const hasParallelCopiesDefault = adfDefaults?.defaultParallelCopies != null;
  const hasRetryCountDefault = adfDefaults?.defaultRetryCount != null;
  const hasRetryIntervalDefault = adfDefaults?.defaultRetryIntervalInSeconds != null;
  const hasLogLevelDefault = !!adfDefaults?.logLevel;
  const hasAzureMonitorDefault = adfDefaults?.enableAzureMonitor != null;

  const [revealedFields, setRevealedFields] = useState<Record<string, boolean>>({});
  const isRevealed = (field: string) => !!revealedFields[field];
  const revealField = (field: string) => setRevealedFields((prev) => ({ ...prev, [field]: true }));
  /** Un-reveal a field and snap its value back to the project default (`apply` writes that value). */
  function hideField(field: string, apply: () => void) {
    setRevealedFields((prev) => {
      const next = { ...prev };
      delete next[field];
      return next;
    });
    apply();
  }

  // Prefill the control/watermark table, connection control table, and ADF
  // runtime defaults (parallel copies, retries, log level, Azure Monitor)
  // from the selected project's Settings the first time they load for that
  // project — so a team's configured values actually take effect on a new
  // pipeline (and can be shown collapsed via `InheritedField` above) instead
  // of every project sharing the same hardcoded default. Only fills in a
  // field the user hasn't already touched away from `emptySpecDraft()`'s
  // built-in default — an explicit edit is never silently overwritten
  // (DEVELOPMENT_NOTES.md rule 7).
  const projectDefaultsAppliedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!draft.projectId) return;
    // Wait for *this* project's settings — marking it applied while the fetch is still in flight
    // (data still the empty placeholder or the previous project's) would skip the defaults forever.
    if (projectSettings.data.projectId !== draft.projectId) return;
    if (projectDefaultsAppliedFor.current === draft.projectId) return;
    projectDefaultsAppliedFor.current = draft.projectId;
    const defaults = emptySpecDraft();
    // §5B default source/target connector (gap #33): only a new pipeline whose connector the
    // user hasn't touched yet — never an edited/hydrated one, never a hand-configured slot.
    const sourceDefault = adfDefaults?.defaultSourceConnector;
    const targetDefault = adfDefaults?.defaultTargetConnector;
    const applySource =
      !editingPipelineId && !!sourceDefault && sourceDefault !== defaults.sourceType && !sourceConnectionId;
    const applyTarget =
      !editingPipelineId && !!targetDefault && targetDefault !== defaults.targetType && !targetConnectionId;
    setDraft((prev) => ({
      ...prev,
      ...(applySource && prev.sourceType === defaults.sourceType
        ? {
            sourceType: sourceDefault!,
            sourceLinkedService: defaultLinkedServiceDraft("source", sourceDefault!),
            sourceDataset: defaultDatasetDraft("source", sourceDefault!),
          }
        : {}),
      ...(applyTarget && prev.targetType === defaults.targetType
        ? {
            targetType: targetDefault!,
            targetLinkedService: defaultLinkedServiceDraft("target", targetDefault!),
            targetDataset: defaultDatasetDraft("target", targetDefault!),
          }
        : {}),
      watermarkTable:
        hasControlTableDefault && prev.watermarkTable === defaults.watermarkTable
          ? `${controlTableSettings!.schemaName}.${controlTableSettings!.tableName}`
          : prev.watermarkTable,
      connectionControlEnabled:
        hasConnectionControlDefault && prev.connectionControlEnabled === defaults.connectionControlEnabled
          ? controlTableSettings!.enableConnectionControlTable
          : prev.connectionControlEnabled,
      connectionControlTable:
        hasConnectionControlDefault && prev.connectionControlTable === defaults.connectionControlTable
          ? `${controlTableSettings!.schemaName}.${controlTableSettings!.connectionTableName || "adf_connection_control"}`
          : prev.connectionControlTable,
      parallelCopies:
        hasParallelCopiesDefault && prev.parallelCopies === defaults.parallelCopies
          ? adfDefaults!.defaultParallelCopies!
          : prev.parallelCopies,
      retryCount:
        hasRetryCountDefault && prev.retryCount === defaults.retryCount
          ? adfDefaults!.defaultRetryCount!
          : prev.retryCount,
      retryIntervalInSeconds:
        hasRetryIntervalDefault && prev.retryIntervalInSeconds === defaults.retryIntervalInSeconds
          ? adfDefaults!.defaultRetryIntervalInSeconds!
          : prev.retryIntervalInSeconds,
      logLevel:
        hasLogLevelDefault && prev.logLevel === defaults.logLevel
          ? (adfDefaults!.logLevel as SpecDraft["logLevel"])
          : prev.logLevel,
      enableAzureMonitor:
        hasAzureMonitorDefault && prev.enableAzureMonitor === defaults.enableAzureMonitor
          ? adfDefaults!.enableAzureMonitor!
          : prev.enableAzureMonitor,
      // §5C filter condition + custom metadata columns, and §5A project tags as pipeline
      // annotations (gap #33) — new pipelines only, and only while still at the built-in empty value.
      ...(!editingPipelineId && templateDefaults?.defaultFilterCondition?.trim() && !prev.sourceFilterCondition
        ? { sourceFilterCondition: templateDefaults.defaultFilterCondition.trim() }
        : {}),
      ...(!editingPipelineId && templateDefaults?.customMetadataColumns?.length && prev.customMetadata.length === 0
        ? { customMetadata: templateDefaults.customMetadataColumns.slice(0, 10).map((c) => ({ ...c })) }
        : {}),
      ...(!editingPipelineId && projectTags.length && !prev.annotations?.length
        ? { annotations: projectTags.map((t) => (t.value ? `${t.key}:${t.value}` : t.key)) }
        : {}),
      // §5B defaultTimeout → Copy activity timeout; §5C defaultWatermarkType → the untouched default
      // watermark column's data type (gap #33) — new pipelines only.
      ...(!editingPipelineId && adfDefaults?.defaultTimeout?.trim() && !prev.copyTimeout
        ? { copyTimeout: adfDefaults.defaultTimeout.trim() }
        : {}),
      ...(!editingPipelineId &&
      templateDefaults?.defaultWatermarkType &&
      prev.watermarkColumns.length === 1 &&
      prev.watermarkColumns[0].name === defaults.watermarkColumns[0].name &&
      prev.watermarkColumns[0].type === defaults.watermarkColumns[0].type
        ? { watermarkColumns: [{ ...prev.watermarkColumns[0], type: templateDefaults.defaultWatermarkType }] }
        : {}),
      // §5C defaultTemplateId (gap #33): the template ids survive only as load-strategy keys (§24), so a
      // non-metadata default picks the matching Loading behavior — new pipelines still on the wizard default only.
      ...(() => {
        const mode = !editingPipelineId && prev.loadTypeMode === "incremental"
          ? LOAD_MODE_BY_TEMPLATE[templateDefaults?.defaultTemplateId ?? "metadata_driven_load"]
          : undefined;
        return mode ? { loadTypeMode: mode, watermarkEnabled: mode !== "full", fullLoad: mode === "full" } : {};
      })(),
    }));
    // §5B default source/target linked service (gap #33): preselect the saved Connection whose
    // linked service carries that name — same path as picking it by hand, new pipelines only.
    if (!editingPipelineId) {
      const pick = (role: "source" | "target", lsName: string | undefined, current: string, list: SavedConnection[]) => {
        const name = lsName?.trim();
        if (!name || current) return;
        const conn = list.find((c) => c.linkedService.name === name);
        if (conn) applySavedConnection(role, conn);
      };
      pick("source", adfDefaults?.defaultSourceLinkedService, sourceConnectionId, savedSourceConnections);
      pick("target", adfDefaults?.defaultTargetLinkedService, targetConnectionId, savedTargetConnections);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.projectId, projectSettings.data]);

  const loadingBehaviorLabel =
    draft.loadTypeMode === "full"
      ? "Full load (snapshot / truncate-and-load)"
      : draft.loadTypeMode === "initial"
        ? "Initial load (seed + watermark start)"
        : draft.loadTypeMode === "composite_watermark"
          ? "Composite watermark load"
          : "Incremental load (delta via watermark)";

  function updateDraft<K extends keyof SpecDraft>(key: K, value: SpecDraft[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => ({ ...prev, [key]: [] }));
  }

  /** Object list (tables / input files) — form rows or imported file rows. */
  function updateSourceObjects(mode: "form" | "file", rows: SourceObjectEntry[]) {
    setDraft((prev) => ({
      ...prev,
      sourceObjectMode: mode,
      sourceObjects: rows,
      sourceObject: rows[0]?.object?.trim() || prev.sourceObject,
    }));
    setErrors((prev) => ({ ...prev, sourceObjects: [] }));
  }

  /** Appends onboarded Bronze objects (`OnboardingObjectPicker`) into the existing row list —
   * additive (dedupe already done by the picker itself), except that the untouched `emptySpecDraft()`
   * placeholder row is replaced rather than kept as a copy of a table nobody onboarded. The picked rows'
   * write behavior / upsert keys / target fields fill the pipeline settings; returns notes for the picker. */
  function importOnboardingObjects(
    rows: SourceObjectEntry[],
    dqRules: SpecDraft["dataQualityRules"] = [],
    picked: OnboardingObjectEntry[] = [],
  ): string[] {
    if (rows.length === 0) return [];
    // Applied against the latest state: the picker calls this after an async DQ-rule fetch, so the
    // `draft` this closure captured can be stale (a row removed meanwhile would come back).
    const placeholder = JSON.stringify(emptySpecDraft().sourceObjects);
    setDraft((prev) => {
      const untouched = !isEditing && JSON.stringify(prev.sourceObjects) === placeholder;
      const sourceObjects = untouched ? rows : [...prev.sourceObjects, ...rows];
      const settings = picked.length ? onboardingPipelineSettings(picked, prev).patch : {};
      return {
        ...prev,
        ...settings,
        sourceObjectMode: "form",
        sourceObjects,
        sourceObject: sourceObjects[0]?.object?.trim() || prev.sourceObject,
      };
    });
    setErrors((prev) => ({ ...prev, sourceObjects: [] }));
    const { notes } = picked.length ? onboardingPipelineSettings(picked, draft) : { notes: [] as string[] };
    // Registry-authored DQ rules (§30.5 `app.dq_rules`) ride along — deduped by name and capped at
    // the pipeline's rule limit; the picker reports what didn't fit.
    if (dqRules.length) {
      setDraft((prev) => {
        const names = new Set(prev.dataQualityRules.map((r) => r.name));
        const fresh = dqRules.filter((r) => !names.has(r.name));
        return { ...prev, dataQualityRules: [...prev.dataQualityRules, ...fresh].slice(0, MAX_PIPELINE_DQ_RULES) };
      });
    }
    return notes;
  }

  /** Drop a saved Connection (Delivery > Connections) into a pipeline slot. */
  function applySavedConnection(kind: "source" | "target", conn: SavedConnection | undefined) {
    if (kind === "source") {
      setSourceConnectionId(conn?.id ?? "");
      if (!conn) {
        // Overriding back to manual editing — a hand-edited LS/DS must always be generated.
        setDraft((prev) => (prev.sourceConnectionReused ? { ...prev, sourceConnectionReused: false } : prev));
        return;
      }
      const ls = cloneLinkedService(conn.linkedService);
      setDraft((prev) => ({
        ...prev,
        sourceLinkedService: ls,
        sourceType: ls.connectorType,
        sourceDataset: { ...cloneDataset(conn.dataset), linkedServiceName: ls.name },
        // Switching to a (possibly different) saved connection — don't carry over a stale "already
        // pushed" confirmation from whatever was picked before; the user re-confirms per connection.
        sourceConnectionReused: false,
        ...(conn.objects.length
          ? {
              sourceObjectMode: conn.objectMode,
              sourceObjects: conn.objects.map((o) => ({ ...o })),
              sourceObject: conn.objects[0]?.object?.trim() || prev.sourceObject,
            }
          : {}),
      }));
      setErrors((prev) => ({ ...prev, sourceLinkedService: [], sourceDataset: [], sourceObjects: [] }));
      return;
    }
    setTargetConnectionId(conn?.id ?? "");
    if (!conn) {
      setDraft((prev) => (prev.targetConnectionReused ? { ...prev, targetConnectionReused: false } : prev));
      return;
    }
    const ls = cloneLinkedService(conn.linkedService);
    setDraft((prev) => ({
      ...prev,
      targetLinkedService: ls,
      targetType: ls.connectorType,
      targetDataset: { ...cloneDataset(conn.dataset), linkedServiceName: ls.name },
      targetPath: conn.dataset.folderPath.trim() || prev.targetPath,
      targetFileName: conn.dataset.fileName.trim() || prev.targetFileName,
      targetConnectionReused: false,
    }));
    setErrors((prev) => ({ ...prev, targetLinkedService: [], targetDataset: [], targetPath: [] }));
  }

  function updateSourceLinkedService(ls: LinkedServiceDraft) {
    setSourceConnectionId("");
    setDraft((prev) => ({
      ...prev,
      sourceLinkedService: ls,
      sourceType: ls.connectorType,
      sourceDataset: { ...prev.sourceDataset, linkedServiceName: ls.name },
    }));
    setErrors((prev) => ({ ...prev, sourceLinkedService: [] }));
  }

  function updateTargetLinkedService(ls: LinkedServiceDraft) {
    setTargetConnectionId("");
    setDraft((prev) => ({
      ...prev,
      targetLinkedService: ls,
      targetType: ls.connectorType,
      targetDataset: { ...prev.targetDataset, linkedServiceName: ls.name },
    }));
    setErrors((prev) => ({ ...prev, targetLinkedService: [] }));
  }

  function updateSourceDataset(ds: DatasetDraft) {
    setDraft((prev) => ({ ...prev, sourceDataset: ds }));
    setErrors((prev) => ({ ...prev, sourceDataset: [] }));
  }

  function updateTargetDataset(ds: DatasetDraft) {
    setDraft((prev) => ({ ...prev, targetDataset: ds }));
    setErrors((prev) => ({ ...prev, targetDataset: [] }));
  }

  /** Validates the whole draft, but only gates "Continue" on fields that live on `forStep` —
   * later steps aren't blocked by fields the user hasn't reached yet. */
  function validateStep(forStep: number): boolean {
    const issues = validateDraft(draft);
    const newErrors: Record<string, string[]> = {};
    for (const issue of issues) {
      if (issue.field) {
        newErrors[issue.field] = [...(newErrors[issue.field] ?? []), issue.message];
      }
    }
    setErrors(newErrors);
    const relevantFields = STEP_FIELDS[forStep] ?? [];
    // Warnings are shown with the errors but never block the step.
    return !issues.some((issue) => issue.severity === "error" && issue.field && relevantFields.includes(issue.field));
  }

  function handleNext() {
    if (!validateStep(step)) return;
    setStep((s) => Math.min(s + 1, 5));
  }

  function handleBack() {
    if (step === 5) {
      setGenerated(false);
      setFinishResult(null);
    }
    setStep((s) => Math.max(s - 1, 1));
  }

  async function handleGenerate() {
    const name = draft.name.trim();
    if (!name) {
      setErrors((prev) => ({ ...prev, name: ["A pipeline name is required."] }));
      setStep(1);
      return;
    }
    setGenerating(true);
    await new Promise((r) => setTimeout(r, 1100));
    const targetEnvironment = data.environments.data.find((env) => env.name === draft.targetEnvironmentName)?.configuration ?? null;
    const code = buildGeneratedArtifacts(draft, targetEnvironment);
    setArtifacts(code);
    setActiveArtifact(code[0] ?? null);
    setCopied(false);
    setGenerated(true);
    setGenerating(false);
  }

  async function handleCopy() {
    if (!activeArtifact?.content) return;
    try {
      await navigator.clipboard.writeText(activeArtifact.content);
      setCopied(true);
      copyTimer.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }

  /**
   * "Create pipeline" used to only ever add a client-only entry — nothing
   * called the backend, so the pipeline could never actually be pushed to Git
   * later (Push to Git needs a server-tracked pipeline id, see
   * `git-push-dialog.tsx`). Now: persist the draft for real
   * (`POST /pipelines`), then generate its artifacts server-side so they're
   * immediately pushable from the Artifacts plane. Falls back to the old
   * client-only behavior if the backend is unreachable (demo mode / API down)
   * so the wizard keeps working either way.
   */
  async function handleFinish() {
    setFinishing(true);
    try {
      // Edit mode (append objects / rename master+child) updates the pipeline this draft was
      // hydrated from instead of creating a separate one — same generate/validate chain either way.
      const created = isEditing && editingPipelineId
        ? await api.pipelines.update(editingPipelineId, {
            name: draft.name,
            templateId: draft.templateId,
            draft: draft as unknown as Record<string, unknown>,
            expectedUpdatedAt: editingUpdatedAt,
          })
        : await api.pipelines.create({
            name: draft.name,
            projectId: draft.projectId,
            templateId: draft.templateId,
            draft: draft as unknown as Record<string, unknown>,
          });
      let persisted = created;
      let validation: ValidationResult | null = null;
      try {
        // Best-effort: if generation fails (e.g. the worker isn't running),
        // the pipeline is still saved as a DRAFT — it can be generated again
        // later from Drafts.
        persisted = await api.pipelines.generate(created.id);
        try {
          // AGENTS.md §29 — show validation results on the frontend as soon
          // as a pipeline is generated, instead of leaving "was it actually
          // valid?" to a separate manual step in Drafts. Best-effort: a
          // failure here still leaves the pipeline visible in Drafts, just
          // without an inline result.
          persisted = await api.pipelines.validate(created.id);
          validation = await api.pipelines.validationResult(created.id);
        } catch {
          // swallow — see comment above
        }
      } catch {
        // swallow — see comment above
      }
      if (isEditing) onUpdated?.(persisted);
      else onCreated(draft, persisted);
      if (validation) {
        // Stay on this screen and show what was found — see the results
        // block rendered below — instead of navigating straight to Drafts.
        setFinishResult({ pipeline: persisted, validation });
      } else {
        onNavigate("pipelines.drafts");
      }
    } catch (err) {
      // §24.14 — a concurrent edit must stop the save visibly, never fall through to "saved".
      if (err instanceof ApiError && err.code === "version_conflict") {
        setSaveConflict(err.message);
        return;
      }
      // Backend unreachable — keep the pipeline visible locally, same as
      // before this change, rather than blocking the user.
      if (!isEditing) onCreated(draft);
      onNavigate("pipelines.drafts");
    } finally {
      setFinishing(false);
    }
  }

  return {
    data,
    user,
    onNavigate,
    onCreated,
    editPipelineId,
    onUpdated,
    onboardingPrefill,
    step,
    setStep,
    draft,
    setDraft,
    errors,
    setErrors,
    editingPipelineId,
    setEditingPipelineId,
    editingUpdatedAt,
    setEditingUpdatedAt,
    saveConflict,
    setSaveConflict,
    hydrating,
    setHydrating,
    isEditing,
    loadPipelineForEdit,
    resetToCreateMode,
    generating,
    setGenerating,
    generated,
    setGenerated,
    artifacts,
    setArtifacts,
    activeArtifact,
    setActiveArtifact,
    copied,
    setCopied,
    finishing,
    setFinishing,
    finishResult,
    setFinishResult,
    savedSourceConnections,
    savedTargetConnections,
    stagingCandidates,
    sourceConnectionId,
    setSourceConnectionId,
    targetConnectionId,
    setTargetConnectionId,
    sourceConnection,
    targetConnection,
    project,
    template,
    canCreate,
    projectSettings,
    namingConvention,
    pipelineNamingRule,
    pipelineNameViolation,
    adfDefaults,
    templateDefaults,
    projectTags,
    controlTableSettings,
    hasControlTableDefault,
    hasConnectionControlDefault,
    hasParallelCopiesDefault,
    hasRetryCountDefault,
    hasRetryIntervalDefault,
    hasLogLevelDefault,
    hasAzureMonitorDefault,
    revealedFields,
    setRevealedFields,
    isRevealed,
    revealField,
    hideField,
    projectDefaultsAppliedFor,
    loadingBehaviorLabel,
    updateDraft,
    updateSourceObjects,
    importOnboardingObjects,
    applySavedConnection,
    updateSourceLinkedService,
    updateTargetLinkedService,
    updateSourceDataset,
    updateTargetDataset,
    validateStep,
    handleNext,
    handleBack,
    handleGenerate,
    handleCopy,
    handleFinish,
  };
}

/** Everything a wizard step reads or calls — the hook's full state + handler surface. */
export type WizardContext = ReturnType<typeof usePipelineWizard>;
