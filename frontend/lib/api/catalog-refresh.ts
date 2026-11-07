import { api } from "@/lib/api/client";
import { pollUntil } from "@/lib/api/poll";
import type { IntegrationJob } from "@/types";

/**
 * §24.8 re-index one branch of the project's repo: queue the worker's `catalog_refresh` job and wait
 * for it to settle. Resolves with the last job state seen — a still-running job finishes out of band.
 */
export async function refreshCatalog(projectId: string, branch?: string, gitToken?: string): Promise<IntegrationJob> {
  const job = await api.catalog.refresh(projectId, { branch: branch || undefined, gitToken: gitToken || undefined });
  return pollUntil(
    () => api.integrations.job(projectId, job.id),
    (j) => j.status === "succeeded" || j.status === "failed",
  );
}
