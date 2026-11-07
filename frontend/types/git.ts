/**
 * Git domain types: provider config, push request/response, and PR shape
 * (AGENTS.md section 9, §14, §23.6).
 */

/**
 * Git provider options for the accelerator.
 * Provider-specific authentication belongs behind an adapter interface.
 */
export type GitProvider = "github" | "azure_repos" | "gitlab" | "custom";

/**
 * Git defaults for a project (AGENTS.md section 5D and section 9).
 * These defaults prefill the push-to-git dialog but can be overridden per push.
 */
export interface GitSettings {
  provider: GitProvider;
  repositoryUrl: string;
  organization?: string;
  project?: string;
  repository: string;
  /** e.g. feature/{pipeline-name}-{timestamp} */
  featureBranchPattern: string;
  baseBranch: string;
  defaultBranch?: string;
  artifactRoot: string;
  commitMessageConvention: string;
  prTitleTemplate: string;
  prBodyTemplate: string;
  reviewers?: string[];
  /** Authentication method: pat, oauth, managed_identity, or ssh */
  authMethod?: "pat" | "oauth" | "managed_identity" | "ssh";
  /** Username or service account for Git operations */
  username?: string;
  /** Key Vault reference for credentials */
  credentialVault?: string;
  /** Git server URL for custom providers */
  gitServerUrl?: string;
  /** Browse path for the repository */
  gitBrowsePath?: string;
  /** Git connect mode: url or account */
  gitConnectMode?: "url" | "account";
  /** Connection status for testing */
  gitConnectionStatus?: "untested" | "testing" | "connected" | "failed";
  /** Connection test message */
  gitConnectionMessage?: string;
  /** When the connection was last tested */
  gitLastTestedAt?: string;
}

/**
 * Payload for POST /api/v1/git/push (AGENTS.md §14, §23.6).
 * `gitToken` is a per-push credential: forwarded to the worker for that
 * provider call only, never persisted and never logged. Omit it to use the
 * project's configured Key Vault credential reference.
 */
export interface GitPushRequestBody {
  pipelineId: string;
  artifactIds: string[];
  provider?: GitProvider;
  repositoryUrl?: string;
  featureBranch: string;
  baseBranch: string;
  commitMessage?: string;
  prTitle?: string;
  prBody?: string;
  reviewers?: string[];
  /** Per-push personal access token — never stored, never logged. */
  gitToken?: string;
}

export interface GitPushResponse {
  id: string;
  pipelineId: string;
  provider: string;
  repositoryUrl: string;
  featureBranch: string;
  baseBranch: string;
  commitSha?: string | null;
  status: string;
  error?: string | null;
  /** True when no Git credential resolved and the worker used the offline
   * SimulatedAdapter instead of a real provider call (AGENTS.md §23.12 gap #14). */
  simulated?: boolean;
  pullRequest?: {
    id: string;
    number?: number | null;
    url?: string | null;
    title: string;
    sourceBranch: string;
    targetBranch: string;
    status: string;
    simulated?: boolean;
  } | null;
}
