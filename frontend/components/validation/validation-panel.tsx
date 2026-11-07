import { AlertCircle, AlertTriangle, Info } from "lucide-react";
import type { ValidationIssue, ValidationLayer } from "@/types";
import { cn } from "@/lib/utils";

interface ValidationPanelProps {
  issues: ValidationIssue[];
  /** Called when a user clicks an issue that carries a field reference. */
  onSelectField?: (field: string) => void;
  emptyMessage?: string;
  className?: string;
}

const ICONS = {
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
} as const;

const TONE = {
  error: "text-red",
  warning: "text-amber",
  info: "text-blue",
} as const;

// §29.1 pyramid order. `schema`/`live` are still target design (§29.5) — never
// populated by the backend today, kept here only so a future issue with one of
// those layers sorts into the right place instead of falling through.
const LAYER_ORDER: ValidationLayer[] = ["syntax", "schema", "semantic", "security", "policy", "live"];
const LAYER_LABELS: Record<ValidationLayer, string> = {
  syntax: "Syntax",
  schema: "Schema",
  semantic: "Semantic",
  security: "Security",
  policy: "Policy",
  live: "Live check",
};

function bySeverity(issues: ValidationIssue[]): ValidationIssue[] {
  const errors = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity === "warning");
  const infos = issues.filter((i) => i.severity === "info");
  return [...errors, ...warnings, ...infos];
}

function IssueRow({ issue, onSelectField }: { issue: ValidationIssue; onSelectField?: (field: string) => void }) {
  const Icon = ICONS[issue.severity];
  const clickable = Boolean(issue.field && onSelectField);
  return (
    <li>
      <button
        type="button"
        disabled={!clickable}
        onClick={() => issue.field && onSelectField?.(issue.field)}
        className={cn(
          "flex w-full items-start gap-2.5 px-4 py-2.5 text-left text-xs",
          clickable ? "hover:bg-surface-2" : "cursor-default",
        )}
      >
        <Icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", TONE[issue.severity])} />
        <span className="min-w-0 flex-1 text-ink-2">
          {issue.message}
          {issue.source ? <span className="ml-1.5 text-faint">· {issue.source}</span> : null}
          {issue.fixHint ? <span className="mt-0.5 block text-muted">Fix: {issue.fixHint}</span> : null}
        </span>
        {issue.field || issue.ruleId ? (
          <code className="shrink-0 text-2xs text-faint">{issue.field ?? issue.ruleId}</code>
        ) : null}
      </button>
    </li>
  );
}

/**
 * Shared validation surface (AGENTS.md §20/§22.20, §29's "unify the 6-layer
 * ValidationResult shape into one panel" DoD). Every layer in §29.1
 * contributes to the same `issues[]` array, tagged by `layer` — when at least
 * one issue carries one, this groups by layer (in pyramid order) and by
 * severity within each layer. Client-side pre-generation checks
 * (`lib/adf/spec.ts`'s `validateDraft`, used for live-as-you-type Build
 * wizard hints) never set `layer` — those fall back to a flat,
 * severity-sorted list, unchanged from before this grouping existed.
 */
export function ValidationPanel({ issues, onSelectField, emptyMessage, className }: ValidationPanelProps) {
  const errors = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity === "warning");
  const infos = issues.filter((i) => i.severity === "info");

  if (issues.length === 0) {
    return (
      <div className={cn("flex items-center gap-2 rounded-md border border-green-bg bg-green-bg/60 px-3.5 py-3 text-xs text-green", className)}>
        <Info className="h-4 w-4 shrink-0" />
        {emptyMessage ?? "All checks passed. Ready to continue."}
      </div>
    );
  }

  const hasLayers = issues.some((i) => Boolean(i.layer));
  const groups = hasLayers
    ? LAYER_ORDER.map((layer) => ({ layer, items: issues.filter((i) => (i.layer ?? "semantic") === layer) })).filter(
        (g) => g.items.length > 0,
      )
    : null;

  return (
    <div className={cn("rounded-md border border-line bg-surface", className)}>
      <div className="flex items-center gap-3 border-b border-line-2 px-4 py-2.5 text-xs font-bold text-ink">
        <span>Validation</span>
        <span className="flex items-center gap-2 font-semibold text-muted">
          {errors.length > 0 && <span className="text-red">{errors.length} error{errors.length === 1 ? "" : "s"}</span>}
          {warnings.length > 0 && <span className="text-amber">{warnings.length} warning{warnings.length === 1 ? "" : "s"}</span>}
          {infos.length > 0 && <span className="text-blue">{infos.length} note{infos.length === 1 ? "" : "s"}</span>}
        </span>
      </div>
      {groups ? (
        <div className="flex flex-col divide-y divide-line-2">
          {groups.map((group) => (
            <div key={group.layer}>
              <div className="px-4 pt-2.5 text-2xs font-bold uppercase tracking-wide text-faint">
                {LAYER_LABELS[group.layer]}
              </div>
              <ul className="m-0 flex list-none flex-col p-0">
                {bySeverity(group.items).map((issue, index) => (
                  <IssueRow key={`${group.layer}-${issue.field ?? "x"}-${index}`} issue={issue} onSelectField={onSelectField} />
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : (
        <ul className="m-0 flex list-none flex-col divide-y divide-line-2 p-0">
          {bySeverity(issues).map((issue, index) => (
            <IssueRow key={`${issue.field ?? "x"}-${index}`} issue={issue} onSelectField={onSelectField} />
          ))}
        </ul>
      )}
    </div>
  );
}
