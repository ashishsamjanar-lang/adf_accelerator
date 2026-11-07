import { describe, expect, it } from "vitest";
import { formatBytes, formatDateTime, severityTone, timeAgo } from "./format";

describe("formatDateTime", () => {
  it("renders an em dash for a missing value", () => {
    expect(formatDateTime(undefined)).toBe("—");
    expect(formatDateTime("")).toBe("—");
  });

  it("formats a real ISO timestamp to a non-empty, different string", () => {
    const out = formatDateTime("2026-01-15T10:30:00Z");
    expect(out).not.toBe("—");
    expect(out.length).toBeGreaterThan(0);
  });
});

describe("timeAgo", () => {
  it("returns 'never' for a missing value", () => {
    expect(timeAgo(undefined)).toBe("never");
  });

  it("returns 'just now' for a timestamp seconds ago", () => {
    const iso = new Date(Date.now() - 5_000).toISOString();
    expect(timeAgo(iso)).toBe("just now");
  });

  it("returns minutes ago inside the hour boundary", () => {
    const iso = new Date(Date.now() - 5 * 60_000).toISOString();
    expect(timeAgo(iso)).toBe("5 minutes ago");
  });

  it("returns hours ago inside the day boundary", () => {
    const iso = new Date(Date.now() - 3 * 3_600_000).toISOString();
    expect(timeAgo(iso)).toBe("3 hours ago");
  });

  it("returns days ago beyond a day", () => {
    const iso = new Date(Date.now() - 2 * 86_400_000).toISOString();
    expect(timeAgo(iso)).toBe("2 days ago");
  });
});

describe("formatBytes", () => {
  it("renders raw bytes under 1 KB", () => {
    expect(formatBytes(500)).toBe("500 B");
  });

  it("renders KB between 1 KB and 1 MB", () => {
    expect(formatBytes(2048)).toBe("2.0 KB");
  });

  it("renders MB at and above 1 MB", () => {
    expect(formatBytes(1024 * 1024 * 3)).toBe("3.0 MB");
  });
});

describe("severityTone", () => {
  it("maps error to red", () => {
    expect(severityTone("error")).toBe("red");
  });

  it("maps warning to amber", () => {
    expect(severityTone("warning")).toBe("amber");
  });

  it("maps everything else (info) to blue", () => {
    expect(severityTone("info")).toBe("blue");
  });
});
