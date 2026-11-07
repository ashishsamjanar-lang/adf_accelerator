import type {
  AdminSession,
  SsoConfiguration,
  SsoConfigurationInput,
  ApiErrorEnvelope,
  ArtifactVersion,
  AuditEntry,
  AuthConfig,
  ConnectionRecord,
  Environment,
  EnvironmentConnection,
  GitPushRequestBody,
  GitPushResponse,
  OnboardingDqRule,
  OnboardingImportRecord,
  OnboardingMappingProfile,
  OnboardingObjectEntry,
  PipelineCreateRequestBody,
  PipelineDefinition,
  PipelineTemplate,
  PipelineUpdateRequestBody,
  Project,
  ProjectSettings,
  RegistryObject,
  RegistryObjectInput,
  SttmImportRecord,
  SttmLayer,
  Team,
  User,
  ValidationResult,
  ManagedIdentity,
  IntegrationJob,
  IntegrationKind,
  OnboardingSync,
  OnboardingSyncDiff,
  OnboardingSyncRequest,
  ProjectIntegration,
  CatalogArtifactDetail,
  CatalogChange,
  CatalogChangeRequest,
  CatalogEntrySummary,
  CatalogListing,
  OnboardingGitDrift,
} from "@/types";

/**
 * Typed API client for the ADF Pipeline Accelerator backend.
 *
 * Routes mirror AGENTS.md §14 and are served under the versioned prefix:
 *   /api/v1/auth /api/v1/teams /api/v1/projects /api/v1/pipelines
 *   /api/v1/templates /api/v1/artifacts /api/v1/git /api/v1/environments
 *   /api/v1/admin
 *
 * The API uses a consistent error envelope, so all failures surface as
 * ApiError with an actionable message. Collection endpoints are
 * paginated; these helpers request the first page of up to 100 items.
 */

/**
 * Defaults to "" (relative) so every request goes to this app's own origin
 * and is proxied server-side to the real backend by middleware.ts
 * (IMPLEMENTATION_PLAN.md gap B-3) — the same built image then
 * works unmodified across DEV/QA/PROD, since nothing environment-specific is
 * baked into the client bundle. Set `NEXT_PUBLIC_API_BASE_URL` only when the
 * browser must call the API origin directly instead of through the proxy
 * (e.g. no shared origin available) — that value IS inlined at build time
 * (Next.js `NEXT_PUBLIC_*` behavior), so a build carrying it is pinned to
 * that one environment.
 */
export const API_BASE_URL =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "")) || "";

/**
 * Dev-only per-request identity override (AGENTS.md §27.2/§27.6, §23.12 gap #16).
 *
 * The backend's `current_user()` (AUTH_MODE=mock only) resolves the caller to
 * whichever seeded user this header names, instead of always the first admin.
 * Persisted to localStorage so a chosen identity survives a page reload;
 * never sent when unset, and the backend ignores it outside mock mode anyway.
 */
const DEBUG_USER_STORAGE_KEY = "adf-forge:debug-user-id";
let debugUserId: string | null =
  typeof window !== "undefined" ? window.localStorage.getItem(DEBUG_USER_STORAGE_KEY) : null;

export function getDebugUserId(): string | null {
  return debugUserId;
}

export function setDebugUserId(id: string | null): void {
  debugUserId = id;
  if (typeof window === "undefined") return;
  if (id) window.localStorage.setItem(DEBUG_USER_STORAGE_KEY, id);
  else window.localStorage.removeItem(DEBUG_USER_STORAGE_KEY);
}

/**
 * AUTH_MODE=password's CSRF cookie (AGENTS.md §27.3, double-submit pattern):
 * the backend sets a non-httpOnly `adf_csrf` cookie on login; every
 * mutating request must echo it back as `X-CSRF-Token`. A no-op under
 * `mock`/no cookie present. The session cookie itself (`adf_session`) is
 * httpOnly and never read here — the browser attaches it automatically.
 */
