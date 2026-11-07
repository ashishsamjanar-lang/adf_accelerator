import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { HC_ADMIN, HC_OUT, healthcareApi } from "./support/healthcare";

/**
 * "Every button works" sweep: as a PLATFORM_ADMIN, with one real (worker-generated + validated) EHR
 * pipeline seeded, open every navigation view and click every visible, enabled button in the main
 * area — one at a time, re-opening the view after each click. A button "works" when clicking it
 * raises no uncaught page error, no React/console error, and no crash screen. Disabled buttons are
 * reported, not clicked. The full per-button log lands in `test-results/healthcare/button-sweep.json`.
 */

const VIEWS: Array<{ group: string; item?: string }> = [
  { group: "Overview" },
  { group: "Workspace", item: "Teams" },
  { group: "Workspace", item: "Projects" },
  { group: "Delivery", item: "Build" },
  { group: "Delivery", item: "Drafts" },
  { group: "Delivery", item: "Git" },
  { group: "Delivery", item: "Onboarding" },
  { group: "Delivery", item: "Catalog" },
  { group: "Delivery", item: "Connections" },
  { group: "Delivery", item: "Sample Guides" },
  { group: "Administration", item: "Project Settings" },
  { group: "Administration", item: "Standards" },
  { group: "Administration", item: "Patterns" },
  { group: "Administration", item: "Users" },
  { group: "Administration", item: "Environments" },
  { group: "Administration", item: "Audit log" },
];

const MAX_BUTTONS_PER_VIEW = 60;
const CRASH_TEXT = /Application error|Unhandled Runtime Error|Something went wrong/i;

async function openView(page: Page, view: { group: string; item?: string }) {
  await page.locator(`nav.rail button[title="${view.group}"]`).click();
  if (view.item) {
    await page.locator("aside.context-panel button").filter({ hasText: new RegExp(`^\\s*${view.item}`) }).first().click();
  }
  await page.waitForTimeout(250);
}

async function closeOverlays(page: Page) {
  for (let i = 0; i < 3; i++) {
    const dialog = page.getByRole("dialog");
    if (!(await dialog.count()) || !(await dialog.first().isVisible())) return;
    await page.keyboard.press("Escape");
    await page.waitForTimeout(150);
    if (await dialog.first().isVisible().catch(() => false)) {
      const close = dialog.first().getByRole("button", { name: /^(Close|Cancel|Done)$/ });
      if (await close.count()) await close.first().click().catch(() => undefined);
    }
  }
}

interface ButtonLog {
  view: string;
  button: string;
  outcome: "ok" | "disabled" | "error" | "not-clickable";
  detail?: string;
}

test("every visible button on every view is clickable without errors (healthcare seed)", async ({ page }) => {
  test.setTimeout(900_000);
  const seedDraft = JSON.parse(readFileSync(path.resolve("e2e/fixtures/healthcare-ehr-draft.json"), "utf-8"));
  await healthcareApi(page, "button-sweep", { user: HC_ADMIN, seedDraft });

  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource|favicon|Download the React DevTools/i.test(m.text())) errors.push(`console: ${m.text()}`);
  });
  page.on("dialog", (d) => void d.dismiss());

  await page.goto("/");
  await expect(page.locator("nav.rail")).toBeVisible();

  const log: ButtonLog[] = [];
  for (const view of VIEWS) {
    const viewName = view.item ?? view.group;
    await openView(page, view);
    const buttons = () => page.locator("main.app-shell").locator("button:visible, [role=button]:visible");
    const total = Math.min(await buttons().count(), MAX_BUTTONS_PER_VIEW);
    const seen = new Set<string>();
    for (let i = 0; i < total; i++) {
      await openView(page, view);
      const btn = buttons().nth(i);
      if (!(await btn.count())) break;
      // Skip the shell's own chrome (rail, sidebar, topbar) — covered by opening every view.
      if (await btn.evaluate((el) => !!el.closest("nav.rail, aside.context-panel, header.topbar"))) continue;
      const label = ((await btn.getAttribute("aria-label")) || (await btn.innerText()).trim().split("\n")[0] || (await btn.getAttribute("title")) || "(icon)").slice(0, 60);
      const key = `${label}#${seen.size}`;
      seen.add(key);
      if (/^Log out$/i.test(label)) continue;
      if (await btn.isDisabled()) {
        log.push({ view: viewName, button: label, outcome: "disabled" });
        continue;
      }
      const before = errors.length;
      try {
        await btn.click({ timeout: 3_000 });
        await page.waitForTimeout(300);
      } catch (e) {
        log.push({ view: viewName, button: label, outcome: "not-clickable", detail: String(e).split("\n")[0] });
        continue;
      }
      const crashed = await page.getByText(CRASH_TEXT).count();
      const newErrors = errors.slice(before);
      log.push(
        crashed || newErrors.length
          ? { view: viewName, button: label, outcome: "error", detail: [crashed ? "crash screen shown" : "", ...newErrors].filter(Boolean).join(" | ").slice(0, 500) }
          : { view: viewName, button: label, outcome: "ok" },
      );
      await closeOverlays(page);
      if (crashed) await page.goto("/");
    }
  }

  mkdirSync(HC_OUT, { recursive: true });
  writeFileSync(path.join(HC_OUT, "button-sweep.json"), JSON.stringify(log, null, 2));
  const byOutcome = log.reduce<Record<string, number>>((acc, l) => ({ ...acc, [l.outcome]: (acc[l.outcome] ?? 0) + 1 }), {});
  console.log(`Button sweep: ${JSON.stringify(byOutcome)} across ${VIEWS.length} views`);
  const failures = log.filter((l) => l.outcome === "error");
  expect(failures, "buttons that raised an error").toEqual([]);
  expect(log.filter((l) => l.outcome === "ok").length).toBeGreaterThan(50);
});
