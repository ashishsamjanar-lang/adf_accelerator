import type { Page, Request } from "@playwright/test";
import type { ArtifactVersion, AuthConfig, GitPushResponse, Project, RegistryObject, SttmImportRecord, User, ValidationResult } from "@/types";

/**
 * In-browser stand-in for the backend (`/api/v1/**`): the E2E specs drive the real Next.js app but
 * never need the API, Postgres, or worker running. Every request is recorded so a spec can assert
 * what the UI actually called; anything not stubbed below answers an empty list (GET) or `{}`.
 */
export interface ApiStub {
  calls: Array<{ method: string; path: string; body: unknown }>;
}

export const E2E_USER: User = {
  id: "u-dev",
  name: "Dana Developer",
  email: "dana@example.com",
  role: "DEVELOPER",
  teamId: "team-a",
  active: true,
};

export const E2E_PROJECT: Project = {
  id: "proj-a",
  teamId: "team-a",
  name: "Customer Analytics",
  description: "",
  pipelineCount: 0,
};

export const E2E_REPO_URL = "https://github.com/contoso/adf-artifacts";

/** Two validated artifacts of an already-generated pipeline — what the Catalog lists for the push journey. */
export const E2E_ARTIFACTS: ArtifactVersion[] = [
  { id: "art-1", pipelineId: "pl-e2e-1", pipelineName: "customer_orders_load", fileName: "pipeline/PL_customer_orders_load_master.json", fileType: "json", artifactType: "pipeline", layer: "pipeline", version: "v1.0.0", state: "validated", bytes: 120 },
  { id: "art-2", pipelineId: "pl-e2e-1", pipelineName: "customer_orders_load", fileName: "linkedService/LS_sql_source.json", fileType: "json", artifactType: "linked_service", layer: "connection", version: "v1.0.0", state: "validated", bytes: 80 },
];

/** What the server's authoritative STTM re-parse returns for the uploaded Silver workbook. */
export const E2E_STTM_IMPORT: SttmImportRecord = {
  id: "sttm-e2e-1", projectId: E2E_PROJECT.id, layer: "silver", fileName: "core_customer.xlsx", fileHash: "h", mode: "upsert",
  sheetTotal: 3, sheetValid: 2, sheetError: 1,
  sheets: [
    {
      sheetName: "core_customer", objectKey: "core_customer", curatedEntity: "customer", valid: true,
      issues: [{ severity: "warning", field: "transformations", message: "Filter_Condition was rewritten from ANSI/Spark SQL to T-SQL; review the filter step." }],
      parsed: {
        objectKey: "core_customer", tag: "sales", curatedEntity: "customer", sourceObjectKeys: ["crm.customer"], enabled: true,
        writeBehavior: "merge", loadingBehavior: "FullLoad",
        transformations: [{ name: "filter_rows", type: "filter", filterCondition: "email IS NOT NULL" }], dataQualityRules: [],
      },
    },
    { sheetName: "orphan", objectKey: "orphan", curatedEntity: "orphan", valid: true, issues: [], parsed: { objectKey: "orphan", tag: "sales", curatedEntity: "orphan", sourceObjectKeys: ["nowhere.x"] } },
    { sheetName: "broken", objectKey: null, curatedEntity: null, valid: false, issues: [{ severity: "error", field: "tag", message: "tag is required." }], parsed: null },
  ],
};

