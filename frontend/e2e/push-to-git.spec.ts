import { expect, test } from "@playwright/test";
import { E2E_ARTIFACTS, E2E_REPO_URL, stubApi } from "./support/api-stub";

/**
 * Push journey (§8/§9): Catalog → select a pipeline's validated artifacts → Push to Git, prefilled from
 * the project's `gitDefaults` → the feature-branch-equals-base guard blocks (§25.5) → fix it → push,
 * which queues a job, polls it, and shows the created pull request.
 */
test("Catalog → Push to Git → PR created", async ({ page }) => {
  const api = await stubApi(page, { artifacts: E2E_ARTIFACTS });
  await page.goto("/");
  await page.getByRole("button", { name: /^Catalog/ }).first().click();

  await page.getByRole("checkbox", { name: "Select all matching artifacts" }).check();
  await page.getByRole("button", { name: /^Push \(2\)/ }).click();

  const dialog = page.getByRole("dialog");
  const featureBranch = dialog.getByPlaceholder("feature/my-pipeline");
  await expect(featureBranch).toHaveValue("feature/customer-orders-load");

  // Always-on rule: never push to the protected base branch.
  await featureBranch.fill("dev");
  await expect(dialog.getByText("The feature branch must not be the protected base branch.")).toBeVisible();
  const pushButton = dialog.getByRole("button", { name: /Push & create PR/ });
  await expect(pushButton).toBeDisabled();

  await featureBranch.fill("feature/customer-orders-e2e");
  await expect(pushButton).toBeEnabled();
  await pushButton.click();

  await expect(dialog.getByText("Pull request created")).toBeVisible();
  const push = api.calls.find((c) => c.method === "POST" && c.path === "/git/push");
  expect(push?.body).toMatchObject({
    pipelineId: "pl-e2e-1",
    artifactIds: ["art-1", "art-2"],
    repositoryUrl: E2E_REPO_URL,
    featureBranch: "feature/customer-orders-e2e",
    baseBranch: "dev",
  });
});

/** §18 single-artifact selection: ticking one row pushes exactly that artifact, nothing else. */
test("Catalog → push a single selected artifact", async ({ page }) => {
  const api = await stubApi(page, { artifacts: E2E_ARTIFACTS });
  await page.goto("/");
  await page.getByRole("button", { name: /^Catalog/ }).first().click();

  await page.getByRole("checkbox", { name: `Select ${E2E_ARTIFACTS[1].fileName}` }).check();
  await page.getByRole("button", { name: /^Push \(1\)/ }).click();

  const dialog = page.getByRole("dialog");
  await dialog.getByPlaceholder("feature/my-pipeline").fill("feature/one-file");
  await dialog.getByRole("button", { name: /Push & create PR/ }).click();
  await expect(dialog.getByText("Pull request created")).toBeVisible();
  const push = api.calls.find((c) => c.method === "POST" && c.path === "/git/push");
  expect(push?.body).toMatchObject({ artifactIds: [E2E_ARTIFACTS[1].id], featureBranch: "feature/one-file" });
});
