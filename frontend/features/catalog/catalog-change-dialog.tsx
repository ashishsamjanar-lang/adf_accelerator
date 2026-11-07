"use client";

/**
 * §24.9/§24.10 — push one change to a file already in Git (raw-JSON update, rename, delete) to a
 * feature branch and open a PR into the base branch. Rename/delete show the inbound-reference blast
 * radius first; a rename can rewrite those references in the same PR. The worker refuses the commit
 * when the file moved on the base branch since it was loaded (§24.14 optimistic concurrency).
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
import type { CatalogChange, CatalogChangeMode, CatalogEntrySummary } from "@/types";

// Vendored from backend/app/catalog_services.py (ARTIFACT_NAME_RE).
const ARTIFACT_NAME_RE = /^[A-Za-z][A-Za-z0-9_]{1,127}$/;

const TITLES: Record<CatalogChangeMode, string> = {
  update: "Push update",
  rename: "Rename through a PR",
  delete: "Delete through a PR",
};

interface CatalogChangeDialogProps {
  open: boolean;
  onClose: () => void;
  onSettled: (change: CatalogChange) => void;
  projectId: string;
  mode: CatalogChangeMode;
  path: string;
  name: string;
  baseBranch: string;
  /** The edited file text, for `update`. */
  content?: string;
}

export function CatalogChangeDialog({ open, onClose, onSettled, projectId, mode, path, name, baseBranch, content }: CatalogChangeDialogProps) {
  const [inbound, setInbound] = useState<CatalogEntrySummary[]>([]);
  const [featureBranch, setFeatureBranch] = useState("");
  const [newName, setNewName] = useState("");
  const [rewrite, setRewrite] = useState(true);
  const [confirm, setConfirm] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");
  const [gitToken, setGitToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CatalogChange | null>(null);

  useEffect(() => {
    if (!open) return;
    setResult(null);
    setError(null);
    setConfirm(false);
    setRewrite(true);
    setNewName(mode === "rename" ? name : "");
    setFeatureBranch(`feature/${mode}-${name}`.toLowerCase());
    setInbound([]);
    if (mode !== "update") {
      api.catalog.impact(projectId, path).then((r) => setInbound(r.inbound)).catch(() => setInbound([]));
    }
  }, [open, mode, name, path, projectId]);

  const sameBranch = featureBranch.trim() !== "" && featureBranch.trim() === baseBranch;
  const nameError =
    mode === "rename" && newName.trim() && !ARTIFACT_NAME_RE.test(newName.trim())
      ? "Start with a letter; letters, digits and '_' only (2–128 characters)."
      : null;
  const needsConfirm = inbound.length > 0 && (mode === "delete" || (mode === "rename" && !rewrite));
  const canSubmit =
    !busy &&
    !result &&
    featureBranch.trim() !== "" &&
    !sameBranch &&
    (mode !== "rename" || (newName.trim() !== "" && newName.trim() !== name && !nameError)) &&
    (!needsConfirm || confirm);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const change = await api.catalog.change(projectId, {
        mode,
        path,
        content: mode === "update" ? content : undefined,
        newName: mode === "rename" ? newName.trim() : undefined,
        rewriteReferences: mode === "rename" ? rewrite : undefined,
        force: needsConfirm ? confirm : undefined,
        featureBranch: featureBranch.trim(),
        commitMessage: commitMessage.trim() || undefined,
        gitToken: gitToken.trim() || undefined,
      });
      setGitToken("");
      const settled = await pollUntil(
        async () => (await api.catalog.changes(projectId)).find((c) => c.id === change.id) ?? change,
        (c) => c.status === "pr_created" || c.status === "failed" || c.status === "conflict",
      );
      setResult(settled);
      onSettled(settled);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The change could not be requested.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} eyebrow="§24.10" title={TITLES[mode]} description={`${path} — PR into ${baseBranch}`}>
      <div className="space-y-4">
        {mode !== "update" ? (
          <div className="rounded-md border border-line p-3 text-sm">
            <p className="font-semibold text-ink">
              {inbound.length ? `${inbound.length} artifact(s) reference ${name}` : `Nothing in ${baseBranch} references ${name}`}
            </p>
            {inbound.length ? (
              <ul className="mt-1 list-disc pl-5 text-ink-2">
                {inbound.map((e) => <li key={e.path}><code>{e.path}</code></li>)}
              </ul>
            ) : null}
          </div>
        ) : null}

        <div className="form-fields">
          {mode === "rename" ? (
            <label>
              <span>New name</span>
              <Input value={newName} onChange={(e) => setNewName(e.target.value)} />
              {nameError ? <small className="text-red">{nameError}</small> : <small>The file is renamed to match — ADF keys a resource on its file name.</small>}
            </label>
          ) : null}
          <label><span>Feature branch</span><Input value={featureBranch} onChange={(e) => setFeatureBranch(e.target.value)} /></label>
          <label><span>Commit message (optional)</span><Input value={commitMessage} onChange={(e) => setCommitMessage(e.target.value)} /></label>
          <label>
            <span>Git token (optional, this change only)</span>
            <Input type="password" autoComplete="off" value={gitToken} onChange={(e) => setGitToken(e.target.value)} placeholder="Uses the project credential reference when blank" />
          </label>
        </div>

        {mode === "rename" && inbound.length ? (
          <Checkbox checked={rewrite} onChange={setRewrite} label={`Rewrite the ${inbound.length} inbound reference(s) in the same PR`} />
        ) : null}
        {needsConfirm ? (
          <Checkbox
            checked={confirm}
            onChange={setConfirm}
            label={mode === "delete" ? "Delete anyway — the referencing artifacts will break until they're updated" : "Leave the inbound references pointing at the old name"}
          />
        ) : null}

        {sameBranch ? <Alert variant="error" title="The feature branch must not equal the protected base branch." /> : null}
        {error ? <Alert variant="error" title={error} /> : null}
        {result?.status === "conflict" ? (
          <Alert variant="error" title="Someone changed this file first" description={`${result.error ?? ""} Close, refresh the catalog, and re-apply your edit.`} />
        ) : null}
        {result?.status === "failed" ? <Alert variant="error" title="The change failed" description={result.error ?? undefined} /> : null}
        {result?.status === "pr_created" ? (
          <Alert
            variant="success"
            title={`PR ${result.prNumber ? `#${result.prNumber} ` : ""}opened: ${result.featureBranch} → ${result.baseBranch}`}
            description={
              result.simulated
                ? "Simulated — no Git credential resolved, nothing reached the provider."
                : [...result.filePaths, ...result.deletePaths.map((p) => `− ${p}`)].join(", ")
            }
            action={result.prUrl ? <a className="text-xs font-semibold underline" href={result.prUrl} target="_blank" rel="noreferrer">Open PR</a> : undefined}
          />
        ) : null}
        {result && result.status === "pending" ? (
          <Alert title="The worker is still running this change" description="It finishes in the background — check Recent changes." />
        ) : null}

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Close</Button>
          <Button onClick={submit} disabled={!canSubmit} variant={mode === "delete" ? "danger" : "primary"}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />}
            Push & open PR
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
