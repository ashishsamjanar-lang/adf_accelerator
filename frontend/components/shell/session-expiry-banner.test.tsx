import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SessionExpiryBanner } from "./session-expiry-banner";
import { api } from "@/lib/api/client";

/** §27 session UX — pre-expiry warning driven by the last successful API call. */

function stubFetchOk(body: unknown = {}) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body } as Response));
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("SessionExpiryBanner", () => {
  it("stays hidden when idle timeout is unknown (non-password modes)", () => {
    render(<SessionExpiryBanner idleTimeoutMinutes={null} />);
    expect(screen.queryByText("Session about to expire")).toBeNull();
  });

  it("warns in the last two minutes and 'Stay signed in' refreshes the session", async () => {
    vi.useFakeTimers({ now: new Date("2026-09-25T10:00:00Z") });
    stubFetchOk({ authMode: "password", idleTimeoutMinutes: 30 });
    await api.auth.config();

    render(<SessionExpiryBanner idleTimeoutMinutes={30} />);
    expect(screen.queryByText("Session about to expire")).toBeNull();

    act(() => {
      vi.advanceTimersByTime(29 * 60_000);
    });
    expect(screen.getByText("Session about to expire")).toBeTruthy();
    expect(screen.getByText(/1:00/)).toBeTruthy();

    stubFetchOk({ id: "u1" });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Stay signed in" }));
    });
    expect(screen.queryByText("Session about to expire")).toBeNull();
  });
});
