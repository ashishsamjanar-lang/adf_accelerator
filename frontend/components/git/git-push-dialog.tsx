"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2, UploadCloud } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { ValidationPanel } from "@/components/validation/validation-panel";
import { PRResultPanel, type PushResult } from "@/components/git/pr-result-panel";
import { EMPTY_GIT_SETTINGS } from "@/lib/api/empty-values";
import { api } from "@/lib/api/client";
import { branchMatchesPattern } from "@/lib/adf/compliance";
import type { ArtifactVersion, GitProvider, GitPushResponse, GitSettings, ValidationIssue } from "@/types";

interface GitPushDialogProps {
  open: boolean;
  onClose: () => void;
  artifacts: ArtifactVersion[];
  /** Project Git defaults; falls back to empty/neutral defaults when none are set. */
  defaults?: GitSettings;
}

const PROVIDERS: { value: GitProvider; label: string }[] = [
  { value: "azure_repos", label: "Azure Repos" },
  { value: "github", label: "GitHub" },
  { value: "gitlab", label: "GitLab" },
  { value: "custom", label: "Custom" },
];

function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

/**
 * Push to Git dialog (AGENTS.md §8, §22.21). Prefilled from project Git
 * defaults, fully editable for this push, and blocked on the required
 * validation rules (feature branch present, not equal to base, artifacts
 * selected and validated).
 */
