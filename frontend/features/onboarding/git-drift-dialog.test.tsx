import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GitDriftDialog } from "./git-drift-dialog";
import { api } from "@/lib/api/client";
import type { OnboardingGitDrift } from "@/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const drift: OnboardingGitDrift = {
  branch: "dev", path: "adf/artifacts/metadata/objects.csv", indexed: true, available: true, blobSha: "abcdef123",
  inSync: 2, lastSyncCommitSha: null,
  rows: [
    { objectKey: "erp.orders", status: "drifted", fields: [{ field: "notes", registry: "", git: "edited in git" }],
      changedInGit: true, changedLocally: false, gitRow: { object_key: "erp.orders", source_system: "erp", source_object: "orders" } },
    { objectKey: "erp.shipments", status: "only_in_git", fields: [], changedInGit: false, changedLocally: false,
      gitRow: { object_key: "erp.shipments", source_system: "erp", source_object: "shipments" } },
    { objectKey: "erp.invoices", status: "only_in_registry", fields: [], changedInGit: false, changedLocally: false, gitRow: null },
  ],
};

describe("GitDriftDialog (§26.6 Git import + drift)", () => {
  it("preselects Git-side changes and adopts them with provenance git-import", async () => {
    vi.spyOn(api.onboarding, "gitDrift").mockResolvedValue(drift);
    const bulk = vi.spyOn(api.onboarding, "bulkUpsert").mockResolvedValue({ added: 1, updated: 1 });
    const onAdopted = vi.fn();
    render(<GitDriftDialog open onClose={() => undefined} projectId="proj-a" canEdit onAdopted={onAdopted} />);

    expect(await screen.findByText(/2 in sync, 3 different/)).toBeTruthy();
    expect(screen.getByText("edited in Git")).toBeTruthy();
    expect(screen.queryByLabelText("Adopt erp.invoices from Git")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Adopt Git for 2 row\(s\)/ }));

    await waitFor(() => expect(bulk).toHaveBeenCalled());
    const [projectId, objects, fileName] = bulk.mock.calls[0];
    expect(projectId).toBe("proj-a");
    expect(objects.map((o) => [o.object_key, o.provenance])).toEqual([["erp.orders", "git-import"], ["erp.shipments", "git-import"]]);
    expect(fileName).toBe(drift.path);
    expect(onAdopted).toHaveBeenCalledWith(2);
  });

  it("tells the user to refresh when the branch was never indexed", async () => {
    vi.spyOn(api.onboarding, "gitDrift").mockResolvedValue({ ...drift, indexed: false, available: false, rows: [], inSync: 0 });
    render(<GitDriftDialog open onClose={() => undefined} projectId="proj-a" canEdit onAdopted={() => undefined} />);
    expect(await screen.findByText(/hasn't been read from Git yet/)).toBeTruthy();
  });
});
