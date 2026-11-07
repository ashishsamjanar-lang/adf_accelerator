"use client";

import { CheckCircle2 } from "lucide-react";
import { PageHeader } from "@/components/common/page-header";
import { Alert } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import { WIZARD_STEP_LABELS } from "@/features/build/wizard-shared";
import type { WizardProps } from "@/features/build/wizard-shared";
import { BasicsStep } from "@/features/build/wizard-basics-step";
import { SourceStep } from "@/features/build/wizard-source-step";
import { TargetStep } from "@/features/build/wizard-target-step";
import { SettingsStep } from "@/features/build/wizard-settings-step";
import { ReviewStep } from "@/features/build/wizard-review-step";
import { usePipelineWizard } from "@/features/build/use-pipeline-wizard";

/**
 * Build wizard shell: header, step rail, and the active step. State + handlers live in
 * `use-pipeline-wizard.ts`; each step is its own `wizard-*-step.tsx` taking the hook's result as
 * `ctx`; small shared pieces (InheritedField, PairEditor, ...) are in `wizard-shared.tsx`.
 */
export function CreatePipelineWizard(props: WizardProps) {
  const ctx = usePipelineWizard(props);
  const {
    step,
    setStep,
    draft,
    hydrating,
    isEditing,
    canCreate,
  } = ctx;

  if (hydrating) {
    return (
      <div className="animate-fade-in">
        <PageHeader eyebrow="BUILD" title="Opening pipeline…" detail="Loading the saved draft to edit." />
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow={isEditing ? "EDIT PIPELINE · METADATA-DRIVEN" : "CREATE PIPELINE · METADATA-DRIVEN"}
        title={isEditing ? `Editing "${draft.name}"` : "New metadata-driven pipeline"}
        detail={
          isEditing
            ? "Reopened from an existing pipeline. Add more onboarded objects, rename the master/child artifacts, or change any other field, then Save to update it."
            : "One template — Metadata-Driven Load. Populate the form, generate the pipeline code, and create the pipeline. Every field below is stored as metadata; the generated ADF code reads a control table, so adding objects later is a metadata-row change, never a pipeline rebuild."
        }
      />

      <div className="wizard-progress">
        <div className="wizard-progress-track">
          <div
            className="wizard-progress-fill"
            style={{ width: `${(step / WIZARD_STEP_LABELS.length) * 100}%` }}
          />
        </div>
        <span className="wizard-progress-label">
          Step {step} of {WIZARD_STEP_LABELS.length} · {WIZARD_STEP_LABELS[step - 1]}
        </span>
      </div>

      <div className="wizard-steps-simple">
        {WIZARD_STEP_LABELS.map((label, index) => {
          const stepNum = index + 1;
          const isActive = stepNum === step;
          const isComplete = stepNum < step;
          return (
            <button
              key={label}
              className={cn("wizard-step-item", isActive && "active", isComplete && "complete")}
              onClick={() => {
                if (isComplete) setStep(stepNum);
              }}
            >
              <span className="wizard-step-dot">
                {isComplete ? <CheckCircle2 className="h-3.5 w-3.5" /> : stepNum}
              </span>
              <span className="wizard-step-label">{label}</span>
            </button>
          );
        })}
      </div>

      {!canCreate ? (
        <div className="form-card">
          <Alert
            variant="warning"
            title="You do not have permission to create pipelines"
            description="Ask a team or platform admin to upgrade your role."
          />
        </div>
      ) : null}

      {canCreate && step === 1 && <BasicsStep ctx={ctx} />}

      {canCreate && step === 2 && <SourceStep ctx={ctx} />}

      {canCreate && step === 3 && <TargetStep ctx={ctx} />}

      {canCreate && step === 4 && <SettingsStep ctx={ctx} />}

      {canCreate && step === 5 && <ReviewStep ctx={ctx} />}
    </div>
  );
}
