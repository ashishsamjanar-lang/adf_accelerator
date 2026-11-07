"use client";

import { ArrowUpRight, Boxes, Cable, Database, Download, Hammer, ListChecks } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { ARTIFACT_ICONS, NAV_ICONS, type ViewId } from "@/components/shell/navigation";
import type { ArtifactType } from "@/types";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { can } from "@/lib/state";
import { downloadTextFile } from "@/lib/download";
import { buildLinkedServiceTemplate, buildSourceObjectsTemplate } from "@/lib/adf/connections";
import { buildOnboardingObjectsTemplate } from "@/lib/adf/onboarding";

/** Mirrors `features/samples/samples-view.tsx`'s SAMPLE_GUIDES — kept in sync
 *  by hand (two short entries; not worth sharing a module for). */
const SAMPLE_PROJECTS: Array<{ title: string; summary: string; icon: typeof Database; badges: string[] }> = [
  {
    title: "Sample 1 · Legacy Policy Admin → Enterprise Lakehouse",
    summary: "One legacy on-prem SQL Server system + nightly flat files, migrated into a governed lake and warehouse.",
    icon: Database,
    badges: ["Full + incremental", "Chunked high-volume loads", "Target table/file naming"],
  },
  {
    title: "Sample 2 · Enterprise Data Hub — 8 source systems",
    summary:
      "SAP, Salesforce, Workday, Oracle EBS and more, consolidated into a bronze/silver/gold medallion lake — build layer-specific pipelines for each with Silver conform/dedupe and Gold join/aggregate transformations.",
    icon: Boxes,
    badges: ["Bronze · Silver · Gold layers", "Layer-wise table naming", "Data quality checks"],
  },
];

const TEMPLATE_DOWNLOADS: Array<{
  title: string;
  body: string;
  icon: typeof Download;
  fileName: string;
  build: () => string;
  view: ViewId;
}> = [
  {
    title: "Onboarding registry",
    body: "Bulk-onboard many source objects at once: source → target, file format, and load behaviour, one row each.",
    icon: ListChecks,
    fileName: "onboarding_objects_template.csv",
    build: buildOnboardingObjectsTemplate,
    view: "delivery.onboarding",
  },
  {
    title: "Source objects (per connection)",
    body: "The table/file list for one already-configured connection — upload it from that connection's edit screen.",
    icon: Cable,
    fileName: "source_objects_template.csv",
    build: buildSourceObjectsTemplate,
    view: "pipelines.connections",
  },
  {
    title: "Linked service (bulk connections)",
    body: "Bulk-create several SQL Server connections at once. A different connector needs its own template — open Connections to pick one.",
    icon: Cable,
    fileName: "linked_service_sql_server_template.csv",
    build: () => buildLinkedServiceTemplate("sql_server", "sql_authentication"),
    view: "pipelines.connections",
  },
];

