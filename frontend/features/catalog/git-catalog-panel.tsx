"use client";

/**
 * §24.8 Git-backed catalog: every artifact in the project's repo on any indexed branch, searchable
 * by name *and* reference, with "used by" back-references. §24.9 round-trip: a tool-owned file opens
 * in Build (its saved draft regenerates it); a foreign or hand-edited file is edited in raw-JSON mode
 * and pushed as an update PR. §24.10 rename/delete go through `CatalogChangeDialog`.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { FileJson, GitBranch, Hammer, Loader2, Pencil, RefreshCw, Search, Trash2, Type } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Alert } from "@/components/ui/alert";
import { EmptyState } from "@/components/common/empty-state";
import { api, ApiError } from "@/lib/api/client";
import { refreshCatalog } from "@/lib/api/catalog-refresh";
import { can } from "@/lib/state";
import { cn } from "@/lib/utils";
import type { FeatureProps } from "@/features/types";
import type { CatalogArtifactDetail, CatalogArtifactType, CatalogChange, CatalogChangeMode, CatalogListing } from "@/types";
import { CatalogChangeDialog } from "@/features/catalog/catalog-change-dialog";

const TYPE_LABELS: Record<CatalogArtifactType, string> = {
  pipeline: "Pipelines",
  dataset: "Datasets",
  linked_service: "Linked services",
  trigger: "Triggers",
  factory: "Factory",
  credential: "Credentials",
  data_flow: "Data flows",
  integration_runtime: "Integration runtimes",
  managed_virtual_network: "Managed VNets",
  metadata: "Metadata / SQL",
};
const EDITABLE: CatalogArtifactType[] = ["pipeline", "dataset", "linked_service", "trigger", "credential", "data_flow", "integration_runtime"];

const OWNERSHIP_BADGE = {
  in_sync: { variant: "green", label: "Tool-owned" },
  drifted: { variant: "amber", label: "Drifted" },
  foreign: { variant: "gray", label: "Foreign" },
} as const;

type Props = Pick<FeatureProps, "data" | "user" | "onNavigate">;

export function GitCatalogPanel({ data, user, onNavigate }: Props) {
  const projects = data.projects.data;
  const canPush = can(user.role, "pipeline:push");
  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  const [branch, setBranch] = useState("");
  const [listing, setListing] = useState<CatalogListing | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState<{ variant: "success" | "error"; title: string; description?: string } | null>(null);
  const [filter, setFilter] = useState<"all" | CatalogArtifactType>("all");
  const [query, setQuery] = useState("");
  const [activePath, setActivePath] = useState<string | null>(null);
  const [detail, setDetail] = useState<CatalogArtifactDetail | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [dialog, setDialog] = useState<CatalogChangeMode | null>(null);
  const [changes, setChanges] = useState<CatalogChange[]>([]);

  useEffect(() => {
    if (!projectId && projects[0]) setProjectId(projects[0].id);
  }, [projectId, projects]);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoadError(null);
    try {
      const [next, recent] = await Promise.all([
        api.catalog.list(projectId, { branch: branch.trim() || undefined }),
        api.catalog.changes(projectId).catch(() => []),
      ]);
      setListing(next);
      setChanges(recent);
    } catch (err) {
      setListing(null);
      setLoadError(err instanceof ApiError ? err.message : "The catalog could not be loaded.");
    }
  }, [projectId, branch]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setEditing(false);
    if (!activePath || !projectId) {
      setDetail(null);
      return;
    }
    api.catalog
      .artifact(projectId, activePath, listing?.branch)
      .then((d) => {
        setDetail(d);
        setDraft(d.content);
      })
      .catch(() => setDetail(null));
  }, [activePath, projectId, listing?.branch]);

  async function refresh() {
    setRefreshing(true);
    setNotice(null);
    try {
      const job = await refreshCatalog(projectId, branch.trim() || undefined);
      if (job.status === "failed") {
        setNotice({ variant: "error", title: "Refresh failed", description: job.error ?? undefined });
      } else if (job.status === "succeeded") {
        const count = Number((job.result as { count?: number } | null)?.count ?? 0);
        const simulated = Boolean((job.result as { simulated?: boolean } | null)?.simulated);
        setNotice({
          variant: "success",
          title: `Indexed ${count} file(s)`,
          description: simulated ? "Simulated — no Git credential resolved; showing what this app pushed." : undefined,
        });
      } else {
        setNotice({ variant: "success", title: "Refresh queued", description: "The worker is still reading the repository." });
      }
      await load();
    } catch (err) {
      setNotice({ variant: "error", title: err instanceof ApiError ? err.message : "Refresh failed" });
    } finally {
      setRefreshing(false);
    }
  }

  const entries = useMemo(() => listing?.entries ?? [], [listing]);
  const counts = useMemo(() => {
    const map: Record<string, number> = { all: entries.length };
    for (const e of entries) map[e.type] = (map[e.type] ?? 0) + 1;
    return map;
  }, [entries]);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries.filter(
      (e) =>
        (filter === "all" || e.type === filter) &&
        (!q || e.name.toLowerCase().includes(q) || e.path.toLowerCase().includes(q) || e.references.some((r) => r.name.toLowerCase().includes(q))),
    );
  }, [entries, filter, query]);

  const onBase = listing ? listing.branch === listing.baseBranch : false;
  const isJson = detail?.path.toLowerCase().endsWith(".json") ?? false;
  const jsonError = useMemo(() => {
    if (!editing || !isJson) return null;
    try {
      JSON.parse(draft);
      return null;
    } catch (err) {
      return err instanceof Error ? err.message : "Invalid JSON";
    }
  }, [draft, editing, isJson]);
  const editable = detail ? EDITABLE.includes(detail.type) : false;

  function settled(change: CatalogChange) {
    setChanges((prev) => [change, ...prev.filter((c) => c.id !== change.id)]);
    if (change.status === "pr_created") setEditing(false);
  }

  if (projects.length === 0) {
    return (
      <section className="panel">
        <EmptyState title="No projects yet" message="Create a project with Git defaults to browse its repository." />
      </section>
    );
  }

  return (
    <div className="space-y-3">
      <div className="panel flex flex-wrap items-end gap-3 p-4">
        <label className="grid gap-1 text-xs font-semibold text-ink-2">
          Project
          <Select className="w-auto" value={projectId} onChange={(e) => { setProjectId(e.target.value); setActivePath(null); }}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </label>
        <label className="grid gap-1 text-xs font-semibold text-ink-2">
          Branch
          <Input
            list="catalog-branches"
            className="w-48"
            placeholder={listing?.baseBranch ?? "base branch"}
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            onBlur={() => void load()}
          />
          <datalist id="catalog-branches">
            {(listing?.branches ?? []).map((b) => <option key={b} value={b} />)}
          </datalist>
        </label>
        <Button variant="outline" onClick={refresh} disabled={refreshing || !projectId}>
          {refreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          Refresh from Git
        </Button>
        <span className="ml-auto flex items-center gap-2 text-xs text-muted">
          <GitBranch className="h-3.5 w-3.5" />
          {listing?.indexedAt ? `${listing.branch} · indexed ${new Date(listing.indexedAt).toLocaleString()}` : "Not indexed yet"}
          {listing?.simulated ? <Badge variant="amber">simulated</Badge> : null}
        </span>
      </div>

      {notice ? <Alert variant={notice.variant} title={notice.title} description={notice.description} /> : null}
      {loadError ? <Alert variant="error" title={loadError} /> : null}

      {listing && entries.length === 0 ? (
        <section className="panel">
          <EmptyState
            title={listing.indexedAt ? "No artifacts on this branch" : "This branch hasn't been indexed"}
            message="Refresh from Git to read the repository under the project's artifact root."
          />
        </section>
      ) : null}

      {entries.length > 0 ? (
        <div className="catalog-layout">
          <aside className="filter-rail">
            <span className="eyebrow">Artifact type</span>
            {(["all", ...Object.keys(TYPE_LABELS)] as Array<"all" | CatalogArtifactType>)
              .filter((id) => id === "all" || counts[id])
              .map((id) => (
                <button key={id} className={cn("filter-item", filter === id && "active")} onClick={() => setFilter(id)}>
                  <span>{id === "all" ? "All artifacts" : TYPE_LABELS[id]}</span>
                  <span className="filter-count">{counts[id] ?? 0}</span>
                </button>
              ))}
          </aside>

          <section className="min-w-0">
            <div className="search-box mb-3">
              <Search className="h-4 w-4" />
              <input placeholder="Search by name or reference…" value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
            <div className="panel catalog-panel">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Name</th><th>Type</th><th>Path</th><th>Origin</th><th>Used by</th></tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 ? (
                      <tr><td colSpan={5} className="px-5 py-8 text-center text-xs text-muted">No artifacts match this filter or search.</td></tr>
                    ) : null}
                    {rows.map((e) => (
                      <tr key={e.path} className={cn("catalog-row", activePath === e.path && "active")} onClick={() => setActivePath(e.path)}>
                        <td><strong>{e.name}</strong></td>
                        <td className="muted">{TYPE_LABELS[e.type] ?? e.type}</td>
                        <td className="muted"><code>{e.path}</code></td>
                        <td><Badge variant={OWNERSHIP_BADGE[e.ownership.state].variant}>{OWNERSHIP_BADGE[e.ownership.state].label}</Badge></td>
                        <td className="muted">{e.usedByCount || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {detail ? (
              <div className="panel mt-3 p-0">
                <div className="artifact-detail-head" style={{ padding: "14px 18px 0" }}>
                  <div>
                    <strong className="text-sm text-ink">{detail.name}</strong>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      <Badge variant={OWNERSHIP_BADGE[detail.ownership.state].variant}>{OWNERSHIP_BADGE[detail.ownership.state].label}</Badge>
                      <Badge variant="blue">{TYPE_LABELS[detail.type] ?? detail.type}</Badge>
                      <Badge variant="forge"><code>{detail.blobSha.slice(0, 7)}</code></Badge>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {detail.ownership.pipelineId ? (
                      <Button size="sm" variant="outline" onClick={() => onNavigate("pipelines.create", { editPipelineId: detail.ownership.pipelineId! })}>
                        <Hammer className="h-3.5 w-3.5" />Open in Build
                      </Button>
                    ) : null}
                    {canPush && onBase ? (
                      <>
                        <Button size="sm" variant="outline" onClick={() => setEditing((v) => !v)}>
                          <Pencil className="h-3.5 w-3.5" />{editing ? "Cancel raw edit" : "Edit raw JSON"}
                        </Button>
                        {editable ? (
                          <>
                            <Button size="sm" variant="outline" onClick={() => setDialog("rename")}><Type className="h-3.5 w-3.5" />Rename</Button>
                            <Button size="sm" variant="outline" onClick={() => setDialog("delete")}><Trash2 className="h-3.5 w-3.5" />Delete</Button>
                          </>
                        ) : null}
                      </>
                    ) : null}
                  </div>
                </div>
                <div className="space-y-3 p-[18px]">
                  {detail.ownership.state === "drifted" ? (
                    <Alert
                      variant="warning"
                      title={`Hand-edited in Git since ${detail.ownership.pipelineName} generated it`}
                      description="Open in Build regenerates it from the saved draft and would replace these edits on the next push. To keep them, edit raw JSON instead."
                    />
                  ) : null}
                  {canPush && !onBase ? (
                    <p className="text-xs text-muted">Changes open a PR into {listing?.baseBranch} — switch to that branch to edit, rename or delete.</p>
                  ) : null}
                  <div className="grid gap-3 text-xs md:grid-cols-3">
                    <div>
                      <span className="eyebrow">References</span>
                      {detail.references.length ? detail.references.map((r) => (
                        <button key={`${r.type}:${r.name}`} className="block text-left text-teal-dark hover:underline" onClick={() => setQuery(r.name)}>
                          {r.name} <span className="text-muted">({r.type.replace("Reference", "")})</span>
                        </button>
                      )) : <p className="text-muted">None</p>}
                    </div>
                    <div>
                      <span className="eyebrow">Used by</span>
                      {detail.usedBy.length ? detail.usedBy.map((u) => (
                        <button key={u.path} className="block text-left text-teal-dark hover:underline" onClick={() => setActivePath(u.path)}>{u.name}</button>
                      )) : <p className="text-muted">Nothing references it</p>}
                    </div>
                    <div>
                      <span className="eyebrow">Parameters</span>
                      <p className="text-ink-2">{detail.parameters.length ? detail.parameters.join(", ") : "None"}</p>
                    </div>
                  </div>
                  {editing ? (
                    <>
                      <Textarea className="font-mono text-xs" rows={18} value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} aria-label="Raw file content" />
                      {jsonError ? <Alert variant="error" title="Not valid JSON" description={jsonError} /> : null}
                      <div className="flex justify-end">
                        <Button size="sm" disabled={Boolean(jsonError) || draft === detail.content} onClick={() => setDialog("update")}>
                          <FileJson className="h-3.5 w-3.5" />Push update…
                        </Button>
                      </div>
                    </>
                  ) : (
                    <pre className="code-viewer">{detail.content || "// empty file"}</pre>
                  )}
                </div>
              </div>
            ) : null}
          </section>
        </div>
      ) : null}

      {changes.length ? (
        <div className="panel p-4 text-xs">
          <span className="eyebrow">Recent changes</span>
          <ul className="mt-2 space-y-1">
            {changes.slice(0, 5).map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-2">
                <Badge variant={c.status === "pr_created" ? "green" : c.status === "pending" ? "blue" : "red"}>{c.status}</Badge>
                <span className="font-semibold">{c.mode}</span>
                <code>{c.path}</code>
                {c.newPath ? <>→ <code>{c.newPath}</code></> : null}
                {c.prUrl ? <a className="underline" href={c.prUrl} target="_blank" rel="noreferrer">PR #{c.prNumber}</a> : null}
                {c.error ? <span className="text-red">{c.error}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {detail && dialog && listing ? (
        <CatalogChangeDialog
          open
          onClose={() => setDialog(null)}
          onSettled={settled}
          projectId={projectId}
          mode={dialog}
          path={detail.path}
          name={detail.name}
          baseBranch={listing.baseBranch}
          content={dialog === "update" ? draft : undefined}
        />
      ) : null}
    </div>
  );
}
