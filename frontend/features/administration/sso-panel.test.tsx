import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SsoPanel } from "./sso-panel";
import { api } from "@/lib/api/client";
import type { SsoConfiguration, Team } from "@/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const teams = [{ id: "team-a", name: "Team A" }] as Team[];

describe("SsoPanel (§27.5 SSO admin config)", () => {
  it("creates an OIDC provider with a group mapping", async () => {
    const saved: SsoConfiguration = {
      id: "sso1", name: "Entra ID", protocol: "oidc", issuerUrl: "https://issuer", clientId: "cid",
      groupClaim: "groups", jitProvisioning: true, enabled: false,
      roleMappings: [{ groupValue: "devs", role: "DEVELOPER", teamId: "team-a" }],
    };
    vi.spyOn(api.admin, "ssoConfigurations").mockResolvedValueOnce([]).mockResolvedValueOnce([saved]);
    const create = vi.spyOn(api.admin, "createSsoConfiguration").mockResolvedValue(saved);

    render(<SsoPanel teams={teams} />);
    await screen.findByText("No identity providers");
    fireEvent.click(screen.getByRole("button", { name: /Add provider/ }));
    fireEvent.change(screen.getByPlaceholderText("e.g. Entra ID"), { target: { value: "Entra ID" } });
    fireEvent.change(screen.getByLabelText("Issuer URL"), { target: { value: "https://issuer" } });
    fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "cid" } });
    fireEvent.click(screen.getByRole("button", { name: /Add mapping/ }));
    fireEvent.change(screen.getByLabelText("Group 1"), { target: { value: "devs" } });
    fireEvent.change(screen.getByLabelText("Role 1"), { target: { value: "DEVELOPER" } });
    fireEvent.change(screen.getByLabelText("Team 1"), { target: { value: "team-a" } });
    fireEvent.click(screen.getByRole("button", { name: /Save provider/ }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0][0]).toMatchObject({
      name: "Entra ID", protocol: "oidc", issuerUrl: "https://issuer", clientId: "cid",
      roleMappings: [{ groupValue: "devs", role: "DEVELOPER", teamId: "team-a" }],
    });
    expect(await screen.findByText("1 group mapping(s)")).toBeTruthy();
  });
});
