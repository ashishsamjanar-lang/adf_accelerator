"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Copy, Download, GitCompare, GitPullRequest, Hammer, ListChecks, Network, Pencil, Plus, ShieldCheck, Trash2, Upload } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/common/empty-state";
import { StatusPill } from "@/components/common/status-pill";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { downloadTextFile } from "@/lib/download";
import { api, ApiError } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import {
  buildOnboardingControlTableSql,
  buildOnboardingObjectsCsv,
  buildOnboardingObjectsTemplate,
  buildRegistryLineage,
  newOnboardingObject,
  ONBOARDING_COLUMNS,
  type OnboardingHeaderMap,
  type ParsedOnboardingObjects,
  parseOnboardingObjectsFile,
  parseOnboardingObjectsWorkbook,
  validateOnboardingRow,
} from "@/lib/adf/onboarding";
import {
  profileMapFor,
} from "@/lib/adf/mapping-profiles";
import { Input } from "@/components/ui/input";
import { ObjectDrawer } from "@/features/onboarding/object-drawer";
import { RegistryTab } from "@/features/onboarding/registry-tab";
import { DqRulesDialog } from "@/features/onboarding/dq-rules-dialog";
import { SyncToGitDialog } from "@/features/onboarding/sync-to-git-dialog";
import { GitDriftDialog } from "@/features/onboarding/git-drift-dialog";
import { pollUntil } from "@/lib/api/poll";
import type { OnboardingImportRecord, OnboardingMappingProfile, OnboardingObjectEntry, Project } from "@/types";

const IMPORT_STEPS = ["Upload", "Map & preview", "Add to registry"] as const;

const NO_ROWS: OnboardingObjectEntry[] = [];
const NO_IMPORTS: OnboardingImportRecord[] = [];

const GRID_COLUMNS = ["Object", "Source", "Target", "Format", "Load", "Status", ""] as const;

function targetLabel(row: OnboardingObjectEntry): string {
  if (row.target_type === "file") return row.target_path || row.target_file_name || "—";
  return [row.target_schema, row.target_object].filter(Boolean).join(".") || "—";
}

interface BronzeTabProps {
  projects: Project[];
  canEdit: boolean;
  canHardDelete: boolean;
  onNavigate: FeatureProps["onNavigate"];
}

