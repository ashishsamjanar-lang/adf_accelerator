import type {
  ArtifactVersion,
  AuditEntry,
  Environment,
  GitSettings,
  PipelineDefinition,
  Project,
  ProjectSettings,
  Team,
  User,
} from "@/types";

/**
 * Neutral, empty `useResource`/`useProjectSettings` fallbacks — the value shown
 * while a fetch is loading, and the value kept if it fails. These intentionally
 * contain no sample/fabricated business data (no sample names, orgs, servers,
 * emails, or counts) — only structural product defaults already documented
 * elsewhere in the codebase (e.g. `featureBranchPattern`, `baseBranch: "dev"`,
 * matching `features/projects/project-settings-view.tsx`'s own inline
 * defaults) survive here, since those are generic template patterns, not
 * per-org business values (DEVELOPMENT_NOTES.md rule 5).
 */

export const EMPTY_USER: User = {
  id: "",
  name: "",
  email: "",
  role: "VIEWER",
  teamId: "",
  active: false,
};

export const EMPTY_TEAMS: Team[] = [];
export const EMPTY_PROJECTS: Project[] = [];
export const EMPTY_ENVIRONMENTS: Environment[] = [];
export const EMPTY_PIPELINES: PipelineDefinition[] = [];
export const EMPTY_ARTIFACTS: ArtifactVersion[] = [];
export const EMPTY_AUDIT: AuditEntry[] = [];
export const EMPTY_USERS: User[] = [];

export const EMPTY_GIT_SETTINGS: GitSettings = {
  provider: "github",
  repositoryUrl: "",
  repository: "",
  featureBranchPattern: "feature/{pipeline-name}-{timestamp}",
  baseBranch: "dev",
  artifactRoot: "adf/artifacts/",
  commitMessageConvention: "feat(artifacts): {pipeline} v{version}",
  prTitleTemplate: "feat: {pipeline} v{version}",
  prBodyTemplate: "## Summary\n- Pipeline: {pipeline}\n- Version: {version}",
  reviewers: [],
};

export const EMPTY_PROJECT_SETTINGS: ProjectSettings = {
  projectId: "",
  name: "",
  description: "",
  teamId: "",
  tags: [],
};
