"use client";

/**
 * §33 STTM-Based Silver/Gold Object Registry — one Silver or one Gold tab
 * (rendered twice from `onboarding-view.tsx`, `layer="silver"`/`layer="mart"`
 * — identical shape, same convention as the backend's own route-registration
 * function, §33.9). Covers §33.12's DoD items 1-4: template download,
 * upload→preview→commit, manual create/edit, and the filterable catalog
 * view. Deliberately one file instead of the plan's original 3-file split
 * (`sttm-upload.tsx`/`registry-object-drawer.tsx`/`registry-catalog-grid.tsx`)
 * — `registry-object-drawer.tsx` is still its own file (reused as a real
 * dialog), but the upload flow and the catalog grid share enough state
 * (the object list, the "which project" picker, RBAC) that splitting them
 * only added prop-drilling with no reuse benefit.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Ban, Download, ListChecks, Pencil, Plus, Trash2, Upload } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { EmptyState } from "@/components/common/empty-state";
import { StatusPill } from "@/components/common/status-pill";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import { downloadBinaryFile } from "@/lib/download";
import { api, ApiError } from "@/lib/api/client";
import { buildSttmTemplate } from "@/lib/adf/sttm";
import { readDataModularWorkbook, type DataModularPreview } from "@/lib/adf/sttm-data-modular";
import { useApiStatus, useProjectSettings } from "@/lib/api/hooks";
import { EMPTY_PROJECT_SETTINGS } from "@/lib/api/empty-values";
import { RegistryObjectDrawer } from "@/features/onboarding/registry-object-drawer";
import type { Project, RegistryObject, RegistryObjectInput, SttmImportRecord, SttmLayer, User } from "@/types";

const GRID_COLUMNS = ["Object", "Tag", "Target table", "Sources", "Status", ""] as const;
// The Data Modeling Accelerator's STTM export template (AGENTS.md §33.14), shipped as a static
// asset byte-identical to data_moduler/docs/templates/ (backend tests/test_sttm_data_modular.py).
const DATA_MODULAR_TEMPLATE_URL = "/templates/STTM_Data_Modular_Template_v2b.xlsx";

interface RegistryTabProps {
  layer: SttmLayer;
  projects: Project[];
  user: User;
}

export function RegistryTab({ layer, projects, user }: RegistryTabProps) {
  const canEdit = user.role !== "VIEWER";
  const canHardDelete = user.role === "PLATFORM_ADMIN" || user.role === "TEAM_ADMIN";
  const label = layer === "mart" ? "Gold" : "Silver";

  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  useEffect(() => {
    if (!projectId && projects[0]) setProjectId(projects[0].id);
  }, [projectId, projects]);

  // Standards-configured custom zone prefix per layer (Administration > Standards) — only
  // affects the drawer's live "Target table" preview; the backend recomputes target_table
  // itself, authoritatively, on save (see services.py's `_set_registry_fields`).
  const settingsResource = useProjectSettings(projectId, EMPTY_PROJECT_SETTINGS);
  const layerPrefixes = settingsResource.data.namingConvention?.layerPrefixes;

  const [mode, setMode] = useState<"upload" | "manual">("manual");
  const [rows, setRows] = useState<RegistryObject[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tagFilter, setTagFilter] = useState("");
  const [domainFilter, setDomainFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<RegistryObject | null | "new">(null);

  const { setError: setApiError } = useApiStatus();
  const reload = useCallback(() => {
    if (!projectId) return;
    setLoading(true);
    api.registry
      .list(layer, projectId, { tag: tagFilter || undefined, domain: domainFilter || undefined, status: statusFilter || undefined, q: q || undefined })
      .then((data) => {
        setRows(data);
        setLoadError(null);
      })
      .catch((err) => {
        // Never leave stale rows under an error — and lift it to the shell's ApiErrorBanner too.
        const message = err instanceof ApiError ? err.message : "Unable to load the registry.";
        setRows([]);
        setLoadError(message);
        setApiError(message);
      })
      .finally(() => setLoading(false));
  }, [layer, projectId, tagFilter, domainFilter, statusFilter, q, setApiError]);

  useEffect(() => {
    reload();
  }, [reload]);

  // --- Upload flow -------------------------------------------------------
  const [imp, setImp] = useState<SttmImportRecord | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [selectedSheets, setSelectedSheets] = useState<Set<string>>(new Set());
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);
  const [commitSummary, setCommitSummary] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  // gaps.md DMT-11: what a Data Modular .xlsx holds, read in the browser before the server preview.
  const [localPreview, setLocalPreview] = useState<DataModularPreview | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function handleUpload(file: File) {
    if (!projectId) return;
    setUploading(true);
    setImportError(null);
    setCommitSummary(null);
    setLocalPreview(null);
    if (file.name.toLowerCase().endsWith(".xlsx")) {
      // Best effort: a legacy-layout or unreadable workbook simply shows no browser preview.
      readDataModularWorkbook(await file.arrayBuffer()).then(setLocalPreview, () => setLocalPreview(null));
    }
    try {
      const record = await api.registry.sttmImport(layer, projectId, file);
      setImp(record);
      setSelectedSheets(new Set(record.sheets.filter((s) => s.valid).map((s) => s.sheetName)));
    } catch (err) {
      setImportError(err instanceof ApiError ? err.message : "Unable to parse this workbook.");
      setImp(null);
    } finally {
      setUploading(false);
    }
  }

  async function commitImport() {
    if (!imp || !projectId) return;
    try {
      // §33.9/§33.11: echo back the version each selected sheet's object_key
      // was seen at, at preview/upload time — lets the commit endpoint 409 a
      // genuinely concurrent edit instead of silently overwriting it (STTM-13).
      const expectedVersions: Record<string, number> = {};
      for (const sheet of imp.sheets) {
        if (selectedSheets.has(sheet.sheetName) && sheet.objectKey && sheet.existingVersion != null) {
          expectedVersions[sheet.objectKey] = sheet.existingVersion;
        }
      }
      const result = await api.registry.sttmImportCommit(layer, projectId, imp.id, {
        sheetNames: Array.from(selectedSheets),
        confirmOverwrite,
        expectedVersions,
      });
      setCommitSummary(
        `Committed ${result.committed.length} object${result.committed.length === 1 ? "" : "s"}` +
          (result.skipped.length ? `; ${result.skipped.length} skipped — ${result.skipped.map((s) => `${s.sheetName}: ${s.reason}`).join(" ")}` : "."),
      );
      setImp(null);
      setMode("manual");
      reload();
    } catch (err) {
      setImportError(err instanceof ApiError ? err.message : "Commit failed.");
    }
  }

  async function handleSave(input: RegistryObjectInput): Promise<string | null> {
    try {
      if (editing && editing !== "new") {
        await api.registry.update(layer, projectId, editing.objectKey, { ...input, expectedVersion: editing.version });
      } else {
        await api.registry.create(layer, projectId, input);
      }
      setEditing(null);
      reload();
      return null;
    } catch (err) {
      return err instanceof ApiError ? err.message : "Save failed.";
    }
  }

  async function handleDisable(row: RegistryObject) {
    try {
      await api.registry.remove(layer, projectId, row.objectKey, false);
      reload();
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : "Could not disable this object.");
    }
  }

  async function handleHardDelete(row: RegistryObject) {
    if (!window.confirm(`Permanently delete "${row.objectKey}"? This cannot be undone.`)) return;
    try {
      await api.registry.remove(layer, projectId, row.objectKey, true);
      reload();
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : "Could not delete this object.");
    }
  }

  if (!projectId) {
    return <EmptyState title="No project selected" message="Create a project first — the Silver/Gold registry is project-scoped." />;
  }

  return (
    <div>
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
        <a className={buttonVariants({ variant: "outline", size: "sm" })} href={DATA_MODULAR_TEMPLATE_URL} download>
          <Download className="h-3.5 w-3.5" />
          STTM template (.xlsx)
        </a>
        <Button
          variant="ghost"
          size="sm"
          title="The older one-sheet-per-object layout; still accepted on upload."
          onClick={async () => {
            const buffer = await buildSttmTemplate(layer);
            downloadBinaryFile(
              `STTM_${label}_Template.xlsx`,
              buffer,
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            );
          }}
        >
          Legacy {label.toLowerCase()} template
        </Button>
        <Button disabled={!canEdit} size="sm" onClick={() => setEditing("new")}>
          <Plus className="h-3.5 w-3.5" />
          Add {label.toLowerCase()} object
        </Button>
      </div>

      {editing ? (
        <RegistryObjectDrawer
          layer={layer}
          initial={editing === "new" ? null : editing}
          layerPrefixes={layerPrefixes}
          onCancel={() => setEditing(null)}
          onSave={handleSave}
        />
      ) : null}

      {/* Shown above both modes: a commit switches back to the catalog, and skipped sheets (E2E-10) must stay visible. */}
      {commitSummary ? <p className="upload-note mb-3">{commitSummary}</p> : null}

      <div className="seg mb-4">
        <button className={cn("seg-btn", mode === "manual" && "active")} onClick={() => setMode("manual")}>
          <ListChecks className="h-3.5 w-3.5" />
          Catalog
        </button>
        <button className={cn("seg-btn", mode === "upload" && "active")} onClick={() => setMode("upload")}>
          <Upload className="h-3.5 w-3.5" />
          STTM upload
        </button>
      </div>

      {mode === "upload" ? (
        <section className="panel p-[22px]">
          <p className="mb-3 text-xs text-muted">
            Upload the Data Modeling Accelerator&apos;s STTM export as is: one workbook per target table, or its
            layer-wise .zip of them. Set Target_Layer to {layer === "mart" ? "GOLD" : "SILVER"} and fill
            Target_Subject_Area (or Target_Domain) for the tag{layer === "mart" ? ", plus Target_Domain for the mart" : ""}.
            Workbooks in the legacy one-sheet-per-object layout are still accepted. Re-uploading is additive — it never
            removes objects already in the registry.
          </p>
          <div
            className="upload-zone"
            onClick={() => canEdit && fileInputRef.current?.click()}
            role="button"
            tabIndex={0}
          >
            <Upload className="h-5 w-5" />
            <strong>Choose an .xlsx workbook or .zip bundle</strong>
            <span>{uploading ? "Parsing…" : "Server-side re-parse + validation happens immediately."}</span>
            <Button variant="outline" className="mt-2" disabled={!canEdit || uploading}>
              Choose file
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.zip,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/zip"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleUpload(file);
                e.target.value = "";
              }}
              hidden
            />
          </div>

          {importError ? <p className="upload-warning">{importError}</p> : null}

          {localPreview ? (
            <details className="upload-preview" open={!imp}>
              <summary className="upload-preview-head">
                <strong>Workbook contents — {localPreview.tableName || "unnamed table"}</strong>
                <span className="text-xs text-muted">
                  read in your browser · {localPreview.layer || "no layer"} · {localPreview.attributes.length} Section 1 values ·{" "}
                  {localPreview.rows.length} mapping rows
                </span>
              </summary>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Row</th>
                      <th>Type</th>
                      <th>Target column</th>
                      <th>Source</th>
                      <th>Transformation</th>
                      <th>DQ rules</th>
                    </tr>
                  </thead>
                  <tbody>
                    {localPreview.rows.map((r) => (
                      <tr key={r.row}>
                        <td>{r.row}</td>
                        <td>{r.recordType}</td>
                        <td className="mono">{r.targetColumn}</td>
                        <td className="mono">{[r.sourceObject, r.sourceColumn].filter(Boolean).join(".")}</td>
                        <td>{r.transformationType}</td>
                        <td>{r.dqRuleTypes}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          ) : null}

          {imp ? (
            <div className="upload-preview">
              <div className="upload-preview-head">
                <strong>Preview — {imp.fileName}</strong>
                <span className="text-xs text-muted">
                  {imp.sheetValid}/{imp.sheetTotal} sheets valid
                </span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th></th>
                      <th>Sheet</th>
                      <th>Curated entity</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {imp.sheets.map((sheet) => (
                      <tr key={sheet.sheetName}>
                        <td>
                          <Checkbox
                            checked={selectedSheets.has(sheet.sheetName)}
                            disabled={!sheet.valid}
                            onChange={(checked) =>
                              setSelectedSheets((prev) => {
                                const next = new Set(prev);
                                if (checked) next.add(sheet.sheetName);
                                else next.delete(sheet.sheetName);
                                return next;
                              })
                            }
                          />
                        </td>
                        <td>{sheet.sheetName}</td>
                        <td>{sheet.curatedEntity ?? "—"}</td>
                        <td>
                          {sheet.valid ? (
                            sheet.issues.length > 0 ? (
                              <span title={sheet.issues.map((i) => i.message).join("; ")} className="flex items-center gap-1">
                                <StatusPill tone="green">Valid</StatusPill>
                                <StatusPill tone="amber">
                                  {sheet.issues.length} warning{sheet.issues.length === 1 ? "" : "s"}
                                </StatusPill>
                              </span>
                            ) : (
                              <StatusPill tone="green">Valid</StatusPill>
                            )
                          ) : (
                            <span title={sheet.issues.map((i) => i.message).join("; ")}>
                              <StatusPill tone="amber">
                                {sheet.issues.length} issue{sheet.issues.length === 1 ? "" : "s"}
                              </StatusPill>
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-2 flex items-center gap-3">
                <Checkbox
                  checked={confirmOverwrite}
                  onChange={setConfirmOverwrite}
                  label="Overwrite manually-created objects with the same object_key"
                />
              </div>
              <Button size="sm" className="mt-2" onClick={commitImport} disabled={selectedSheets.size === 0}>
                <ListChecks className="h-3.5 w-3.5" />
                Commit {selectedSheets.size} object{selectedSheets.size === 1 ? "" : "s"}
              </Button>
            </div>
          ) : null}
        </section>
      ) : (
        <section className="panel">
          <div className="panel-toolbar flex flex-wrap items-center gap-2">
            <Input className="w-auto" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
            <Input className="w-auto" placeholder="Tag" value={tagFilter} onChange={(e) => setTagFilter(e.target.value)} />
            <Input className="w-auto" placeholder="Domain" value={domainFilter} onChange={(e) => setDomainFilter(e.target.value)} />
            <Select className="w-auto" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">Any status</option>
              <option value="draft">draft</option>
              <option value="valid">valid</option>
              <option value="invalid">invalid</option>
            </Select>
            <span className="ml-auto text-xs text-muted">
              {loading ? "Loading…" : `${rows.length} object${rows.length === 1 ? "" : "s"}`}
            </span>
          </div>
          {loadError ? <p className="upload-warning px-4">{loadError}</p> : null}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>{GRID_COLUMNS.map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={GRID_COLUMNS.length} className="p-0">
                      <EmptyState
                        status={loadError ? "error" : loading ? "loading" : "empty"}
                        errorMessage={loadError ?? undefined}
                        title={`No ${label.toLowerCase()} objects onboarded`}
                        message="Add one by hand, or switch to STTM upload to bring in many at once."
                        action={
                          <Button disabled={!canEdit} onClick={() => setEditing("new")}>
                            <Plus className="h-3.5 w-3.5" />
                            Add {label.toLowerCase()} object
                          </Button>
                        }
                      />
                    </td>
                  </tr>
                ) : (
                  rows.map((row) => (
                    <tr key={row.objectKey}>
                      <td>{row.objectKey}</td>
                      <td>{row.tag}</td>
                      <td>{row.targetTable}</td>
                      <td>{row.sourceObjectKeys.join(", ") || "—"}</td>
                      <td>
                        {!row.enabled ? (
                          <StatusPill tone="gray">Disabled</StatusPill>
                        ) : row.status === "invalid" ? (
                          <StatusPill tone="amber">Invalid</StatusPill>
                        ) : (
                          <StatusPill tone="green">Valid</StatusPill>
                        )}
                        {row.provenanceSource === "upload" ? <small className="ml-1 text-muted">via upload</small> : null}
                      </td>
                      <td>
                        {canEdit ? (
                          <div className="flex items-center gap-1">
                            <button type="button" className="editor-remove" title="Edit" onClick={() => setEditing(row)}>
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            {row.enabled ? (
                              <button type="button" className="editor-remove" title="Disable" onClick={() => handleDisable(row)}>
                                <Ban className="h-3.5 w-3.5" />
                              </button>
                            ) : null}
                            {canHardDelete ? (
                              <button type="button" className="editor-remove" title="Delete permanently" onClick={() => handleHardDelete(row)}>
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            ) : null}
                          </div>
                        ) : null}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