export function GitPushDialog({ open, onClose, artifacts, defaults }: GitPushDialogProps) {
  // Gap #33: resolve the artifacts' own project's `gitDefaults` on open. The
  // project list endpoint never includes settings, so callers can't supply them.
  const [fetchedDefaults, setFetchedDefaults] = useState<GitSettings | undefined>();
  const pipelineId = artifacts[0]?.pipelineId;
  useEffect(() => {
    if (!open || defaults || !pipelineId) return;
    let cancelled = false;
    api.pipelines
      .details(pipelineId)
      .then((pipeline) => api.projects.settings(pipeline.projectId))
      .then((settings) => {
        if (!cancelled) setFetchedDefaults(settings.gitDefaults);
      })
      .catch(() => undefined); // no saved defaults reachable — the form stays on neutral defaults, fully editable
    return () => {
      cancelled = true;
    };
  }, [open, defaults, pipelineId]);
  const git = useMemo(
    () => ({ ...EMPTY_GIT_SETTINGS, ...(defaults ?? fetchedDefaults) }),
    [defaults, fetchedDefaults],
  );

  const [provider, setProvider] = useState<GitProvider>(git.provider);
  const [organization, setOrganization] = useState("");
  const [project, setProject] = useState("");
  const [repository, setRepository] = useState("");
  const [repositoryUrl, setRepositoryUrl] = useState("");
  const [artifactRoot, setArtifactRoot] = useState("");
  const [featureBranch, setFeatureBranch] = useState("");
  const [baseBranch, setBaseBranch] = useState("dev");
  const [commitMessage, setCommitMessage] = useState("");
  const [prTitle, setPrTitle] = useState("");
  const [prBody, setPrBody] = useState("");
  const [reviewers, setReviewers] = useState("");

  // Re-seed every field from the defaults each time the dialog opens or the
  // project defaults arrive — a mount-time useState seed would miss both.
  useEffect(() => {
    if (!open) return;
    const pipelineName = artifacts[0]?.pipelineName ?? "artifacts";
    const version = artifacts[0]?.version ?? "v1.0.0";
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    setProvider(git.provider);
    setOrganization(git.organization ?? "");
    setProject(git.project ?? "");
    setRepository(git.repository);
    setRepositoryUrl(git.repositoryUrl);
    setArtifactRoot(git.artifactRoot);
    setFeatureBranch(git.featureBranchPattern.replace("{pipeline-name}", slug(pipelineName)).replace("{timestamp}", stamp));
    setBaseBranch(git.baseBranch || "dev");
    setCommitMessage(
      git.commitMessageConvention
        .replace("{pipeline}", pipelineName)
        .replace("{version}", version)
        .replace("{artifact_count}", String(artifacts.length)),
    );
    setPrTitle(git.prTitleTemplate.replace("{pipeline}", pipelineName).replace("{version}", version));
    setPrBody(git.prBodyTemplate);
    setReviewers((git.reviewers ?? []).join(", "));
  }, [open, git, artifacts]);
  const [createPr, setCreatePr] = useState(true);
  /** Per-push credential. Never stored on the client; sent only with this push. */
  const [gitToken, setGitToken] = useState("");

  const [pushing, setPushing] = useState(false);
  const [result, setResult] = useState<PushResult | null>(null);
  const [pushError, setPushError] = useState<string | null>(null);

  const issues: ValidationIssue[] = useMemo(() => {
    const list: ValidationIssue[] = [];
    if (artifacts.length === 0) {
      list.push({ severity: "error", message: "Select at least one artifact to push.", field: "artifacts" });
    }
    const unvalidated = artifacts.filter((a) => a.state === "generated");
    if (unvalidated.length > 0) {
      list.push({
        severity: "error",
        message: `${unvalidated.length} selected artifact(s) have not passed validation.`,
        field: "artifacts",
        source: unvalidated.map((a) => a.fileName).join(", "),
      });
    }
    if (!featureBranch.trim()) {
      list.push({ severity: "error", message: "A feature branch is required.", field: "featureBranch" });
    }
    if (featureBranch.trim() && featureBranch.trim() === baseBranch.trim()) {
      list.push({
        severity: "error",
        message: "The feature branch must not be the protected base branch.",
        field: "featureBranch",
      });
    }
    if (["main", "master"].includes(featureBranch.trim().toLowerCase())) {
      list.push({ severity: "error", message: "Never push directly to a protected branch.", field: "featureBranch" });
    }
    if (!repository.trim() && !repositoryUrl.trim()) {
      list.push({ severity: "error", message: "A repository or repository URL is required.", field: "repository" });
    }
    if (!commitMessage.trim()) {
      list.push({ severity: "warning", message: "Commit message is empty.", field: "commitMessage" });
    }
    // §25.3 git.branch — advisory: the project's branch naming convention.
    if (featureBranch.trim() && !branchMatchesPattern(featureBranch, git.featureBranchPattern)) {
      list.push({
        severity: "warning",
        message: `Feature branch doesn't follow the project pattern "${git.featureBranchPattern}".`,
        field: "featureBranch",
      });
    }
    return list;
  }, [artifacts, featureBranch, baseBranch, repository, repositoryUrl, commitMessage, git.featureBranchPattern]);

  const blocked = issues.some((i) => i.severity === "error");

  /** Turn the real, server-tracked push state into the result panel's shape. */
  function toPushResult(push: GitPushResponse): PushResult {
    const failed = push.status === "failed";
    // The push job pushes artifacts, then opens a PR. A non-null commitSha
    // means the push itself succeeded even if the later PR step failed.
    const state: PushResult["state"] = failed
      ? push.commitSha
        ? "PR_CREATION_FAILED"
        : "GIT_PUSH_FAILED"
      : push.status === "pr_created"
        ? "PR_CREATED"
        : "PUSHED";
    return {
      state,
      featureBranch: push.featureBranch,
      baseBranch: push.baseBranch,
      commitSha: push.commitSha ?? "—",
      fileCount: artifacts.length,
      prUrl: push.pullRequest?.url ?? undefined,
      prNumber: push.pullRequest?.number ?? undefined,
      message: push.error ?? undefined,
      simulated: push.simulated ?? push.pullRequest?.simulated ?? false,
    };
  }

  /** Poll GET /git/push/{id} — the push runs as an async worker job, not synchronously. */
  async function pollPushStatus(id: string, attempts = 14, intervalMs = 1500): Promise<GitPushResponse> {
    for (let i = 0; i < attempts; i++) {
      const current = await api.git.get(id);
      if (current.status === "pushed" || current.status === "pr_created" || current.status === "failed") {
        return current;
      }
      await new Promise((r) => setTimeout(r, intervalMs));
    }
    throw new Error(
      "The push was queued but hasn't finished processing yet. Check the pipeline's Artifacts tab in a moment — this dialog won't guess at the outcome.",
    );
  }

  async function handlePush() {
    if (blocked) return;
    setPushing(true);
    setPushError(null);

    const pipelineId = artifacts[0]?.pipelineId ?? "";
    if (!pipelineId || pipelineId.startsWith("standalone-")) {
      setPushError(
        "These artifacts were generated client-side and aren't tracked by the backend, so they can't be pushed yet. Save the pipeline first, then push from its Artifacts tab.",
      );
      setPushing(false);
      return;
    }

    try {
      const queued = await api.git.push({
        pipelineId,
        artifactIds: artifacts.map((a) => a.id),
        provider,
        repositoryUrl: repositoryUrl.trim() || undefined,
        featureBranch: featureBranch.trim(),
        baseBranch: baseBranch.trim(),
        commitMessage: commitMessage.trim() || undefined,
        prTitle: createPr ? prTitle.trim() || undefined : undefined,
        prBody: createPr ? prBody.trim() || undefined : undefined,
        reviewers: reviewers.split(",").map((r) => r.trim()).filter(Boolean),
        gitToken: gitToken.trim() || undefined,
      });
      const final = await pollPushStatus(queued.id);
      setResult(toPushResult(final));
    } catch (err) {
      setPushError(err instanceof Error ? err.message : "Failed to reach the backend to push these artifacts.");
    } finally {
      setGitToken("");
      setPushing(false);
    }
  }

  function reset() {
    setResult(null);
    setPushError(null);
    setGitToken("");
    onClose();
  }

  return (
    <Dialog
      open={open}
      onClose={reset}
      wide
      eyebrow="GIT HAND-OFF"
      title="Push to Git"
      description="Push the selected artifacts to a feature branch and open a pull request into the base branch. Project defaults are prefilled and editable for this push only."
    >
      {result ? (
        <div className="flex flex-col gap-4">
          <PRResultPanel result={result} />
          <div className="flex justify-end">
            <Button onClick={reset}>Done</Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-5">
          <section>
            <p className="mb-2 text-xs font-extrabold uppercase tracking-wide text-ink">Selected artifacts ({artifacts.length})</p>
            <div className="max-h-32 overflow-y-auto rounded-md border border-line bg-surface-2 p-2">
              {artifacts.length === 0 ? (
                <p className="m-0 px-1 py-2 text-xs text-muted">No artifacts selected.</p>
              ) : (
                <ul className="m-0 flex list-none flex-col gap-1 p-0">
                  {artifacts.map((a) => (
                    <li key={a.id} className="flex items-center justify-between gap-3 px-1 text-xs">
                      <code className="truncate text-ink-2">{artifactRoot}{a.fileName}</code>
                      <Badge variant={a.state === "generated" ? "amber" : "green"}>{a.state}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          <section className="grid grid-cols-2 gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-bold text-ink-2">Provider</span>
              <Select value={provider} onChange={(e) => setProvider(e.target.value as GitProvider)}>
                {PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </Select>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-bold text-ink-2">Organization</span>
              <Input value={organization} onChange={(e) => setOrganization(e.target.value)} placeholder="e.g. contoso" />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-bold text-ink-2">Project</span>
              <Input value={project} onChange={(e) => setProject(e.target.value)} placeholder="e.g. adf-accelerator" />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-bold text-ink-2">Repository</span>
              <Input value={repository} onChange={(e) => setRepository(e.target.value)} placeholder="e.g. adf-artifacts" />
            </label>
            <label className="col-span-2 flex flex-col gap-1.5">
              <span className="text-xs font-bold text-ink-2">Repository URL</span>
              <Input value={repositoryUrl} onChange={(e) => setRepositoryUrl(e.target.value)} placeholder="https://dev.azure.com/org/project/_git/repo" />
            </label>
            <label className="col-span-2 flex flex-col gap-1.5">
              <span className="text-xs font-bold text-ink-2">Artifact root / path</span>
              <Input value={artifactRoot} onChange={(e) => setArtifactRoot(e.target.value)} placeholder="adf/artifacts/" />
            </label>
            <label className="col-span-2 flex flex-col gap-1.5">
              <span className="text-xs font-bold text-ink-2">Git access token (PAT)</span>
              <Input
                type="password"
                value={gitToken}
                onChange={(e) => setGitToken(e.target.value)}
                placeholder="Leave blank to use the project's configured credential"
                autoComplete="off"
                spellCheck={false}
              />
              <span className="text-2xs text-muted">
                Used for this push only — sent with the request, never stored and never logged. Blank falls
                back to the Key Vault credential reference in Project Settings.
              </span>
            </label>
          </section>

          <section className="grid grid-cols-2 gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-bold text-ink-2">Feature branch <span className="text-red">*</span></span>
              <Input
                value={featureBranch}
                onChange={(e) => setFeatureBranch(e.target.value)}
                placeholder="feature/my-pipeline"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-bold text-ink-2">Base branch <span className="text-red">*</span></span>
              <Input value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)} placeholder="dev" />
            </label>
          </section>

          <section className="flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-bold text-ink-2">Commit message</span>
              <Input value={commitMessage} onChange={(e) => setCommitMessage(e.target.value)} />
            </label>
            <Checkbox
              checked={createPr}
              onChange={setCreatePr}
              label="Create a pull request"
              hint={`Opens a PR from ${featureBranch || "the feature branch"} into ${baseBranch || "the base branch"}.`}
            />
            {createPr ? (
              <>
                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-bold text-ink-2">PR title</span>
                  <Input value={prTitle} onChange={(e) => setPrTitle(e.target.value)} />
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-bold text-ink-2">PR description</span>
                  <Textarea value={prBody} onChange={(e) => setPrBody(e.target.value)} rows={4} />
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-bold text-ink-2">Reviewers</span>
                  <Input value={reviewers} onChange={(e) => setReviewers(e.target.value)} placeholder="comma-separated" />
                </label>
              </>
            ) : null}
          </section>

          <ValidationPanel issues={issues} emptyMessage="All push checks passed." />

          {pushError ? (
            <Alert variant="error" title="Push failed" description={pushError} />
          ) : null}

          <div className="flex justify-end gap-2 border-t border-line-2 pt-4">
            <Button variant="ghost" onClick={reset}>Cancel</Button>
            <Button onClick={handlePush} disabled={blocked || pushing}>
              {pushing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <UploadCloud className="h-3.5 w-3.5" />}
              {pushing ? "Pushing…" : createPr ? "Push & create PR" : "Push to branch"}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
