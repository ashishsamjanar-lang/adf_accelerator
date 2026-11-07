import { CheckCircle2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { LIFECYCLE_STEPS, isFailureState, stateProgress, stateLabel } from "@/lib/state";
import type { DeploymentState } from "@/types";

interface LifecycleStepperProps {
  state: DeploymentState;
  compact?: boolean;
}

/**
 * Visual render of the artifact/Git lifecycle state machine (AGENTS.md §10):
 * DRAFT -> GENERATED -> VALIDATED -> READY_TO_PUSH
 *        -> PUSHING -> PUSHED -> PR_CREATED
 * Failures are rendered as blocked steps at their point of failure.
 * A Git push is NOT a deployment; the accelerator ends at the Git/PR boundary.
 */
export function LifecycleStepper({ state, compact }: LifecycleStepperProps) {
  const progress = stateProgress(state);
  const failed = isFailureState(state);

  return (
    <ol
      className={cn(
        "m-0 flex list-none items-center gap-0 p-0",
        compact ? "justify-start flex-wrap gap-y-2" : "justify-between",
      )}
      aria-label={`Lifecycle state: ${stateLabel(state)}`}
    >
      {LIFECYCLE_STEPS.map((step, index) => {
        const stepNumber = index + 1;
        const complete = stepNumber < progress;
        const current = stepNumber === progress;
        const blocked = failed && !complete && !current;
        const terminal = failed && index === LIFECYCLE_STEPS.length - 1;
        return (
          <li key={step.state} className="flex min-w-0 flex-1 items-center">
            <div className="flex flex-col items-center gap-1.5 px-1 text-center">
              <span
                className={cn(
                  "grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-bold",
                  complete && "bg-teal text-white",
                  current && !failed && "bg-teal text-white ring-4 ring-teal/20",
                  current && failed && "bg-red text-white ring-4 ring-red/20",
                  blocked && "bg-surface-3 text-faint",
                )}
              >
                {complete ? <CheckCircle2 className="h-3.5 w-3.5" /> : blocked || terminal ? <XCircle className="h-3.5 w-3.5" /> : stepNumber}
              </span>
              <span
                className={cn(
                  "max-w-16 text-2xs font-extrabold uppercase tracking-wide",
                  complete || current ? "text-teal-dark" : blocked ? "text-red" : "text-faint",
                )}
              >
                {step.label}
              </span>
            </div>
            {index < LIFECYCLE_STEPS.length - 1 ? (
              <div className={cn("h-px flex-1", complete ? "bg-teal" : "bg-line")} />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}