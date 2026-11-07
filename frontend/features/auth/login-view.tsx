"use client";

/**
 * Username (or email) + password sign-in (AGENTS.md §27.2 extension) — rendered only when
 * the backend reports `AUTH_MODE=password` (`useAuthGate`, `lib/api/hooks.tsx`).
 * There is no "sign up" link anywhere here: new accounts are always
 * provisioned by a platform admin (`POST /admin/users`), never self-service.
 */
import { useState } from "react";
import { LockKeyhole } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert } from "@/components/ui/alert";
import { api, ApiError } from "@/lib/api/client";

export function LoginView({ onSignedIn, sessionExpired = false }: { onSignedIn: () => void; sessionExpired?: boolean }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(undefined);
    try {
      await api.auth.login(username, password);
      onSignedIn();
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
            <LockKeyhole className="h-4 w-4" />
          </span>
          <div>
            <h1 className="m-0 text-lg font-bold tracking-tight text-ink">Sign in</h1>
            <p className="m-0 text-2xs text-muted">ADF Pipeline Accelerator</p>
          </div>
        </div>

        {error ? <Alert variant="error" description={error} className="mb-4" /> : null}
        {!error && sessionExpired ? (
          <Alert variant="info" description="Your session expired. Sign in again to continue." className="mb-4" />
        ) : null}

        <div className="mb-3.5 flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-ink" htmlFor="login-username">
            Username or email
          </label>
          <Input
            id="login-username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </div>
        <div className="mb-5 flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-ink" htmlFor="login-password">
            Password
          </label>
          <Input
            id="login-password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        <Button type="submit" className="w-full" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </Button>
        <p className="mb-0 mt-4 text-2xs text-faint">
          No account? A platform admin provisions access — there is no self-service sign-up.
        </p>
      </form>
    </div>
  );
}
