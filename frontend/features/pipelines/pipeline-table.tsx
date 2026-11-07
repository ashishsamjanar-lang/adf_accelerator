"use client";

import type { ArtifactType, PipelineDefinition } from "@/types";
import { stateLabel, stateProgress, isFailureState } from "@/lib/state";
import { formatDateTime, STATE_TONES, type Tone } from "@/lib/format";
import { StatusPill } from "@/components/common/status-pill";
import { EmptyState, type EmptyStateStatus } from "@/components/common/empty-state";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Pencil, Workflow } from "lucide-react";

export const TEMPLATE_GLYPH_TONE: Record<PipelineDefinition["type"], "green" | "blue" | "amber"> = {
  full_load: "blue",
  incremental_load: "green",
  initial_load: "amber",
  composite_watermark_load: "amber",
  metadata_driven_load: "amber",
};

export function pipelineGlyphTone(type: PipelineDefinition["type"]): "green" | "blue" | "amber" {
  return TEMPLATE_GLYPH_TONE[type];
}

interface PipelineTableProps {
  pipelines: PipelineDefinition[];
  /** Opens this pipeline back in the Build wizard's edit mode (rename master/child, append
   * onboarded objects) — omitted hides the Actions column entirely (e.g. no `pipeline:edit`
   * permission), rather than showing a button that would just 403. */
  onEdit?: (pipeline: PipelineDefinition) => void;
  /** Distinguishes a truly empty list from loading / an unreachable backend (see `resourceStatus`). */
  status?: EmptyStateStatus;
  errorMessage?: string;
}

export function PipelineTable({ pipelines, onEdit, status, errorMessage }: PipelineTableProps) {
  if (pipelines.length === 0) {
    return (
      <EmptyState
        status={status}
        errorMessage={errorMessage}
        title="No published pipelines yet"
        message="Create a pipeline and push it forward — in-progress work appears under Drafts."
      />
    );
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Pipeline</th>
            <th>Template</th>
            <th>Artifact</th>
            <th>State</th>
            <th>Created</th>
            <th>Updated</th>
            {onEdit ? <th /> : null}
          </tr>
        </thead>
        <tbody>
          {pipelines.map((pipeline) => {
            const tone = STATE_TONES[pipeline.state];
            const progress = Math.max(stateProgress(pipeline.state) - 1, 0) * 12.5;
            return (
              <tr key={pipeline.id}>
                <td>
                  <div className="pipeline-name">
                    <span className={cn("pipeline-glyph", pipelineGlyphTone(pipeline.type))}>
                      <Workflow className="h-4 w-4" />
                    </span>
                    <div>
                      <strong>{pipeline.name}</strong>
                      <small>{pipeline.projectName}</small>
                    </div>
                  </div>
                </td>
                <td>
                  <code>{templateLabel(pipeline.type)}</code>
                </td>
                <td>
                  <code>{pipeline.latestArtifactVersion ?? "—"}</code>
                </td>
                <td>
                  <div className="flex items-center gap-2">
                    <StatusPill tone={tone as Tone}>{stateLabel(pipeline.state)}</StatusPill>
                    <Progress
                      value={progress}
                      tone={isFailureState(pipeline.state) ? "amber" : tone === "green" ? "green" : "blue"}
                      className="w-16"
                    />
                  </div>
                </td>
                <td className="text-muted" title={pipeline.createdAt ? formatDateTime(pipeline.createdAt) : undefined}>
                  {pipeline.createdByName ?? pipeline.createdBy ?? "—"}
                </td>
                <td className="text-muted">{formatDateTime(pipeline.updatedAt)}</td>
                {onEdit ? (
                  <td>
                    <Button size="sm" variant="ghost" onClick={() => onEdit(pipeline)}>
                      <Pencil className="h-3.5 w-3.5" />
                      Edit
                    </Button>
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function templateLabel(type: ArtifactType | PipelineDefinition["type"]): string {
  const labels: Record<string, string> = {
    full_load: "Full Load",
    incremental_load: "Incremental Load",
    initial_load: "Initial Load",
    composite_watermark_load: "Composite Watermark Load",
    metadata_driven_load: "Metadata-Driven Load",
    pipeline: "ADF Pipeline",
    dataset: "Dataset",
    linked_service: "Linked Service",
    parameter: "Parameter",
    trigger: "Trigger",
  };
  return labels[type] ?? String(type);
}