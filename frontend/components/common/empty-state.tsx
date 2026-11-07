import type { ReactNode } from "react";
import { AlertTriangle, Inbox, Loader2 } from "lucide-react";

export type EmptyStateStatus = "empty" | "error" | "loading";

interface EmptyStateProps {
  title: string;
  message: string;
  action?: ReactNode;
  /**
   * Why there's nothing to show (gap #31). `empty` (default) renders the
   * caller's title/message; `error` means the fetch failed — the list may not
   * really be empty — so it renders `errorMessage` and hides `action`;
   * `loading` renders a neutral placeholder.
   */
  status?: EmptyStateStatus;
  errorMessage?: string;
}

/** Useful empty states (AGENTS.md UX principles section 3). */
export function EmptyState({ title, message, action, status = "empty", errorMessage }: EmptyStateProps) {
  const Icon = status === "error" ? AlertTriangle : status === "loading" ? Loader2 : Inbox;
  const shownTitle = status === "error" ? "Couldn't load this list" : status === "loading" ? "Loading…" : title;
  const shownMessage =
    status === "error"
      ? `${errorMessage ?? "The backend API is unreachable."} This is not an empty list — retry once the backend is reachable.`
      : status === "loading"
        ? "Fetching the latest data from the backend."
        : message;
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <div
        className={
          status === "error"
            ? "grid h-11 w-11 place-items-center rounded-lg bg-red-50 text-red-600"
            : "grid h-11 w-11 place-items-center rounded-lg bg-mint text-teal"
        }
      >
        <Icon className={status === "loading" ? "h-5 w-5 animate-spin" : "h-5 w-5"} />
      </div>
      <div>
        <p className="m-0 text-sm font-bold text-ink">{shownTitle}</p>
        <p className="m-0 mt-1 max-w-sm text-xs leading-5 text-muted">{shownMessage}</p>
      </div>
      {status === "empty" ? action : null}
    </div>
  );
}

/** Maps a `useResource` result onto an `EmptyState` status. */
export function resourceStatus(resource: { loading: boolean; failed: boolean }): EmptyStateStatus {
  return resource.failed ? "error" : resource.loading ? "loading" : "empty";
}
