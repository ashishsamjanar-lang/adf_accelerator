"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import type { ProjectSettings, User } from "@/types";
import { api, ApiError, getDebugUserId, getLastApiActivityAt, SESSION_EXPIRED_EVENT, setDebugUserId } from "./client";

/**
 * React data helpers for the typed API client.
 *
 * useResource() calls the backend; on failure it keeps the caller's empty/
 * neutral value as `data` and sets `failed`/`error` so the UI can show a
 * real loading/error/empty state. It never substitutes fabricated sample
 * content — there is no fixture fallback. `ApiStatusProvider`/`useApiStatus`
 * lift the latest fetch error into the shell so `ApiErrorBanner`
 * (`components/shell/api-error-banner.tsx`) can surface it once instead of
 * per-resource.
 */

interface ApiStatusState {
  error: string | null;
  setError: (message: string | null) => void;
}

const ApiStatusContext = createContext<ApiStatusState>({
  error: null,
  setError: () => undefined,
});

export function ApiStatusProvider({ children }: { children: ReactNode }) {
  const [error, setErrorState] = useState<string | null>(null);
  const setError = useCallback((message: string | null) => setErrorState(message), []);
  return <ApiStatusContext.Provider value={{ error, setError }}>{children}</ApiStatusContext.Provider>;
}

export function useApiStatus(): ApiStatusState {
  return useContext(ApiStatusContext);
}

export interface Resource<T> {
  data: T;
  loading: boolean;
  failed: boolean;
  error?: string;
  reload: () => void;
}

export function useResource<T>(fetcher: () => Promise<T>, emptyValue: T, key: string): Resource<T> {
  const { setError } = useApiStatus();
  const [data, setData] = useState<T>(emptyValue);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [error, setErrorMessage] = useState<string | undefined>();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetcher();
      setData(result);
      setFailed(false);
      setErrorMessage(undefined);
      setError(null);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Unable to reach the backend API.";
      setData(emptyValue);
      setFailed(true);
      setErrorMessage(message);
      setError(message);
    } finally {
      setLoading(false);
    }
    // Key captures the identity of the fetch target; see callers for memoised fetchers.
  }, [fetcher, emptyValue, key, setError]);

  useEffect(() => {
    void load();
  }, [load]);

  return { data, loading, failed, error, reload: () => void load() };
}

export interface ProjectSettingsResource extends Resource<ProjectSettings> {
  saving: boolean;
  saveError?: string;
  /** PUTs the full settings document, then re-fetches so `data` reflects the persisted state. */
  save: (document: ProjectSettings) => Promise<boolean>;
}

/**
 * Per-project settings (§5), including `namingConvention` (§25 naming
 * standards — prefixes/suffixes/case per artifact type). Unlike
 * `useAcceleratorData`'s bag, this is fetched per-project on demand since
 * the project in view is chosen client-side (Project Settings, Standards).
 *
 * `emptyValue` should be a stable, module-level constant (e.g.
 * `EMPTY_PROJECT_SETTINGS`) — a fresh object literal on every render defeats
 * `useResource`'s memoisation and re-fetches in a loop.
 */
export function useProjectSettings(projectId: string | undefined, emptyValue: ProjectSettings): ProjectSettingsResource {
  const fetcher = useCallback(() => {
    if (!projectId) return Promise.reject(new Error("No project selected."));
    return api.projects.settings(projectId);
  }, [projectId]);
  const resource = useResource(fetcher, emptyValue, `project-settings:${projectId ?? "none"}`);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string>();

  const save = useCallback(
    async (document: ProjectSettings) => {
      if (!projectId) {
        setSaveError("No project selected.");
        return false;
      }
      setSaving(true);
      setSaveError(undefined);
      try {
        await api.projects.updateSettings(projectId, document);
        resource.reload();
        return true;
      } catch (err) {
        setSaveError(err instanceof ApiError ? err.message : "Unable to save project settings.");
        return false;
      } finally {
        setSaving(false);
      }
    },
    [projectId, resource],
  );

  return { ...resource, saving, saveError, save };
}

export interface DevIdentity {
  /** True once `/auth/dev/users` has answered — i.e. the live backend is reachable and running AUTH_MODE=mock. */
  available: boolean;
  users: User[];
  currentUserId: string | null;
  switching: boolean;
  error?: string;
  /** Validate + persist the chosen identity; `onSwitched` runs after success (reload the rest of the app's data). */
  switchTo: (userId: string, onSwitched?: () => void) => Promise<void>;
}

/**
 * Dev-only user-switcher (AGENTS.md §27.2/§27.6, §23.12 gap #16).
 *
 * Probes `GET /api/v1/auth/dev/users` once on mount — it 404s whenever the
 * backend isn't running `AUTH_MODE=mock` (including when the backend is
 * unreachable, since there's no live backend to ask), so `available` doubles
 * as "is there anything to show here". A confirmed switch is persisted via
 * `setDebugUserId` (localStorage-backed) so every subsequent request acts as
 * the chosen user until switched again.
 */
