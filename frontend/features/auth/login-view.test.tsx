import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LoginView } from "./login-view";
import { api, ApiError } from "@/lib/api/client";
import type { User } from "@/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function fillAndSubmit(username: string, password: string) {
  fireEvent.change(screen.getByLabelText("Username or email"), { target: { value: username } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginView (AUTH_MODE=password)", () => {
  it("signs in with a username", async () => {
    const login = vi.spyOn(api.auth, "login").mockResolvedValue({ id: "u-1" } as User);
    const onSignedIn = vi.fn();
    render(<LoginView onSignedIn={onSignedIn} />);

    fillAndSubmit("jane.doe", "a strong passphrase");
    await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
    expect(login).toHaveBeenCalledWith("jane.doe", "a strong passphrase");
  });

  it("shows the server's error on bad credentials", async () => {
    vi.spyOn(api.auth, "login").mockRejectedValue(
      new ApiError(401, "invalid_credentials", "Incorrect username or password."),
    );
    const onSignedIn = vi.fn();
    render(<LoginView onSignedIn={onSignedIn} />);

    fillAndSubmit("jane.doe", "wrong");
    expect(await screen.findByText("Incorrect username or password.")).toBeTruthy();
    expect(onSignedIn).not.toHaveBeenCalled();
  });
});
