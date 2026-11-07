import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { expect, type Locator, type Page } from "@playwright/test";
import type { ArtifactVersion, User } from "@/types";
import { E2E_PROJECT, E2E_USER, stubApi, type ApiStub } from "./api-stub";

/**
 * Healthcare scenario harness (`healthcare-scenarios.spec.ts`). Layers on top of `stubApi`: the
 * pipeline routes (create/update/generate/validate/validation, the Drafts list and the Catalog's
 * artifact list) are answered by the *real* worker generator + validator through `worker_bridge.py`,
 * so the validation panel the UI shows is the worker's actual verdict, not a canned "passed".
 */

export const HC_OUT = path.resolve(process.env.HC_OUT ?? "test-results/healthcare");
const BRIDGE = path.resolve("e2e/support/worker_bridge.py");
const PYTHON = process.env.PYTHON ?? "python";

export interface WorkerIssue {
  severity: string;
  message: string;
  layer?: string;
  ruleId?: string;
  source?: string;
}

export interface WorkerResult {
  generated: boolean;
  draftIssues: WorkerIssue[];
  artifacts: Array<{ fileName: string; fileType: string; artifactType: string; layer: string; bytes: number }>;
  validation: { state: string; issues: WorkerIssue[] } | null;
}

export interface HealthcareApi extends ApiStub {
  /** The last draft the UI saved (POST/PUT /pipelines). */
  draft(): Record<string, unknown> | null;
  /** The worker's verdict on that draft, once Generate ran. */
  worker(): WorkerResult | null;
}

export function runWorker(draft: unknown, outDir?: string): WorkerResult {
  const args = [BRIDGE, ...(outDir ? [outDir] : [])];
  const stdout = execFileSync(PYTHON, args, { input: JSON.stringify(draft), encoding: "utf-8", maxBuffer: 32 * 1024 * 1024 });
  return JSON.parse(stdout) as WorkerResult;
}

export const HC_ADMIN: User = { ...E2E_USER, id: "u-admin", name: "Priya Admin", email: "priya.admin@stmarys-health.org", role: "PLATFORM_ADMIN" };

export async function healthcareApi(
  page: Page,
  slug: string,
  options: {
    user?: User;
    seedDraft?: Record<string, unknown>;
    /** A running `backend_bridge.py` — the project's Onboarding/STTM/registry routes are proxied to it. */
    backendUrl?: string;
  } = {},
): Promise<HealthcareApi> {
  const base = await stubApi(page);
  const outDir = path.join(HC_OUT, slug);
  let pipeline: Record<string, unknown> | null = null;
  let result: WorkerResult | null = null;
  if (options.seedDraft) {
    // An already generated + validated pipeline, so Drafts/Catalog/Git have real rows to act on.
    const now = new Date().toISOString();
    pipeline = {
      id: "pl-e2e-1", name: options.seedDraft.name, type: options.seedDraft.templateId, projectId: E2E_PROJECT.id,
      projectName: E2E_PROJECT.name, teamId: E2E_PROJECT.teamId, teamName: "Team A", specVersion: "2.1", state: "VALIDATED",
      createdAt: now, updatedAt: now, createdBy: E2E_USER.id, draft: options.seedDraft,
    };
    result = runWorker(options.seedDraft);
  }

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const method = request.method();
    const p = new URL(request.url()).pathname.replace(/^\/api\/v1/, "");
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    const isJson = (request.headers()["content-type"] ?? "").includes("application/json");
    const body = isJson && request.postData() ? (JSON.parse(request.postData()!) as Record<string, unknown>) : null;
    // The base stub records calls; record overlay-answered ones too so a spec sees the whole chain.
    const record = () => base.calls.push({ method, path: p, body });

    if (options.user && p === "/auth/me") return json(options.user);
    // Real parser, upstream-key resolution and registry tables (healthcare-medallion.spec.ts).
    if (options.backendUrl && /^\/projects\/[^/]+\/(onboarding|silver-objects|mart-objects)(\/|$)/.test(p)) {
      record();
      const response = await route.fetch({ url: `${options.backendUrl}/api/v1${p}${new URL(request.url()).search}` });
      return route.fulfill({ response });
    }
    // Object-shaped responses the base stub's catch-all `[]` would get wrong (an array has an
    // `.entries` *method*, which defeats the panel's `?? []` fallback) — mirror the real backend's
    // "nothing configured / nothing indexed yet" answers instead.
    const integration = p.match(/^\/projects\/([^/]+)\/integrations\/([^/]+)$/);
    if (integration && method === "GET") {
      return json({ projectId: integration[1], kind: integration[2], enabled: false, config: {}, updatedBy: null, updatedAt: null });
    }
    if (/^\/projects\/[^/]+\/catalog$/.test(p) && method === "GET") {
      return json({ branch: "dev", baseBranch: "dev", branches: ["dev"], indexedAt: null, simulated: true, entries: [] });
    }
    if (p === "/pipelines" && method === "POST") {
      record();
      const now = new Date().toISOString();
      pipeline = {
        id: "pl-e2e-1", name: body!.name, type: body!.templateId, projectId: body!.projectId, projectName: E2E_PROJECT.name,
        teamId: E2E_PROJECT.teamId, teamName: "Team A", specVersion: "2.1", state: "DRAFT", createdAt: now, updatedAt: now,
        createdBy: E2E_USER.id, draft: body!.draft,
      };
      return json(pipeline, 201);
    }
    if (p === "/pipelines" && method === "GET") return json(pipeline ? [pipeline] : []);
    if (p === "/pipelines/pl-e2e-1" && method === "PUT" && pipeline) {
      record();
      pipeline = { ...pipeline, ...body, draft: body!.draft ?? pipeline.draft, state: "DRAFT", updatedAt: new Date().toISOString() };
      return json(pipeline);
    }
    if (p === "/pipelines/pl-e2e-1" && method === "GET" && pipeline) return json(pipeline);
    const action = p.match(/^\/pipelines\/pl-e2e-1\/(generate|validate|validation)$/);
    if (action && pipeline) {
      record();
      if (action[1] === "generate") {
        mkdirSync(outDir, { recursive: true });
        result = runWorker(pipeline.draft, outDir);
        writeFileSync(path.join(outDir, "_draft.json"), JSON.stringify(pipeline.draft, null, 2));
        writeFileSync(path.join(outDir, "_worker_result.json"), JSON.stringify(result, null, 2));
        pipeline = { ...pipeline, state: result.generated ? "GENERATED" : "GENERATION_FAILED" };
        return json(pipeline);
      }
      if (action[1] === "validate") {
        const ok = result?.validation?.state === "passed";
        pipeline = { ...pipeline, state: ok ? "VALIDATED" : "VALIDATION_FAILED" };
        return json(pipeline);
      }
      return json({
        pipelineId: "pl-e2e-1",
        state: result?.validation?.state ?? "failed",
        issues: result?.validation?.issues ?? result?.draftIssues ?? [],
        validatedAt: new Date().toISOString(),
        correlationId: `hc-${slug}`,
      });
    }
    if (p === "/artifacts" && method === "GET" && pipeline && result) {
      const artifacts: ArtifactVersion[] = result.artifacts.map((a, i) => ({
        id: `art-${i + 1}`, pipelineId: "pl-e2e-1", pipelineName: String(pipeline!.name), fileName: a.fileName,
        fileType: a.fileType as ArtifactVersion["fileType"], artifactType: a.artifactType as ArtifactVersion["artifactType"],
        layer: a.layer as ArtifactVersion["layer"], version: "v1.0.0",
        state: result!.validation?.state === "passed" ? "validated" : "generated", bytes: a.bytes,
      }));
      return json(artifacts);
    }
    return route.fallback();
  });

  return Object.assign(base, { draft: () => (pipeline?.draft as Record<string, unknown>) ?? null, worker: () => result });
}

