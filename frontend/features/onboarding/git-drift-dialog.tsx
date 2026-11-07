"use client";

/**
 * §26.6 Git import + drift — compares the registry with `metadata/objects.csv` as it now is in Git
 * (the catalog index; "Refresh from Git" re-reads it). Rows only in Git are import candidates; rows
 * that differ are drifted — adopt Git's copy (upserted with provenance `git-import`) or keep the
 * registry's, which the next Sync to Git writes back. Nothing here writes to Git.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Download, Loader2, RefreshCw } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { api, ApiError } from "@/lib/api/client";
import { refreshCatalog } from "@/lib/api/catalog-refresh";
import type { OnboardingGitDrift, OnboardingObjectEntry } from "@/types";

interface GitDriftDialogProps {
  open: boolean;
  onClose: () => void;
  projectId: string;
  canEdit: boolean;
  /** Called after Git rows were adopted into the registry. */
  onAdopted: (count: number) => void;
}

const STATUS_BADGE = {
  drifted: { variant: "amber", label: "Drifted" },
  only_in_git: { variant: "blue", label: "Only in Git" },
  only_in_registry: { variant: "gray", label: "Not in Git yet" },
} as const;

export function GitDriftDialog({ open, onClose, projectId, canEdit, onAdopted }: GitDriftDialogProps) {
  const [branch, setBranch] = useState("");
  const [drift, setDrift] = useState<OnboardingGitDrift | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<"load" | "refresh" | "adopt" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy("load");
    setError(null);
    try {
      const next = await api.onboarding.gitDrift(projectId, branch.trim() || undefined);
      setDrift(next);
      // Default selection: everything Git has that the registry doesn't, plus rows only Git changed.
      setSelected(new Set(next.rows.filter((r) => r.status === "only_in_git" || (r.changedInGit && !r.changedLocally && r.status === "drifted")).map((r) => r.objectKey)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Drift could not be computed.");
    } finally {
      setBusy(null);
    }
  }, [projectId, branch]);

  useEffect(() => {
    if (open && projectId) void load();
    // Only on open / project change — branch edits reload explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectId]);

  async function refresh() {
    setBusy("refresh");
    setError(null);
    try {
      const job = await refreshCatalog(projectId, branch.trim() || undefined);
      if (job.status === "failed") setError(job.error ?? "Refreshing from Git failed.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Refreshing from Git failed.");
    } finally {
      setBusy(null);
    }
    await load();
  }

  const adoptable = useMemo(() => (drift?.rows ?? []).filter((r) => r.gitRow), [drift]);

  async function adopt() {
    const rows = adoptable.filter((r) => selected.has(r.objectKey));
    if (!rows.length) return;
    setBusy("adopt");
    setError(null);
    try {
      const objects = rows.map((r) => ({ ...(r.gitRow as unknown as OnboardingObjectEntry), provenance: "git-import" as const }));
      await api.onboarding.bulkUpsert(projectId, objects, drift?.path);
      onAdopted(rows.length);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Adopting Git's rows failed.");
      setBusy(null);
    }
  }

  function toggle(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  return (
    <Dialog open={open} onClose={onClose} wide eyebrow="§26.6" title="Import from Git / drift" description="The registry vs. metadata/objects.csv on a branch of the project repository.">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-2">
          <label className="grid gap-1 text-xs font-semibold text-ink-2">
            Branch
            <Input className="w-48" placeholder={drift?.branch ?? "base branch"} value={branch} onChange={(e) => setBranch(e.target.value)} />
          </label>
          <Button variant="outline" onClick={() => void load()} disabled={busy !== null}>Compare</Button>
          <Button variant="outline" onClick={refresh} disabled={busy !== null}>
            {busy === "refresh" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            Refresh from Git
          </Button>
        </div>

        {error ? <Alert variant="error" title={error} /> : null}
        {drift && !drift.indexed ? (
          <Alert title={`${drift.branch} hasn't been read from Git yet`} description="Refresh from Git to index the repository, then compare." />
        ) : null}
        {drift && drift.indexed && !drift.available ? (
          <Alert title={`No ${drift.path} on ${drift.branch}`} description="Sync the registry to Git first, or check the project's artifact root." />
        ) : null}

        {drift?.available ? (
          <>
            <p className="text-sm text-ink-2">
              <code>{drift.path}</code> @ <code>{drift.blobSha?.slice(0, 7)}</code> — {drift.inSync} in sync, {drift.rows.length} different
              {drift.lastSyncCommitSha ? ` (last sync ${drift.lastSyncCommitSha.slice(0, 7)})` : ""}.
            </p>
            {drift.rows.length === 0 ? <Alert variant="success" title="The registry matches Git." /> : (
              <div className="table-wrap max-h-[50vh] overflow-auto rounded-md border border-line">
                <table>
                  <thead>
                    <tr><th style={{ width: 34 }} /><th>Object</th><th>Status</th><th>Differences (registry → Git)</th></tr>
                  </thead>
                  <tbody>
                    {drift.rows.map((r) => (
                      <tr key={r.objectKey}>
                        <td>
                          {r.gitRow ? (
                            <input type="checkbox" className="h-3.5 w-3.5 accent-teal" checked={selected.has(r.objectKey)} onChange={() => toggle(r.objectKey)} aria-label={`Adopt ${r.objectKey} from Git`} />
                          ) : null}
                        </td>
                        <td><code>{r.objectKey}</code></td>
                        <td>
                          <div className="flex flex-wrap gap-1">
                            <Badge variant={STATUS_BADGE[r.status].variant}>{STATUS_BADGE[r.status].label}</Badge>
                            {r.changedInGit ? <Badge variant="purple">edited in Git</Badge> : null}
                            {r.changedLocally ? <Badge variant="teal">edited here</Badge> : null}
                          </div>
                        </td>
                        <td className="text-xs text-ink-2">
                          {r.fields.length
                            ? r.fields.map((f) => <div key={f.field}><strong>{f.field}</strong>: {f.registry || "∅"} → {f.git || "∅"}</div>)
                            : r.status === "only_in_git" ? "New in Git — import it" : "Only in the registry — the next sync adds it"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : null}

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Close</Button>
          {adoptable.length ? (
            <Button onClick={adopt} disabled={!canEdit || busy !== null || !adoptable.some((r) => selected.has(r.objectKey))}>
              {busy === "adopt" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              Adopt Git for {adoptable.filter((r) => selected.has(r.objectKey)).length} row(s)
            </Button>
          ) : null}
        </div>
      </div>
    </Dialog>
  );
}
