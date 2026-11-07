"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { PipelineTable } from "@/features/pipelines/pipeline-table";
import { resourceStatus } from "@/components/common/empty-state";
import { Pagination } from "@/components/common/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { usePagination } from "@/lib/use-pagination";
import { TEMPLATES, TEMPLATE_LIST } from "@/lib/adf/spec";
import { can } from "@/lib/state";
import type { PipelineTemplateId } from "@/types";

type StateFilter = "all" | "in-flight" | "pushed" | "failed";

export function PipelinesView({ data, user, onNavigate }: FeatureProps) {
  const [query, setQuery] = useState("");
  const [templateFilter, setTemplateFilter] = useState<PipelineTemplateId | "all">("all");
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const isLoading = data.pipelines.loading;

  // Delivery → Pipelines shows only published / onboarded assets.
  // In-progress work (DRAFT) lives under Delivery → Drafts.
  const published = data.pipelines.data.filter((pipeline) => pipeline.state !== "DRAFT");
  const pipelines = published.filter((pipeline) => {
    const q = query.trim().toLowerCase();
    const matchesQuery =
      !q ||
      `${pipeline.name} ${pipeline.projectName} ${TEMPLATES[pipeline.type].name}`.toLowerCase().includes(q);
    const matchesTemplate = templateFilter === "all" || pipeline.type === templateFilter;
    const matchesState =
      stateFilter === "all" ||
      (stateFilter === "in-flight" && (pipeline.state === "GENERATED" || pipeline.state === "VALIDATED" || pipeline.state === "PUSHING")) ||
      (stateFilter === "pushed" && (pipeline.state === "PUSHED" || pipeline.state === "PR_CREATED")) ||
      (stateFilter === "failed" && pipeline.state.startsWith("FAILED"));
    return matchesQuery && matchesTemplate && matchesState;
  });

  const { page, setPage, totalPages, totalItems, pageSize, pageItems } = usePagination(pipelines, {
    resetKey: `${query}|${templateFilter}|${stateFilter}`,
  });

  if (isLoading) {
    return (
      <div className="animate-fade-in">
        <div className="page-heading">
          <Skeleton className="h-4 w-32 mb-2" />
          <Skeleton className="h-8 w-64 mb-2" />
          <Skeleton className="h-4 w-96" />
        </div>
        <Skeleton className="h-64 mt-6" />
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="PIPELINE CATALOG"
        title="Pipelines"
        detail="All published / onboarded pipelines. In-progress pipeline creation lives under Drafts."
        action={
          can(user.role, "pipeline:create") ? (
            <Button onClick={() => onNavigate("pipelines.create")}>
              <Plus className="h-3.5 w-3.5" />
              Create pipeline
            </Button>
          ) : undefined
        }
      />
      <section className="panel catalog-panel">
        <div className="catalog-toolbar">
          <label className="search-box">
            ⌕
            <Input
              placeholder="Search pipelines, projects, or templates"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="border-0 bg-transparent outline-none ring-0 focus:border-0 focus:ring-0"
            />
          </label>
          <div className="flex items-center gap-2">
            <Select value={templateFilter} onChange={(e) => setTemplateFilter(e.target.value as PipelineTemplateId | "all")} aria-label="Filter by template" className="w-auto">
              <option value="all">All templates</option>
              {TEMPLATE_LIST.map((template) => (
                <option key={template.id} value={template.id}>
                  {template.name}
                </option>
              ))}
            </Select>
            <Select value={stateFilter} onChange={(e) => setStateFilter(e.target.value as StateFilter)} aria-label="Filter by state" className="w-auto">
              <option value="all">All states</option>
              <option value="in-flight">Generated → Committed</option>
              <option value="pushed">Pushed / PR created</option>
              <option value="failed">Failed</option>
            </Select>
          </div>
        </div>
                <PipelineTable
          pipelines={pageItems}
          status={data.pipelines.data.length ? "empty" : resourceStatus(data.pipelines)}
          errorMessage={data.pipelines.error ?? undefined}
          onEdit={
            can(user.role, "pipeline:create")
              ? (pipeline) => onNavigate("pipelines.create", { editPipelineId: pipeline.id })
              : undefined
          }
        />
        <Pagination
          page={page}
          totalPages={totalPages}
          totalItems={totalItems}
          pageSize={pageSize}
          onPageChange={setPage}
          itemLabel="pipelines"
        />
      </section>
    </div>
  );
}