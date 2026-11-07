import { expect, test } from "@playwright/test";
import { E2E_PROJECT, stubApi } from "./support/api-stub";

/**
 * Browser happy path (IMPLEMENTATION_PLAN.md "No browser E2E"): Build → fill every step → Generate the
 * artifact preview → Create pipeline, which must persist, generate and validate server-side and show
 * the inline validation result (§29). The backend is stubbed in-browser (`support/api-stub.ts`).
 */
test("Build → Generate → Validate happy path", async ({ page }) => {
  const api = await stubApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: /^Build/ }).first().click();

  // Step 1 — Basics.
  await expect(page.getByText("Step 1 of 5 · Basics")).toBeVisible();
  await page.getByPlaceholder("e.g. customer_metadata_load").fill("customer_orders_load");
  await page
    .locator("main select")
    .filter({ has: page.locator("option", { hasText: E2E_PROJECT.name }) })
    .selectOption(E2E_PROJECT.id);
  await page.getByRole("button", { name: /^Continue$/ }).click();

  // Step 2 — Source: SQL Server linked service (secret is a Key Vault secret *name*, never a value).
  await expect(page.getByText("Step 2 of 5 · Source")).toBeVisible();
  await page.getByPlaceholder("sqlserver01.contoso.com").fill("sqlsrv01.database.windows.net");
  await page.getByPlaceholder("AdventureWorks").fill("customerdb");
  await page.getByPlaceholder("sa", { exact: true }).fill("svc_reader");
  await page.getByPlaceholder("sqlserver-password").fill("sqlserver-password");
  await page.getByRole("button", { name: /^Continue$/ }).last().click();

  // Step 3 — Target: ADLS Gen2 with an account-key secret name.
  await expect(page.getByText("Step 3 of 5 · Target")).toBeVisible();
  await page.getByPlaceholder("mystorageaccount", { exact: true }).fill("stgcustomer");
  await page.getByPlaceholder("https://mystorageaccount.dfs.core.windows.net").fill("https://stgcustomer.dfs.core.windows.net");
  await page.getByPlaceholder("storage-account-key").fill("storage-account-key");
  await page.getByRole("button", { name: /^Continue$/ }).last().click();

  // Step 4 — Pipeline Settings: defaults are valid as-is.
  await expect(page.getByText("Step 4 of 5 · Pipeline Settings")).toBeVisible();
  await page.getByRole("button", { name: /^Continue$/ }).last().click();

  // Step 5 — Review & Generate: client-side preview of the full artifact set.
  await expect(page.getByText("Step 5 of 5 · Review & Generate")).toBeVisible();
  await expect(page.getByText("Specification is valid. Generate the ADF artifacts to continue.")).toBeVisible();
  await page.getByRole("button", { name: /Generate pipeline code/ }).click();
  await expect(page.getByText("PL_customer_orders_load_master.json").first()).toBeVisible();
  await expect(page.getByText("PL_customer_orders_load_child.json").first()).toBeVisible();

  // Create → persist, generate and validate server-side, then the inline results screen.
  await page.getByRole("button", { name: /^Create pipeline$/ }).click();
  await expect(page.getByRole("heading", { name: "Validation results" })).toBeVisible();
  await expect(page.getByText("Validated", { exact: true })).toBeVisible();
  await expect(page.getByText("No issues found — this pipeline is ready for Push to Git.")).toBeVisible();

  const chain = api.calls.filter((c) => c.method !== "GET" || c.path.endsWith("/validation")).map((c) => `${c.method} ${c.path}`);
  expect(chain).toEqual([
    "POST /pipelines",
    "POST /pipelines/pl-e2e-1/generate",
    "POST /pipelines/pl-e2e-1/validate",
    "GET /pipelines/pl-e2e-1/validation",
  ]);
  const created = api.calls.find((c) => c.method === "POST" && c.path === "/pipelines")?.body as {
    name: string;
    projectId: string;
    draft: { sourceLinkedService: { fields: Record<string, string> } };
  };
  expect(created).toMatchObject({ name: "customer_orders_load", projectId: E2E_PROJECT.id });
  expect(created.draft.sourceLinkedService.fields.server).toBe("sqlsrv01.database.windows.net");
});