export function useDevIdentity(): DevIdentity {
  const [available, setAvailable] = useState(false);
  const [users, setUsers] = useState<User[]>([]);
  const [currentUserId, setCurrentUserId] = useState<string | null>(() => getDebugUserId());
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    let cancelled = false;
    api.auth.dev
      .users()
      .then((list) => {
        if (cancelled) return;
        setUsers(list);
        setAvailable(true);
      })
      .catch(() => {
        // 404 (not mock mode) or unreachable — no switcher to show.
        if (!cancelled) setAvailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const switchTo = useCallback(async (userId: string, onSwitched?: () => void) => {
    setSwitching(true);
    setError(undefined);
    try {
      await api.auth.dev.switchUser(userId);
      setDebugUserId(userId);
      setCurrentUserId(userId);
      onSwitched?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to switch the acting user.");
    } finally {
      setSwitching(false);
    }
  }, []);

  return { available, users, currentUserId, switching, error, switchTo };
}

/**
 * Gates the app shell behind a real login when the backend runs
 * `AUTH_MODE=password` (AGENTS.md §27.2 extension). Under `mock` (the
 * default — nothing changes) or `oidc`/`saml` this resolves to `"ready"`
 * immediately, same as before this hook existed.
 *
 * Deliberately bypasses `useResource`'s error handling: falling back to a
 * placeholder user on a 401 would silently show real app content without a
 * real session, which is fine for `mock`'s "API is down" case but never
 * correct once real auth is switched on.
 */
export type AuthGateStatus = "checking" | "login_required" | "must_change_password" | "ready";

export interface AuthGate {
  status: AuthGateStatus;
  user: User | null;
  /** True when the gate fell back to login because an in-session request came back 401. */
  sessionExpired: boolean;
  /** Server idle-session length in minutes (password mode only) — null disables the pre-expiry warning. */
  idleTimeoutMinutes: number | null;
  /** Re-runs the me() check — call after a successful login/password change. */
  recheck: () => void;
  logout: () => Promise<void>;
}

export function useAuthGate(): AuthGate {
  const [status, setStatus] = useState<AuthGateStatus>("checking");
  const [user, setUser] = useState<User | null>(null);
  const [tick, setTick] = useState(0);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [idleTimeoutMinutes, setIdleTimeoutMinutes] = useState<number | null>(null);

  // A 401 mid-session (idle/absolute timeout, admin reset) only matters once we know we're in
  // password mode with a signed-in user — under `mock` a 401 is just another API error.
  useEffect(() => {
    if (status !== "ready" || !user) return;
    const onExpired = () => {
      setUser(null);
      setSessionExpired(true);
      setStatus("login_required");
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [status, user]);

  useEffect(() => {
    let cancelled = false;
    api.auth
      .config()
      .then((config) => {
        if (cancelled) return;
        setIdleTimeoutMinutes(config.idleTimeoutMinutes ?? null);
        if (config.authMode !== "password") {
          setStatus("ready");
          return;
        }
        return api.auth
          .me()
          .then((me) => {
            if (cancelled) return;
            setUser(me);
            setStatus(me.mustChangePassword ? "must_change_password" : "ready");
          })
          .catch(() => {
            if (!cancelled) setStatus("login_required");
          });
      })
      .catch(() => {
        // Can't even reach /auth/config — treat like mock/offline so the
        // existing error handling in useResource still applies.
        if (!cancelled) setStatus("ready");
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const recheck = useCallback(() => {
    setSessionExpired(false);
    setTick((t) => t + 1);
  }, []);
  const logout = useCallback(async () => {
    await api.auth.logout().catch(() => undefined);
    setUser(null);
    setStatus("login_required");
  }, []);

  return { status, user, sessionExpired, idleTimeoutMinutes, recheck, logout };
}

/** Seconds before idle expiry at which `useIdleExpiryWarning` starts warning. */
export const IDLE_WARNING_LEAD_SECONDS = 120;

export interface IdleExpiryWarning {
  /** Seconds until the session idles out, or null while outside the warning window. */
  secondsLeft: number | null;
  /** Makes one authenticated call, which slides the server's idle expiry forward. */
  staySignedIn: () => void;
}

/**
 * §27 pre-expiry warning: estimates idle expiry from the last successful API call
 * (`getLastApiActivityAt`) and surfaces a countdown in the final
 * `IDLE_WARNING_LEAD_SECONDS`. Inert when `idleTimeoutMinutes` is null (every mode but password).
 */
export function useIdleExpiryWarning(idleTimeoutMinutes: number | null): IdleExpiryWarning {
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);

  useEffect(() => {
    if (!idleTimeoutMinutes) {
      setSecondsLeft(null);
      return;
    }
    const tick = () => {
      const left = Math.ceil((getLastApiActivityAt() + idleTimeoutMinutes * 60_000 - Date.now()) / 1000);
      setSecondsLeft(left > 0 && left <= IDLE_WARNING_LEAD_SECONDS ? left : null);
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [idleTimeoutMinutes]);

  const staySignedIn = useCallback(() => {
    // A 401 here already fires SESSION_EXPIRED_EVENT, which `useAuthGate` handles.
    api.auth.me().then(() => setSecondsLeft(null), () => undefined);
  }, []);

  return { secondsLeft, staySignedIn };
}
