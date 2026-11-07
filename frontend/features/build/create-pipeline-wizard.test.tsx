import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CreatePipelineWizard } from "./create-pipeline-wizard";
import { api } from "@/lib/api/client";
import { emptySpecDraft, validateDraft, type SpecDraft } from "@/lib/adf/spec";
import type { AcceleratorData } from "@/lib/api/data";
import type { PipelineDefinition, User } from "@/types";

/**
 * Build wizard happy path (IMPLEMENTATION_PLAN.md "No per-feature component tests"): reopen a saved,
 * valid draft → Continue through every step → Generate the preview → Save, which must chain the real
 * update → generate → validate → validationResult calls and render the inline results screen (§29).
 */

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function validDraft(): SpecDraft {
  const base = emptySpecDraft();
  return {
    ...base,
    name: "customer_incremental",
    projectId: "proj-a",
    sourceLinkedService: {
      ...base.sourceLinkedService,
      fields: { server: "sqlsrv01.database.windows.net", database: "customerdb", userName: "svc_reader", password: "sqlserver-password" },
    },
    targetLinkedService: {
      ...base.targetLinkedService,
      fields: { accountName: "stgcustomer", url: "https://stgcustomer.dfs.core.windows.net", accountKey: "storage-account-key" },
    },
    sourceDataset: { ...base.sourceDataset, linkedServiceName: base.sourceLinkedService.name },
    targetDataset: { ...base.targetDataset, linkedServiceName: base.targetLinkedService.name },
  };
}

const resource = <T,>(data: T) => ({ data, loading: false, failed: false, reload: () => undefined });

function wizardData(): AcceleratorData {
  return {
    currentUser: resource({} as User),
    teams: resource([]),
    projects: resource([{ id: "proj-a", name: "Project A" }]),
    environments: resource([]),
    pipelines: resource([]),
    artifacts: resource([]),
    users: resource([]),
    audit: resource([]),
  } as unknown as AcceleratorData;
}

