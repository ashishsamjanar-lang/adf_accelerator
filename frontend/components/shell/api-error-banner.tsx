"use client";

import { Alert } from "@/components/ui/alert";
import { useApiStatus } from "@/lib/api/hooks";

/**
 * Surfaces `ApiStatusContext` — the latest `useResource` fetch failure, if
 * any — so a real API/network problem is never silently masked by an
 * otherwise-normal-looking screen (there is no fixture fallback to fall
 * back to).
 */
export function ApiErrorBanner() {
  const { error } = useApiStatus();
  if (!error) return null;

  return (
    <Alert
      variant="error"
      title="Backend unreachable"
      description={error}
      className="mx-6 mt-4 rounded-lg"
    />
  );
}
