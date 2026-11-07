import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SessionsPanel } from "./administration-view";
import { api } from "@/lib/api/client";
import type { AdminSession } from "@/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const row = (overrides: Partial<AdminSession>): AdminSession => ({
  handle: "h1",
  userId: "u-a",
  userName: "Alice",
  userEmail: "a@test.local",
  issuedAt: new Date().toISOString(),
  idleExpiresAt: new Date().toISOString(),
  current: false,
  ...overrides,
});

describe("SessionsPanel (§27 session admin)", () => {
  it("lists sessions, revokes another user's, and disables revoking your own", async () => {
    const sessions = vi
      .spyOn(api.admin, "sessions")
      .mockResolvedValueOnce([row({}), row({ handle: "h2", userName: "Me", current: true })])
      .mockResolvedValueOnce([row({ handle: "h2", userName: "Me", current: true })]);
    const revoke = vi.spyOn(api.admin, "revokeSession").mockResolvedValue(undefined);

    render(<SessionsPanel />);
    const aliceRevoke = await screen.findByRole("button", { name: "Revoke session for Alice" });
    expect(screen.getByRole("button", { name: "Revoke session for Me" })).toHaveProperty("disabled", true);
    expect(screen.getByText("This session")).toBeTruthy();

    fireEvent.click(aliceRevoke);
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("h1"));
    await waitFor(() => expect(screen.queryByText("Alice")).toBeNull());
    expect(sessions).toHaveBeenCalledTimes(2);
  });

  it("shows the empty state when no one is signed in", async () => {
    vi.spyOn(api.admin, "sessions").mockResolvedValue([]);
    render(<SessionsPanel />);
    expect(await screen.findByText("No active sessions")).toBeTruthy();
  });
});
