/**
 * API request/response and audit/log shapes that don't belong to a single
 * domain above: saved connections, validation results, audit trail, the
 * generic error envelope, log entries, and pipeline run metrics
 * (AGENTS.md §14, §16-17).
 */

import type { Severity } from "./core";

/**
 * Server-side mirror of a saved connection (§24.17 `connection_library`),
 * returned by `api.connections.*`. `draft` is opaque here — it holds
 * `{ linkedService, objectMode, objects, dataset }`, typed precisely as
 * `SavedConnection` in `lib/adf/connection-store.ts`, which is what actually
 * reads/writes it.
 */
export interface ConnectionRecord {
  id: string;
  projectId: string;
  teamId: string;
  name: string;
  role: "source" | "target" | "both";
  connectorType: string;
  description: string;
  draft: Record<string, unknown>;
  createdAt?: string | null;
  updatedAt?: string | null;
  createdBy?: string | null;
  createdByName?: string | null;
}

/**
 * §29.1's validation pyramid layer an issue came from. `schema` (vendored ADF
 * resource schemas) and `live` (opt-in ARM-validate) are still target design
 * (§29.5) — the backend never emits them today, listed here only so the type
 * matches the full §29.3 contract.
 */
export type ValidationLayer = "syntax" | "schema" | "semantic" | "security" | "policy" | "live";

export interface ValidationIssue {
  severity: Severity;
  message: string;
  source?: string; // artifact or field this issue relates to
  field?: string;
  // Defaults to "semantic" server-side (backend/app/api/schemas.py::ValidationIssueOut) —
  // optional here too so a stale cached/demo-fixture issue with no `layer` still renders.
  layer?: ValidationLayer;
  // §29.3 — stable rule id (Standards catalogue id where one exists) + one-line remediation.
  ruleId?: string;
  fixHint?: string;
}

export interface ValidationResult {
  pipelineId: string;
  // "pending" until a validate job has actually run for this pipeline
  // (backend/app/services.py::latest_validation_result's no-job default).
  state: "passed" | "failed" | "pending";
  issues: ValidationIssue[];
  validatedAt: string | null;
  correlationId: string;
}

/**
 * Payload for POST /api/v1/pipelines (AGENTS.md §7, §14, §16). `draft` is the
 * raw SpecDraft (camelCase, `lib/adf/spec.ts`) — the backend stores it as-is
 * and the worker's generator (`worker/app/generator/`) parses it back out on
 * generate, so its shape must stay in parity with the frontend's own
 * `SpecDraft` → `buildGeneratedArtifacts` pipeline (see the parity test).
 */
export interface PipelineCreateRequestBody {
  name: string;
  projectId: string;
  templateId: string;
  draft?: Record<string, unknown>;
}

/** Payload for PUT /api/v1/pipelines/{id} — every field optional, only what changed need be sent.
 * Backs the Build wizard's edit mode (rename master/child, append objects) alongside `create` above. */
export interface PipelineUpdateRequestBody {
  name?: string;
  templateId?: string;
  draft?: Record<string, unknown>;
  /** §24.14 — the `updatedAt` this edit started from; a mismatch 409s `version_conflict`. */
  expectedUpdatedAt?: string;
}

export interface AuditEntry {
  id: string;
  timestamp: string;
  actor: string;
  actorName?: string;
  action: string;
  target: string;
  details: string;
}

/** GET /admin/sessions (§27 session admin). `handle` is a one-way hash — never the cookie value. */
export interface AdminSession {
  handle: string;
  userId: string;
  userName?: string | null;
  userEmail?: string | null;
  teamId?: string | null;
  issuedAt?: string | null;
  idleExpiresAt?: string | null;
  expiresAt?: string | null;
  ip?: string | null;
  userAgent?: string | null;
  /** True for the caller's own session. */
  current: boolean;
}

/** §27.5 SSO provider config (admin metadata only — no login path reads it yet). */
export interface SsoRoleMapping {
  groupValue: string;
  role: "PLATFORM_ADMIN" | "TEAM_ADMIN" | "DEVELOPER" | "VIEWER";
  teamId?: string | null;
}

export interface SsoConfigurationInput {
  name: string;
  protocol: "oidc" | "saml";
  issuerUrl?: string | null;
  clientId?: string | null;
  /** Key Vault reference only (vault name + secret name) — never a secret value. */
  clientSecretKeyVault?: string | null;
  clientSecretName?: string | null;
  metadataUrl?: string | null;
  entityId?: string | null;
  groupClaim: string;
  jitProvisioning: boolean;
  enabled: boolean;
  roleMappings: SsoRoleMapping[];
}

export interface SsoConfiguration extends SsoConfigurationInput {
  id: string;
  createdAt?: string | null;
  updatedAt?: string | null;
  updatedByName?: string | null;
}

/** GET /auth/config (public, unauthenticated) — tells the shell which login
 * UI to render (AGENTS.md §27.2 extension). */
export interface AuthConfig {
  authMode: "mock" | "password" | "oidc" | "saml";
  /** Sliding idle-session length (password mode only, else null) — drives the pre-expiry warning. */
  idleTimeoutMinutes?: number | null;
}

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: string;
    correlation_id?: string;
  };
}

// Log entry
export interface LogEntry {
  id: string;
  timestamp: string;
  level: "Info" | "Warning" | "Error" | "Verbose";
  pipelineName: string;
  activityName?: string;
  message: string;
  correlationId: string;
  customProperties?: Record<string, string>;
}

// Metrics
export interface PipelineMetrics {
  pipelineName: string;
  totalRuns: number;
  successfulRuns: number;
  failedRuns: number;
  averageDuration: number;
  lastRunTime?: string;
  lastStatus?: string;
}
