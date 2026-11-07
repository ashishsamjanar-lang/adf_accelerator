import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import type { User } from "@/types";
import { Topbar } from "./topbar";

afterEach(() => {
  cleanup();
});

const user: User = {
  id: "u-1",
  name: "Priya Raman",
  email: "priya@example.com",
  role: "DEVELOPER",
  teamId: "team-a",
  active: true,
};

describe("Topbar", () => {
  // AGENTS.md §1/§4 product rule 3 (gaps.md E2E-13): the header shows the authenticated user's name only.
  it("shows the user's name with no greeting or welcome text", () => {
    const { container } = render(<Topbar view="home" workspaceName="Acme" user={user} onNavigate={() => {}} />);
    const header = container.querySelector("header.topbar") as HTMLElement;
    expect(header.textContent).toContain("Priya Raman");
    expect(header.textContent).not.toMatch(/\b(welcome|hello|hi|hey|good (morning|afternoon|evening))\b/i);
  });
});
