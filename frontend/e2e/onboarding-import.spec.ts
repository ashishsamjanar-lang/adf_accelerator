import { expect, test } from "@playwright/test";
import { E2E_PROJECT, stubApi } from "./support/api-stub";

/**
 * Onboarding journey (§26.3): paste a CSV into the Bronze import panel → preview → commit, which must
 * bulk-upsert the parsed rows for the selected project and then show them in the registry grid.
 */
test("Onboarding CSV import → preview → commit", async ({ page }) => {
  const api = await stubApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: /^Onboarding/ }).first().click();

  await page.getByRole("button", { name: /CSV \/ Excel import/ }).click();
  await page
    .locator("textarea")
    .first()
    .fill("source_system,source_object,target_type,target_file_format,loading_behavior\nerp,customer,table,Parquet,FullLoad");
  await expect(page.getByText(/1 object found/)).toBeVisible();

  // Commit lands back on the manual grid, reloaded from the (stubbed) registry.
  await page.getByRole("button", { name: /Add 1 object to registry/ }).click();
  await expect(page.getByRole("cell", { name: "customer" }).first()).toBeVisible();

  const bulk = api.calls.find((c) => c.method === "POST" && c.path === `/projects/${E2E_PROJECT.id}/onboarding/objects/bulk`);
  expect(bulk?.body).toMatchObject({
    objects: [{ source_system: "erp", source_object: "customer", loading_behavior: "FullLoad" }],
  });
});