function bodyOf(request: Request): unknown {
  const raw = request.postData();
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

/** An already-onboarded Silver registry object, for seeding the Build registry picker. */
export const E2E_SILVER_OBJECT: RegistryObject = {
  ...(E2E_STTM_IMPORT.sheets[0].parsed as RegistryObject),
  projectId: E2E_PROJECT.id, version: 1, targetTable: "curated.customer", notes: "", status: "valid", provenanceSource: "upload", businessKeys: ["customer_id"],
};

export async function stubApi(
  page: Page,
  options: { artifacts?: ArtifactVersion[]; silver?: RegistryObject[] } = {},
): Promise<ApiStub> {
  const stub: ApiStub = { calls: [] };
  // The one pipeline this session "persists" — POST /pipelines creates it, later calls echo it back.
  let pipeline: Record<string, unknown> | null = null;
  // The Bronze onboarding registry this session "persists" — bulk upsert adds, the list echoes it.
  const onboarding: Array<Record<string, unknown>> = [];
  // The Silver registry this session "persists" — an STTM import commit adds its valid, resolvable sheets.
  const silver: RegistryObject[] = [...(options.silver ?? [])];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const method = request.method();
    const path = new URL(request.url()).pathname.replace(/^\/api\/v1/, "");
    const body = bodyOf(request);
    stub.calls.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });

    if (path === "/auth/config") return json({ authMode: "mock", idleTimeoutMinutes: null } satisfies AuthConfig);
    if (path === "/auth/me") return json(E2E_USER);
    if (path === "/projects" && method === "GET") return json([E2E_PROJECT]);
    if (path === "/pipelines" && method === "POST") {
      const b = body as { name: string; projectId: string; templateId: string; draft: unknown };
      const now = new Date().toISOString();
      pipeline = {
        id: "pl-e2e-1",
        name: b.name,
        type: b.templateId,
        projectId: b.projectId,
        projectName: E2E_PROJECT.name,
        teamId: E2E_PROJECT.teamId,
        teamName: "Team A",
        specVersion: "2.1",
        state: "DRAFT",
        createdAt: now,
        updatedAt: now,
        createdBy: E2E_USER.id,
        draft: b.draft,
      };
      return json(pipeline, 201);
    }
    if (path === "/artifacts" && method === "GET") return json(options.artifacts ?? []);
    if (path === "/pipelines/pl-e2e-1" && method === "GET") {
      return json(pipeline ?? { id: "pl-e2e-1", name: "customer_orders_load", projectId: E2E_PROJECT.id, state: "VALIDATED" });
    }
    if (path === `/projects/${E2E_PROJECT.id}/settings` && method === "GET") {
      return json({ projectId: E2E_PROJECT.id, gitDefaults: { provider: "github", repositoryUrl: E2E_REPO_URL, repository: "adf-artifacts", featureBranchPattern: "feature/{pipeline-name}", baseBranch: "dev", artifactRoot: "adf/", commitMessageConvention: "feat: {pipeline}", prTitleTemplate: "feat: {pipeline}", prBodyTemplate: "Generated artifacts" } });
    }
    // Push is an async job: POST queues it, the first poll already reports the PR (§9/§23.6).
    const pushed = (status: string): GitPushResponse => {
      const b = (stub.calls.find((c) => c.method === "POST" && c.path === "/git/push")?.body ?? {}) as { featureBranch?: string; baseBranch?: string };
      return {
        id: "push-e2e-1", pipelineId: "pl-e2e-1", provider: "github", repositoryUrl: E2E_REPO_URL,
        featureBranch: b.featureBranch ?? "", baseBranch: b.baseBranch ?? "dev", status,
        commitSha: status === "queued" ? null : "abc1234", simulated: false,
        pullRequest: status === "queued" ? null : { id: "pr-1", number: 42, url: `${E2E_REPO_URL}/pull/42`, title: "feat", sourceBranch: b.featureBranch ?? "", targetBranch: "dev", status: "open" },
      };
    };
    if (path === "/git/push" && method === "POST") return json(pushed("queued"), 202);
    if (path === "/git/push/push-e2e-1") return json(pushed("pr_created"));
    const registryPath = `/projects/${E2E_PROJECT.id}/onboarding/objects`;
    if (path === registryPath && method === "GET") return json(onboarding);
    if (path === `${registryPath}/bulk` && method === "POST") {
      const objects = (body as { objects: Array<Record<string, unknown>> }).objects;
      onboarding.push(...objects);
      return json({ added: objects.length, updated: 0 });
    }
    const silverPath = `/projects/${E2E_PROJECT.id}/silver-objects`;
    if (path === silverPath && method === "GET") return json(silver);
    if (path === `${silverPath}/sttm-import` && method === "POST") return json(E2E_STTM_IMPORT, 201);
    if (path === `${silverPath}/sttm-import/${E2E_STTM_IMPORT.id}/commit` && method === "POST") {
      // Mirrors the backend: `orphan` names an upstream key with no Bronze row, so commit skips it (E2E-10).
      const parsed = E2E_STTM_IMPORT.sheets[0].parsed!;
      silver.push({
        ...(parsed as RegistryObject),
        projectId: E2E_PROJECT.id, version: 1, targetTable: "curated.customer", notes: "", status: "valid", provenanceSource: "upload", businessKeys: [],
      });
      return json({
        committed: ["core_customer"],
        skipped: [{ sheetName: "orphan", reason: "Source object key(s) not found in this project's Bronze onboarding registry: nowhere.x." }],
        import: E2E_STTM_IMPORT,
      });
    }
    const action = path.match(/^\/pipelines\/([^/]+)\/(generate|validate|validation)$/);
    if (action && pipeline) {
      if (action[2] === "generate") return json((pipeline = { ...pipeline, state: "GENERATED" }));
      if (action[2] === "validate") return json((pipeline = { ...pipeline, state: "VALIDATED" }));
      return json({
        pipelineId: String(pipeline.id),
        state: "passed",
        issues: [],
        validatedAt: new Date().toISOString(),
        correlationId: "e2e-correlation",
      } satisfies ValidationResult);
    }
    return json(method === "GET" ? [] : {});
  });
  return stub;
}
