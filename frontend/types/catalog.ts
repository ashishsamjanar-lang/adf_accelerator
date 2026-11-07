/**
 * §24.8 Git-backed catalog, §24.9/§24.10 catalog changes, §26.6 onboarding Git
 * drift — mirrors `backend/app/catalog_services.py`.
 */

/** ADF Git-mode folder type, or `metadata` for control-table SQL / objects.csv / manifest. */
export type CatalogArtifactType =
  | "pipeline"
  | "dataset"
  | "linked_service"
  | "trigger"
  | "factory"
  | "credential"
  | "data_flow"
  | "integration_runtime"
  | "managed_virtual_network"
  | "metadata";

export interface CatalogReference {
  /** ADF reference type, e.g. `LinkedServiceReference`. */
  type: string;
  name: string;
}

/**
 * §24.9 origin: `in_sync`/`drifted` = a file this app generated (hand-edited since, for `drifted`);
 * `foreign` = not generated here — edited in raw-JSON mode.
 */
export interface CatalogOwnership {
  state: "in_sync" | "drifted" | "foreign";
  pipelineId: string | null;
  pipelineName: string | null;
}

export interface CatalogEntrySummary {
  path: string;
  type: CatalogArtifactType;
  name: string;
}

export interface CatalogEntry extends CatalogEntrySummary {
  blobSha: string;
  references: CatalogReference[];
  parameters: string[];
  usedByCount: number;
  ownership: CatalogOwnership;
}

export interface CatalogListing {
  branch: string;
  baseBranch: string;
  /** Every branch that has been indexed for this project. */
  branches: string[];
  indexedAt: string | null;
  /** True when no Git credential resolved — the index is the app's own simulated pushes. */
  simulated: boolean;
  entries: CatalogEntry[];
}

export interface CatalogArtifactDetail extends CatalogEntrySummary {
  branch: string;
  blobSha: string;
  content: string;
  references: CatalogReference[];
  parameters: string[];
  usedBy: CatalogEntrySummary[];
  ownership: CatalogOwnership;
  simulated: boolean;
  indexedAt: string | null;
}

export type CatalogChangeMode = "update" | "rename" | "delete";

export interface CatalogChangeRequest {
  mode: CatalogChangeMode;
  path: string;
  content?: string;
  newName?: string;
  rewriteReferences?: boolean;
  force?: boolean;
  featureBranch: string;
  commitMessage?: string;
  prTitle?: string;
  gitToken?: string;
}

export interface CatalogChange {
  id: string;
  projectId: string;
  mode: CatalogChangeMode;
  path: string;
  newPath: string | null;
  featureBranch: string;
  baseBranch: string;
  commitMessage: string;
  filePaths: string[];
  deletePaths: string[];
  status: "pending" | "pushed" | "pr_created" | "failed" | "conflict";
  commitSha: string | null;
  prNumber: number | null;
  prUrl: string | null;
  simulated: boolean;
  error: string | null;
  createdAt: string | null;
}

export interface OnboardingDriftField {
  field: string;
  registry: string;
  git: string;
}

export interface OnboardingDriftRow {
  objectKey: string;
  status: "drifted" | "only_in_git" | "only_in_registry";
  fields: OnboardingDriftField[];
  /** Git's copy moved since the last successful sync. */
  changedInGit: boolean;
  /** The registry's copy moved since the last successful sync. */
  changedLocally: boolean;
  /** Git's row, typed as an onboarding object — what "Adopt Git" upserts. */
  gitRow: Record<string, unknown> | null;
}

export interface OnboardingGitDrift {
  branch: string;
  path: string;
  /** False until the catalog has been refreshed for this branch. */
  indexed: boolean;
  /** False when `metadata/objects.csv` isn't in the indexed repo. */
  available: boolean;
  blobSha: string | null;
  rows: OnboardingDriftRow[];
  inSync: number;
  lastSyncCommitSha?: string | null;
}
