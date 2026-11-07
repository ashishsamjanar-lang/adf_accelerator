import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CatalogChangeDialog } from "./catalog-change-dialog";
import { api } from "@/lib/api/client";
import type { CatalogChange } from "@/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const PATH = "adf/artifacts/linkedService/LS_sql.json";
const done: CatalogChange = {
  id: "gcc-1", projectId: "proj-a", mode: "rename", path: PATH, newPath: "adf/artifacts/linkedService/LS_dw.json",
  featureBranch: "feature/rename-ls_sql", baseBranch: "dev", commitMessage: "", filePaths: [], deletePaths: [PATH],
  status: "pr_created", commitSha: "c1", prNumber: 4, prUrl: "https://pr/4", simulated: false, error: null, createdAt: null,
};

describe("CatalogChangeDialog (§24.10 rename / delete)", () => {
  it("shows the blast radius and sends a rename that rewrites inbound references", async () => {
    vi.spyOn(api.catalog, "impact").mockResolvedValue({
      path: PATH, branch: "dev", inbound: [{ path: "adf/artifacts/dataset/DS_orders.json", type: "dataset", name: "DS_orders" }],
    });
    const change = vi.spyOn(api.catalog, "change").mockResolvedValue({ ...done, status: "pending" });
    vi.spyOn(api.catalog, "changes").mockResolvedValue([done]);
    const onSettled = vi.fn();

    render(
      <CatalogChangeDialog open onClose={() => undefined} onSettled={onSettled} projectId="proj-a" mode="rename" path={PATH} name="LS_sql" baseBranch="dev" />,
    );
    expect(await screen.findByText(/1 artifact\(s\) reference LS_sql/)).toBeTruthy();
    const submit = screen.getByRole("button", { name: /Push & open PR/ }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true); // name unchanged

    fireEvent.change(screen.getByLabelText(/^New name/), { target: { value: "bad name" } });
    expect(screen.getByText(/Start with a letter/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/^New name/), { target: { value: "LS_dw" } });
    fireEvent.click(submit);

    await waitFor(() => expect(change).toHaveBeenCalled());
    expect(change.mock.calls[0][1]).toMatchObject({ mode: "rename", newName: "LS_dw", rewriteReferences: true, featureBranch: "feature/rename-ls_sql" });
    expect(await screen.findByText(/PR #4 opened/)).toBeTruthy();
    expect(onSettled).toHaveBeenCalledWith(done);
  });

  it("requires confirmation to delete a referenced artifact", async () => {
    vi.spyOn(api.catalog, "impact").mockResolvedValue({
      path: PATH, branch: "dev", inbound: [{ path: "adf/artifacts/dataset/DS_orders.json", type: "dataset", name: "DS_orders" }],
    });
    render(
      <CatalogChangeDialog open onClose={() => undefined} onSettled={() => undefined} projectId="proj-a" mode="delete" path={PATH} name="LS_sql" baseBranch="dev" />,
    );
    const confirm = await screen.findByLabelText(/Delete anyway/);
    const submit = screen.getByRole("button", { name: /Push & open PR/ }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(confirm);
    expect(submit.disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("Feature branch"), { target: { value: "dev" } });
    expect(submit.disabled).toBe(true);
  });
});