export function HomeView({ data, user, onNavigate }: FeatureProps) {
  const pipelines = data.pipelines.data;
  const artifacts = data.artifacts.data;
  const isLoading = data.pipelines.loading;

  const today = new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

    const committedCount = pipelines.filter((p) => p.state === "PUSHED" || p.state === "PR_CREATED").length;
  const draftCount = pipelines.filter((p) => p.state === "DRAFT" || p.state === "GENERATED").length;
  const failedCount = pipelines.filter((p) => p.state.startsWith("FAILED")).length;

  // Icons come from the shared maps so every entry point matches the rail exactly.
  const artifactSummary: Array<{ label: string; value: number; type: ArtifactType }> = [
    { label: "ADF Pipelines", value: artifacts.filter((a) => a.artifactType === "pipeline").length, type: "pipeline" },
    { label: "Datasets", value: artifacts.filter((a) => a.artifactType === "dataset").length, type: "dataset" },
    { label: "Linked Services", value: artifacts.filter((a) => a.artifactType === "linked_service").length, type: "linked_service" },
    { label: "Parameters", value: artifacts.filter((a) => a.artifactType === "parameter").length, type: "parameter" },
    { label: "Triggers", value: artifacts.filter((a) => a.artifactType === "trigger").length, type: "trigger" },
  ];

  const shortcuts: Array<{ label: string; view: ViewId; color: string }> = [
    { label: "Build", view: "pipelines.create", color: "forge" },
    { label: "Catalog", view: "delivery.catalog", color: "blue" },
    { label: "Onboarding", view: "delivery.onboarding", color: "amber" },
    { label: "Drafts", view: "pipelines.drafts", color: "teal" },
    { label: "Git", view: "pipelines.git", color: "green" },
    { label: "Patterns", view: "pipelines.templates", color: "purple" },
    { label: "Sample Guides", view: "delivery.samples", color: "blue" },
    { label: "Teams", view: "teams", color: "teal" },
    { label: "Projects", view: "projects", color: "green" },
    { label: "Project Settings", view: "admin.project_settings", color: "gray" },
    { label: "Standards", view: "admin.standards", color: "blue" },
  ];

  if (isLoading) {
    return (
      <div className="animate-fade-in">
        <div className="page-heading">
          <Skeleton className="h-4 w-32 mb-2" />
          <Skeleton className="h-8 w-64 mb-2" />
          <Skeleton className="h-4 w-96" />
        </div>
        <div className="metric-grid">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-64 mt-6" />
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow={today.toUpperCase()}
        title="Overview"
        detail="Governed ADF delivery workspace. Create, validate, and push pipeline specifications to Git."
        action={
          can(user.role, "pipeline:create") ? (
            <Button onClick={() => onNavigate("pipelines.create")}>
              <Hammer className="h-3.5 w-3.5" />
              Open Build
            </Button>
          ) : undefined
        }
      />

      <div className="metric-grid">
        {[
          { label: "Active pipelines", value: String(pipelines.filter((p) => p.state !== "DRAFT").length), note: `${pipelines.length} total`, tone: "forge" as const, trend: "vs last month" },
          { label: "Ready for Git push", value: String(committedCount), note: "validated artifacts", tone: "blue" as const, trend: "traceable to spec" },
          { label: "Need attention", value: String(failedCount), note: failedCount ? `${failedCount} need retry` : "all clear", tone: failedCount ? ("amber" as const) : ("green" as const), trend: "needs decision" },
          { label: "Drafts in progress", value: String(draftCount), note: "work in progress", tone: "forge" as const, trend: "deterministic builds" },
        ].map((metric) => (
          <article className="metric-card" key={metric.label}>
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
            <small className="metric-note">
              <b className={metric.tone === "amber" ? "amber-text" : metric.tone === "forge" ? "forge-text" : "positive"}>{metric.trend}</b>
              {metric.note}
            </small>
          </article>
        ))}
      </div>

      <div className="section-heading">
        <div>
          <span className="eyebrow">SHORTCUTS</span>
          <h2>Quick access</h2>
          <p>Navigate to any component or start creating pipelines.</p>
        </div>
      </div>

      <div className="shortcut-grid">
        {shortcuts.map((shortcut) => {
          const Icon = NAV_ICONS[shortcut.view] ?? Hammer;
          return (
            <button
              key={shortcut.view}
              className={`shortcut-card shortcut-${shortcut.color}`}
              onClick={() => onNavigate(shortcut.view)}
            >
              <span className="shortcut-icon">
                <Icon className="h-5 w-5" />
              </span>
              <span className="shortcut-label">{shortcut.label}</span>
            </button>
          );
        })}
      </div>

      <div className="section-heading" style={{ marginTop: "34px" }}>
        <div>
          <span className="eyebrow">TEMPLATES</span>
          <h2>Bulk-onboarding templates</h2>
          <p>Download a ready-to-fill template with a dummy example, then upload it back in to onboard many items at once.</p>
        </div>
      </div>

      <div className="template-grid">
        {TEMPLATE_DOWNLOADS.map((template) => (
          <article key={template.title} className="panel template-card">
            <span className="template-icon">
              <template.icon className="h-4 w-4" />
            </span>
            <h3>{template.title}</h3>
            <p>{template.body}</p>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => downloadTextFile(template.fileName, template.build())}>
                <Download className="h-3.5 w-3.5" />
                Download template
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onNavigate(template.view)}>
                Open
              </Button>
            </div>
          </article>
        ))}
      </div>

      <div className="section-heading" style={{ marginTop: "34px" }}>
        <div>
          <span className="eyebrow">REFERENCE IMPLEMENTATIONS</span>
          <h2>Sample projects</h2>
          <p>Worked, enterprise-grade guides — including bronze/silver/gold layer-specific pipeline creation.</p>
        </div>
      </div>

      <div className="template-grid">
        {SAMPLE_PROJECTS.map((sample) => (
          <article key={sample.title} className="panel template-card">
            <span className="template-icon">
              <sample.icon className="h-4 w-4" />
            </span>
            <h3>{sample.title}</h3>
            <p>{sample.summary}</p>
            <div className="mb-4 flex flex-wrap gap-1.5">
              {sample.badges.map((badge) => (
                <span key={badge} className="rounded border border-line bg-soft px-2 py-0.5 font-mono text-2xs text-muted">
                  {badge}
                </span>
              ))}
            </div>
            <Button size="sm" variant="outline" onClick={() => onNavigate("delivery.samples")}>
              Open guide
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Button>
          </article>
        ))}
      </div>

      <div className="section-heading" style={{ marginTop: "34px" }}>
        <div>
          <span className="eyebrow">SNAPSHOTS</span>
          <h2>Artifact dashboard</h2>
          <p>Overview of generated and published artifacts across all types.</p>
        </div>
      </div>

      <div className="dashboard-grid">
        <div className="dashboard-card">
          <div className="dashboard-card-head">
            <span className="eyebrow">ARTIFACT TYPES</span>
          </div>
          <div className="dashboard-metric-rows">
            {artifactSummary.map((item) => {
              const Icon = ARTIFACT_ICONS[item.type];
              return (
                <div key={item.label} className="dashboard-metric-row">
                  <span className="dashboard-metric-label">
                    <Icon className="h-3.5 w-3.5" />
                    {item.label}
                  </span>
                  <span className="dashboard-metric-value">{item.value}</span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="dashboard-card">
          <div className="dashboard-card-head">
            <span className="eyebrow">PIPELINE LIFECYCLE</span>
          </div>
          <div className="dashboard-metric-rows">
            <div className="dashboard-metric-row">
              <span className="dashboard-metric-label">DRAFT</span>
              <span className="dashboard-metric-value">{pipelines.filter((p) => p.state === "DRAFT").length}</span>
            </div>
            <div className="dashboard-metric-row">
              <span className="dashboard-metric-label">GENERATED</span>
              <span className="dashboard-metric-value">{pipelines.filter((p) => p.state === "GENERATED").length}</span>
            </div>
            <div className="dashboard-metric-row">
              <span className="dashboard-metric-label">PUSHED</span>
              <span className="dashboard-metric-value">{pipelines.filter((p) => p.state === "PUSHED").length}</span>
            </div>
            <div className="dashboard-metric-row">
              <span className="dashboard-metric-label">PR CREATED</span>
              <span className="dashboard-metric-value">{pipelines.filter((p) => p.state === "PR_CREATED").length}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}