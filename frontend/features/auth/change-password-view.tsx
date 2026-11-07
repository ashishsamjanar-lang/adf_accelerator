"use client";

/**
 * Forced password change (AGENTS.md §27.3) — rendered whenever the signed-in
 * user's `mustChangePassword` flag is set: a brand-new account (`POST
 * /admin/users` issues a one-time temp password) or an admin-triggered
 * reset (`POST /admin/users/{id}/reset-password`).
 */
import { useState } from "react";
import { KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert } from "@/components/ui/alert";
import { api, ApiError } from "@/lib/api/client";

export function ChangePasswordView({ onChanged }: { onChanged: () => void }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (newPassword !== confirmPassword) {
      setError("New password and confirmation don't match.");
      return;
    }
    setSubmitting(true);
    setError(undefined);
    try {
      await api.auth.changePassword(currentPassword, newPassword);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="grid min-h-screen place-items-center px-5"
      style={{
        background:
          "radial-gradient(1000px circle at 15% 10%, rgba(99,102,241,0.20) 0%, transparent 55%), " +
          "radial-gradient(900px circle at 90% 85%, rgba(124,58,237,0.16) 0%, transparent 55%), " +
          "linear-gradient(160deg, #f5f4fd 0%, #eceaf9 55%, #e4e2f6 100%)",
      }}
    >
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-[400px] rounded-xl border border-line bg-white p-7 shadow-[0_25px_75px_#1e1b3b30]"
      >
        <div className="mb-5 flex items-center gap-2.5">
          <span className="grid h-9 w-9 flex-shrink-0 place-items-center rounded-full bg-mint text-teal-dark">
            <KeyRound className="h-4 w-4" />
          </span>
          <div>
            <h1 className="m-0 text-lg font-bold tracking-tight text-ink">Set a new password</h1>
            <p className="m-0 text-2xs text-muted">Required before you can continue</p>
          </div>
        </div>

        {error ? <Alert variant="error" description={error} className="mb-4" /> : null}

        <div className="mb-3.5 flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-ink" htmlFor="cp-current">
            Temporary / current password
          </label>
          <Input
            id="cp-current"
            type="password"
            autoComplete="current-password"
            required
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
          />
        </div>
        <div className="mb-3.5 flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-ink" htmlFor="cp-new">
            New password
          </label>
          <Input
            id="cp-new"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
        </div>
        <div className="mb-5 flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-ink" htmlFor="cp-confirm">
            Confirm new password
          </label>
          <Input
            id="cp-confirm"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
          />
        </div>

        <Button type="submit" className="w-full" disabled={submitting}>
          {submitting ? "Saving…" : "Save password"}
        </Button>
      </form>
    </div>
  );
}
