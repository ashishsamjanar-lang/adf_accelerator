import { expect, test } from "@playwright/test";
import { E2E_PROJECT, E2E_STTM_IMPORT, stubApi } from "./support/api-stub";

/**
 * Silver STTM journey (§33.9, E2E-03): upload a workbook → server-parsed preview → commit only the valid
 * sheets, echoing their names, and land on the catalog with the committed object and the skipped reason.
 */
test("Silver STTM upload → preview → commit", async ({ page }) => {
  const api = await stubApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: /^Onboarding/ }).first().click();
  await page.getByRole("button", { name: "Silver", exact: true }).click();
  await page.getByRole("button", { name: /STTM upload/ }).click();

  await page.locator('input[type="file"]').setInputFiles({
    name: E2E_STTM_IMPORT.fileName,
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("stub workbook — the server parse is stubbed"),
  });
  await expect(page.getByText("2/3 sheets valid")).toBeVisible();
  await expect(page.getByText("1 warning")).toBeVisible();
  await expect(page.getByText("1 issue")).toBeVisible();

  // Only the two valid sheets are preselected; the invalid one can't be committed.
  await page.getByRole("button", { name: /Commit 2 objects/ }).click();
  await expect(page.getByText(/Committed 1 object; 1 skipped — orphan: Source object key\(s\) not found/)).toBeVisible();
  await expect(page.getByRole("cell", { name: "core_customer" })).toBeVisible();

  const commit = api.calls.find((c) => c.method === "POST" && c.path.endsWith(`/sttm-import/${E2E_STTM_IMPORT.id}/commit`));
  expect(commit?.path).toBe(`/projects/${E2E_PROJECT.id}/silver-objects/sttm-import/${E2E_STTM_IMPORT.id}/commit`);
  expect(commit?.body).toMatchObject({ sheetNames: ["core_customer", "orphan"] });
});