describe("CreatePipelineWizard", () => {
  it("saves, generates and validates a pipeline end to end", async () => {
    const draft = validDraft();
    expect(validateDraft(draft).filter((i) => i.severity === "error")).toEqual([]);
    // Every other fetch (project settings, connections, registries, ...) resolves empty.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })));
    const pipeline = { id: "p1", name: draft.name, projectId: "proj-a", state: "VALIDATED", draft } as unknown as PipelineDefinition;
    vi.spyOn(api.pipelines, "details").mockResolvedValue(pipeline as never);
    const update = vi.spyOn(api.pipelines, "update").mockResolvedValue(pipeline as never);
    const generate = vi.spyOn(api.pipelines, "generate").mockResolvedValue(pipeline as never);
    const validate = vi.spyOn(api.pipelines, "validate").mockResolvedValue(pipeline as never);
    vi.spyOn(api.pipelines, "validationResult").mockResolvedValue({
      pipelineId: "p1",
      state: "passed",
      issues: [],
      validatedAt: null,
      correlationId: "c1",
    });
    const onUpdated = vi.fn();

    render(
      <CreatePipelineWizard
        data={wizardData()}
        user={{ id: "u1", name: "Dev", role: "DEVELOPER" } as User}
        onNavigate={vi.fn()}
        onCreated={vi.fn()}
        editPipelineId="p1"
        onUpdated={onUpdated}
      />,
    );

    // Hydrated edit mode lands on step 2; Continue through steps 2 → 5.
    await screen.findByText(/Editing "customer_incremental"/);
    for (let i = 0; i < 3; i++) {
      const buttons = await screen.findAllByRole("button", { name: /^Continue$/ });
      fireEvent.click(buttons[buttons.length - 1]);
    }
    fireEvent.click(await screen.findByRole("button", { name: /Generate pipeline code/ }));
    await screen.findAllByText("PL_customer_incremental_master.json", {}, { timeout: 3000 });

    fireEvent.click(screen.getByRole("button", { name: /Save pipeline/ }));
    await waitFor(() => expect(validate).toHaveBeenCalledWith("p1"));
    expect(update).toHaveBeenCalledWith("p1", expect.objectContaining({ name: "customer_incremental" }));
    expect(generate).toHaveBeenCalledWith("p1");
    expect(onUpdated).toHaveBeenCalled();
    expect(await screen.findByText("Validated")).toBeTruthy();
  });

  it("create mode: Basics blocks Continue on an invalid name, then advances once it's fixed", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })));
    const create = vi.spyOn(api.pipelines, "create");
    render(
      <CreatePipelineWizard
        data={wizardData()}
        user={{ id: "u1", name: "Dev", role: "DEVELOPER" } as User}
        onNavigate={vi.fn()}
        onCreated={vi.fn()}
      />,
    );

    await screen.findByText(/Name the pipeline, choose its project/);
    const nameInput = screen.getByPlaceholderText(/customer_metadata_load/);
    fireEvent.change(nameInput, { target: { value: "Bad Name!" } });
    fireEvent.click(screen.getByRole("button", { name: /^Continue$/ }));
    // Still on Basics, with the name error rendered inline.
    expect(screen.queryByText(/Configure where data comes from/)).toBeNull();
    expect(document.querySelector("small.text-red")).not.toBeNull();

    fireEvent.change(nameInput, { target: { value: "customer_full_load" } });
    fireEvent.click(screen.getByRole("button", { name: /^Continue$/ }));
    await screen.findByText(/Configure where data comes from/);
    // Advancing a step never persists anything — only Save does.
    expect(create).not.toHaveBeenCalled();
  });

  // AGENTS.md §18 "Template form prefill" + "Project-default vs pipeline-override" (DEVELOPMENT_NOTES.md rule 7):
  // a field still at its built-in default takes the project's value; a value the pipeline set is kept;
  // and saving the pipeline never writes the project defaults back.
  it.each([
    { label: "prefills a field still at its built-in default", retryCount: undefined, saved: 7 },
    { label: "keeps a pipeline's own override", retryCount: 5, saved: 5 },
  ])("project defaults: $label", async ({ retryCount, saved }) => {
    const base = validDraft();
    const draft = { ...base, retryCount: retryCount ?? base.retryCount };
    expect(base.retryCount).not.toBe(7);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })));
    vi.spyOn(api.projects, "settings").mockResolvedValue({ projectId: "proj-a", adfDefaults: { defaultRetryCount: 7 } } as never);
    const updateSettings = vi.spyOn(api.projects, "updateSettings");
    const pipeline = { id: "p1", name: draft.name, projectId: "proj-a", state: "VALIDATED", draft } as unknown as PipelineDefinition;
    vi.spyOn(api.pipelines, "details").mockResolvedValue(pipeline as never);
    const update = vi.spyOn(api.pipelines, "update").mockResolvedValue(pipeline as never);
    vi.spyOn(api.pipelines, "generate").mockResolvedValue(pipeline as never);
    const validate = vi.spyOn(api.pipelines, "validate").mockResolvedValue(pipeline as never);
    vi.spyOn(api.pipelines, "validationResult").mockResolvedValue({
      pipelineId: "p1", state: "passed", issues: [], validatedAt: null, correlationId: "c1",
    });

    render(
      <CreatePipelineWizard
        data={wizardData()}
        user={{ id: "u1", name: "Dev", role: "DEVELOPER" } as User}
        onNavigate={vi.fn()}
        onCreated={vi.fn()}
        editPipelineId="p1"
        onUpdated={vi.fn()}
      />,
    );

    await screen.findByText(/Editing "customer_incremental"/);
    for (let i = 0; i < 3; i++) {
      const buttons = await screen.findAllByRole("button", { name: /^Continue$/ });
      fireEvent.click(buttons[buttons.length - 1]);
    }
    fireEvent.click(await screen.findByRole("button", { name: /Generate pipeline code/ }));
    await screen.findAllByText("PL_customer_incremental_master.json", {}, { timeout: 3000 });
    fireEvent.click(screen.getByRole("button", { name: /Save pipeline/ }));
    await waitFor(() => expect(validate).toHaveBeenCalledWith("p1"));
    expect(update).toHaveBeenCalledWith("p1", expect.objectContaining({ draft: expect.objectContaining({ retryCount: saved }) }));
    expect(updateSettings).not.toHaveBeenCalled();
  });
});
