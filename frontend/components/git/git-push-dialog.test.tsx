import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { GitPushDialog } from "./git-push-dialog";
import { api } from "@/lib/api/client";
import type { ArtifactVersion, GitSettings } from "@/types";

/**
 * AGENTS.md §18 "No hard-coded project/Git values test" (product rule 5): with no saved project
 * defaults the push dialog's destination fields are blank, never a sample organization/repository;
 * with defaults, every field shows the project's own values.
 */

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const artifact = {
  id: "a1",
  pipelineId: "p1",
  pipelineName: "customer_load",
  fileName: "pipeline/PL_customer_load_master.json",
  fileType: "json",
  artifactType: "pipeline",
  layer: "pipeline",
  version: "v1.0.0",
  state: "validated",
  bytes: 10,
} as ArtifactVersion;

const value = (label: string | RegExp) => (screen.getByLabelText(label) as HTMLInputElement).value;

describe("GitPushDialog", () => {
  it("leaves the Git destination blank when the project has no saved defaults", async () => {
    const details = vi.spyOn(api.pipelines, "details").mockRejectedValue(new Error("unreachable"));
    render(<GitPushDialog open onClose={vi.fn()} artifacts={[artifact]} />);
    await waitFor(() => expect(details).toHaveBeenCalled());

    for (const label of ["Organization", "Project", "Repository", "Repository URL"]) expect(value(label)).toBe("");
    // Only the generic template defaults remain: the protected base branch and a pipeline-derived feature branch.
    expect(value(/base branch/i)).toBe("dev");
    expect(value(/feature branch/i)).toMatch(/^feature\/customer-load-\d{8}$/);
  });

  it("prefills every destination field from the project's Git defaults", () => {
    const defaults = {
      provider: "azure_repos",
      organization: "fabrikam",
      project: "data-platform",
      repository: "adf-finance",
      repositoryUrl: "https://dev.azure.com/fabrikam/data-platform/_git/adf-finance",
      artifactRoot: "factory/",
      featureBranchPattern: "feat/{pipeline-name}",
      baseBranch: "develop",
      commitMessageConvention: "chore: {pipeline}",
      prTitleTemplate: "{pipeline} {version}",
      prBodyTemplate: "",
      reviewers: [],
    } as GitSettings;
    render(<GitPushDialog open onClose={vi.fn()} artifacts={[artifact]} defaults={defaults} />);

    expect(value("Organization")).toBe("fabrikam");
    expect(value("Project")).toBe("data-platform");
    expect(value("Repository")).toBe("adf-finance");
    expect(value("Repository URL")).toBe(defaults.repositoryUrl);
    expect(value("Artifact root / path")).toBe("factory/");
    expect(value(/feature branch/i)).toBe("feat/customer-load");
    expect(value(/base branch/i)).toBe("develop");
  });
});
