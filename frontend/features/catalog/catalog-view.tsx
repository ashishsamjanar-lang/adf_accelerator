"use client";

import { useMemo, useState } from "react";
import { GitBranch, Hammer, Search, UploadCloud } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, resourceStatus } from "@/components/common/empty-state";
import { GitPushDialog } from "@/components/git/git-push-dialog";
import { Pagination } from "@/components/common/pagination";
import { ARTIFACT_ICONS } from "@/components/shell/navigation";
import { LAYER_LABELS } from "@/lib/format";
import { usePagination } from "@/lib/use-pagination";
import { can } from "@/lib/state";
import { cn } from "@/lib/utils";
import type { ArtifactType } from "@/types";
import { GitCatalogPanel } from "@/features/catalog/git-catalog-panel";

type FilterId = "all" | ArtifactType;

const FILTERS: { id: FilterId; label: string }[] = [
  { id: "all", label: "All artifacts" },
  { id: "pipeline", label: "ADF Pipelines" },
  { id: "dataset", label: "Datasets" },
  { id: "linked_service", label: "Linked Services" },
  { id: "parameter", label: "Parameters" },
  { id: "trigger", label: "Triggers" },
];

const FOLDER: Record<ArtifactType, string> = {
  pipeline: "pipeline",
  dataset: "dataset",
  linked_service: "linkedService",
  parameter: "parameters",
  trigger: "trigger",
  factory: "factory",
  credential: "credential",
};

