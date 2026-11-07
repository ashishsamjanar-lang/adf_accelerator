import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProjectSettingsView } from "./project-settings-view";
import { api } from "@/lib/api/client";
import type { AcceleratorData } from "@/lib/api/data";
import type { ProjectSettings, User } from "@/types";

/**
 * AGENTS.md §18 "Project Settings persistence test": a saved settings document loads into every
 * section, and Save writes it back unchanged — the ADF/template/Git defaults used to stay on their
 * fallbacks after the async load, so a plain Save overwrote the stored values.
 */

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const resource = <T,>(data: T) => ({ data, loading: false, failed: false, reload: () => undefined });

function viewData(): AcceleratorData {
  return {
    currentUser: resource({} as User),
    teams: resource([{ id: "team-a", name: "Team A" }]),
    projects: resource([{ id: "proj-a", name: "Project A", teamId: "team-a" }]),
    environments: resource([]),
    pipelines: resource([]),
    artifacts: resource([]),
    users: resource([]),
    audit: resource([]),
  } as unknown as AcceleratorData;
}

const saved: ProjectSettings = {
  projectId: "proj-a",
  name: "Finance ingestion",
  description: "Stored description",
  teamId: "team-a",
  tags: [{ key: "domain", value: "finance" }],
  namingConvention: { pipelinePattern: "pl_{source}_{object}" },
  adfDefaults: { factoryName: "adf-finance-dev", resourceGroup: "rg-finance", defaultRetryCount: 5, defaultTimeout: "0.02:00:00" },
  templateDefaults: { defaultTemplateId: "incremental_load", controlTable: { schemaName: "ctl", tableName: "objects" }, defaultFilterCondition: "is_active = 1" },
  gitDefaults: {
    provider: "github",
    repositoryUrl: "https://github.com/contoso/adf-finance",
    repository: "contoso/adf-finance",
    featureBranchPattern: "feature/{pipeline-name}",
    baseBranch: "develop",
    artifactRoot: "factory/",
    commitMessageConvention: "chore: {pipeline}",
    prTitleTemplate: "{pipeline}",
    prBodyTemplate: "body",
    reviewers: ["lead@contoso.com"],
  },
} as ProjectSettings;

describe("ProjectSettingsView", () => {
  it("loads the saved settings into every section and saves them back unchanged", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })));
    vi.spyOn(api.projects, "settings").mockResolvedValue(saved);
    const update = vi.spyOn(api.projects, "updateSettings").mockResolvedValue(saved);

    render(<ProjectSettingsView data={viewData()} user={{ id: "u1", name: "Dev", role: "TEAM_ADMIN" } as User} onNavigate={vi.fn()} projectId="proj-a" />);
    await screen.findByDisplayValue("Finance ingestion");
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    const [projectId, document] = update.mock.calls[0];
    expect(projectId).toBe("proj-a");
    expect(document).toMatchObject({
      name: "Finance ingestion",
      description: "Stored description",
      tags: [{ key: "domain", value: "finance" }],
      namingConvention: { pipelinePattern: "pl_{source}_{object}" },
      adfDefaults: { factoryName: "adf-finance-dev", resourceGroup: "rg-finance", defaultRetryCount: 5, defaultTimeout: "0.02:00:00" },
      templateDefaults: { defaultTemplateId: "incremental_load", controlTable: { schemaName: "ctl", tableName: "objects" }, defaultFilterCondition: "is_active = 1" },
      gitDefaults: saved.gitDefaults,
    });
  });
});
