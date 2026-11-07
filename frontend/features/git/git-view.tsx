"use client";

import { useState } from "react";
import { GitCommitHorizontal } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { EmptyState, resourceStatus } from "@/components/common/empty-state";
import { Pagination } from "@/components/common/pagination";
import { usePagination } from "@/lib/use-pagination";
import { formatDateTime } from "@/lib/format";

type GitFeedItem =
  | { kind: "pipeline"; id: string; pipelineName: string; artifactVersion: string; gitCommit: string; committedAt: string; artifactCount: number; projectName: string }
  | { kind: "artifact"; id: string; fileName: string; version: string; artifactType: string; pipelineName: string; bytes: number };

export function GitView({ data }: FeatureProps) {
  const [query, setQuery] = useState("");

  // Git = every artifact published to Git. Show generated artifacts whose state
  // is committed plus pipelines currently pushed / PR-created.
  const committedArtifacts = data.artifacts.data.filter((a) => a.state === "committed");
  const gitPipelines = data.pipelines.data
    .filter((p) => p.state === "PUSHED" || p.state === "PR_CREATED")
    .map((pipeline) => ({
      pipelineName: pipeline.name,
      artifactVersion: pipeline.latestArtifactVersion ?? "v1.0.0",
      gitCommit: "—", // Would come from backend after actual Git commit
      committedAt: pipeline.updatedAt,
      artifactCount: pipeline.spec?.sourceObjects?.length ?? 0,
      projectName: pipeline.projectName,
      id: pipeline.id,
    }));

  const allItems: GitFeedItem[] = [
    ...gitPipelines.map((item): GitFeedItem => ({ kind: "pipeline", ...item })),
    ...committedArtifacts.map((artifact): GitFeedItem => ({
      kind: "artifact",
      id: artifact.id,
      fileName: artifact.fileName,
      version: artifact.version,
      artifactType: artifact.artifactType,
      pipelineName: artifact.pipelineName,
      bytes: (artifact.content ?? "").length,
    })),
  ];

  const q = query.trim().toLowerCase();
  const items = !q
    ? allItems
    : allItems.filter((item) =>
        (item.kind === "pipeline" ? `${item.pipelineName} ${item.projectName}` : `${item.fileName} ${item.pipelineName}`)
          .toLowerCase()
          .includes(q),
      );

  const { page, setPage, totalPages, totalItems, pageSize, pageItems } = usePagination(items, { resetKey: query });

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="GIT ARTIFACTS"
        title="Git"
        detail="All artifacts published to Git. Every commit is immutable and traceable to the specification that produced it."
      />
      {allItems.length > 0 ? (
        <div className="catalog-toolbar mb-4">
          <label className="search-box">
            ⌕
            <Input
              placeholder="Search by pipeline, artifact, or project"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="border-0 bg-transparent outline-none ring-0 focus:border-0 focus:ring-0"
            />
          </label>
        </div>
      ) : null}
      <section className="panel">
        {allItems.length === 0 ? (
          <EmptyState status={resourceStatus({ loading: data.pipelines.loading || data.artifacts.loading, failed: data.pipelines.failed || data.artifacts.failed })} errorMessage={data.pipelines.error ?? data.artifacts.error} title="No Git artifacts yet" message="Push a pipeline to Git to see its artifacts here." />
        ) : items.length === 0 ? (
          <EmptyState title="No Git artifacts match" message="Try a different search term." />
        ) : (
          <div className="divide-y divide-line">
            {pageItems.map((item) =>
              item.kind === "pipeline" ? (
                <div key={`${item.id}-${item.artifactVersion}`} className="flex items-center gap-3 px-5 py-4">
                  <span className="pipeline-glyph">
                    <GitCommitHorizontal className="h-4 w-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <strong className="text-xs text-ink">{item.pipelineName}</strong>
                      <Badge variant="forge">{item.artifactVersion}</Badge>
                      <Badge variant="blue">committed</Badge>
                    </div>
                    <p className="m-0 mt-1 text-xs text-muted">
                      {item.projectName} · {item.artifactCount} artifacts · commit{" "}
                      <code className="font-mono text-2xs">{item.gitCommit}</code> · {formatDateTime(item.committedAt)}
                    </p>
                  </div>
                </div>
              ) : (
                <div key={item.id} className="flex items-center gap-3 px-5 py-4">
                  <span className="pipeline-glyph">
                    <GitCommitHorizontal className="h-4 w-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <strong className="text-xs text-ink">{item.fileName}</strong>
                      <Badge variant="forge">{item.version}</Badge>
                      <Badge variant="blue">{item.artifactType.replace(/_/g, " ")}</Badge>
                    </div>
                    <p className="m-0 mt-1 text-xs text-muted">
                      {item.pipelineName} · {item.bytes} bytes
                    </p>
                  </div>
                </div>
              ),
            )}
          </div>
        )}
        <Pagination
          page={page}
          totalPages={totalPages}
          totalItems={totalItems}
          pageSize={pageSize}
          onPageChange={setPage}
          itemLabel="Git artifacts"
        />
      </section>
    </div>
  );
}