export function CatalogView({ data, user, onNavigate, initialQuery }: FeatureProps & { initialQuery?: string }) {
  const all = data.artifacts.data;
  const gitDefaults = data.projects.data[0]?.settings?.gitDefaults;
  const canPush = can(user.role, "pipeline:push");

  const [filter, setFilter] = useState<FilterId>("all");
  const [query, setQuery] = useState(initialQuery ?? "");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [pushOpen, setPushOpen] = useState(false);
  // "generated" = this app's artifact store; "git" = the §24.8 index read from the repo itself.
  const [source, setSource] = useState<"generated" | "git">("generated");

  const counts = useMemo(() => {
    const map: Record<string, number> = { all: all.length };
    for (const artifact of all) map[artifact.artifactType] = (map[artifact.artifactType] ?? 0) + 1;
    return map;
  }, [all]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return all
      .filter((artifact) => filter === "all" || artifact.artifactType === filter)
      .filter((artifact) => !q || artifact.fileName.toLowerCase().includes(q) || artifact.pipelineName.toLowerCase().includes(q))
      .sort((a, b) => a.fileName.localeCompare(b.fileName));
  }, [all, filter, query]);

  const selectedArtifacts = useMemo(() => all.filter((a) => selected.has(a.id)), [all, selected]);
  const detail = rows.find((a) => a.id === activeId) ?? null;

  const { page, setPage, totalPages, totalItems, pageSize, pageItems } = usePagination(rows, {
    resetKey: `${filter}|${query}`,
  });

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  // Selects/clears every artifact currently matching the type filter + search — not just the page —
  // so "push everything for this pipeline" (search by pipeline name) is a single click, not one
  // checkbox per artifact type across every type-scoped Artifacts route.
  const allRowsSelected = rows.length > 0 && rows.every((row) => selected.has(row.id));
  function toggleAllRows() {
    setSelected((prev) => {
      if (allRowsSelected) {
        const next = new Set(prev);
        for (const row of rows) next.delete(row.id);
        return next;
      }
      const next = new Set(prev);
      for (const row of rows) next.add(row.id);
      return next;
    });
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="GIT-BACKED CATALOG"
        title="Catalog"
        detail="Every artifact in the connected repository, on any branch. Browse by type, open one in Build to edit it, or select artifacts and push them to a feature branch."
        action={
          source === "git" ? undefined : (
          <div className="flex items-center gap-2">
            <span className="branch-chip">
              <GitBranch className="h-3.5 w-3.5" />
              {gitDefaults?.baseBranch ?? "dev"}
            </span>
            <Button
              variant="outline"
              disabled={!canPush || selectedArtifacts.length === 0}
              onClick={() => setPushOpen(true)}
            >
              <UploadCloud className="h-3.5 w-3.5" />
              Push{selectedArtifacts.length ? ` (${selectedArtifacts.length})` : ""}
            </Button>
          </div>
          )
        }
      />

      <div className="mb-4 flex gap-2" role="tablist" aria-label="Catalog source">
        {([["generated", "Generated artifacts"], ["git", "Git repository"]] as const).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={source === id}
            className={cn(
              "rounded-md border px-3 py-1.5 text-xs font-bold transition-colors",
              source === id ? "border-teal bg-teal-50 text-teal" : "border-line bg-surface text-ink-2 hover:border-line-2",
            )}
            onClick={() => setSource(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {source === "git" ? (
        <GitCatalogPanel data={data} user={user} onNavigate={onNavigate} />
      ) : all.length === 0 ? (
        <section className="panel">
          <EmptyState
            status={resourceStatus(data.artifacts)}
            errorMessage={data.artifacts.error}
            title="Nothing in the catalog yet"
            message="Generate a pipeline or author a standalone linked service, dataset or trigger in Build. Pushed artifacts show up here indexed straight from Git."
            action={<Button onClick={() => onNavigate("pipelines.create")}><Hammer className="h-3.5 w-3.5" />Open Build</Button>}
          />
        </section>
      ) : (
        <div className="catalog-layout">
          <aside className="filter-rail">
            <span className="eyebrow">Artifact type</span>
            {FILTERS.map((entry) => (
              <button
                key={entry.id}
                className={cn("filter-item", filter === entry.id && "active")}
                onClick={() => {
                  setFilter(entry.id);
                  setActiveId(null);
                }}
              >
                <span>{entry.label}</span>
                <span className="filter-count">{counts[entry.id] ?? 0}</span>
              </button>
            ))}
          </aside>

          <section className="min-w-0">
            <div className="search-box mb-3">
              <Search className="h-4 w-4" />
              <input
                placeholder="Search by name or pipeline…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>

            <div className="panel catalog-panel">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th style={{ width: 34 }}>
                        {rows.length > 0 ? (
                          <input
                            type="checkbox"
                            className="h-3.5 w-3.5 accent-teal"
                            checked={allRowsSelected}
                            ref={(el) => {
                              if (el) el.indeterminate = !allRowsSelected && rows.some((row) => selected.has(row.id));
                            }}
                            onChange={toggleAllRows}
                            aria-label="Select all matching artifacts"
                          />
                        ) : null}
                      </th>
                      <th>Name</th>
                      <th>Type</th>
                      <th>Repository path</th>
                      <th>Version</th>
                      <th>State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-5 py-8 text-center text-xs text-muted">
                          No artifacts match this filter or search.
                        </td>
                      </tr>
                    ) : null}
                    {pageItems.map((artifact) => {
                      const Icon = ARTIFACT_ICONS[artifact.artifactType] ?? ARTIFACT_ICONS.pipeline;
                      return (
                        <tr
                          key={artifact.id}
                          className={cn("catalog-row", detail?.id === artifact.id && "active")}
                          onClick={() => setActiveId(artifact.id)}
                        >
                          <td onClick={(event) => event.stopPropagation()}>
                            <input
                              type="checkbox"
                              className="h-3.5 w-3.5 accent-teal"
                              aria-label={`Select ${artifact.fileName}`}
                              checked={selected.has(artifact.id)}
                              onChange={() => toggle(artifact.id)}
                            />
                          </td>
                          <td>
                            <span className="pipeline-name">
                              <span className="pipeline-glyph"><Icon className="h-4 w-4" /></span>
                              <span>
                                <strong>{artifact.fileName}</strong>
                                <small>{artifact.pipelineName}</small>
                              </span>
                            </span>
                          </td>
                          <td className="muted">{LAYER_LABELS[artifact.layer]}</td>
                          <td className="muted"><code>{FOLDER[artifact.artifactType]}/{artifact.fileName}</code></td>
                          <td className="muted">{artifact.version}</td>
                          <td>
                            <Badge variant={artifact.state === "generated" ? "amber" : artifact.state === "validated" ? "blue" : "green"}>
                              {artifact.state}
                            </Badge>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={page}
                totalPages={totalPages}
                totalItems={totalItems}
                pageSize={pageSize}
                onPageChange={setPage}
                itemLabel="artifacts"
              />
            </div>

            {detail ? (
              <div className="panel mt-3 p-0">
                <div className="artifact-detail-head" style={{ padding: "14px 18px 0" }}>
                  <div>
                    <strong className="text-sm text-ink">{detail.fileName}</strong>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      <Badge variant="forge">{detail.version}</Badge>
                      <Badge variant="blue">{LAYER_LABELS[detail.layer]}</Badge>
                      <Badge variant="blue">{detail.fileType}</Badge>
                    </div>
                  </div>
                  <Button size="sm" variant="outline" onClick={() => onNavigate("pipelines.create", { editPipelineId: detail.pipelineId })}>
                    <Hammer className="h-3.5 w-3.5" />
                    Open in Build
                  </Button>
                </div>
                <div className="p-[18px]">
                  <pre className="code-viewer">{detail.content ?? "// no preview available"}</pre>
                </div>
              </div>
            ) : null}
          </section>
        </div>
      )}

      <GitPushDialog
        open={pushOpen}
        onClose={() => setPushOpen(false)}
        artifacts={selectedArtifacts}
      />
    </div>
  );
}
