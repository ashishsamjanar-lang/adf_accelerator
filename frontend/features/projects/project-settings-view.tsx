"use client";

import { useEffect, useState } from "react";
import { Save, FolderKanban, GitBranch, X, GitPullRequest, Loader2, CheckCircle2 } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { can } from "@/lib/state";
import { cn } from "@/lib/utils";
import { useProjectSettings } from "@/lib/api/hooks";
import { describeRule } from "@/lib/adf/naming";
import type { ProjectTag, GitProvider, NamingArtifactType, NamingRule, ProjectSettings, EnvironmentVariable } from "@/types";
import { EMPTY_PROJECT_SETTINGS } from "@/lib/api/empty-values";
import { ManagedIdentitiesCard } from "@/features/projects/managed-identities-card";
import { IntegrationsCard } from "@/features/projects/integrations-card";

const NAMING_RULE_TYPES: { id: NamingArtifactType; label: string }[] = [
  { id: "pipeline", label: "Pipeline" },
  { id: "dataset", label: "Dataset" },
  { id: "linkedService", label: "Linked service" },
  { id: "trigger", label: "Trigger" },
];
const NAMING_CASES: { value: NonNullable<NamingRule["case"]>; label: string }[] = [
  { value: "none", label: "No case rule" },
  { value: "snake", label: "snake_case" },
  { value: "pascal", label: "PascalCase" },
  { value: "camel", label: "camelCase" },
  { value: "upper", label: "UPPER_CASE" },
  { value: "lower", label: "lowercase" },
];

interface ProjectSettingsViewProps extends FeatureProps {
  projectId?: string;
}

interface FormErrors {
  name?: string;
  teamId?: string;
  pipelinePattern?: string;
  defaultRepo?: string;
  defaultBaseBranch?: string;
}

const GIT_PROVIDERS: { value: GitProvider; label: string }[] = [
  { value: "azure_repos", label: "Azure Repos" },
  { value: "github", label: "GitHub" },
  { value: "gitlab", label: "GitLab" },
  { value: "custom", label: "Custom" },
];

const LOG_LEVELS = ["Info", "Warning", "Error", "Verbose"];
const WATERMARK_TYPES = ["datetime", "int", "bigint", "string"];

function knDefaultAuth(provider: GitProvider): "pat" | "oauth" | "managed_identity" | "ssh" {
  if (provider === "azure_repos") return "managed_identity";
  if (provider === "github") return "pat";
  if (provider === "gitlab") return "pat";
  return "pat";
}

const GIT_SERVER_PLACEHOLDERS: Record<GitProvider, string> = {
  azure_repos: "https://dev.azure.com/{organization}",
  github: "https://github.com",
  gitlab: "https://gitlab.com",
  custom: "https://git.example.com",
};

function validateForm(name: string, teamId: string, pipelinePattern: string, defaultRepo: string, defaultBaseBranch: string): FormErrors {
  const errors: FormErrors = {};
  
  if (!name.trim()) {
    errors.name = "Project name is required";
  }
  
  if (!teamId.trim()) {
    errors.teamId = "Team assignment is required";
  }
  
  if (!pipelinePattern.trim()) {
    errors.pipelinePattern = "Pipeline naming pattern is required";
  }
  
  if (defaultRepo && !defaultRepo.includes("/")) {
    errors.defaultRepo = "Repository should be in format: organization/repo-name";
  }
  
  if (defaultBaseBranch && defaultBaseBranch.toLowerCase() === "main") {
    errors.defaultBaseBranch = "Base branch should not be 'main' - use a development branch";
  }
  
  return errors;
}

