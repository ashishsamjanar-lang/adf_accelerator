import type { DeploymentState, Role } from "../types";

/**
 * Artifact/Git lifecycle state machine (AGENTS.md section 10).
 *
 * DRAFT -> GENERATED -> VALIDATED -> READY_TO_PUSH
 *        -> PUSHING -> PUSHED -> PR_CREATED
 *
 * Failures are explicit and retryable. A Git push is NOT a deployment;
 * the accelerator deliberately ends at the Git/PR boundary.
 */
export const LIFECYCLE_STEPS: { state: DeploymentState; label: string; step: number }[] = [
  { state: "DRAFT", label: "Draft", step: 1 },
  { state: "GENERATED", label: "Generated", step: 2 },
  { state: "VALIDATED", label: "Validated", step: 3 },
  { state: "READY_TO_PUSH", label: "Ready to push", step: 4 },
  { state: "PUSHING", label: "Pushing", step: 5 },
  { state: "PUSHED", label: "Pushed", step: 6 },
  { state: "PR_CREATED", label: "PR created", step: 7 },
];

export const FAILURE_STATES: Record<string, { state: DeploymentState; label: string }> = {
  GENERATION_FAILED: { state: "GENERATION_FAILED", label: "Generation failed" },
  VALIDATION_FAILED: { state: "VALIDATION_FAILED", label: "Validation failed" },
  GIT_PUSH_FAILED: { state: "GIT_PUSH_FAILED", label: "Git push failed" },
  PR_CREATION_FAILED: { state: "PR_CREATION_FAILED", label: "PR creation failed" },
};

/** All valid states including failures. */
export const ALL_STATES: DeploymentState[] = [
  ...LIFECYCLE_STEPS.map((s) => s.state),
  ...Object.values(FAILURE_STATES).map((f) => f.state),
];

export function stateLabel(state: DeploymentState): string {
  const step = LIFECYCLE_STEPS.find((s) => s.state === state);
  if (step) return step.label;
  return FAILURE_STATES[state]?.label ?? state;
}

/** 1-based progress index for the lifecycle stepper (failures render as blocked). */
export function stateProgress(state: DeploymentState): number {
  return LIFECYCLE_STEPS.find((s) => s.state === state)?.step ?? 0;
}

export function isFailureState(state: DeploymentState): boolean {
  return state.startsWith("FAILED_") || state.endsWith("_FAILED");
}

export function isProgressable(state: DeploymentState): boolean {
  return !isFailureState(state) && state !== "DRAFT";
}

/**
 * RBAC permission model (AGENTS.md section 12).
 * Authorization is enforced server-side; the UI uses the same matrix so
 * actions are only offered to users who are allowed to take them.
 */
export type Permission =
  | "pipeline:create"
  | "pipeline:generate"
  | "pipeline:validate"
  | "pipeline:push"
  | "admin:team"
  | "admin:platform";

export const ROLE_LABELS: Record<Role, string> = {
  PLATFORM_ADMIN: "Platform admin",
  TEAM_ADMIN: "Team admin",
  DEVELOPER: "Developer",
  VIEWER: "Viewer",
};

export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  PLATFORM_ADMIN: "Full platform administration and Git push access",
  TEAM_ADMIN: "Team/project administration and controlled Git push",
  DEVELOPER: "Create/modify pipelines; push to feature branches",
  VIEWER: "Read-only access",
};

const PERMISSIONS: Record<Role, Permission[]> = {
  PLATFORM_ADMIN: [
    "pipeline:create",
    "pipeline:generate",
    "pipeline:validate",
    "pipeline:push",
    "admin:team",
    "admin:platform",
  ],
  TEAM_ADMIN: [
    "pipeline:create",
    "pipeline:generate",
    "pipeline:validate",
    "pipeline:push",
    "admin:team",
  ],
  DEVELOPER: ["pipeline:create", "pipeline:generate", "pipeline:validate", "pipeline:push"],
  VIEWER: [],
};

export function can(role: Role, permission: Permission): boolean {
  return PERMISSIONS[role]?.includes(permission) ?? false;
}

export const ROLES: Role[] = ["PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER", "VIEWER"];