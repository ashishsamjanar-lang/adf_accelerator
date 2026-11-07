/**
 * Foundational enums shared across every other domain-type module
 * (AGENTS.md sections 4, 10, 12). Kept dependency-free so any other
 * types/*.ts file can import from here without a cycle.
 */

export type Role = "PLATFORM_ADMIN" | "TEAM_ADMIN" | "DEVELOPER" | "VIEWER";

/**
 * The natural key of a registered ADF instance (`Environment.name` in org.ts).
 * Historically capped at "DEV" | "QA" | "PROD" — widened to `string` so an
 * org can register any number of named ADF instances (multi-instance
 * support), not just a fixed three. `DEV`/`QA`/`PROD` remain the always-seeded
 * defaults (`backend/app/seed.py`), just no longer the ceiling.
 */
export type EnvironmentName = string;

/**
 * Artifact/Git lifecycle state machine (AGENTS.md section 10).
 *
 * DRAFT -> GENERATED -> VALIDATED -> READY_TO_PUSH
 *        -> PUSHING -> PUSHED -> PR_CREATED
 *
 * Failure states are explicit and retryable. A Git push is NOT a deployment;
 * the accelerator deliberately ends at the Git/PR boundary.
 */
export type DeploymentState =
  | "DRAFT"
  | "GENERATED"
  | "VALIDATED"
  | "READY_TO_PUSH"
  | "PUSHING"
  | "PUSHED"
  | "PR_CREATED"
  | "GENERATION_FAILED"
  | "VALIDATION_FAILED"
  | "GIT_PUSH_FAILED"
  | "PR_CREATION_FAILED";

// Pipeline templates - ALL metadata-driven
export type PipelineTemplateId =
  | "full_load"
  | "incremental_load"
  | "metadata_driven_load"
  | "initial_load"
  | "composite_watermark_load";

export type ArtifactType =
  | "pipeline"
  | "dataset"
  | "linked_service"
  | "parameter"
  | "trigger"
  | "factory"
  // §32.5 — a `Microsoft.DataFactory/factories/credentials` (user-assigned managed identity)
  // resource. Worker/backend-only for now (worker/app/generator/codegen.py's
  // `build_credential_artifact`, mirrored in pipeline-codegen.ts) — emitted when a linked
  // service's `identityKind` is "user" (the Linked Service editor's managed-identity picker).
  | "credential";

/** §32.6 — a registered user-assigned managed identity (`app.managed_identities`). */
export interface ManagedIdentity {
  id: string;
  teamId: string;
  name: string;
  resourceId: string;
  clientId?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  createdBy?: string | null;
  createdByName?: string | null;
}

/** Deployment layers (AGENTS.md §23.9) */
export type ArtifactLayer =
  | "infrastructure"
  | "connection"
  | "data"
  | "pipeline"
  | "runtime"
  | "metadata";

export type Severity = "error" | "warning" | "info";