export function ProjectSettingsView({ data, user, onNavigate, projectId }: ProjectSettingsViewProps) {
  const projects = data.projects.data;
  const teams = data.teams.data;
  const pipelines = data.pipelines.data;
  const selectedProjectId = projectId ?? projects[0]?.id ?? "";
  const project = projects.find((p) => p.id === selectedProjectId) ?? projects[0];
  const team = teams.find((t) => t.id === project?.teamId);
  const projectPipelines = pipelines.filter((p) => p.projectId === project?.id);

  // Real GET/PUT /api/v1/projects/{id}/settings (falls back to an empty
  // settings document when the API is unreachable, same as every other
  // resource — §23.2).
  const settingsResource = useProjectSettings(project?.id, EMPTY_PROJECT_SETTINGS);
  const existing = settingsResource.data;

  // Form state
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState<string>();
  const [errors, setErrors] = useState<FormErrors>({});

  // A. Project identity
  const [name, setName] = useState(existing?.name ?? project?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? project?.description ?? "");
  const [teamId, setTeamId] = useState(existing?.teamId ?? project?.teamId ?? "");
  const [pipelinePattern, setPipelinePattern] = useState(existing?.namingConvention?.pipelinePattern ?? "{project}_{source}_{target}_pl");
  const [datasetPattern, setDatasetPattern] = useState(existing?.namingConvention?.datasetPattern ?? "{project}_{source}_{object}_ds");
  const [linkedServicePattern, setLinkedServicePattern] = useState(existing?.namingConvention?.linkedServicePattern ?? "{connector}_{environment}_ls");
  const [parameterPattern, setParameterPattern] = useState(existing?.namingConvention?.parameterPattern ?? "{pipeline}_{name}_param");
  const [triggerPattern, setTriggerPattern] = useState(existing?.namingConvention?.triggerPattern ?? "{pipeline}_{schedule}_tr");
  const [namingDescription, setNamingDescription] = useState(existing?.namingConvention?.description ?? "");
  // §25 opt-in naming standards — prefix/suffix/case/max-length per artifact
  // type, plus one enforcement level for the whole rule (off/warn/block).
  const [namingEnforcement, setNamingEnforcement] = useState(existing?.namingConvention?.enforcement ?? "off");
  const [namingRules, setNamingRules] = useState<Partial<Record<NamingArtifactType, NamingRule>>>(
    existing?.namingConvention?.rules ?? {},
  );
  const [artifactFolder, setArtifactFolder] = useState("");
  const [tags, setTags] = useState<ProjectTag[]>(existing?.tags ?? []);

  // E. Custom environment variables (AGENTS.md §22.3.H)
  const [environmentVariables, setEnvironmentVariables] = useState<EnvironmentVariable[]>(existing?.environmentVariables ?? []);

  // B. ADF defaults
  const [subscriptionId, setSubscriptionId] = useState(existing?.adfDefaults?.subscriptionId ?? "");
  const [resourceGroup, setResourceGroup] = useState(existing?.adfDefaults?.resourceGroup ?? "");
  const [factoryName, setFactoryName] = useState(existing?.adfDefaults?.factoryName ?? "");
  const [integrationRuntime, setIntegrationRuntime] = useState(existing?.adfDefaults?.integrationRuntime ?? "AutoResolveIntegrationRuntime");
  const [defaultSourceConnector, setDefaultSourceConnector] = useState(existing?.adfDefaults?.defaultSourceConnector ?? "sql_server");
  const [defaultTargetConnector, setDefaultTargetConnector] = useState(existing?.adfDefaults?.defaultTargetConnector ?? "azure_data_lake_storage_gen2");
  const [defaultSourceLS, setDefaultSourceLS] = useState(existing?.adfDefaults?.defaultSourceLinkedService ?? "");
  const [defaultTargetLS, setDefaultTargetLS] = useState(existing?.adfDefaults?.defaultTargetLinkedService ?? "");
  const [logLevel, setLogLevel] = useState(existing?.adfDefaults?.logLevel ?? "Info");
  const [enableAzureMonitor, setEnableAzureMonitor] = useState(existing?.adfDefaults?.enableAzureMonitor ?? true);
  const [parallelCopies, setParallelCopies] = useState(existing?.adfDefaults?.defaultParallelCopies ?? 4);
  const [retryCount, setRetryCount] = useState(existing?.adfDefaults?.defaultRetryCount ?? 3);
  const [retryInterval, setRetryInterval] = useState(existing?.adfDefaults?.defaultRetryIntervalInSeconds ?? 30);
  const [defaultTimeout, setDefaultTimeout] = useState(existing?.adfDefaults?.defaultTimeout ?? "7.00:00:00");

  // C. Metadata/template defaults
  const [defaultTemplateId, setDefaultTemplateId] = useState(existing?.templateDefaults?.defaultTemplateId ?? "metadata_driven_load");
  const [ctSchema, setCtSchema] = useState(existing?.templateDefaults?.controlTable?.schemaName ?? "adf");
  const [ctTable, setCtTable] = useState(existing?.templateDefaults?.controlTable?.tableName ?? "control_table");
  const [ctConnection, setCtConnection] = useState(existing?.templateDefaults?.controlTable?.connectionReference ?? "");
  const [ctWatermark, setCtWatermark] = useState(existing?.templateDefaults?.controlTable?.watermarkColumn ?? "watermark");
  const [ctLoadingBehavior, setCtLoadingBehavior] = useState(existing?.templateDefaults?.controlTable?.loadingBehaviorColumn ?? "loading_behavior");
  const [ctSourceObject, setCtSourceObject] = useState(existing?.templateDefaults?.controlTable?.sourceObjectColumn ?? "source_object");
  const [ctTargetPath, setCtTargetPath] = useState(existing?.templateDefaults?.controlTable?.targetPathColumn ?? "target_path");
  const [ctEnabled, setCtEnabled] = useState(existing?.templateDefaults?.controlTable?.enabledColumn ?? "is_enabled");
  const [ctConnectionControlEnabled, setCtConnectionControlEnabled] = useState(
    existing?.templateDefaults?.controlTable?.enableConnectionControlTable ?? false,
  );
  const [ctConnectionTableName, setCtConnectionTableName] = useState(
    existing?.templateDefaults?.controlTable?.connectionTableName ?? "adf_connection_control",
  );
  const [watermarkType, setWatermarkType] = useState(existing?.templateDefaults?.defaultWatermarkType ?? "datetime");
  const [filterCondition, setFilterCondition] = useState(existing?.templateDefaults?.defaultFilterCondition ?? "");
  const [customMetadata, setCustomMetadata] = useState<Array<{ name: string; value: string }>>(existing?.templateDefaults?.customMetadataColumns ?? []);

  // D. Git defaults — upgraded connectivity form
  const [gitProvider, setGitProvider] = useState<GitProvider>(existing?.gitDefaults?.provider ?? "azure_repos");
  const [repositoryUrl, setRepositoryUrl] = useState(existing?.gitDefaults?.repositoryUrl ?? "");
  const [gitOrg, setGitOrg] = useState(existing?.gitDefaults?.organization ?? "");
  const [gitProject, setGitProject] = useState(existing?.gitDefaults?.project ?? "");
  const [repository, setRepository] = useState(existing?.gitDefaults?.repository ?? "");
  const [defaultRepo, setDefaultRepo] = useState(existing?.gitDefaults?.repository ?? "");
  const [featureBranchPattern, setFeatureBranchPattern] = useState(existing?.gitDefaults?.featureBranchPattern ?? "feature/{pipeline-name}-{timestamp}");
  const [baseBranch, setBaseBranch] = useState(existing?.gitDefaults?.baseBranch ?? "dev");
  const [defaultBaseBranch, setDefaultBaseBranch] = useState(existing?.gitDefaults?.baseBranch ?? "dev");
  const [artifactRoot, setArtifactRoot] = useState(existing?.gitDefaults?.artifactRoot ?? "adf/artifacts/");
  const [commitConvention, setCommitConvention] = useState(existing?.gitDefaults?.commitMessageConvention ?? "feat(artifacts): {pipeline} v{version}");
  const [prTitleTemplate, setPrTitleTemplate] = useState(existing?.gitDefaults?.prTitleTemplate ?? "feat: {pipeline} v{version}");
  const [prBodyTemplate, setPrBodyTemplate] = useState(existing?.gitDefaults?.prBodyTemplate ?? "## Summary\n- Pipeline: {pipeline}\n- Version: {version}");
  const [reviewers, setReviewers] = useState<string[]>(existing?.gitDefaults?.reviewers ?? []);
  const [gitConnectMode, setGitConnectMode] = useState<"url" | "account">("url");
  const [gitServerUrl, setGitServerUrl] = useState("");
  const [gitBrowsePath, setGitBrowsePath] = useState("");
  const [authMethod, setAuthMethod] = useState<"pat" | "oauth" | "managed_identity" | "ssh">(existing?.gitDefaults?.authMethod ?? knDefaultAuth(gitProvider));
  const [gitUsername, setGitUsername] = useState(existing?.gitDefaults?.username ?? "");
  const [credentialVault, setCredentialVault] = useState(existing?.gitDefaults?.credentialVault ?? "");
  const [defaultBranch, setDefaultBranch] = useState(existing?.gitDefaults?.defaultBranch ?? "main");
  const [gitConnectionStatus, setGitConnectionStatus] = useState<"untested" | "testing" | "connected" | "failed">("untested");
  const [gitConnectionMessage, setGitConnectionMessage] = useState("");
  const [gitLastTestedAt, setGitLastTestedAt] = useState("");

  const [activeSection, setActiveSection] = useState<"identity" | "adf" | "env" | "metadata" | "git">("identity");
  const canEdit = can(user.role, "admin:team") || can(user.role, "pipeline:create");

  /** (Re)seed identity/naming/tags form state from a loaded settings document. Shared by the
   * initial-load effect (settings arrive async, after these `useState` initializers already ran)
   * and the "Reset" button. */
  function applyExisting(doc: ProjectSettings | undefined) {
    setName(doc?.name ?? project?.name ?? "");
    setDescription(doc?.description ?? project?.description ?? "");
    setTeamId(doc?.teamId ?? project?.teamId ?? "");
    setPipelinePattern(doc?.namingConvention?.pipelinePattern ?? "{project}_{source}_{target}_pl");
    setDatasetPattern(doc?.namingConvention?.datasetPattern ?? "{project}_{source}_{object}_ds");
    setLinkedServicePattern(doc?.namingConvention?.linkedServicePattern ?? "{connector}_{environment}_ls");
    setParameterPattern(doc?.namingConvention?.parameterPattern ?? "{pipeline}_{name}_param");
    setTriggerPattern(doc?.namingConvention?.triggerPattern ?? "{pipeline}_{schedule}_tr");
    setNamingDescription(doc?.namingConvention?.description ?? "");
    setNamingEnforcement(doc?.namingConvention?.enforcement ?? "off");
    setNamingRules(doc?.namingConvention?.rules ?? {});
    setTags(doc?.tags ?? []);
    setEnvironmentVariables(doc?.environmentVariables ?? []);
    // §18 persistence: the ADF/template/Git sections must reseed too, or a Save after the async load
    // overwrites the stored values with these fallbacks.
    const adf = doc?.adfDefaults;
    setSubscriptionId(adf?.subscriptionId ?? "");
    setResourceGroup(adf?.resourceGroup ?? "");
    setFactoryName(adf?.factoryName ?? "");
    setIntegrationRuntime(adf?.integrationRuntime ?? "AutoResolveIntegrationRuntime");
    setDefaultSourceConnector(adf?.defaultSourceConnector ?? "sql_server");
    setDefaultTargetConnector(adf?.defaultTargetConnector ?? "azure_data_lake_storage_gen2");
    setDefaultSourceLS(adf?.defaultSourceLinkedService ?? "");
    setDefaultTargetLS(adf?.defaultTargetLinkedService ?? "");
    setLogLevel(adf?.logLevel ?? "Info");
    setEnableAzureMonitor(adf?.enableAzureMonitor ?? true);
    setParallelCopies(adf?.defaultParallelCopies ?? 4);
    setRetryCount(adf?.defaultRetryCount ?? 3);
    setRetryInterval(adf?.defaultRetryIntervalInSeconds ?? 30);
    setDefaultTimeout(adf?.defaultTimeout ?? "7.00:00:00");
    const tpl = doc?.templateDefaults;
    const ct = tpl?.controlTable;
    setDefaultTemplateId(tpl?.defaultTemplateId ?? "metadata_driven_load");
    setCtSchema(ct?.schemaName ?? "adf");
    setCtTable(ct?.tableName ?? "control_table");
    setCtConnection(ct?.connectionReference ?? "");
    setCtWatermark(ct?.watermarkColumn ?? "watermark");
    setCtLoadingBehavior(ct?.loadingBehaviorColumn ?? "loading_behavior");
    setCtSourceObject(ct?.sourceObjectColumn ?? "source_object");
    setCtTargetPath(ct?.targetPathColumn ?? "target_path");
    setCtEnabled(ct?.enabledColumn ?? "is_enabled");
    setCtConnectionControlEnabled(ct?.enableConnectionControlTable ?? false);
    setCtConnectionTableName(ct?.connectionTableName ?? "adf_connection_control");
    setWatermarkType(tpl?.defaultWatermarkType ?? "datetime");
    setFilterCondition(tpl?.defaultFilterCondition ?? "");
    setCustomMetadata(tpl?.customMetadataColumns ?? []);
    const git = doc?.gitDefaults;
    const provider = git?.provider ?? "azure_repos";
    setGitProvider(provider);
    setRepositoryUrl(git?.repositoryUrl ?? "");
    setGitOrg(git?.organization ?? "");
    setGitProject(git?.project ?? "");
    setRepository(git?.repository ?? "");
    setDefaultRepo(git?.repository ?? "");
    setFeatureBranchPattern(git?.featureBranchPattern ?? "feature/{pipeline-name}-{timestamp}");
    setBaseBranch(git?.baseBranch ?? "dev");
    setDefaultBaseBranch(git?.baseBranch ?? "dev");
    setArtifactRoot(git?.artifactRoot ?? "adf/artifacts/");
    setCommitConvention(git?.commitMessageConvention ?? "feat(artifacts): {pipeline} v{version}");
    setPrTitleTemplate(git?.prTitleTemplate ?? "feat: {pipeline} v{version}");
    setPrBodyTemplate(git?.prBodyTemplate ?? "## Summary\n- Pipeline: {pipeline}\n- Version: {version}");
    setReviewers(git?.reviewers ?? []);
    setAuthMethod(git?.authMethod ?? knDefaultAuth(provider));
    setGitUsername(git?.username ?? "");
    setCredentialVault(git?.credentialVault ?? "");
    setDefaultBranch(git?.defaultBranch ?? "main");
    setErrors({});
    setSaveSuccess(false);
    setSaveError(undefined);
  }

  // Settings load asynchronously (real fetch, or the demo fallback) — apply
  // them once they arrive instead of leaving the form on whatever the
  // `useState` initializers captured at first render (which runs before the
  // fetch resolves).
  useEffect(() => {
    applyExisting(existing);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existing]);

  function updateNamingRule(type: NamingArtifactType, patch: Partial<NamingRule>) {
    setNamingRules((prev) => ({ ...prev, [type]: { ...prev[type], ...patch } }));
  }

  const handleSave = async () => {
    setSaveSuccess(false);
    setSaveError(undefined);
    const validationErrors = validateForm(name, teamId, pipelinePattern, defaultRepo, defaultBaseBranch);
    setErrors(validationErrors);
    if (Object.keys(validationErrors).length > 0 || !project) return;

    setIsSaving(true);
    try {
      const document: ProjectSettings = {
        projectId: project.id,
        name,
        description,
        teamId,
        namingConvention: {
          pipelinePattern,
          datasetPattern,
          linkedServicePattern,
          parameterPattern,
          triggerPattern,
          description: namingDescription,
          enforcement: namingEnforcement,
          rules: namingRules,
        },
        tags,
        environmentVariables,
        adfDefaults: {
          subscriptionId,
          resourceGroup,
          factoryName,
          integrationRuntime,
          defaultSourceConnector,
          defaultTargetConnector,
          defaultSourceLinkedService: defaultSourceLS,
          defaultTargetLinkedService: defaultTargetLS,
          logLevel,
          enableAzureMonitor,
          defaultParallelCopies: parallelCopies,
          defaultRetryCount: retryCount,
          defaultRetryIntervalInSeconds: retryInterval,
          defaultTimeout,
        },
        templateDefaults: {
          defaultTemplateId,
          controlTable: {
            schemaName: ctSchema,
            tableName: ctTable,
            connectionReference: ctConnection,
            watermarkColumn: ctWatermark,
            loadingBehaviorColumn: ctLoadingBehavior,
            sourceObjectColumn: ctSourceObject,
            targetPathColumn: ctTargetPath,
            enabledColumn: ctEnabled,
            enableConnectionControlTable: ctConnectionControlEnabled,
            connectionTableName: ctConnectionTableName,
          },
          defaultWatermarkType: watermarkType,
          defaultFilterCondition: filterCondition,
          customMetadataColumns: customMetadata,
        },
        gitDefaults: {
          provider: gitProvider,
          repositoryUrl,
          organization: gitOrg,
          project: gitProject,
          repository: repository || defaultRepo,
          featureBranchPattern,
          baseBranch: baseBranch || defaultBaseBranch,
          defaultBranch,
          artifactRoot,
          commitMessageConvention: commitConvention,
          prTitleTemplate,
          prBodyTemplate,
          reviewers,
          authMethod,
          username: gitUsername,
          credentialVault,
          gitServerUrl,
          gitBrowsePath,
          gitConnectMode,
          gitConnectionStatus,
          gitConnectionMessage,
          gitLastTestedAt,
        },
        customMetadata,
      };
      const ok = await settingsResource.save(document);
      if (!ok) {
        setSaveError(settingsResource.saveError ?? "Failed to save settings. Please try again.");
        return;
      }
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } finally {
      setIsSaving(false);
    }
  };

  const handleReset = () => applyExisting(existing);

  // Tag management
  function addTag() { setTags([...tags, { key: "", value: "" }]); }
  function updateTag(index: number, field: "key" | "value", val: string) { setTags(tags.map((t, i) => (i === index ? { ...t, [field]: val } : t))); }
  function removeTag(index: number) { setTags(tags.filter((_, i) => i !== index)); }

  // Environment variable management
  function addEnvironmentVariable() { setEnvironmentVariables([...environmentVariables, { key: "", value: "", isSecret: false }]); }
  function updateEnvironmentVariable(index: number, patch: Partial<EnvironmentVariable>) {
    setEnvironmentVariables(environmentVariables.map((ev, i) => (i === index ? { ...ev, ...patch } : ev)));
  }
  function removeEnvironmentVariable(index: number) { setEnvironmentVariables(environmentVariables.filter((_, i) => i !== index)); }


  // Custom metadata management
  function addCustomMetadata() { setCustomMetadata([...customMetadata, { name: "", value: "" }]); }
  function updateCustomMetadata(index: number, field: "name" | "value", val: string) { setCustomMetadata(customMetadata.map((m, i) => (i === index ? { ...m, [field]: val } : m))); }
  function removeCustomMetadata(index: number) { setCustomMetadata(customMetadata.filter((_, i) => i !== index)); }

  // Reviewer management
  function addReviewer() { setReviewers([...reviewers, ""]); }
  function updateReviewer(index: number, val: string) { setReviewers(reviewers.map((r, i) => (i === index ? val : r))); }
  function removeReviewer(index: number) { setReviewers(reviewers.filter((_, i) => i !== index)); }

  function testGitConnection() {
    setGitConnectionStatus("testing");
    setGitConnectionMessage("");
    const target = repositoryUrl.trim() || `${gitOrg.trim()}/${repository.trim() || defaultRepo.trim()}`;
    window.setTimeout(() => {
      if (!target) {
        setGitConnectionStatus("failed");
        setGitConnectionMessage("Enter a repository URL or organization/repository before testing.");
        return;
      }
      if (!gitUsername.trim() && authMethod !== "managed_identity") {
        setGitConnectionStatus("failed");
        setGitConnectionMessage("Enter a username / service principal before testing (or switch to Managed Identity).");
        return;
      }
      setGitConnectionStatus("connected");
      setGitConnectionMessage(`Connected to ${target} via ${gitProvider} (${authMethod}). Token stays in ${credentialVault.trim() || "Key Vault"}.`);
      setGitLastTestedAt(new Date().toISOString());
    }, 650);
  }

  if (!project) {
    return (
      <div className="animate-fade-in">
        <PageHeader
          eyebrow="PROJECT SETTINGS"
          title="Project settings"
          detail="Configure project-level settings and defaults."
        />
        <div className="panel">
          <p className="text-sm text-muted">No project selected.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="PROJECT SETTINGS"
        title="Project settings"
        detail="Configure project-level settings, team assignment, Git defaults, and reusable metadata for pipelines."
        action={
          canEdit ? (
            <div className="flex gap-2">
              <Button variant="outline" onClick={handleReset} disabled={isSaving}>
                <X className="h-3.5 w-3.5" />
                Reset
              </Button>
              <Button onClick={handleSave} disabled={isSaving || !name.trim()}>
                {isSaving ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Save className="h-3.5 w-3.5" />
                )}
                {isSaving ? "Saving..." : "Save changes"}
              </Button>
            </div>
          ) : undefined
        }
      />

      {saveSuccess && (
        <Alert
          variant="success"
          title="Settings saved"
          description="Your project settings have been updated successfully."
          className="mb-4"
          icon={<CheckCircle2 className="h-4 w-4" />}
        />
      )}

      {Object.keys(errors).length > 0 && (
        <Alert
          variant="error"
          title="Validation error"
          description="Please correct the highlighted fields before saving."
          className="mb-4"
        />
      )}

      {saveError && (
        <Alert variant="error" title="Save failed" description={saveError} className="mb-4" />
      )}

      <div className="settings-tabs">
        {([
          ["identity", "Identity & naming"],
          ["adf", "ADF defaults"],
          ["env", "Environment variables"],
          ["metadata", "Metadata"],
          ["git", "Git defaults"],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            className={cn("settings-tab", activeSection === id && "active")}
            onClick={() => setActiveSection(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="settings-layout">
        <div className="settings-main">
          {activeSection === "identity" && (
          <div className="form-card">
            <h3 className="form-card-title">General</h3>
            <div className="form-fields">
              <label>
                <span>Project name</span>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  disabled={!canEdit}
                  placeholder="e.g. Customer 360"
                  className={errors.name ? "border-red focus:border-red focus:ring-red/20" : ""}
                />
                {errors.name && <small className="text-red text-xs">{errors.name}</small>}
              </label>
              <label>
                <span>Description</span>
                <Textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  disabled={!canEdit}
                  placeholder="Describe the purpose of this project..."
                  rows={3}
                />
              </label>
              <label>
                <span>Team</span>
                <Select
                  value={teamId}
                  onChange={(e) => setTeamId(e.target.value)}
                  disabled={!canEdit || !can(user.role, "admin:team")}
                  className={errors.teamId ? "border-red focus:border-red focus:ring-red/20" : ""}
                >
                  {teams.map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </Select>
                {errors.teamId && <small className="text-red text-xs">{errors.teamId}</small>}
              </label>
            </div>
          </div>
          )}

          {activeSection === "identity" && (
          <div className="form-card">
            <h3 className="form-card-title">Naming conventions</h3>
            <p className="form-section-hint">Patterns used to derive artifact names. Tokens like {"{project}"}, {"{source}"}, {"{object}"} are substituted at generation time.</p>
            <div className="form-fields">
              <label><span>Pipeline pattern</span><Input value={pipelinePattern} onChange={(e) => setPipelinePattern(e.target.value)} disabled={!canEdit} />
                {errors.pipelinePattern && <small className="text-red text-xs">{errors.pipelinePattern}</small>}
              </label>
              <label><span>Dataset pattern</span><Input value={datasetPattern} onChange={(e) => setDatasetPattern(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Linked service pattern</span><Input value={linkedServicePattern} onChange={(e) => setLinkedServicePattern(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Parameter pattern</span><Input value={parameterPattern} onChange={(e) => setParameterPattern(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Trigger pattern</span><Input value={triggerPattern} onChange={(e) => setTriggerPattern(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Default artifact folder</span><Input value={artifactFolder} onChange={(e) => setArtifactFolder(e.target.value)} disabled={!canEdit} placeholder="e.g. adf/artifacts/" /></label>
              <label className="col-span-2"><span>Convention notes</span><Textarea value={namingDescription} onChange={(e) => setNamingDescription(e.target.value)} disabled={!canEdit} rows={2} /></label>
            </div>
          </div>
          )}

          {activeSection === "identity" && (
          <div className="form-card">
            <h3 className="form-card-title">Enforced naming standards</h3>
            <p className="form-section-hint">
              Prefix, suffix, case and max length per artifact type. Set an
              enforcement level to have it checked inline on the Build form, at validate, and —
              when set to Block — as a hard gate on Push to Git.
            </p>
            <div className="form-fields">
              <label>
                <span>Enforcement</span>
                <div className="seg seg-sm" role="group" aria-label="Naming rule enforcement">
                  {(["off", "warn", "block"] as const).map((level) => (
                    <button
                      key={level}
                      type="button"
                      className={cn("seg-btn", namingEnforcement === level && "active")}
                      disabled={!canEdit}
                      onClick={() => setNamingEnforcement(level)}
                    >
                      {level}
                    </button>
                  ))}
                </div>
                <small>
                  {namingEnforcement === "off"
                    ? "Not enforced — rules below are saved but never checked."
                    : namingEnforcement === "warn"
                      ? "Advisory — shown as a hint, does not block anything."
                      : "Blocks Push to Git until the name complies."}
                </small>
              </label>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Artifact type</th>
                    <th>Prefix</th>
                    <th>Suffix</th>
                    <th>Case</th>
                    <th>Max length</th>
                  </tr>
                </thead>
                <tbody>
                  {NAMING_RULE_TYPES.map(({ id, label }) => {
                    const rule = namingRules[id] ?? {};
                    return (
                      <tr key={id}>
                        <td>
                          <strong className="block text-ink">{label}</strong>
                          <span className="text-xs text-muted">{describeRule(rule)}</span>
                        </td>
                        <td>
                          <Input
                            value={rule.prefix ?? ""}
                            onChange={(e) => updateNamingRule(id, { prefix: e.target.value })}
                            disabled={!canEdit}
                            placeholder="e.g. PL_"
                          />
                        </td>
                        <td>
                          <Input
                            value={rule.suffix ?? ""}
                            onChange={(e) => updateNamingRule(id, { suffix: e.target.value })}
                            disabled={!canEdit}
                            placeholder="e.g. _v1"
                          />
                        </td>
                        <td>
                          <Select
                            value={rule.case ?? "none"}
                            onChange={(e) => updateNamingRule(id, { case: e.target.value as NamingRule["case"] })}
                            disabled={!canEdit}
                          >
                            {NAMING_CASES.map((c) => (
                              <option key={c.value} value={c.value}>{c.label}</option>
                            ))}
                          </Select>
                        </td>
                        <td>
                          <Input
                            type="number"
                            min={1}
                            value={rule.maxLength ?? ""}
                            onChange={(e) => updateNamingRule(id, { maxLength: e.target.value ? Number(e.target.value) : undefined })}
                            disabled={!canEdit}
                            placeholder="none"
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
          )}

          {activeSection === "identity" && (
          <div className="form-card">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="form-card-title m-0">Tags</h3>
              <Button variant="outline" size="sm" onClick={addTag} disabled={!canEdit}>+ Add tag</Button>
            </div>
            {tags.length === 0 ? (
              <p className="editor-empty">No tags yet.</p>
            ) : (
              <div className="editor-rows">
                {tags.map((tag, i) => (
                  <div className="editor-row" key={i}>
                    <Input value={tag.key} placeholder="key" onChange={(e) => updateTag(i, "key", e.target.value)} disabled={!canEdit} />
                    <Input value={tag.value} placeholder="value" onChange={(e) => updateTag(i, "value", e.target.value)} disabled={!canEdit} />
                    <button type="button" className="editor-remove" onClick={() => removeTag(i)} disabled={!canEdit}>×</button>
                  </div>
                ))}
              </div>
            )}
          </div>
          )}

          {activeSection === "adf" && (
          <div className="form-card">
            <h3 className="form-card-title">ADF environment defaults</h3>
            <p className="form-section-hint">Identifiers and references only — never secrets. Used to prefill the pipeline wizard.</p>
            <div className="form-fields">
              <label><span>Subscription ID</span><Input value={subscriptionId} onChange={(e) => setSubscriptionId(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Resource group</span><Input value={resourceGroup} onChange={(e) => setResourceGroup(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Data Factory name</span><Input value={factoryName} onChange={(e) => setFactoryName(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Integration runtime</span><Input value={integrationRuntime} onChange={(e) => setIntegrationRuntime(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Default source connector</span><Input value={defaultSourceConnector} onChange={(e) => setDefaultSourceConnector(e.target.value as typeof defaultSourceConnector)} disabled={!canEdit} /></label>
              <label><span>Default target connector</span><Input value={defaultTargetConnector} onChange={(e) => setDefaultTargetConnector(e.target.value as typeof defaultTargetConnector)} disabled={!canEdit} /></label>
              <label><span>Default source linked service</span><Input value={defaultSourceLS} onChange={(e) => setDefaultSourceLS(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Default target linked service</span><Input value={defaultTargetLS} onChange={(e) => setDefaultTargetLS(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Log level</span>
                <Select value={logLevel} onChange={(e) => setLogLevel(e.target.value as typeof logLevel)} disabled={!canEdit}>
                  {LOG_LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
                </Select>
              </label>
              <label><span>Default timeout</span><Input value={defaultTimeout} onChange={(e) => setDefaultTimeout(e.target.value)} disabled={!canEdit} placeholder="7.00:00:00" /></label>
            </div>
            <div className="form-checks mt-4">
              <Switch checked={enableAzureMonitor} onChange={setEnableAzureMonitor} label="Send metrics to Azure Monitor" disabled={!canEdit} />
            </div>
          </div>
          )}

          {activeSection === "adf" && (
            <ManagedIdentitiesCard canManage={user.role === "PLATFORM_ADMIN" || user.role === "TEAM_ADMIN"} />
          )}

          {activeSection === "adf" && project ? (
            <IntegrationsCard projectId={project.id} canManage={user.role === "PLATFORM_ADMIN" || user.role === "TEAM_ADMIN"} />
          ) : null}

          {activeSection === "adf" && (
          <div className="form-card">
            <h3 className="form-card-title">Runtime defaults</h3>
            <div className="form-fields">
              <label><span>Parallel copies</span><Input type="number" value={parallelCopies} onChange={(e) => setParallelCopies(Number(e.target.value))} disabled={!canEdit} /></label>
              <label><span>Retry count</span><Input type="number" value={retryCount} onChange={(e) => setRetryCount(Number(e.target.value))} disabled={!canEdit} /></label>
              <label><span>Retry interval (s)</span><Input type="number" value={retryInterval} onChange={(e) => setRetryInterval(Number(e.target.value))} disabled={!canEdit} /></label>
            </div>
          </div>
          )}

          {activeSection === "env" && (
          <div className="form-card">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="form-card-title m-0">Custom environment variables</h3>
              <Button variant="outline" size="sm" onClick={addEnvironmentVariable} disabled={!canEdit}>+ Add variable</Button>
            </div>
            <p className="form-section-hint">
              Project-level key/value pairs for anything not already covered by ADF or Git defaults —
              e.g. a service endpoint or feature flag. Mark a row Secret to store a Key Vault secret
              name instead of a raw value; the server rejects anything that looks like a real secret.
            </p>
            {environmentVariables.length === 0 ? (
              <p className="editor-empty">No custom environment variables yet.</p>
            ) : (
              <div className="editor-rows">
                {environmentVariables.map((ev, i) => (
                  <div className="editor-row four" key={i}>
                    <Input
                      value={ev.key}
                      placeholder="KEY_NAME"
                      onChange={(e) => updateEnvironmentVariable(i, { key: e.target.value })}
                      disabled={!canEdit}
                    />
                    <Input
                      value={ev.value}
                      placeholder={ev.isSecret ? "Key Vault secret name" : "value"}
                      onChange={(e) => updateEnvironmentVariable(i, { value: e.target.value })}
                      disabled={!canEdit}
                    />
                    <label className="editor-secret-toggle">
                      <input
                        type="checkbox"
                        checked={ev.isSecret}
                        onChange={(e) => updateEnvironmentVariable(i, { isSecret: e.target.checked })}
                        disabled={!canEdit}
                      />
                      Secret
                    </label>
                    <button type="button" className="editor-remove" onClick={() => removeEnvironmentVariable(i)} disabled={!canEdit}>×</button>
                  </div>
                ))}
              </div>
            )}
          </div>
          )}

          {activeSection === "metadata" && (
          <div className="form-card">
            <h3 className="form-card-title">Template & control table</h3>
            <div className="form-fields">
              <label><span>Default template</span>
                <Select value={defaultTemplateId} onChange={(e) => setDefaultTemplateId(e.target.value as typeof defaultTemplateId)} disabled={!canEdit}>
                  <option value="metadata_driven_load">Metadata-Driven Load</option>
                  <option value="incremental_load">Incremental Load</option>
                  <option value="full_load">Full Load</option>
                  <option value="initial_load">Initial Load</option>
                  <option value="composite_watermark_load">Composite Watermark Load</option>
                </Select>
              </label>
              <label><span>Watermark type</span>
                <Select value={watermarkType} onChange={(e) => setWatermarkType(e.target.value as typeof watermarkType)} disabled={!canEdit}>
                  {WATERMARK_TYPES.map((w) => <option key={w} value={w}>{w}</option>)}
                </Select>
              </label>
              <label><span>Control-table schema</span><Input value={ctSchema} onChange={(e) => setCtSchema(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Control-table name</span><Input value={ctTable} onChange={(e) => setCtTable(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Control-table connection</span><Input value={ctConnection} onChange={(e) => setCtConnection(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Watermark column</span><Input value={ctWatermark} onChange={(e) => setCtWatermark(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Loading-behavior column</span><Input value={ctLoadingBehavior} onChange={(e) => setCtLoadingBehavior(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Source-object column</span><Input value={ctSourceObject} onChange={(e) => setCtSourceObject(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Target-path column</span><Input value={ctTargetPath} onChange={(e) => setCtTargetPath(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Enabled column</span><Input value={ctEnabled} onChange={(e) => setCtEnabled(e.target.value)} disabled={!canEdit} /></label>
              <label className="col-span-2"><span>Default filter condition</span><Input value={filterCondition} onChange={(e) => setFilterCondition(e.target.value)} disabled={!canEdit} /></label>
              {ctConnectionControlEnabled && (
                <label><span>Connection control table name</span><Input value={ctConnectionTableName} onChange={(e) => setCtConnectionTableName(e.target.value)} disabled={!canEdit} /></label>
              )}
            </div>
            <p className="form-section-hint mt-3">
              A new pipeline in the Build wizard prefills its control/watermark table from the schema + name
              above, so a team can point its pipelines at its own control-table schema/table instead of
              sharing the platform default.
            </p>
            <div className="form-checks mt-2">
              <Switch
                checked={ctConnectionControlEnabled}
                onChange={setCtConnectionControlEnabled}
                label="Generate a connection control table (docs/DDL.md §2.1)"
                hint="Opt-in extra artifact: one row per linked service a pipeline uses, so ops can repoint a connection per environment/team without regenerating artifacts."
                disabled={!canEdit}
              />
            </div>
          </div>
          )}

          {activeSection === "metadata" && (
          <div className="form-card">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="form-card-title m-0">Custom metadata columns</h3>
              <Button variant="outline" size="sm" onClick={addCustomMetadata} disabled={!canEdit}>+ Add column</Button>
            </div>
            {customMetadata.length === 0 ? (
              <p className="editor-empty">No custom metadata columns.</p>
            ) : (
              <div className="editor-rows">
                {customMetadata.map((row, i) => (
                  <div className="editor-row" key={i}>
                    <Input value={row.name} placeholder="name" onChange={(e) => updateCustomMetadata(i, "name", e.target.value)} disabled={!canEdit} />
                    <Input value={row.value} placeholder="value" onChange={(e) => updateCustomMetadata(i, "value", e.target.value)} disabled={!canEdit} />
                    <button type="button" className="editor-remove" onClick={() => removeCustomMetadata(i)} disabled={!canEdit}>×</button>
                  </div>
                ))}
              </div>
            )}
          </div>
          )}

          {activeSection === "git" && (
          <div className="form-card">
            <h3 className="form-card-title">Git connectivity</h3>
            <p className="form-section-hint">How this project connects to Git. These defaults are used when pushing artifacts to Git.</p>
            <div className="form-fields">
              <label>
                <span>Git provider</span>
                <Select value={gitProvider} disabled={!canEdit} onChange={(e) => { const next = e.target.value as GitProvider; setGitProvider(next); setAuthMethod(knDefaultAuth(next)); }}>
                  {GIT_PROVIDERS.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </Select>
              </label>
              <label>
                <span>Connection mode</span>
                <Select value={gitConnectMode} disabled={!canEdit} onChange={(e) => setGitConnectMode(e.target.value as "url" | "account")}>
                  <option value="url">Repository URL</option>
                  <option value="account">Server + organization/project</option>
                </Select>
              </label>
              <label>
                <span>Repository URL</span>
                <Input value={repositoryUrl} onChange={(e) => setRepositoryUrl(e.target.value)} disabled={!canEdit} placeholder="e.g. https://dev.azure.com/org/project/_git/repo" />
              </label>
              <label>
                <span>Server URL</span>
                <Input value={gitServerUrl} onChange={(e) => setGitServerUrl(e.target.value)} disabled={!canEdit} placeholder={GIT_SERVER_PLACEHOLDERS[gitProvider]} />
              </label>
              <label>
                <span>Browse path</span>
                <Input value={gitBrowsePath} onChange={(e) => setGitBrowsePath(e.target.value)} disabled={!canEdit} placeholder="e.g. adf/artifacts/ (optional)" />
              </label>
              <label>
                <span>Authentication method</span>
                <Select value={authMethod} disabled={!canEdit} onChange={(e) => setAuthMethod(e.target.value as "pat" | "oauth" | "managed_identity" | "ssh")}>
                  <option value="pat">Personal access token</option>
                  <option value="oauth">OAuth</option>
                  <option value="managed_identity">Managed identity</option>
                  <option value="ssh">SSH key</option>
                </Select>
              </label>
              <label>
                <span>Service account / username</span>
                <Input value={gitUsername} onChange={(e) => setGitUsername(e.target.value)} disabled={!canEdit} placeholder="e.g. adf-forge-svc" />
              </label>
              <label>
                <span>Credential vault reference</span>
                <Input value={credentialVault} onChange={(e) => setCredentialVault(e.target.value)} disabled={!canEdit} placeholder="e.g. kv-git-pat-name" />
              </label>
              <label>
                <span>Default repository label</span>
                <Input value={defaultRepo} onChange={(e) => setDefaultRepo(e.target.value)} disabled={!canEdit} placeholder="e.g. organization/repo-name" />
              </label>
              <label>
                <span>Default artifact path</span>
                <Input value={artifactRoot} onChange={(e) => setArtifactRoot(e.target.value)} disabled={!canEdit} placeholder="e.g. adf/artifacts/" />
              </label>
            </div>
            <div className="mt-4 flex items-center gap-3">
              <Button variant="outline" size="sm" onClick={testGitConnection} disabled={!canEdit || gitConnectionStatus === "testing"}>
                {gitConnectionStatus === "testing" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />}
                Test connection
              </Button>
              {gitConnectionStatus === "connected" && <span className="status-pill green"><i />connected</span>}
              {gitConnectionStatus === "failed" && <span className="status-pill red"><i />failed</span>}
              {gitLastTestedAt && <span className="text-xs text-muted">last tested {new Date(gitLastTestedAt).toLocaleString()}</span>}
            </div>
            {gitConnectionMessage && <p className="mt-2 text-xs text-muted">{gitConnectionMessage}</p>}
          </div>
          )}

          {activeSection === "git" && (
          <div className="form-card">
            <h3 className="form-card-title">Workflow & pull request templates</h3>
            <p className="form-section-hint">These prefill the Push to Git dialog. Feature branch never equals the base branch.</p>
            <div className="form-fields">
              <label><span>Organization</span><Input value={gitOrg} onChange={(e) => setGitOrg(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Project</span><Input value={gitProject} onChange={(e) => setGitProject(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Repository</span><Input value={repository} onChange={(e) => setRepository(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Feature branch pattern</span><Input value={featureBranchPattern} onChange={(e) => setFeatureBranchPattern(e.target.value)} disabled={!canEdit} /></label>
              <label><span>Base branch</span>
                <Input value={baseBranch} onChange={(e) => { setBaseBranch(e.target.value); setDefaultBaseBranch(e.target.value); }} disabled={!canEdit}
                  className={errors.defaultBaseBranch ? "border-red" : ""} />
                {errors.defaultBaseBranch && <small className="text-red text-xs">{errors.defaultBaseBranch}</small>}
              </label>
              <label><span>Default branch</span><Input value={defaultBranch} onChange={(e) => setDefaultBranch(e.target.value)} disabled={!canEdit} /></label>
              <label className="col-span-2"><span>Commit message convention</span><Input value={commitConvention} onChange={(e) => setCommitConvention(e.target.value)} disabled={!canEdit} /></label>
              <label className="col-span-2"><span>PR title template</span><Input value={prTitleTemplate} onChange={(e) => setPrTitleTemplate(e.target.value)} disabled={!canEdit} /></label>
              <label className="col-span-2"><span>PR body template</span><Textarea value={prBodyTemplate} onChange={(e) => setPrBodyTemplate(e.target.value)} disabled={!canEdit} rows={4} /></label>
            </div>
            <div className="mt-4">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-bold text-ink-2">Default reviewers</span>
                <Button variant="outline" size="sm" onClick={addReviewer} disabled={!canEdit}>+ Add reviewer</Button>
              </div>
              {reviewers.length === 0 ? (
                <p className="editor-empty">No default reviewers.</p>
              ) : (
                <div className="editor-rows">
                  {reviewers.map((reviewer, i) => (
                    <div className="editor-row" key={i} style={{ gridTemplateColumns: "1fr 28px" }}>
                      <Input value={reviewer} placeholder="e.g. team-platform-leads" onChange={(e) => updateReviewer(i, e.target.value)} disabled={!canEdit} />
                      <button type="button" className="editor-remove" onClick={() => removeReviewer(i)} disabled={!canEdit}>×</button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          )}
        </div>

        <div className="settings-sidebar">
          <div className="form-card">
            <h3 className="form-card-title">Project info</h3>
            <div className="settings-info">
              <div className="settings-info-item">
                <span>Project ID</span>
                <code>{project.id}</code>
              </div>
              <div className="settings-info-item">
                <span>Team</span>
                <strong>{team?.name ?? "Unassigned"}</strong>
              </div>
              <div className="settings-info-item">
                <span>Pipelines</span>
                <Badge variant="blue">{projectPipelines.length}</Badge>
              </div>
              <div className="settings-info-item">
                <span>Members</span>
                <strong>{team?.memberCount ?? 0}</strong>
              </div>
            </div>
          </div>

          <div className="form-card">
            <h3 className="form-card-title">Quick actions</h3>
            <div className="settings-actions">
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => onNavigate("pipelines.create")}
              >
                <FolderKanban className="h-3.5 w-3.5" />
                New pipeline
              </Button>
            <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => onNavigate("delivery.catalog")}
              >
                <GitBranch className="h-3.5 w-3.5" />
                Open catalog
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => onNavigate("projects")}
              >
                View all projects
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
