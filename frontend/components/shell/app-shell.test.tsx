import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor, within } from "@testing-library/react";
import { act, fireEvent } from "@testing-library/react";
import { AppShell } from "./app-shell";
import { NAV_GROUPS } from "./navigation";

/**
 * End-to-end frontend wiring smoke test (IMPLEMENTATION_PLAN.md "Cross-cutting
 * test coverage" #4 — "no component/integration tests yet").
 *
 * This is deliberately not a per-feature test (each feature already has, or
 * will have, its own unit coverage) — it exists to prove the *shell* actually
 * wires together end to end the way a real user session does:
 *
 *   Sidebar click -> AppShell view state -> Topbar breadcrumb
 *                                        -> renderView()'s feature component
 *
 * for every destination in the real navigation model (`navigation.ts`), using
 * the real `useAcceleratorData`/`ApiStatusProvider` stack. No backend is
 * running in this test (jsdom has no server to answer `fetch`), so every
 * resource legitimately falls back to its empty value exactly the way it
 * does in a real browser when the API is unreachable (`lib/api/hooks.tsx`) —
 * that fallback path is itself part of what's being verified: if the shell,
 * a feature view, or an empty-state render ever go out of sync (a renamed
 * prop, a view that assumes a non-empty list, a nav entry with no
 * `renderView` case), rendering throws and this test fails.
 */

afterEach(() => {
  cleanup();
});

// jsdom has no `fetch`; every `useResource` call rejects and falls back to
// its empty value (the same path a browser takes when the API is down)
// instead of leaving `fetch` undefined and throwing a ReferenceError out of
// `load()`'s try/catch — either way it's caught, but stubbing it keeps the
// test's intent explicit and quiet (no unhandled rejection warnings).
vi.stubGlobal(
  "fetch",
  vi.fn().mockRejectedValue(new Error("no backend in this test — expect the empty-state fallback")),
);

function railButtonFor(container: HTMLElement, groupLabel: string): HTMLElement {
  const rail = container.querySelector(".rail") as HTMLElement;
  const button = within(rail).getByTitle(groupLabel);
  return button;
}

function navItemButtonFor(container: HTMLElement, itemLabel: string): HTMLElement {
  const panel = container.querySelector(".context-panel") as HTMLElement;
  const button = within(panel)
    .getAllByRole("button")
    .find((el) => el.textContent?.startsWith(itemLabel));
  if (!button) {
    throw new Error(`No nav-item button found for "${itemLabel}" in the open group's context panel.`);
  }
  return button;
}

function breadcrumbTitle(container: HTMLElement): string | null {
  return container.querySelector(".breadcrumbs strong")?.textContent ?? null;
}

describe("AppShell wiring", () => {
  it("boots to the Overview (home) view with no crash and no leaked placeholders", async () => {
    const { container } = render(<AppShell />);

    expect(breadcrumbTitle(container)).toBe("Overview");

    // Home renders a loading skeleton (no text) until `useAcceleratorData`'s
    // resources settle — wait for the real content (an empty state, since
    // there's no backend in this test) before checking it's well-formed.
    await waitFor(() => {
      const content = container.querySelector(".page-content")?.textContent ?? "";
      expect(content.length).toBeGreaterThan(0);
    });
    const content = container.querySelector(".page-content")?.textContent ?? "";
    expect(content).not.toMatch(/undefined|\[object Object\]|NaN/);
  });

  it("navigates to every item in every nav group and renders its view without throwing", async () => {
    const { container } = render(<AppShell />);

    for (const group of NAV_GROUPS) {
      await act(async () => {
        fireEvent.click(railButtonFor(container, group.label));
      });

      for (const item of group.items) {
        // A single-item group (e.g. "Overview") already navigated when its
        // rail button was clicked above; clicking the same item again in the
        // context panel is still exercising the real click path, so no need
        // to special-case it.
        await act(async () => {
          fireEvent.click(navItemButtonFor(container, item.label));
        });

        expect(breadcrumbTitle(container)).toBe(item.label);

        // Some views fetch their own per-view resource (e.g. Project
        // Settings) and render a skeleton until it settles — wait for real
        // content the same way the "boots to Overview" test does.
        await waitFor(() => {
          const content = container.querySelector(".page-content")?.textContent ?? "";
          expect(content.length).toBeGreaterThan(0);
        });
        const content = container.querySelector(".page-content")?.textContent ?? "";
        expect(content).not.toMatch(/undefined|\[object Object\]|NaN/);
      }
    }
  });

});
