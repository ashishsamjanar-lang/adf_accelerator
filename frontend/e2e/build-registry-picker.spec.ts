import { expect, test } from "@playwright/test";
import { E2E_PROJECT, E2E_SILVER_OBJECT, stubApi } from "./support/api-stub";

/**
 * Build registry picker (§33.8, gaps.md E2E-03): a Silver pipeline picks an onboarded registry object,
 * which replaces the manual curated-entity input and fixes the computed target table; "Enter manually
 * instead" brings the input back, keeping the picked entity as its value.
 */
test("Build → Silver layer → pick a registry object", async ({ page }) => {
  const api = await stubApi(page, { silver: [E2E_SILVER_OBJECT] });
  await page.goto("/");
  await page.getByRole("button", { name: /^Build/ }).first().click();
  await expect(page.getByText("Step 1 of 5 · Basics")).toBeVisible();
  await page
    .locator("main select")
    .filter({ has: page.locator("option", { hasText: E2E_PROJECT.name }) })
    .selectOption(E2E_PROJECT.id);

  await page.locator("button.layer-card", { hasText: "Silver" }).click();
  const picker = page.locator("main select").filter({ has: page.locator("option", { hasText: "customer (sales)" }) });
  await picker.selectOption(E2E_SILVER_OBJECT.objectKey);

  await expect(page.getByText("Prefilled from the Silver registry")).toBeVisible();
  await expect(page.getByPlaceholder("e.g. gl_transaction")).toHaveCount(0);
  await expect(page.locator("code", { hasText: "curated.customer" })).toBeVisible();
  expect(api.calls.some((c) => c.method === "GET" && c.path === `/projects/${E2E_PROJECT.id}/silver-objects`)).toBe(true);

  await page.getByRole("button", { name: /Enter manually instead/ }).click();
  await expect(page.getByPlaceholder("e.g. gl_transaction")).toHaveValue("customer");
});
