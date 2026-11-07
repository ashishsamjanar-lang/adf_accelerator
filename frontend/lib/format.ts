import type { ArtifactLayer, DeploymentState, Severity } from "../types";

const MS_PER_DAY = 86_400_000;

export function formatDateTime(iso?: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function timeAgo(iso?: string): string {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)} minutes ago`;
  if (diff < MS_PER_DAY) return `${Math.round(diff / 3_600_000)} hours ago`;
  return `${Math.round(diff / MS_PER_DAY)} days ago`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Human label for artifact layers (AGENTS.md §23.9). */
export const LAYER_LABELS: Record<ArtifactLayer, string> = {
  infrastructure: "0 · Infrastructure",
  connection: "1 · Connections",
  data: "2 · Data objects",
  pipeline: "3 · Pipelines",
  runtime: "4 · Runtime",
  metadata: "5 · SQL / metadata",
};

export type Tone = "green" | "blue" | "amber" | "gray" | "red" | "forge";

export function severityTone(severity: Severity): Tone {
  if (severity === "error") return "red";
  if (severity === "warning") return "amber";
  return "blue";
}

export const STATE_TONES: Record<DeploymentState, Tone> = {
  DRAFT: "gray",
  GENERATED: "blue",
  VALIDATED: "blue",
  READY_TO_PUSH: "blue",
  PUSHING: "amber",
  PUSHED: "green",
  PR_CREATED: "green",
  GENERATION_FAILED: "red",
  VALIDATION_FAILED: "red",
  GIT_PUSH_FAILED: "red",
  PR_CREATION_FAILED: "red",
};