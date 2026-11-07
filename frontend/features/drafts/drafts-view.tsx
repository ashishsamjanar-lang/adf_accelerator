"use client";

import { useState } from "react";
import { FileEdit } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { EmptyState, resourceStatus } from "@/components/common/empty-state";
import { StatusPill } from "@/components/common/status-pill";
import { Pagination } from "@/components/common/pagination";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ValidationPanel } from "@/components/validation/validation-panel";
import { usePagination } from "@/lib/use-pagination";
import { formatDateTime, STATE_TONES } from "@/lib/format";
import { stateLabel, can } from "@/lib/state";
import { api, ApiError } from "@/lib/api/client";
import { pollUntil } from "@/lib/api/poll";
import type { DeploymentState, ValidationIssue, ValidationResult } from "@/types";

type DraftStateFilter = "all" | DeploymentState;

/** Which backend action (if any) a draft's current lifecycle state can move it through next (§9, §16-17). */
function nextAction(state: DeploymentState): "generate" | "validate" | null {
  if (state === "DRAFT") return "generate";
  if (state === "GENERATED") return "validate";
  return null;
}

export function DraftsView({ data, user, onNavigate }: FeatureProps) {
  // Drafts = all in-progress pipeline creation (DRAFT or GENERATED but not yet pushed).
  const allDrafts = data.pipelines.data.filter(
    (pipeline) => pipeline.state === "DRAFT" || pipeline.state === "GENERATED" || pipeline.state === "VALIDATED" || pipeline.state === "READY_TO_PUSH",
  );

  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState<DraftStateFilter>("all");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});
  /** AGENTS.md §29 — the latest validate result per pipeline, shown inline as
   * soon as Generate (which chains into Validate) or Validate finishes, so a
   * user doesn't have to leave Drafts to see whether anything failed. */
  const [validationById, setValidationById] = useState<Record<string, ValidationResult>>({});
  /** §29.1 layer 6 — opt-in live ARM validate-mode result per pipeline (never deploys). */
  const [liveById, setLiveById] = useState<Record<string, { issues: ValidationIssue[]; message: string }>>({});
  const [liveBusyId, setLiveBusyId] = useState<string | null>(null);

  const drafts = allDrafts.filter((pipeline) => {
    const q = query.trim().toLowerCase();
    const matchesQuery = !q || `${pipeline.name} ${pipeline.projectName}`.toLowerCase().includes(q);
    const matchesState = stateFilter === "all" || pipeline.state === stateFilter;
    return matchesQuery && matchesState;
  });

  const { page, setPage, totalPages, totalItems, pageSize, pageItems } = usePagination(drafts, {
    resetKey: `${query}|${stateFilter}`,
  });

  /**
   * Runs the pipeline's next backend step (generate or validate) — see
   * `nextAction` — so a draft actually advances through the real lifecycle
   * instead of sitting in DRAFT forever with no way to reach Push to Git.
   * Falls back to a visible per-row error (backend down, or this draft was
   * only ever created client-side and has no server-tracked id) rather than
   * failing silently.
   */
  async function runNextAction(pipelineId: string, action: "generate" | "validate") {
    setBusyId(pipelineId);
    setRowError((prev) => ({ ...prev, [pipelineId]: "" }));
    setValidationById((prev) =>
      Object.fromEntries(Object.entries(prev).filter(([id]) => id !== pipelineId)),
    );
    try {
      await api.pipelines[action](pipelineId);
      // Once a pipeline is generated, immediately chain into Validate and
      // show the result here — AGENTS.md §29's "show validation results on
      // the frontend once a pipeline is generated" — instead of leaving
      // Validate as a separate, invisible-result manual step.
      if (action === "generate" || action === "validate") {
        try {
          if (action === "generate") await api.pipelines.validate(pipelineId);
          const result = await api.pipelines.validationResult(pipelineId);
          setValidationById((prev) => ({ ...prev, [pipelineId]: result }));
        } catch {
          // Best-effort — the pipeline still advanced; just no inline result.
        }
      }
      data.pipelines.reload();
      if (action === "generate") data.artifacts.reload();
    } catch (err) {
      setRowError((prev) => ({
        ...prev,
        [pipelineId]:
          err instanceof ApiError
            ? err.message
            : `Couldn't reach the backend to ${action} this pipeline. It may only exist client-side — try recreating it once the API is reachable.`,
      }));
    } finally {
      setBusyId(null);
    }
  }

  async function runLiveCheck(pipelineId: string, projectId: string) {
    setLiveBusyId(pipelineId);
    setRowError((prev) => ({ ...prev, [pipelineId]: "" }));
    try {
      const queued = await api.integrations.liveCheck(projectId, pipelineId);
      const job = await pollUntil(
        () => api.integrations.job(projectId, queued.id),
        (j) => j.status === "succeeded" || j.status === "failed",
        { timeoutMs: 120_000 },
      );
      if (job.status === "failed") throw new Error(job.error ?? "The live check failed.");
      const result = (job.result ?? {}) as { issues?: { severity: string; message: string; ruleId?: string; target?: string }[] };
      const issues: ValidationIssue[] = (result.issues ?? []).map((i) => ({
        severity: i.severity === "error" ? "error" : "warning",
        message: i.message,
        layer: "live",
        ruleId: i.ruleId,
        source: i.target ?? undefined,
      }));
      const message = job.status === "succeeded"
        ? "Live ARM check passed — the sandbox resource group accepted every generated resource (validate-mode, nothing deployed)."
        : "The live check is still running in the worker — try again shortly.";
      setLiveById((prev) => ({ ...prev, [pipelineId]: { issues, message } }));
    } catch (err) {
      setRowError((prev) => ({
        ...prev,
        [pipelineId]: err instanceof Error ? err.message : "Couldn't run the live check.",
      }));
    } finally {
      setLiveBusyId(null);
    }
  }

  const canGenerate = can(user.role, "pipeline:generate");
  const canValidate = can(user.role, "pipeline:validate");

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="IN-PROGRESS WORK"
        title="Drafts"
        detail="All in-progress pipeline creation. Generate, then validate, to move a draft toward Push to Git in the Artifacts plane."
      />
      {allDrafts.length > 0 ? (
        <div className="catalog-toolbar mb-4">
          <label className="search-box">
            ⌕
            <Input
              placeholder="Search drafts by name or project"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="border-0 bg-transparent outline-none ring-0 focus:border-0 focus:ring-0"
            />
          </label>
          <Select
            value={stateFilter}
            onChange={(e) => setStateFilter(e.target.value as DraftStateFilter)}
            aria-label="Filter by state"
            className="w-auto"
          >
            <option value="all">All states</option>
            <option value="DRAFT">Draft</option>
            <option value="GENERATED">Generated</option>
            <option value="VALIDATED">Validated</option>
            <option value="READY_TO_PUSH">Ready to push</option>
          </Select>
        </div>
      ) : null}
      <section className="panel">
        {allDrafts.length === 0 ? (
          <EmptyState
            status={resourceStatus(data.pipelines)}
            errorMessage={data.pipelines.error}
            title="No open drafts"
            message="Start a new pipeline and your inputs are preserved as you work, so nothing is lost between sessions."
            action={
              <Button onClick={() => onNavigate("pipelines.create")}>Create pipeline</Button>
            }
          />
        ) : drafts.length === 0 ? (
          <EmptyState title="No drafts match" message="Try a different search term or state filter." />
        ) : (
          <div className="divide-y divide-line">
            {pageItems.map((pipeline) => {
              const action = nextAction(pipeline.state);
              const allowed = action === "generate" ? canGenerate : action === "validate" ? canValidate : true;
              const busy = busyId === pipeline.id;
              const error = rowError[pipeline.id];
              const validation = validationById[pipeline.id];
              return (
                <div key={pipeline.id}>
                <div className="project-row" style={{ gridTemplateColumns: "34px minmax(180px,1fr) 90px 70px auto" }}>
                  <span className="project-mark">
                    <FileEdit className="h-3.5 w-3.5" />
                  </span>
                  <div>
                    <strong>{pipeline.name}</strong>
                    <span>
                      {pipeline.projectName} · spec v{pipeline.specVersion} · created by{" "}
                      {pipeline.createdByName ?? pipeline.createdBy ?? "—"}
                    </span>
                    {error ? <span className="mt-1 block text-2xs text-red">{error}</span> : null}
                  </div>
                  <StatusPill tone={STATE_TONES[pipeline.state]}>{stateLabel(pipeline.state)}</StatusPill>
                  <span className="text-xs text-muted">{formatDateTime(pipeline.updatedAt)}</span>
                  <div className="flex items-center justify-end gap-2">
                    {action ? (
                      <Button
                        size="sm"
                        disabled={!allowed || busy}
                        title={!allowed ? "Requires the corresponding pipeline permission" : undefined}
                        onClick={() => runNextAction(pipeline.id, action)}
                      >
                        {busy ? (action === "generate" ? "Generating…" : "Validating…") : action === "generate" ? "Generate" : "Validate"}
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        onClick={() => onNavigate("delivery.catalog", { catalogQuery: pipeline.name })}
                        title="Opens the Catalog filtered to this pipeline's artifacts — pipeline, dataset, linked service, trigger, and parameter/control-table files together"
                      >
                        Review artifacts
                      </Button>
                    )}
                    {pipeline.state !== "DRAFT" ? (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={!canValidate || liveBusyId === pipeline.id}
                        title="Optional validation layer 6: ARM validate-mode against the project's sandbox resource group (Project Settings → Integrations). Never deploys."
                        onClick={() => runLiveCheck(pipeline.id, pipeline.projectId)}
                      >
                        {liveBusyId === pipeline.id ? "Checking…" : "Live ARM check"}
                      </Button>
                    ) : null}
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onNavigate("pipelines.create", { editPipelineId: pipeline.id })}
                    >
                      Continue setup
                    </Button>
                  </div>
                </div>
                {validation ? (
                  <div className="px-4 pb-3">
                    <ValidationPanel
                      issues={validation.issues}
                      emptyMessage={
                        validation.state === "pending"
                          ? "Validation is queued — check back shortly."
                          : "No issues found — this pipeline is ready for Push to Git."
                      }
                    />
                  </div>
                ) : null}
                {liveById[pipeline.id] ? (
                  <div className="px-4 pb-3">
                    <ValidationPanel issues={liveById[pipeline.id].issues} emptyMessage={liveById[pipeline.id].message} />
                  </div>
                ) : null}
                </div>
              );
            })}
          </div>
        )}
        <Pagination
          page={page}
          totalPages={totalPages}
          totalItems={totalItems}
          pageSize={pageSize}
          onPageChange={setPage}
          itemLabel="drafts"
        />
      </section>
    </div>
  );
}
