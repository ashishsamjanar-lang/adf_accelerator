import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { LinkedServiceEditor } from "./connection-editors";
import { defaultLinkedServiceDraft, type LinkedServiceDraft } from "@/lib/adf/connections";

/**
 * First per-feature component test (IMPLEMENTATION_PLAN.md "No per-feature component tests"):
 * the Linked Service designer's grouped sections + inline required-field state (gap #27) and the
 * system- vs user-assigned managed-identity picker (§32.5, MI3).
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Harness({ initial, errors = [] }: { initial: LinkedServiceDraft; errors?: string[] }) {
  const [ls, setLs] = useState(initial);
  return (
    <>
      <LinkedServiceEditor title="Target" ls={ls} connectors={[ls.connectorType]} onChange={setLs} errors={errors} />
      <output data-testid="state">{JSON.stringify(ls)}</output>
    </>
  );
}

const state = () => JSON.parse(screen.getByTestId("state").textContent ?? "{}") as LinkedServiceDraft;

describe("LinkedServiceEditor", () => {
  it("groups fields into sections and tags the connector category", () => {
    const { container } = render(<Harness initial={defaultLinkedServiceDraft("source", "azure_sql_database")} />);
    expect(container.querySelector(".ls-editor")?.getAttribute("data-category")).toBeTruthy();
    expect(screen.getByText("Connection")).toBeTruthy();
    expect(container.querySelectorAll(".field-section").length).toBeGreaterThan(1);
  });

  it("marks empty required fields inline only once validation errors are reported", () => {
    const initial = defaultLinkedServiceDraft("source", "azure_sql_database");
    const { container, unmount } = render(<Harness initial={initial} />);
    expect(container.querySelector(".field-cell.is-invalid")).toBeNull();
    unmount();
    const withErrors = render(<Harness initial={initial} errors={["missing server"]} />);
    expect(withErrors.container.querySelector(".field-cell.is-invalid")).not.toBeNull();
  });

  it("snapshots a picked user-assigned identity onto the draft", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify([{ id: "mi-1", teamId: "t", name: "CRED_uami_ingest", resourceId: "/subscriptions/x/uami" }]),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const initial = { ...defaultLinkedServiceDraft("target", "azure_data_lake_storage_gen2"), authType: "managed_identity" as const };
    render(<Harness initial={initial} />);

    fireEvent.change(screen.getByDisplayValue(/System-assigned/), { target: { value: "user" } });
    await waitFor(() => expect(screen.getByRole("option", { name: "CRED_uami_ingest" })).toBeTruthy());
    fireEvent.change(screen.getByDisplayValue(/select a registered identity/), { target: { value: "CRED_uami_ingest" } });

    expect(state()).toMatchObject({
      identityKind: "user",
      credentialName: "CRED_uami_ingest",
      credentialResourceId: "/subscriptions/x/uami",
    });

    // Switching auth away from managed identity clears the identity snapshot.
    fireEvent.change(screen.getByDisplayValue(/Managed identity/), { target: { value: "account_key" } });
    expect(state().identityKind).toBeUndefined();
  });

  it("registers a new user-assigned identity in place and selects it", async () => {
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json([]))
      .mockResolvedValueOnce(json({ id: "mi-2", teamId: "t", name: "cred_new", resourceId: "/subscriptions/y/uami" }, 201));
    vi.stubGlobal("fetch", fetchMock);
    const initial = { ...defaultLinkedServiceDraft("target", "azure_data_lake_storage_gen2"), authType: "managed_identity" as const };
    render(<Harness initial={initial} />);

    fireEvent.change(screen.getByDisplayValue(/System-assigned/), { target: { value: "user" } });
    await waitFor(() => expect(screen.getByText(/No user-assigned identities/)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Register new identity/ }));
    fireEvent.change(screen.getByLabelText("Identity name"), { target: { value: "cred_new" } });
    fireEvent.change(screen.getByLabelText("Identity resource ID"), { target: { value: "/subscriptions/y/uami" } });
    fireEvent.click(screen.getByRole("button", { name: "Register" }));

    await waitFor(() => expect(state().credentialName).toBe("cred_new"));
    expect(state()).toMatchObject({ identityKind: "user", credentialResourceId: "/subscriptions/y/uami" });
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toContain("/api/v1/managed-identities");
    expect(JSON.parse(init.body)).toEqual({ name: "cred_new", resourceId: "/subscriptions/y/uami", clientId: null });
  });
});