// ── Form helpers ─────────────────────────────────────────────────────────────────────────────────

function xq(text: string): string {
  return text.includes("'") ? `concat('${text.split("'").join(`', "'", '`)}')` : `'${text}'`;
}

/** A `<label><span>Label</span><input|select|textarea/></label>` control, matched on the exact span text. */
export function labelled(scope: Page | Locator, label: string): Locator {
  return scope.locator(`xpath=.//label[span[normalize-space(.)=${xq(label)}]]`).locator("input, select, textarea").first();
}

/** A connector-schema `.field-cell` control (linked service / dataset designers), matched on its label. */
export function fieldCell(scope: Page | Locator, label: string): Locator {
  return scope
    .locator(`xpath=.//div[contains(concat(' ', normalize-space(@class), ' '), ' field-cell ')][div[contains(@class,'field-label')]/span[normalize-space(.)=${xq(label)}]]`)
    .locator("input, select")
    .first();
}

/** The `.form-card` whose title is `title` (one of the wizard's section cards). */
export function card(page: Page, title: string): Locator {
  return page.locator(`xpath=//div[contains(@class,'form-card')][.//h3[normalize-space(.)=${xq(title)}]]`).first();
}

export interface ConnectorFill {
  connector: string;
  fields: Record<string, string>;
}

/** Pick a connector in the step's linked service designer and fill its fields by label. */
export async function fillLinkedService(page: Page, ls: ConnectorFill) {
  const connector = fieldCell(page, "Connector");
  if ((await connector.inputValue()) !== ls.connector) await connector.selectOption(ls.connector);
  for (const [label, value] of Object.entries(ls.fields)) {
    const control = fieldCell(page, label);
    if ((await control.evaluate((el) => el.tagName)) === "SELECT") await control.selectOption(value);
    else await control.fill(value);
  }
}

/** Click the step's Continue and wait for `nextStep`; on a validation block, fail with the shown errors. */
export async function continueTo(page: Page, nextStep: string) {
  await page.getByRole("button", { name: /^Continue$/ }).last().click();
  const next = page.getByText(nextStep);
  try {
    await expect(next).toBeVisible({ timeout: 8_000 });
  } catch {
    const errors = await page.locator("main .text-red, main .error-list li, main .upload-warning").allInnerTexts();
    throw new Error(`Wizard did not advance to "${nextStep}". Visible errors:\n- ${errors.join("\n- ") || "(none)"}`);
  }
}

export async function selectProject(page: Page) {
  await page
    .locator("main select")
    .filter({ has: page.locator("option", { hasText: E2E_PROJECT.name }) })
    .first()
    .selectOption(E2E_PROJECT.id);
}
