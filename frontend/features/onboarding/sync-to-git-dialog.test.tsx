import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SyncToGitDialog } from "./sync-to-git-dialog";
import { api } from "@/lib/api/client";
import { newOnboardingObject } from "@/lib/adf/onboarding";
import type { OnboardingSync, ProjectSettings } from "@/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const row = { ...newOnboardingObject(), object_key: "erp.customer", source_system: "erp", source_object: "customer" };

describe("SyncToGitDialog (§26.5 Sync to Git)", () => {
  it("shows the per-row diff, sends the registry files, and reports the PR", async () => {
    vi.spyOn(api.onboarding, "syncDiff").mockResolvedValue({
      baseSyncId: "osync-0", baseCommitSha: "abcdef1234", basePrUrl: null,
      added: ["erp.customer"], removed: ["erp.legacy"], changed: [], unchanged: 0,
    });
    vi.spyOn(api.projects, "settings").mockResolvedValue({
      gitDefaults: { provider: "github", repositoryUrl: "https://github.com/contoso/adf", baseBranch: "dev" },
    } as unknown as ProjectSettings);
    const done: OnboardingSync = {
      id: "osync-1", projectId: "proj-a", provider: "github", repositoryUrl: "https://github.com/contoso/adf",
      featureBranch: "feature/x", baseBranch: "dev", commitMessage: "", filePaths: ["adf/artifacts/metadata/objects.csv"],
      objectCount: 1, status: "pr_created", commitSha: "c1", prNumber: 7, prUrl: "https://pr/7", simulated: false,
      error: null, createdBy: null, createdAt: null,
    };
    const sync = vi.spyOn(api.onboarding, "syncToGit").mockResolvedValue({ ...done, status: "pending" });
    vi.spyOn(api.onboarding, "syncs").mockResolvedValue([done]);

    render(<SyncToGitDialog open onClose={() => undefined} projectId="proj-a" rows={[row]} />);
    expect(await screen.findByText(/Changes since the last sync \(abcdef1\)/)).toBeTruthy();
    expect(screen.getByText(/−1 removed:/)).toBeTruthy();
    await waitFor(() => expect((screen.getByLabelText("Repository URL") as HTMLInputElement).value).toBe("https://github.com/contoso/adf"));

    fireEvent.click(screen.getByLabelText(/Include registry-lineage.json/)); // skip the Silver/Gold fetch
    fireEvent.click(screen.getByRole("button", { name: /Push & open PR/ }));

    await waitFor(() => expect(sync).toHaveBeenCalled());
    const body = sync.mock.calls[0][1];
    expect(body.objectKeys).toEqual(["erp.customer"]);
    expect(body.files.map((f) => f.name)).toEqual(["adf_control_table.sql", "objects.csv"]);
    expect(body.baseBranch).toBe("dev");
    expect(await screen.findByText(/PR #7 opened/)).toBeTruthy();
  });

  it("blocks a feature branch equal to the base branch", async () => {
    vi.spyOn(api.onboarding, "syncDiff").mockResolvedValue({
      baseSyncId: null, baseCommitSha: null, basePrUrl: null, added: [], removed: [], changed: [], unchanged: 0,
    });
    vi.spyOn(api.projects, "settings").mockResolvedValue({ gitDefaults: { repositoryUrl: "r", baseBranch: "dev" } } as unknown as ProjectSettings);
    render(<SyncToGitDialog open onClose={() => undefined} projectId="proj-a" rows={[row]} />);
    await waitFor(() => expect((screen.getByLabelText("Base branch") as HTMLInputElement).value).toBe("dev"));
    fireEvent.change(screen.getByLabelText("Feature branch"), { target: { value: "dev" } });
    expect(screen.getByText(/must not equal the protected base branch/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /Push & open PR/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});
