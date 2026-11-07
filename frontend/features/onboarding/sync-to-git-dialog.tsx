"use client";

/**
 * §26.5 "Sync to Git" — writes this project's registry (control-table SQL + `metadata/objects.csv`,
 * optionally registry lineage) to a feature branch and opens a PR into the base branch. Shows the
 * per-row diff vs. the last successful sync first. Files are built by the same `lib/adf/onboarding.ts`
 * builders the download buttons use; the API pins their paths and rejects a stale registry.
 */
import { useEffect, useState } from "react";
import { GitPullRequest, Loader2 } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert } from "@/components/ui/alert";
import { Checkbox } from "@/components/ui/checkbox";
import { api, ApiError } from "@/lib/api/client";
import { pollUntil } from "@/lib/api/poll";
import { buildOnboardingControlTableSql, buildOnboardingObjectsCsv, buildRegistryLineage } from "@/lib/adf/onboarding";
import type { GitSettings, OnboardingObjectEntry, OnboardingSync, OnboardingSyncDiff, OnboardingSyncRequest } from "@/types";

interface SyncToGitDialogProps {
  open: boolean;
  onClose: () => void;
  projectId: string;
  rows: OnboardingObjectEntry[];
}

export function SyncToGitDialog({ open, onClose, projectId, rows }: SyncToGitDialogProps) {
  const [diff, setDiff] = useState<OnboardingSyncDiff | null>(null);
  const [git, setGit] = useState<Partial<GitSettings>>({});
  const [repositoryUrl, setRepositoryUrl] = useState("");
  const [featureBranch, setFeatureBranch] = useState("");
  const [baseBranch, setBaseBranch] = useState("");
  const [prTitle, setPrTitle] = useState("");
  const [gitToken, setGitToken] = useState("");
  const [includeLineage, setIncludeLineage] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OnboardingSync | null>(null);

  useEffect(() => {
    if (!open || !projectId) return;
    setResult(null);
    setError(null);
    setFeatureBranch(`feature/onboarding-sync-${new Date().toISOString().slice(0, 10)}`);
    api.onboarding.syncDiff(projectId).then(setDiff).catch(() => setDiff(null));
    api.projects
      .settings(projectId)
      .then((s) => {
        setGit(s.gitDefaults ?? {});
        setRepositoryUrl(s.gitDefaults?.repositoryUrl ?? "");
        setBaseBranch(s.gitDefaults?.baseBranch || "dev");
      })
      .catch(() => setBaseBranch("dev"));
  }, [open, projectId]);

  const sameBranch = featureBranch.trim() !== "" && featureBranch.trim() === baseBranch.trim();
  const canSubmit = !busy && rows.length > 0 && repositoryUrl.trim() !== "" && featureBranch.trim() !== "" && !sameBranch;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const files: OnboardingSyncRequest["files"] = [
        { name: "adf_control_table.sql", content: buildOnboardingControlTableSql(rows) },
        { name: "objects.csv", content: buildOnboardingObjectsCsv(rows) },
      ];
      if (includeLineage) {
        const [silver, gold] = await Promise.all([api.registry.list("silver", projectId), api.registry.list("mart", projectId)]);
        files.push({ name: "registry-lineage.json", content: JSON.stringify(buildRegistryLineage(rows, silver, gold), null, 2) + "\n" });
      }
      const sync = await api.onboarding.syncToGit(projectId, {
        files,
        objectKeys: rows.map((r) => r.object_key),
        provider: git.provider,
        repositoryUrl: repositoryUrl.trim(),
        featureBranch: featureBranch.trim(),
        baseBranch: baseBranch.trim(),
        prTitle: prTitle.trim() || undefined,
        gitToken: gitToken.trim() || undefined,
      });
      setGitToken("");
      const settled = await pollUntil(
        async () => (await api.onboarding.syncs(projectId)).find((s) => s.id === sync.id) ?? sync,
        (s) => s.status === "pr_created" || s.status === "failed",
      );
      setResult(settled);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sync to Git failed.");
    } finally {
      setBusy(false);
    }
  }

  const noChanges = diff && diff.baseSyncId && !diff.added.length && !diff.removed.length && !diff.changed.length;

  return (
    <Dialog open={open} onClose={onClose} eyebrow="§26.5" title="Sync registry to Git" description="Control-table SQL + metadata/objects.csv on a feature branch, then a PR into the base branch.">
      <div className="space-y-4">
        {diff ? (
          <div className="rounded-md border border-line p-3 text-sm">
            <p className="font-semibold text-ink">
              {diff.baseSyncId ? `Changes since the last sync${diff.baseCommitSha ? ` (${diff.baseCommitSha.slice(0, 7)})` : ""}` : "First sync — every object is new"}
            </p>
            {noChanges ? <p className="mt-1 text-ink-2">No registry changes since the last sync.</p> : null}
            <ul className="mt-2 space-y-1 text-ink-2">
              {diff.added.length ? <li><span className="font-semibold text-emerald-700">+{diff.added.length} added:</span> {diff.added.join(", ")}</li> : null}
              {diff.changed.map((c) => (
                <li key={c.objectKey}><span className="font-semibold text-amber-700">~ {c.objectKey}:</span> {c.fields.join(", ")}</li>
              ))}
              {diff.removed.length ? <li><span className="font-semibold text-red-700">−{diff.removed.length} removed:</span> {diff.removed.join(", ")}</li> : null}
              {diff.baseSyncId ? <li>{diff.unchanged} unchanged</li> : null}
            </ul>
          </div>
        ) : null}

        <div className="form-fields">
          <label><span>Repository URL</span><Input value={repositoryUrl} onChange={(e) => setRepositoryUrl(e.target.value)} /></label>
          <label><span>Feature branch</span><Input value={featureBranch} onChange={(e) => setFeatureBranch(e.target.value)} /></label>
          <label><span>Base branch</span><Input value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)} /></label>
          <label><span>PR title (optional)</span><Input value={prTitle} onChange={(e) => setPrTitle(e.target.value)} /></label>
          <label>
            <span>Git token (optional, this sync only)</span>
            <Input type="password" autoComplete="off" value={gitToken} onChange={(e) => setGitToken(e.target.value)} placeholder="Uses the project credential reference when blank" />
          </label>
        </div>
        <Checkbox checked={includeLineage} onChange={setIncludeLineage} label="Include registry-lineage.json (Bronze → Silver/Gold)" />
        {sameBranch ? <Alert variant="error" title="The feature branch must not equal the protected base branch." /> : null}
        {error ? <Alert variant="error" title={error} /> : null}
        {result?.status === "failed" ? <Alert variant="error" title="The sync failed" description={result.error ?? undefined} /> : null}
        {result?.status === "pr_created" ? (
          <Alert
            variant="success"
            title={`PR ${result.prNumber ? `#${result.prNumber} ` : ""}opened: ${result.featureBranch} → ${result.baseBranch}`}
            description={result.simulated ? "Simulated — no Git credential resolved, nothing reached the provider." : result.filePaths.join(", ")}
            action={result.prUrl ? <a className="text-xs font-semibold underline" href={result.prUrl} target="_blank" rel="noreferrer">Open PR</a> : undefined}
          />
        ) : null}
        {result && result.status !== "pr_created" && result.status !== "failed" ? (
          <Alert title="The worker is still running this sync" description="Reopen this dialog later — the diff updates once the PR is created." />
        ) : null}

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Close</Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />}
            Push & open PR
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