function BronzeTab({ projects, canEdit, canHardDelete, onNavigate }: BronzeTabProps) {
  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  useEffect(() => {
    if (!projectId && projects[0]) setProjectId(projects[0].id);
  }, [projectId, projects]);

  // Through useResource so a real fetch failure reaches the shell's ApiErrorBanner (gap #31).
  const fetchRows = useCallback(
    () => (projectId ? api.onboarding.list(projectId) : Promise.resolve(NO_ROWS)),
    [projectId],
  );
  const registry = useResource(fetchRows, NO_ROWS, `onboarding:${projectId}`);
  const rows = registry.data;
  const loading = registry.loading;
  const loadError = registry.failed ? registry.error ?? "Unable to load the registry." : null;
  const fetchImports = useCallback(
    () => (projectId ? api.onboarding.imports(projectId) : Promise.resolve(NO_IMPORTS)),
    [projectId],
  );
  const importHistory = useResource(fetchImports, NO_IMPORTS, `onboarding-imports:${projectId}`);
  const reload = () => {
    registry.reload();
    importHistory.reload();
  };
  const [dqObjectKey, setDqObjectKey] = useState<string | null>(null);
  const [mode, setMode] = useState<"upload" | "manual">("manual");
  const [editing, setEditing] = useState<{ entry: OnboardingObjectEntry; isNew: boolean } | null>(null);

  const [pending, setPending] = useState<OnboardingObjectEntry[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [fileName, setFileName] = useState("");
  const [rawText, setRawText] = useState("");
  // §26.3 Map step — the parsed text (CSV/TSV/JSON, or an .xlsx's first sheet as TSV) is kept so
  // remapping a header re-parses without asking for the file again.
  const [source, setSource] = useState<{ text: string; name: string } | null>(null);
  const [headerMap, setHeaderMap] = useState<OnboardingHeaderMap>({});
  const [unmappedHeaders, setUnmappedHeaders] = useState<string[]>([]);
  // §26.3 saved mapping profiles — per project, server-side (§26.7 `/mapping-profiles`).
  const [profiles, setProfiles] = useState<OnboardingMappingProfile[]>([]);
  const [profileName, setProfileName] = useState("");
  // Recorded on the import audit row (`onboarding_imports.mapping_profile_id`) at commit.
  const [appliedProfileId, setAppliedProfileId] = useState<string | null>(null);
  const reloadProfiles = useCallback(() => {
    if (!projectId) return setProfiles([]);
    api.onboarding.mappingProfiles
      .list(projectId)
      .then(setProfiles)
      .catch(() => setProfiles([]));
  }, [projectId]);
  useEffect(reloadProfiles, [reloadProfiles]);
  async function saveProfile() {
    try {
      await api.onboarding.mappingProfiles.save(projectId, { name: profileName, headerMap });
      setProfileName("");
      reloadProfiles();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not save the mapping profile.");
    }
  }
  async function removeProfile(profileId: string) {
    try {
      await api.onboarding.mappingProfiles.remove(projectId, profileId);
      reloadProfiles();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not delete the mapping profile.");
    }
  }
  const [dragging, setDragging] = useState(false);
  const [commitSummary, setCommitSummary] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const [driftOpen, setDriftOpen] = useState(false);
  const [purviewBusy, setPurviewBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function applyParsed(parsed: ParsedOnboardingObjects, map: OnboardingHeaderMap) {
    setPending(parsed.objects);
    setWarnings(parsed.warnings);
    // Keep already-mapped headers listed so the user can change or undo a mapping.
    setUnmappedHeaders([...new Set([...Object.keys(map), ...parsed.unmappedHeaders])]);
    setCommitSummary(null);
  }

  function ingest(content: string, name: string) {
    setHeaderMap({});
    setAppliedProfileId(null);
    setSource({ text: content, name });
    applyParsed(parseOnboardingObjectsFile(content, name), {});
    setFileName(name);
    setRawText(content);
  }

  async function handleFile(file: File) {
    if (!/\.xlsx$/i.test(file.name)) return ingest(await file.text(), file.name);
    const parsed = await parseOnboardingObjectsWorkbook(await file.arrayBuffer(), file.name);
    setHeaderMap({});
    setAppliedProfileId(null);
    setSource(parsed.sourceText !== undefined ? { text: parsed.sourceText, name: file.name.replace(/\.xlsx$/i, ".tsv") } : null);
    applyParsed(parsed, {});
    setFileName(file.name);
    setRawText("");
  }

  function remapHeader(header: string, columnKey: string) {
    if (!source) return;
    const next = { ...headerMap };
    if (columnKey) next[header] = columnKey;
    else delete next[header];
    setHeaderMap(next);
    applyParsed(parseOnboardingObjectsFile(source.text, source.name, next), next);
  }

  function applyProfile(name: string) {
    const profile = profiles.find((p) => p.name === name);
    if (!source || !profile) return;
    const next = { ...headerMap, ...profileMapFor(profile, unmappedHeaders) };
    setHeaderMap(next);
    setAppliedProfileId(profile.id);
    applyParsed(parseOnboardingObjectsFile(source.text, source.name, next), next);
  }

  async function commitPending() {
    if (!pending.length || !projectId) return;
    try {
      const { added, updated } = await api.onboarding.bulkUpsert(projectId, pending, fileName, appliedProfileId ?? undefined);
      setCommitSummary(
        `Added ${added} object${added === 1 ? "" : "s"}${updated ? `, updated ${updated} existing` : ""} — nothing else in the registry was changed.`,
      );
      setPending([]);
      setWarnings([]);
      setRawText("");
      setFileName("");
      setSource(null);
      setHeaderMap({});
      setAppliedProfileId(null);
      setUnmappedHeaders([]);
      setMode("manual");
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Import failed.");
    }
  }

  /** Registry-wide lineage: Bronze rows already loaded, Silver/Gold fetched on demand. */
  async function downloadLineage() {
    try {
      const [silver, gold] = await Promise.all([
        api.registry.list("silver", projectId),
        api.registry.list("mart", projectId),
      ]);
      downloadTextFile("registry-lineage.json", JSON.stringify(buildRegistryLineage(rows, silver, gold), null, 2), "application/json");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Could not build registry lineage.");
    }
  }

  /** Same registry lineage, pushed to Microsoft Purview by the worker (Project Settings → Integrations). */
  async function pushLineageToPurview() {
    setPurviewBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const [silver, gold] = await Promise.all([api.registry.list("silver", projectId), api.registry.list("mart", projectId)]);
      const queued = await api.integrations.purviewPush(projectId, buildRegistryLineage(rows, silver, gold));
      const job = await pollUntil(
        () => api.integrations.job(projectId, queued.id),
        (j) => j.status === "succeeded" || j.status === "failed",
      );
      if (job.status === "failed") setActionError(job.error ?? "The Purview push failed.");
      else if (job.status === "succeeded") {
        const r = job.result ?? {};
        setNotice(`Pushed to Purview: ${r.processes} processes, ${r.datasets} datasets (${r.created} created, ${r.updated} updated).`);
      } else setNotice("The Purview push is still running in the worker.");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Could not push lineage to Purview.");
    } finally {
      setPurviewBusy(false);
    }
  }

  async function handleSave(entry: OnboardingObjectEntry, isNew: boolean) {
    if (!projectId) return;
    try {
      if (isNew) {
        await api.onboarding.create(projectId, entry);
      } else {
        await api.onboarding.update(projectId, entry.object_key, entry);
      }
      setEditing(null);
      setActionError(null);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Save failed.");
    }
  }

  async function handleDelete(row: OnboardingObjectEntry) {
    const verb = canHardDelete ? "Permanently delete" : "Disable";
    if (!window.confirm(`${verb} "${row.object_key}"?`)) return;
    try {
      await api.onboarding.remove(projectId, row.object_key, canHardDelete);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Could not remove this object.");
    }
  }

  async function handleDuplicate(row: OnboardingObjectEntry) {
    let candidate = `${row.object_key}_copy`;
    let n = 2;
    while (rows.some((r) => r.object_key === candidate)) {
      candidate = `${row.object_key}_copy${n}`;
      n += 1;
    }
    try {
      await api.onboarding.create(projectId, { ...row, object_key: candidate, status: "draft" });
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Could not duplicate this object.");
    }
  }

  if (!projectId) {
    return <EmptyState title="No project selected" message="Create a project first — the onboarding registry is project-scoped." />;
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-xs font-semibold text-ink-2">
          Project
          <Select className="w-auto" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </label>
        <Button
          variant="outline"
          size="sm"
          onClick={() => downloadTextFile("onboarding_objects_template.csv", buildOnboardingObjectsTemplate())}
        >
          <Download className="h-3.5 w-3.5" />
          Template CSV
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!rows.length}
          title={rows.length ? undefined : "Add at least one object to generate a control table"}
          onClick={() => downloadTextFile("adf_control_table.sql", buildOnboardingControlTableSql(rows))}
        >
          <Download className="h-3.5 w-3.5" />
          Control-table SQL
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!rows.length}
          title="Snapshot of this project's registry in the template's column order — re-uploads cleanly"
          onClick={() => downloadTextFile("objects.csv", buildOnboardingObjectsCsv(rows))}
        >
          <Download className="h-3.5 w-3.5" />
          objects.csv
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!rows.length}
          title="Source → Bronze → Silver/Gold lineage across this project's registries (OpenLineage naming)"
          onClick={downloadLineage}
        >
          <Download className="h-3.5 w-3.5" />
          Registry lineage
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!rows.length || !canEdit}
          title="Write control-table SQL + metadata/objects.csv to a feature branch and open a PR (§26.5)"
          onClick={() => setSyncOpen(true)}
        >
          <GitPullRequest className="h-3.5 w-3.5" />
          Sync to Git
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!projectId}
          title="Compare the registry with metadata/objects.csv in Git; import new rows or adopt Git's edits (§26.6)"
          onClick={() => setDriftOpen(true)}
        >
          <GitCompare className="h-3.5 w-3.5" />
          Import / drift
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!rows.length || !canEdit || purviewBusy}
          title="Push registry lineage to Microsoft Purview (enable it in Project Settings → Integrations)"
          onClick={pushLineageToPurview}
        >
          <Network className="h-3.5 w-3.5" />
          {purviewBusy ? "Pushing…" : "Push to Purview"}
        </Button>
        <Button disabled={!canEdit} size="sm" onClick={() => setEditing({ entry: newOnboardingObject(), isNew: true })}>
          <Plus className="h-3.5 w-3.5" />
          Add object
        </Button>
      </div>

      {loadError ? <p className="upload-warning mb-3">{loadError}</p> : null}
      {actionError ? <p className="upload-warning mb-3">{actionError}</p> : null}
      {notice ? <p className="mb-3 text-xs font-semibold text-emerald-700">{notice}</p> : null}
      <SyncToGitDialog
        open={syncOpen}
        onClose={() => setSyncOpen(false)}
        projectId={projectId}
        rows={rows}
      />
      <GitDriftDialog
        open={driftOpen}
        onClose={() => setDriftOpen(false)}
        projectId={projectId}
        canEdit={canEdit}
        onAdopted={(count) => {
          setNotice(`Adopted ${count} row(s) from Git.`);
          reload();
        }}
      />

      {editing ? (
        <ObjectDrawer
          initial={editing.entry}
          otherObjectKeys={rows.filter((r) => r.object_key !== editing.entry.object_key).map((r) => r.object_key)}
          onCancel={() => setEditing(null)}
          onSave={(entry) => handleSave(entry, editing.isNew)}
        />
      ) : null}

      {dqObjectKey ? (
        <DqRulesDialog projectId={projectId} objectKey={dqObjectKey} canEdit={canEdit} onClose={() => setDqObjectKey(null)} />
      ) : null}

      <div className="seg mb-4">
        <button className={cn("seg-btn", mode === "upload" && "active")} onClick={() => setMode("upload")}>
          <Upload className="h-3.5 w-3.5" />
          CSV / Excel import
        </button>
        <button className={cn("seg-btn", mode === "manual" && "active")} onClick={() => setMode("manual")}>
          <ListChecks className="h-3.5 w-3.5" />
          Manual grid
        </button>
      </div>

      {mode === "upload" ? (
        <section className="panel p-[22px]">
          <div className="wizard-steps-simple">
            {IMPORT_STEPS.map((step, index) => (
              <span
                key={step}
                className={cn(
                  "wizard-step-item",
                  (index === 0 || (index === 1 && pending.length > 0)) && !commitSummary && "active",
                )}
              >
                <span className="wizard-step-dot">{index + 1}</span>
                <span className="wizard-step-label">{step}</span>
              </span>
            ))}
          </div>

          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => downloadTextFile("onboarding_objects_template.csv", buildOnboardingObjectsTemplate())}
            >
              <Download className="h-3.5 w-3.5" />
              Download template
            </Button>
            <span className="text-xs text-muted">
              One header row + one row per object. Re-uploading only adds/updates by object_key — it never
              removes rows already in your registry.
            </span>
          </div>

          <div
            className={cn("upload-zone", dragging && "dragging")}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const file = e.dataTransfer.files?.[0];
              if (file && canEdit) handleFile(file);
            }}
            onClick={() => canEdit && fileInputRef.current?.click()}
            role="button"
            tabIndex={0}
          >
            <Upload className="h-5 w-5" />
            <strong>Drop a CSV, TSV, JSON or Excel (.xlsx) file</strong>
            <span>A header row is required — download the template above if you're starting fresh.</span>
            <Button variant="outline" className="mt-2" disabled={!canEdit}>
              Choose file
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.tsv,.txt,.json,.xlsx,text/csv,text/plain,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleFile(file);
                e.target.value = "";
              }}
              hidden
            />
          </div>

          <label className="mt-3 block">
            <span className="text-xs font-semibold text-ink-2">…or paste the file contents</span>
            <Textarea
              className="mt-1"
              rows={4}
              value={rawText}
              placeholder="source_system,source_object,target_type,target_file_format,loading_behavior&#10;erp,customer,table,Parquet,FullLoad"
              onChange={(e) => {
                const value = e.target.value;
                setRawText(value);
                if (value.trim()) ingest(value, fileName || "pasted-onboarding.csv");
                else {
                  setPending([]);
                  setWarnings([]);
                  setSource(null);
                  setUnmappedHeaders([]);
                }
              }}
            />
          </label>

          {commitSummary ? <p className="upload-note">{commitSummary}</p> : null}
          {fileName && !commitSummary ? (
            <p className="upload-note">
              Parsed <strong>{fileName}</strong> — {pending.length} object{pending.length === 1 ? "" : "s"} found.
            </p>
          ) : null}
          {warnings.map((warning, i) => (
            <small key={i} className="upload-warning">
              {warning}
            </small>
          ))}

          {source && unmappedHeaders.length > 0 ? (
            <div className="upload-preview">
              <div className="upload-preview-head">
                <strong>Map columns</strong>
                <span className="text-xs text-muted">
                  {unmappedHeaders.length} header{unmappedHeaders.length === 1 ? "" : "s"} not recognized — map each to a
                  registry column, or leave it ignored.
                </span>
              </div>
              {projectId ? (
                <div className="flex flex-wrap items-center gap-2 px-3 pt-3">
                  {profiles.length ? (
                    <Select aria-label="Apply mapping profile" value="" disabled={!canEdit} onChange={(e) => applyProfile(e.target.value)}>
                      <option value="">Apply saved profile…</option>
                      {profiles.map((p) => (
                        <option key={p.name} value={p.name}>
                          {p.name}
                        </option>
                      ))}
                    </Select>
                  ) : null}
                  <Input
                    aria-label="Mapping profile name"
                    className="max-w-[14rem]"
                    value={profileName}
                    placeholder="Profile name, e.g. SAP export"
                    disabled={!canEdit}
                    onChange={(e) => setProfileName(e.target.value)}
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!canEdit || !profileName.trim() || !Object.keys(headerMap).length}
                    onClick={() => void saveProfile()}
                  >
                    Save mapping as profile
                  </Button>
                  {profiles.map((p) => (
                    <Button
                      key={p.id}
                      size="sm"
                      variant="ghost"
                      aria-label={`Delete profile ${p.name}`}
                      disabled={!canEdit}
                      onClick={() => void removeProfile(p.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      {p.name}
                    </Button>
                  ))}
                </div>
              ) : null}
              <div className="grid gap-2 p-3" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)" }}>
                {unmappedHeaders.map((header) => (
                  <label key={header} className="contents">
                    <span className="self-center text-xs font-semibold text-ink-2">{header}</span>
                    <Select
                      aria-label={`Map ${header}`}
                      value={headerMap[header] ?? ""}
                      disabled={!canEdit}
                      onChange={(e) => remapHeader(header, e.target.value)}
                    >
                      <option value="">— Ignore —</option>
                      {ONBOARDING_COLUMNS.map((c) => (
                        <option key={c.key} value={c.key}>
                          {c.key}
                          {c.required ? " (required)" : ""}
                        </option>
                      ))}
                    </Select>
                  </label>
                ))}
              </div>
            </div>
          ) : null}

          {pending.length > 0 ? (
            <div className="upload-preview">
              <div className="upload-preview-head">
                <strong>Preview</strong>
                <span className="text-xs text-muted">{pending.length} rows</span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>{GRID_COLUMNS.slice(0, -1).map((c) => <th key={c}>{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {pending.slice(0, 12).map((row, i) => {
                      const rowIssues = validateOnboardingRow(row);
                      return (
                        <tr key={i}>
                          <td>{row.object_key}</td>
                          <td>{row.source_system}.{row.source_object}</td>
                          <td>{targetLabel(row)}</td>
                          <td>{row.target_file_format}</td>
                          <td>{row.loading_behavior}{row.watermark_column ? ` · ${row.watermark_column}` : ""}</td>
                          <td>
                            {rowIssues.length ? (
                              <StatusPill tone="amber">{rowIssues.length} issue{rowIssues.length === 1 ? "" : "s"}</StatusPill>
                            ) : (
                              <StatusPill tone="green">Valid</StatusPill>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {pending.length > 12 ? (
                  <p className="upload-note">…and {pending.length - 12} more.</p>
                ) : null}
              </div>
              <Button size="sm" onClick={commitPending} disabled={!canEdit}>
                <ListChecks className="h-3.5 w-3.5" />
                Add {pending.length} object{pending.length === 1 ? "" : "s"} to registry
              </Button>
            </div>
          ) : null}

          {importHistory.data.length ? (
            <div className="mt-4">
              <span className="text-xs font-semibold text-ink-2">Import history</span>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>File</th>
                      <th>Rows</th>
                      <th>Added</th>
                      <th>Updated</th>
                      <th>Profile</th>
                      <th>By</th>
                      <th>When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {importHistory.data.slice(0, 10).map((imp) => (
                      <tr key={imp.id}>
                        <td title={`sha256 ${imp.fileHash}`}>{imp.fileName}</td>
                        <td>{imp.rowTotal}</td>
                        <td>{imp.added}</td>
                        <td>{imp.updated}</td>
                        <td>{imp.mappingProfileName ?? (imp.mappingProfileId ? "(deleted)" : "—")}</td>
                        <td>{imp.committedByName ?? imp.committedBy ?? "—"}</td>
                        <td>{imp.committedAt ? new Date(imp.committedAt).toLocaleString() : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </section>
      ) : (
        <section className="panel">
          <div className="panel-toolbar">
            <span className="text-xs font-semibold text-ink-2">Object registry</span>
            <span className="text-xs text-muted">
              {loading ? "Loading…" : `${rows.length} object${rows.length === 1 ? "" : "s"}`}
            </span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>{GRID_COLUMNS.map((column) => <th key={column}>{column}</th>)}</tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={GRID_COLUMNS.length} className="p-0">
                      <EmptyState
                        status={loadError ? "error" : loading ? "loading" : "empty"}
                        errorMessage={loadError ?? undefined}
                        title="No objects onboarded"
                        message="Add rows by hand, or switch to CSV / Excel import to bring in many objects at once. Each row maps one source object to one target with its file format and load type."
                        action={
                          <Button disabled={!canEdit} onClick={() => setEditing({ entry: newOnboardingObject(), isNew: true })}>
                            <Plus className="h-3.5 w-3.5" />
                            Add object
                          </Button>
                        }
                      />
                    </td>
                  </tr>
                ) : (
                  rows.map((row) => {
                    const rowIssues = validateOnboardingRow(row);
                    return (
                      <tr key={row.object_key}>
                        <td>{row.object_key}</td>
                        <td>{row.source_system}.{row.source_object}</td>
                        <td>{targetLabel(row)}</td>
                        <td>{row.target_file_format}</td>
                        <td>
                          {row.loading_behavior}
                          {row.watermark_column ? ` · ${row.watermark_column}` : ""}
                        </td>
                        <td>
                          {!row.enabled ? (
                            <StatusPill tone="gray">Disabled</StatusPill>
                          ) : rowIssues.length ? (
                            <StatusPill tone="amber">
                              {rowIssues.length} issue{rowIssues.length === 1 ? "" : "s"}
                            </StatusPill>
                          ) : (
                            <StatusPill tone="green">Valid</StatusPill>
                          )}
                        </td>
                        <td>
                          {canEdit ? (
                            <div className="flex items-center gap-1">
                              <button
                                type="button"
                                className="editor-remove"
                                title="Edit"
                                onClick={() => setEditing({ entry: row, isNew: false })}
                              >
                                <Pencil className="h-3.5 w-3.5" />
                              </button>
                              <button
                                type="button"
                                className="editor-remove"
                                title="Build pipeline from this object"
                                onClick={() =>
                                  onNavigate("pipelines.create", { onboardingPrefill: { projectId, objectKeys: [row.object_key] } })
                                }
                              >
                                <Hammer className="h-3.5 w-3.5" />
                              </button>
                              <button
                                type="button"
                                className="editor-remove"
                                title="Data quality rules"
                                onClick={() => setDqObjectKey(row.object_key)}
                              >
                                <ShieldCheck className="h-3.5 w-3.5" />
                              </button>
                              <button
                                type="button"
                                className="editor-remove"
                                title="Duplicate"
                                onClick={() => handleDuplicate(row)}
                              >
                                <Copy className="h-3.5 w-3.5" />
                              </button>
                              <button
                                type="button"
                                className="editor-remove"
                                title={canHardDelete ? "Delete" : "Disable"}
                                onClick={() => handleDelete(row)}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}

export function OnboardingView({ data, user, onNavigate }: FeatureProps) {
  const [layerTab, setLayerTab] = useState<"bronze" | "silver" | "gold">("bronze");
  const canEdit = user.role !== "VIEWER";
  const canHardDelete = user.role === "PLATFORM_ADMIN" || user.role === "TEAM_ADMIN";

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="SOURCE OBJECT INTAKE"
        title="Onboarding"
        detail="Register source objects (Bronze), and curated/mart entities with their transformations and data quality rules (Silver/Gold via STTM). Each registry feeds the Build wizard's dropdown and, on publish, the matching control table."
      />

      <div className="seg mb-4">
        <button className={cn("seg-btn", layerTab === "bronze" && "active")} onClick={() => setLayerTab("bronze")}>
          Bronze
        </button>
        <button className={cn("seg-btn", layerTab === "silver" && "active")} onClick={() => setLayerTab("silver")}>
          Silver
        </button>
        <button className={cn("seg-btn", layerTab === "gold" && "active")} onClick={() => setLayerTab("gold")}>
          Gold
        </button>
      </div>

      {layerTab === "bronze" ? <BronzeTab projects={data.projects.data} canEdit={canEdit} canHardDelete={canHardDelete} onNavigate={onNavigate} /> : null}
      {layerTab === "silver" ? <RegistryTab layer="silver" projects={data.projects.data} user={user} /> : null}
      {layerTab === "gold" ? <RegistryTab layer="mart" projects={data.projects.data} user={user} /> : null}

      {layerTab === "bronze" ? (
        <>
          <div className="section-heading" style={{ marginTop: 28 }}>
            <div>
              <span className="eyebrow">OUTPUTS</span>
              <h2>What the registry produces</h2>
              <p>Nothing is automatic — you choose the output once the registry is clean.</p>
            </div>
          </div>
          <div className="template-grid">
            {[
              { title: "Generate ingestion pipeline", body: "Open Build with the pattern preselected and every per-object setting prefilled from the selected rows." },
              { title: "Generate control-table SQL", body: "DDL plus one seed row per enabled object, the connection control table, and an objects.csv snapshot — pushed as a PR." },
              { title: "Sync to Git", body: "Write or update the control-table SQL and metadata/objects.csv on a feature branch, with a per-row diff against the last synced commit." },
            ].map((card) => (
              <article key={card.title} className="panel template-card">
                <span className="template-icon"><ListChecks className="h-4 w-4" /></span>
                <h3>{card.title}</h3>
                <p>{card.body}</p>
              </article>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