const CSRF_COOKIE_NAME = "adf_csrf";
const CSRF_HEADER_NAME = "X-CSRF-Token";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function readCsrfCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${CSRF_COOKIE_NAME}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export class ApiError extends Error {
  readonly code: string;
  readonly details?: string;
  readonly correlationId?: string;
  readonly status: number;

  constructor(status: number, code: string, message: string, details?: string, correlationId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.correlationId = correlationId;
  }
}

/** §27 session UX — fired on a 401 from any non-auth endpoint so `useAuthGate` can bounce to the login screen. */
export const SESSION_EXPIRED_EVENT = "adf:session-expired";

// Every successful request slides the server's idle expiry forward (`deps.py`), so the time of the
// last one is the client's best estimate of when the session will idle out.
let lastApiActivityAt = Date.now();
/** Epoch ms of the last successful API response — feeds `useIdleExpiryWarning`. */
export function getLastApiActivityAt(): number {
  return lastApiActivityAt;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const csrfToken = UNSAFE_METHODS.has(method) ? readCsrfCookie() : null;
  // A `FormData` body (the §33.9 sttm-import upload, so far the only file
  // upload in this app) must NOT get an explicit `Content-Type` — the
  // browser sets `multipart/form-data; boundary=...` itself, and forcing
  // `application/json` here would make the backend fail to parse it.
  const isFormData = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    // Required for AUTH_MODE=password's cookie session (§27.3) — the
    // `adf_session`/`adf_csrf` cookies are otherwise never sent. A no-op
    // under `mock` (no cookies are ever set in that mode).
    credentials: "include",
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(debugUserId ? { "X-Debug-User-Id": debugUserId } : {}),
      ...(csrfToken ? { [CSRF_HEADER_NAME]: csrfToken } : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/api/v1/auth/") && typeof window !== "undefined") {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    let envelope: ApiErrorEnvelope | undefined;
    try {
      envelope = (await response.json()) as ApiErrorEnvelope;
    } catch {
      // non-JSON error body
    }
    throw new ApiError(
      response.status,
      envelope?.error.code ?? "http_error",
      envelope?.error.message ?? `Request to ${path} failed with status ${response.status}.`,
      envelope?.error.details,
      envelope?.error.correlation_id,
    );
  }

  lastApiActivityAt = Date.now();
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function resourcePath(base: string, id?: string): string {
  return id ? `${base}/${encodeURIComponent(id)}` : base;
}

export const api = {
  auth: {
    /** Public, unauthenticated — which login UI to render (§27.2 extension). */
    config: () => request<AuthConfig>("/api/v1/auth/config"),
    me: () => request<User>("/api/v1/auth/me"),
    /** AUTH_MODE=password only — 404s otherwise. `username` also accepts an email. */
    login: (username: string, password: string) =>
      request<User>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
    logout: () => request<void>("/api/v1/auth/logout", { method: "POST" }),
    changePassword: (currentPassword: string, newPassword: string) =>
      request<User>("/api/v1/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ currentPassword, newPassword }),
      }),
    /**
     * Dev-only user-switcher (§27.2/§27.6, gap #16) — 404s unless the backend
     * is running `AUTH_MODE=mock`. `dev.users` is the picker list; `dev.switchUser`
     * validates the target id, then the caller persists it via `setDebugUserId`
     * so it's sent back as `X-Debug-User-Id` on every subsequent request.
     */
    dev: {
      users: () => request<User[]>("/api/v1/auth/dev/users"),
      switchUser: (userId: string) =>
        request<User>("/api/v1/auth/dev/switch-user", {
          method: "POST",
          body: JSON.stringify({ userId }),
        }),
    },
  },

  teams: {
    list: () => request<Team[]>("/api/v1/teams"),
    /** PLATFORM_ADMIN only server-side (§11) — platform-admin team onboarding. */
    create: (body: { name: string; description?: string }) =>
      request<Team>("/api/v1/teams", { method: "POST", body: JSON.stringify(body) }),
    details: (id: string) => request<Team>(resourcePath("/api/v1/teams", id)),
    members: (id: string) => request<User[]>(`/api/v1/teams/${encodeURIComponent(id)}/members`),
  },

  projects: {
    list: () => request<Project[]>("/api/v1/projects"),
    /** PLATFORM_ADMIN only server-side (§11) — platform-admin project onboarding. */
    create: (body: { teamId: string; name: string; description?: string }) =>
      request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify(body) }),
    details: (id: string) => request<Project>(resourcePath("/api/v1/projects", id)),
    /** Full settings aggregate (§5) — includes `namingConvention` (§25 naming standards). */
    settings: (id: string) =>
      request<ProjectSettings>(`/api/v1/projects/${encodeURIComponent(id)}/settings`),
    /** Replaces the whole settings document — callers must send the full aggregate, not a patch. */
    updateSettings: (id: string, document: ProjectSettings) =>
      request<ProjectSettings>(`/api/v1/projects/${encodeURIComponent(id)}/settings`, {
        method: "PUT",
        body: JSON.stringify(document),
      }),
  },

  pipelines: {
    list: (projectId?: string) =>
      request<PipelineDefinition[]>(
        projectId ? `/api/v1/pipelines?project_id=${encodeURIComponent(projectId)}` : "/api/v1/pipelines",
      ),
    /** Persist a pipeline draft (§7, §16) — the Build wizard's "Create pipeline" step. */
    create: (body: PipelineCreateRequestBody) =>
      request<PipelineDefinition>("/api/v1/pipelines", { method: "POST", body: JSON.stringify(body) }),
    /** Edit an existing pipeline — rename it, or replace its saved draft (append objects, rename
     * the master/child artifact names). Backs the Build wizard's edit mode. */
    update: (id: string, body: PipelineUpdateRequestBody) =>
      request<PipelineDefinition>(resourcePath("/api/v1/pipelines", id), { method: "PUT", body: JSON.stringify(body) }),
    details: (id: string) => request<PipelineDefinition>(resourcePath("/api/v1/pipelines", id)),
    generate: (id: string) =>
      request<PipelineDefinition>(`/api/v1/pipelines/${encodeURIComponent(id)}/generate`, { method: "POST" }),
    validate: (id: string) =>
      request<PipelineDefinition>(`/api/v1/pipelines/${encodeURIComponent(id)}/validate`, { method: "POST" }),
    /** Latest validate job's result, or a `pending` stub if none has run yet. */
    validationResult: (id: string) =>
      request<ValidationResult>(`/api/v1/pipelines/${encodeURIComponent(id)}/validation`),
    commit: (id: string) =>
      request<PipelineDefinition>(`/api/v1/pipelines/${encodeURIComponent(id)}/commit`, { method: "POST" }),
  },

  templates: {
    list: () => request<PipelineTemplate[]>("/api/v1/templates"),
    details: (id: string) => request<PipelineTemplate>(resourcePath("/api/v1/templates", id)),
  },

  artifacts: {
    list: (pipelineId?: string) =>
      request<ArtifactVersion[]>(
        pipelineId ? `/api/v1/artifacts?pipeline_id=${encodeURIComponent(pipelineId)}` : "/api/v1/artifacts",
      ),
    details: (id: string) => request<ArtifactVersion>(resourcePath("/api/v1/artifacts", id)),
    diff: (id: string) => request<{ additions: number; deletions: number }>(`/api/v1/artifacts/${encodeURIComponent(id)}/diff`),
  },

  environments: {
    list: () => request<Environment[]>("/api/v1/environments"),
    /** Create a new environment's connection config (§23.12 gap 12, IMPLEMENTATION_PLAN.md A-4). */
    create: (body: EnvironmentConnection & { label: string; status: string; description: string }) =>
      request<Environment>("/api/v1/environments", { method: "POST", body: JSON.stringify(body) }),
    /** Replace an environment's connection config by name (DEV | QA | PROD). */
    update: (name: string, body: EnvironmentConnection & { label: string; status: string; description: string }) =>
      request<Environment>(`/api/v1/environments/${encodeURIComponent(name)}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    remove: (name: string) =>
      request<void>(`/api/v1/environments/${encodeURIComponent(name)}`, { method: "DELETE" }),
  },

  /** §32.6 — user-assigned managed identities, team-scoped server-side (MI3). */
  managedIdentities: {
    list: () => request<ManagedIdentity[]>("/api/v1/managed-identities"),
    /** Register a user-assigned identity (TEAM_ADMIN+, scoped to the caller's team server-side, §32.8). */
    create: (body: { name: string; resourceId: string; clientId?: string | null }) =>
      request<ManagedIdentity>("/api/v1/managed-identities", { method: "POST", body: JSON.stringify(body) }),
    remove: (id: string) =>
      request<void>(`/api/v1/managed-identities/${encodeURIComponent(id)}`, { method: "DELETE" }),
  },

  connections: {
    /** Saved connections for a project (§24.17 `connection_library`) — the metastore-backed mirror of `lib/adf/connection-store.ts`. */
    list: (projectId?: string) =>
      request<ConnectionRecord[]>(
        projectId ? `/api/v1/connections?project_id=${encodeURIComponent(projectId)}` : "/api/v1/connections",
      ),
    create: (body: {
      projectId: string;
      name: string;
      role: string;
      connectorType: string;
      description: string;
      draft: Record<string, unknown>;
    }) => request<ConnectionRecord>("/api/v1/connections", { method: "POST", body: JSON.stringify(body) }),
    update: (
      id: string,
      body: Partial<{
        name: string;
        role: string;
        connectorType: string;
        description: string;
        draft: Record<string, unknown>;
      }>,
    ) => request<ConnectionRecord>(resourcePath("/api/v1/connections", id), { method: "PUT", body: JSON.stringify(body) }),
    remove: (id: string) => request<void>(resourcePath("/api/v1/connections", id), { method: "DELETE" }),
  },

  /** Static connector-registry helpers that back a downloadable template / upload-parse round trip (§24.17, §26). Kept here for parity/other callers — the frontend generates and parses these client-side too (`lib/adf/connections.ts`) for zero-latency downloads. */
  connectorTemplates: {
    sourceObjectsTemplateUrl: () => `${API_BASE_URL}/api/v1/connectors/templates/source-objects`,
    parseSourceObjects: (content: string, fileName: string) =>
      request<{ objects: unknown[]; warnings: string[] }>("/api/v1/connectors/templates/source-objects/parse", {
        method: "POST",
        body: JSON.stringify({ content, fileName }),
      }),
    linkedServiceTemplateUrl: (connectorType: string, authType?: string) =>
      `${API_BASE_URL}/api/v1/connectors/${encodeURIComponent(connectorType)}/linked-service-template${
        authType ? `?auth_type=${encodeURIComponent(authType)}` : ""
      }`,
    parseLinkedService: (content: string, fileName: string) =>
      request<{ values: Record<string, unknown> | null; warnings: string[] }>("/api/v1/connectors/linked-service/parse", {
        method: "POST",
        body: JSON.stringify({ content, fileName }),
      }),
  },

  /** §33 STTM-Based Silver/Gold Object Registry — `layer` is "silver" or "mart" (the DB table name),
   *  matching the backend's `/projects/{id}/{silver,mart}-objects` path segments. */
  registry: {
    list: (
      layer: SttmLayer,
      projectId: string,
      filters?: { tag?: string; domain?: string; status?: string; sourceSystem?: string; q?: string },
    ) => {
      const params = new URLSearchParams();
      if (filters?.tag) params.set("tag", filters.tag);
      if (filters?.domain) params.set("domain", filters.domain);
      if (filters?.status) params.set("status", filters.status);
      if (filters?.sourceSystem) params.set("source_system", filters.sourceSystem);
      if (filters?.q) params.set("q", filters.q);
      const qs = params.toString();
      return request<RegistryObject[]>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${layer}-objects${qs ? `?${qs}` : ""}`,
      );
    },
    get: (layer: SttmLayer, projectId: string, objectKey: string) =>
      request<RegistryObject>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${layer}-objects/${encodeURIComponent(objectKey)}`,
      ),
    create: (layer: SttmLayer, projectId: string, body: RegistryObjectInput) =>
      request<RegistryObject>(`/api/v1/projects/${encodeURIComponent(projectId)}/${layer}-objects`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (layer: SttmLayer, projectId: string, objectKey: string, body: RegistryObjectInput & { expectedVersion: number }) =>
      request<RegistryObject>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${layer}-objects/${encodeURIComponent(objectKey)}`,
        { method: "PUT", body: JSON.stringify(body) },
      ),
    remove: (layer: SttmLayer, projectId: string, objectKey: string, hard = false) =>
      request<void>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${layer}-objects/${encodeURIComponent(objectKey)}${hard ? "?hard=true" : ""}`,
        { method: "DELETE" },
      ),
    /** Server-side authoritative re-parse of an uploaded `.xlsx` (§33.9) — the client-side
     *  `lib/adf/sttm.ts` parse is preview-only; this call is what actually gets committed. */
    sttmImport: (layer: SttmLayer, projectId: string, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<SttmImportRecord>(`/api/v1/projects/${encodeURIComponent(projectId)}/${layer}-objects/sttm-import`, {
        method: "POST",
        body: form,
      });
    },
    sttmImportCommit: (
      layer: SttmLayer,
      projectId: string,
      importId: string,
      body: { sheetNames?: string[]; confirmOverwrite?: boolean; expectedVersions?: Record<string, number> },
    ) =>
      request<{ committed: string[]; skipped: { sheetName: string; reason: string }[]; import: SttmImportRecord }>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${layer}-objects/sttm-import/${encodeURIComponent(importId)}/commit`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    sttmImports: (projectId: string) =>
      request<SttmImportRecord[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/sttm-imports`),
  },

  /** §26 Bronze Source Onboarding Registry — snake_case wire format (not this
   *  file's usual camelCase) since `OnboardingObjectEntry`'s field names
   *  double as CSV template headers (§26.3); see `backend/app/api/schemas.py`'s
   *  `OnboardingObjectOut` for the matching deviation server-side. */
  onboarding: {
    list: (projectId: string, filters?: { status?: string; sourceSystem?: string; q?: string }) => {
      const params = new URLSearchParams();
      if (filters?.status) params.set("status", filters.status);
      if (filters?.sourceSystem) params.set("source_system", filters.sourceSystem);
      if (filters?.q) params.set("q", filters.q);
      const qs = params.toString();
      return request<OnboardingObjectEntry[]>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/objects${qs ? `?${qs}` : ""}`,
      );
    },
    create: (projectId: string, body: OnboardingObjectEntry) =>
      request<OnboardingObjectEntry>(`/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/objects`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (projectId: string, objectKey: string, body: Partial<OnboardingObjectEntry>) =>
      request<OnboardingObjectEntry>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/objects/${encodeURIComponent(objectKey)}`,
        { method: "PUT", body: JSON.stringify(body) },
      ),
    remove: (projectId: string, objectKey: string, hard = false) =>
      request<void>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/objects/${encodeURIComponent(objectKey)}${hard ? "?hard=true" : ""}`,
        { method: "DELETE" },
      ),
    /** `fileName` and the applied §26.3 mapping profile are recorded on the §26.8 `onboarding_imports` audit row. */
    bulkUpsert: (projectId: string, objects: OnboardingObjectEntry[], fileName?: string, mappingProfileId?: string) =>
      request<{ added: number; updated: number }>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/objects/bulk`,
        { method: "POST", body: JSON.stringify({ objects, file_name: fileName || undefined, mapping_profile_id: mappingProfileId }) },
      ),
    /** §30.5 `app.dq_rules` authored on one Bronze object (camelCase, unlike the object rows). */
    dqRules: {
      list: (projectId: string, objectKey: string) =>
        request<OnboardingDqRule[]>(
          `/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/objects/${encodeURIComponent(objectKey)}/dq-rules`,
        ),
      create: (
        projectId: string,
        objectKey: string,
        body: Pick<OnboardingDqRule, "checkType" | "config" | "enforcement">,
      ) =>
        request<OnboardingDqRule>(
          `/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/objects/${encodeURIComponent(objectKey)}/dq-rules`,
          { method: "POST", body: JSON.stringify(body) },
        ),
      remove: (projectId: string, objectKey: string, ruleId: string) =>
        request<void>(
          `/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/objects/${encodeURIComponent(objectKey)}/dq-rules/${encodeURIComponent(ruleId)}`,
          { method: "DELETE" },
        ),
    },
    /** §26 `app.onboarding_imports` — one row per bulk upload commit, newest first. */
    imports: (projectId: string) =>
      request<OnboardingImportRecord[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/imports`),
    /** §26.3/§26.7 saved Map-step profiles; `save` upserts by case-insensitive name. */
    mappingProfiles: {
      list: (projectId: string) =>
        request<OnboardingMappingProfile[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/mapping-profiles`),
      save: (projectId: string, body: { name: string; headerMap: Record<string, string> }) =>
        request<OnboardingMappingProfile>(`/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/mapping-profiles`, {
          method: "POST",
          body: JSON.stringify(body),
        }),
      remove: (projectId: string, profileId: string) =>
        request<void>(
          `/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/mapping-profiles/${encodeURIComponent(profileId)}`,
          { method: "DELETE" },
        ),
    },
    /** §26.5 Sync to Git — control-table SQL + objects.csv (+ lineage) to a feature branch, PR into the base. */
    syncToGit: (projectId: string, body: OnboardingSyncRequest) =>
      request<OnboardingSync>(`/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/sync-to-git`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    syncs: (projectId: string) =>
      request<OnboardingSync[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/syncs`),
    syncDiff: (projectId: string) =>
      request<OnboardingSyncDiff>(`/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/sync-diff`),
    /** §26.6 registry vs. `metadata/objects.csv` as last indexed from Git (refresh the catalog first). */
    gitDrift: (projectId: string, branch?: string) =>
      request<OnboardingGitDrift>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/onboarding/git-drift${branch ? `?branch=${encodeURIComponent(branch)}` : ""}`,
      ),
  },

  /** §24.8 Git-backed catalog index + §24.9/§24.10 update / rename / delete PRs (worker reads/writes Git). */
  catalog: {
    list: (projectId: string, query: { branch?: string; type?: string; q?: string } = {}) => {
      const params = new URLSearchParams(Object.entries(query).filter(([, v]) => v) as [string, string][]);
      const qs = params.toString();
      return request<CatalogListing>(`/api/v1/projects/${encodeURIComponent(projectId)}/catalog${qs ? `?${qs}` : ""}`);
    },
    refresh: (projectId: string, body: { branch?: string; gitToken?: string } = {}) =>
      request<IntegrationJob>(`/api/v1/projects/${encodeURIComponent(projectId)}/catalog/refresh`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    artifact: (projectId: string, path: string, branch?: string) =>
      request<CatalogArtifactDetail>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/catalog/artifact?${new URLSearchParams({ path, ...(branch ? { branch } : {}) })}`,
      ),
    impact: (projectId: string, path: string, branch?: string) =>
      request<{ path: string; branch: string; inbound: CatalogEntrySummary[] }>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/catalog/impact?${new URLSearchParams({ path, ...(branch ? { branch } : {}) })}`,
      ),
    change: (projectId: string, body: CatalogChangeRequest) =>
      request<CatalogChange>(`/api/v1/projects/${encodeURIComponent(projectId)}/catalog/changes`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    changes: (projectId: string) =>
      request<CatalogChange[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/catalog/changes`),
  },

  /** Per-project §29.1 layer-6 live ARM check + Purview lineage push (worker makes the Azure calls). */
  integrations: {
    get: (projectId: string, kind: IntegrationKind) =>
      request<ProjectIntegration>(`/api/v1/projects/${encodeURIComponent(projectId)}/integrations/${kind}`),
    save: (projectId: string, kind: IntegrationKind, body: { enabled: boolean; config: Record<string, string> }) =>
      request<ProjectIntegration>(`/api/v1/projects/${encodeURIComponent(projectId)}/integrations/${kind}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    liveCheck: (projectId: string, pipelineId: string) =>
      request<IntegrationJob>(`/api/v1/projects/${encodeURIComponent(projectId)}/validate/live-check`, {
        method: "POST",
        body: JSON.stringify({ pipelineId }),
      }),
    purviewPush: (projectId: string, lineage: unknown) =>
      request<IntegrationJob>(`/api/v1/projects/${encodeURIComponent(projectId)}/lineage/purview-push`, {
        method: "POST",
        body: JSON.stringify({ lineage }),
      }),
    job: (projectId: string, jobId: string) =>
      request<IntegrationJob>(`/api/v1/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobId)}`),
  },

  git: {
    /**
     * Push selected artifacts to a feature branch (and optionally open a PR).
     * `body.gitToken`, when set, is a per-push credential — the backend forwards
     * it to the worker for that provider call only and never persists it.
     */
    push: (body: GitPushRequestBody) =>
      request<GitPushResponse>("/api/v1/git/push", { method: "POST", body: JSON.stringify(body) }),
    /** Poll the async push job's current state (§9, §23.6 — push is queued, not synchronous). */
    get: (id: string) => request<GitPushResponse>(resourcePath("/api/v1/git/push", id)),
  },

  admin: {
    users: () => request<User[]>("/api/v1/admin/users"),
    audit: () => request<AuditEntry[]>("/api/v1/admin/audit"),
    /** §27 session admin — live sessions (team-scoped for TEAM_ADMIN); empty under AUTH_MODE=mock. */
    sessions: () => request<AdminSession[]>("/api/v1/admin/sessions"),
    revokeSession: (handle: string) =>
      request<void>(`/api/v1/admin/sessions/${encodeURIComponent(handle)}`, { method: "DELETE" }),
    /** §27.5 SSO provider config — PLATFORM_ADMIN only; stored metadata, not yet used to sign in. */
    ssoConfigurations: () => request<SsoConfiguration[]>("/api/v1/admin/sso-configurations"),
    createSsoConfiguration: (body: SsoConfigurationInput) =>
      request<SsoConfiguration>("/api/v1/admin/sso-configurations", { method: "POST", body: JSON.stringify(body) }),
    updateSsoConfiguration: (id: string, body: SsoConfigurationInput) =>
      request<SsoConfiguration>(resourcePath("/api/v1/admin/sso-configurations", id), { method: "PUT", body: JSON.stringify(body) }),
    deleteSsoConfiguration: (id: string) =>
      request<void>(resourcePath("/api/v1/admin/sso-configurations", id), { method: "DELETE" }),
    /**
     * User onboarding (§11/§27 extension) — PLATFORM_ADMIN only; no
     * self-signup route exists anywhere. `username` defaults server-side to the
     * email's local part. With no `password`, returns a one-time `tempPassword`
     * (this app has no email sender) the caller must show the admin exactly
     * once — it is never retrievable again after this response; with one,
     * `tempPassword` is null. Either way the user must change it on first login.
     */
    createUser: (body: {
      name: string;
      email: string;
      username?: string;
      password?: string;
      role: string;
      teamId?: string | null;
    }) =>
      request<User & { tempPassword: string | null }>("/api/v1/admin/users", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    /** PLATFORM_ADMIN unrestricted; TEAM_ADMIN scoped server-side to their own team. */
    updateUser: (id: string, body: Partial<{ name: string; role: string; teamId: string; active: boolean }>) =>
      request<User>(`/api/v1/admin/users/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    /** New one-time temp password + revokes every existing session for that user. */
    resetPassword: (id: string) =>
      request<{ tempPassword: string }>(`/api/v1/admin/users/${encodeURIComponent(id)}/reset-password`, {
        method: "POST",
      }),
  },
};