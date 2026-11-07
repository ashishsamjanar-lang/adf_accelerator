import { CheckCircle2, GitPullRequest, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export interface PushResult {
  state: "PUSHED" | "PR_CREATED" | "GIT_PUSH_FAILED" | "PR_CREATION_FAILED";
  featureBranch: string;
  baseBranch: string;
  commitSha: string;
  fileCount: number;
  prUrl?: string;
  prNumber?: number;
  message?: string;
  /** No Git credential resolved server-side — this was an offline dry run, not a real push/PR. */
  simulated?: boolean;
}

/**
 * Outcome of a Push to Git operation (AGENTS.md §8, §10). A push is not a
 * deployment — states are limited to PUSHED / PR_CREATED (+ failures).
 */
export function PRResultPanel({ result, className }: { result: PushResult; className?: string }) {
  const failed = result.state.endsWith("_FAILED");
  const Icon = failed ? XCircle : CheckCircle2;

  return (
    <div
      className={cn(
        "rounded-md border p-4",
        failed ? "border-red-bg bg-red-bg/50" : "border-green-bg bg-green-bg/50",
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <Icon className={cn("h-4 w-4", failed ? "text-red" : "text-green")} />
        <strong className="text-sm text-ink">
          {result.state === "PUSHED" && "Artifacts pushed to feature branch"}
          {result.state === "PR_CREATED" && "Pull request created"}
          {result.state === "GIT_PUSH_FAILED" && "Git push failed"}
          {result.state === "PR_CREATION_FAILED" && "Pull request creation failed"}
        </strong>
        {result.simulated ? (
          <span className="rounded-full border border-amber/30 bg-amber-bg px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-amber">
            Simulated — no credential
          </span>
        ) : null}
      </div>

      {result.simulated ? (
        <p className="m-0 mt-2 text-xs leading-5 text-muted">
          No Git credential resolved for this repository, so nothing was actually pushed to a real remote. Configure a
          Git credential (project settings or this push&apos;s token field) and push again.
        </p>
      ) : null}

      {result.message ? <p className="m-0 mt-2 text-xs leading-5 text-muted">{result.message}</p> : null}

      <dl className="m-0 mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
        <div>
          <dt className="text-faint">Feature branch</dt>
          <dd className="m-0 font-mono text-ink-2">{result.featureBranch}</dd>
        </div>
        <div>
          <dt className="text-faint">Base branch</dt>
          <dd className="m-0 font-mono text-ink-2">{result.baseBranch}</dd>
        </div>
        <div>
          <dt className="text-faint">Commit</dt>
          <dd className="m-0 font-mono text-ink-2">{result.commitSha}</dd>
        </div>
        <div>
          <dt className="text-faint">Files</dt>
          <dd className="m-0 text-ink-2">{result.fileCount}</dd>
        </div>
      </dl>

      {result.prUrl ? (
        <a
          href={result.prUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-flex items-center gap-1.5 text-xs font-bold text-teal-dark"
        >
          <GitPullRequest className="h-3.5 w-3.5" />
          View PR {result.prNumber ? `#${result.prNumber}` : ""}
        </a>
      ) : null}
    </div>
  );
}
