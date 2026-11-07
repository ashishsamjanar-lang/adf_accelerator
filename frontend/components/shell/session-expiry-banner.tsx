"use client";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useIdleExpiryWarning } from "@/lib/api/hooks";

/** §27 session UX — warns shortly before an idle password-mode session expires. */
export function SessionExpiryBanner({ idleTimeoutMinutes }: { idleTimeoutMinutes: number | null }) {
  const { secondsLeft, staySignedIn } = useIdleExpiryWarning(idleTimeoutMinutes);
  if (secondsLeft === null) return null;

  const minutes = Math.floor(secondsLeft / 60);
  const seconds = String(secondsLeft % 60).padStart(2, "0");
  return (
    <Alert
      variant="warning"
      title="Session about to expire"
      description={`You'll be signed out for inactivity in ${minutes}:${seconds}.`}
      className="mx-6 mt-4 rounded-lg"
      action={
        <Button size="sm" variant="outline" onClick={staySignedIn}>
          Stay signed in
        </Button>
      }
    />
  );
}
