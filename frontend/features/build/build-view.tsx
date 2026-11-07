"use client";

import { useState } from "react";
import { CalendarClock, Hammer, Link2, Table2 } from "lucide-react";
import type { FeatureProps, OnboardingPrefill } from "@/features/types";
import { cn } from "@/lib/utils";
import type { SpecDraft } from "@/lib/adf/spec";
import type { StandaloneArtifactKind } from "@/lib/adf/standalone";
import { CreatePipelineWizard } from "@/features/build/create-pipeline-wizard";
import { StandaloneArtifactForm } from "@/features/build/standalone-artifact-form";
import type { PipelineDefinition } from "@/types";

type BuildArtifactType = "pipeline" | StandaloneArtifactKind;

interface BuildViewProps extends FeatureProps {
  onCreated: (draft: SpecDraft) => void;
  editPipelineId?: string | null;
  onUpdated?: (pipeline: PipelineDefinition) => void;
  onboardingPrefill?: OnboardingPrefill | null;
}

const ARTIFACT_TABS: Array<{ id: BuildArtifactType; label: string; icon: typeof Hammer }> = [
  { id: "pipeline", label: "Pipeline", icon: Hammer },
  { id: "linked_service", label: "Linked service", icon: Link2 },
  { id: "dataset", label: "Dataset", icon: Table2 },
  { id: "trigger", label: "Trigger", icon: CalendarClock },
];

/**
 * The Build surface (AGENTS.md §24.2): one entry point with an artifact-type
 * switch. Pipeline opens the metadata-driven wizard; the other types open a
 * standalone single-artifact form so a linked service, dataset or trigger can
 * be authored on its own and pushed straight to a feature branch.
 */
export function BuildView({ onCreated, editPipelineId, onUpdated, onboardingPrefill, ...props }: BuildViewProps) {
  const [artifactType, setArtifactType] = useState<BuildArtifactType>("pipeline");

  return (
    <div className="animate-fade-in">
      <div className="mb-5 flex flex-wrap gap-2" role="tablist" aria-label="Artifact type">
        {ARTIFACT_TABS.map((tab) => {
          const Icon = tab.icon;
          const active = tab.id === artifactType;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={active}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-bold transition-colors",
                active
                  ? "border-teal bg-teal-50 text-teal"
                  : "border-line bg-surface text-ink-2 hover:border-line-2",
              )}
              onClick={() => setArtifactType(tab.id)}
            >
              <Icon className="h-3.5 w-3.5" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {artifactType === "pipeline" ? (
        <CreatePipelineWizard
          {...props}
          onCreated={onCreated}
          editPipelineId={editPipelineId}
          onUpdated={onUpdated}
          onboardingPrefill={onboardingPrefill}
        />
      ) : (
        <StandaloneArtifactForm key={artifactType} {...props} kind={artifactType} />
      )}
    </div>
  );
}
