import { describe, expect, it } from "vitest";
import { NAV_GROUPS, VIEW_TITLES } from "./navigation";

describe("navigation", () => {
  // AGENTS.md §1/§4 product rule 1 (§18): no Release/Deployment navigation anywhere.
  it("has no release or deployment entry", () => {
    const labels = [
      ...NAV_GROUPS.flatMap((g) => [g.id, g.label, ...g.items.flatMap((i) => [i.id, i.label, i.hint ?? "", i.section ?? ""])]),
      ...Object.values(VIEW_TITLES),
    ];
    expect(labels.filter((l) => /\b(release|deploy)/i.test(l))).toEqual([]);
  });
});
