"use client";

import { useState } from "react";
import { ArrowUpRight, BookOpen, Boxes, Database, GitPullRequest, Layers, Workflow } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Two enterprise-grade reference implementations, authored as standalone HTML
 * documents under public/sample-guides/ (not React) so they read like a real
 * shareable spec doc and can be opened on their own outside the app shell.
 * This screen is just the in-app index + inline viewer for them.
 */
interface SampleGuide {
  id: string;
  href: string;
  eyebrow: string;
  title: string;
  summary: string;
  sources: string;
  icon: typeof Database;
  badges: string[];
}

const SAMPLE_GUIDES: SampleGuide[] = [
  {
    id: "legacy-migration",
    href: "/sample-guides/legacy-migration-guide.html",
    eyebrow: "Sample 1 · Legacy migration",
    title: "Legacy Policy Admin → Enterprise Lakehouse",
    summary:
      "One legacy on-premises SQL Server system (plus its nightly flat-file exports) migrated into a governed lake and warehouse — decommissioning a 20-year-old batch job.",
    sources: "1 legacy source (SQL Server tables + SFTP flat files) → lake + warehouse tables",
    icon: Database,
    badges: ["Full + incremental", "Daily / hourly / near-real-time", "Chunked high-volume loads + orchestration", "Standard metadata & logging", "Target table/file naming", "Data quality checks", "End-to-end CI/CD"],
  },
  {
    id: "multi-source-etl",
    href: "/sample-guides/multi-source-etl-guide.html",
    eyebrow: "Sample 2 · Multi-source ETL",
    title: "Enterprise Data Hub — 8 source systems",
    summary:
      "SAP, Salesforce, Workday, Oracle EBS, on-prem SQL Server, vendor SFTP files, a marketing REST API and a clickstream event feed, consolidated into one governed ingestion platform.",
    sources: "8 sources (tables, files, APIs, streaming) → bronze/silver/gold lake + warehouse tables & file extracts",
    icon: Boxes,
    badges: ["Bronze · Silver · Gold layers", "Full + incremental", "Daily / hourly / near-real-time", "Chunked high-volume loads + orchestration", "Standard metadata & logging", "Layer-wise table naming", "Data quality checks", "End-to-end CI/CD"],
  },
];

const STEP_COLORS = {
  blue: { bg: "bg-blue-bg", border: "border-blue/40", icon: "bg-blue text-white", title: "text-blue" },
  purple: { bg: "bg-purple-bg", border: "border-purple/40", icon: "bg-purple text-white", title: "text-purple" },
  green: { bg: "bg-green-bg", border: "border-green/40", icon: "bg-green text-white", title: "text-green" },
} as const;

const GUIDE_STRUCTURE: Array<{ title: string; body: string; icon: typeof Layers; color: keyof typeof STEP_COLORS }> = [
  { title: "1. Define the problem", body: "Business and technical context, pain points, and the success criteria the implementation is judged against.", icon: BookOpen, color: "blue" },
  { title: "2. Approach", body: "Target architecture, where ADF Forge fits versus what ADF itself executes, the impact on how the team builds, and the CI/CD strategy.", icon: Workflow, color: "purple" },
  { title: "3. Implementation", body: "Source inventory, chunked/parallel extraction for high-volume tables, orchestration design, standard folder structure, target table/file naming standardization, standard metadata columns, standard logging, data quality checks, trigger/cadence design, a Build-wizard walkthrough, the CI/CD pipeline stage by stage, and promoting onboarded source & control-table metadata across dev/test/prod.", icon: GitPullRequest, color: "green" },
];

export function SamplesView(_props: FeatureProps) {
  const [openId, setOpenId] = useState<string | null>(null);
  const openGuide = SAMPLE_GUIDES.find((guide) => guide.id === openId) ?? null;

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="ENTERPRISE REFERENCE IMPLEMENTATIONS"
        title="Sample Guides"
        detail="Two worked, enterprise-grade project implementation guides — one legacy-to-target migration, one multi-source ETL hub — each built with ADF Forge end-to-end and shipped through a real dev → test → prod CI/CD pipeline. Read them in the app or open the full HTML document on its own."
      />

      <div className="mb-5 grid gap-3 sm:grid-cols-3">
        {GUIDE_STRUCTURE.map((section) => {
          const colors = STEP_COLORS[section.color];
          return (
            <div
              key={section.title}
              className={cn("flex gap-3 rounded-lg border-2 px-4 py-3.5 shadow-sm", colors.bg, colors.border)}
            >
              <span className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-full", colors.icon)}>
                <section.icon className="h-4 w-4" />
              </span>
              <div>
                <strong className={cn("block text-sm font-bold", colors.title)}>{section.title}</strong>
                <span className="text-xs text-ink-2">{section.body}</span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {SAMPLE_GUIDES.map((guide) => (
          <article key={guide.id} className="panel template-card flex flex-col">
            <span className="template-icon">
              <guide.icon className="h-4 w-4" />
            </span>
            <span className="eyebrow">{guide.eyebrow}</span>
            <h3>{guide.title}</h3>
            <p>{guide.summary}</p>
            <p className="-mt-3 mb-4 font-mono text-2xs text-muted">{guide.sources}</p>
            <div className="mb-4 flex flex-wrap gap-1.5">
              {guide.badges.map((badge) => (
                <span key={badge} className="rounded border border-line bg-soft px-2 py-0.5 font-mono text-2xs text-muted">
                  {badge}
                </span>
              ))}
            </div>
            <div className="mt-auto flex gap-2">
              <Button className="flex-1" onClick={() => setOpenId(openId === guide.id ? null : guide.id)}>
                {openId === guide.id ? "Hide guide" : "View guide"}
              </Button>
              <a
                href={guide.href}
                target="_blank"
                rel="noopener noreferrer"
                className={cn(buttonVariants({ variant: "outline" }), "shrink-0")}
              >
                Open in new tab
                <ArrowUpRight className="h-3.5 w-3.5" />
              </a>
            </div>
          </article>
        ))}
      </div>

      {openGuide ? (
        <section className="panel mt-5 overflow-hidden p-0">
          <div className="panel-toolbar">
            <span className="text-xs font-semibold text-ink-2">{openGuide.title}</span>
            <a
              href={openGuide.href}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs font-bold text-teal-dark hover:underline"
            >
              Open full-width
              <ArrowUpRight className="h-3 w-3" />
            </a>
          </div>
          <iframe
            key={openGuide.id}
            src={openGuide.href}
            title={openGuide.title}
            className="h-[75vh] w-full border-0"
          />
        </section>
      ) : null}
    </div>
  );
}
