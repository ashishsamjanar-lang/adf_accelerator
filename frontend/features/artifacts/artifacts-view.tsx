"use client";

import { useMemo, useState } from "react";
import { UploadCloud } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState, resourceStatus } from "@/components/common/empty-state";
import { GitPushDialog } from "@/components/git/git-push-dialog";
import { Pagination } from "@/components/common/pagination";
import { usePagination } from "@/lib/use-pagination";
import { LAYER_LABELS, formatBytes } from "@/lib/format";
import { can } from "@/lib/state";
import type { ArtifactType, ArtifactVersion } from "@/types";
import { cn } from "@/lib/utils";

const CATEGORIES: { id: ArtifactType; label: string }[] = [
  { id: "pipeline", label: "ADF Pipelines" },
  { id: "dataset", label: "Datasets" },
  { id: "linked_service", label: "Linked Services" },
  { id: "parameter", label: "Parameters" },
  { id: "trigger", label: "Triggers" },
];

export function ArtifactCategoryView({ data, user, categoryId }: FeatureProps & { categoryId: ArtifactType }) {
  const artifacts = data.artifacts.data.filter((a) => a.artifactType === categoryId);
  const label = CATEGORIES.find((c) => c.id === categoryId)?.label ?? categoryId;
  const canPush = can(user.role, "pipeline:push");

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pushOpen, setPushOpen] = useState(false);
  const [query, setQuery] = useState("");

  const filteredArtifacts = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return artifacts;
    return artifacts.filter(
      (a) => a.fileName.toLowerCase().includes(q) || a.pipelineName.toLowerCase().includes(q),
    );
  }, [artifacts, query]);

  const { page, setPage, totalPages, totalItems, pageSize, pageItems } = usePagination(filteredArtifacts, {
    resetKey: query,
  });

  const selectedArtifacts = useMemo(
    () => artifacts.filter((a) => selected.has(a.id)),
    [artifacts, selected],
  );

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) => (prev.size === artifacts.length ? new Set() : new Set(artifacts.map((a) => a.id))));
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="ARTIFACT CATEGORY"
        title={label}
        detail="Every artifact is immutable, versioned, and traceable to the specification that produced it. Select one or more artifacts and push them to a Git feature branch."
        action={
          <Button
            disabled={!canPush || selectedArtifacts.length === 0}
            onClick={() => setPushOpen(true)}
            title={!canPush ? "Requires push permission" : undefined}
          >
            <UploadCloud className="h-3.5 w-3.5" />
            Push to Git{selectedArtifacts.length ? ` (${selectedArtifacts.length})` : ""}
          </Button>
        }
      />

      <section className="panel">
        {artifacts.length === 0 ? (
          <EmptyState status={resourceStatus(data.artifacts)} errorMessage={data.artifacts.error} title={`No ${label.toLowerCase()} yet`} message="Generate a pipeline to see its artifacts here." />
        ) : (
          <>
            <div className="panel-toolbar">
              <label className="flex items-center gap-2 text-xs font-semibold text-ink-2">
                <input
                  type="checkbox"
                  checked={selected.size === artifacts.length && artifacts.length > 0}
                  ref={(el) => {
                    if (el) el.indeterminate = selected.size > 0 && selected.size < artifacts.length;
                  }}
                  onChange={toggleAll}
                  className="h-4 w-4 accent-teal"
                />
                {selected.size > 0 ? `${selected.size} selected` : "Select all"}
              </label>
              <label className="search-box">
                ⌕
                <Input
                  placeholder="Search by name or pipeline"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  className="border-0 bg-transparent outline-none ring-0 focus:border-0 focus:ring-0"
                />
              </label>
              <span className="text-xs text-muted">{artifacts.length} artifact{artifacts.length === 1 ? "" : "s"}</span>
            </div>
            {filteredArtifacts.length === 0 ? (
              <EmptyState title="No artifacts match" message="Try a different search term." />
            ) : (
              <ArtifactReviewPanel
                key={pageItems[0]?.id ?? label}
                artifacts={pageItems}
                selected={selected}
                onToggle={toggle}
              />
            )}
            <Pagination
              page={page}
              totalPages={totalPages}
              totalItems={totalItems}
              pageSize={pageSize}
              onPageChange={setPage}
              itemLabel="artifacts"
            />
          </>
        )}
      </section>

      <GitPushDialog
        open={pushOpen}
        onClose={() => setPushOpen(false)}
        artifacts={selectedArtifacts}
      />
    </div>
  );
}

function ArtifactReviewPanel({
  artifacts,
  selected,
  onToggle,
}: {
  artifacts: ArtifactVersion[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(artifacts[0]?.id ?? null);
  const detail = artifacts.find((a) => a.id === selectedId) ?? artifacts[0] ?? null;

  if (!detail) return null;

  return (
    <div className="artifact-review">
      <div className="artifact-list">
        {artifacts.map((artifact) => (
          <div
            key={artifact.id}
            className={cn("artifact-item", detail.id === artifact.id && "active")}
          >
            <div className="artifact-row">
              <label className="flex min-w-0 items-center gap-2">
                <input
                  type="checkbox"
                  checked={selected.has(artifact.id)}
                  onChange={() => onToggle(artifact.id)}
                  className="h-3.5 w-3.5 shrink-0 accent-teal"
                />
                <button type="button" className="artifact-name truncate text-left" onClick={() => setSelectedId(artifact.id)}>
                  {artifact.fileName}
                </button>
              </label>
              <Badge variant="forge">{artifact.version}</Badge>
            </div>
            <div className="artifact-meta">
              <span>{artifact.pipelineName}</span>
              <span>{formatBytes(artifact.bytes)}</span>
            </div>
          </div>
        ))}
      </div>
      <div className="artifact-detail">
        <div className="artifact-detail-head">
          <div>
            <strong className="text-sm text-ink">{detail.fileName}</strong>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <Badge variant="forge">{detail.version}</Badge>
              <Badge variant="blue">{LAYER_LABELS[detail.layer]}</Badge>
              <Badge variant="blue">{detail.fileType}</Badge>
              <Badge variant={detail.state === "generated" ? "amber" : "green"}>{detail.state}</Badge>
            </div>
          </div>
          <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(detail.content ?? "")}>
            Copy code
          </Button>
        </div>
        <div className="artifact-detail-meta">
          <span>Pipeline: {detail.pipelineName}</span>
          <span>Version: {detail.version}</span>
          <span>Size: {formatBytes(detail.bytes)}</span>
        </div>
        <pre className="code-viewer">{detail.content}</pre>
      </div>
    </div>
  );
}
