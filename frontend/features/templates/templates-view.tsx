"use client";

import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { setPrefillTemplate, TEMPLATE_LIST } from "@/lib/adf/spec";
import { LayoutTemplate } from "lucide-react";

export function TemplatesView({ onNavigate }: FeatureProps) {
  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="TEMPLATE REGISTRY"
        title="Pipeline templates"
        detail="Metadata-driven only: every pipeline is generated from a template plus a metadata/control table — no free-form authoring. Choose Full Load, Incremental (Delta/watermark) Load, Initial Load, Composite Watermark Load, or Metadata-Driven Load. Every template exposes each parameter you need: source/target connectors, control table settings, watermark columns (up to 5), parallel copies, retry/error handling, logging, and custom metadata."
      />
      <div className="template-grid">
        {TEMPLATE_LIST.map((template) => (
          <article className="panel template-card" key={template.id}>
            <span className="template-icon"><LayoutTemplate className="h-4 w-4" /></span>
            <span className="eyebrow">TEMPLATE · v{template.version}</span>
            <h3>{template.name}</h3>
            <p>{template.description}</p>
            <div className="mb-4 flex flex-wrap gap-1.5">
              {template.supportedSourceTypes.slice(0, 4).map((source) => (
                <span key={source} className="rounded border border-line bg-soft px-2 py-0.5 font-mono text-2xs text-muted">
                  src:{source.replace(/_/g, " ").substring(0, 12)}
                </span>
              ))}
              {template.supportedSourceTypes.length > 4 && (
                <span className="rounded border border-line bg-soft px-2 py-0.5 font-mono text-2xs text-muted">
                  +{template.supportedSourceTypes.length - 4} more
                </span>
              )}
            </div>
            <div className="mb-4 flex flex-wrap gap-1.5">
              {template.supportedTargetTypes.slice(0, 4).map((target) => (
                <span key={target} className="rounded border border-line bg-soft px-2 py-0.5 font-mono text-2xs text-muted">
                  dst:{target.replace(/_/g, " ").substring(0, 12)}
                </span>
              ))}
              {template.supportedTargetTypes.length > 4 && (
                <span className="rounded border border-line bg-soft px-2 py-0.5 font-mono text-2xs text-muted">
                  +{template.supportedTargetTypes.length - 4} more
                </span>
              )}
            </div>
            <div className="mb-4">
              <span className="eyebrow mb-1 block">Generation contract</span>
              <ul className="m-0 list-none space-y-1 p-0">
                {template.fields.slice(0, 6).map((field) => (
                  <li key={field} className="font-mono text-2xs text-muted">
                    {field}
                  </li>
                ))}
                {template.fields.length > 6 && (
                  <li className="font-mono text-2xs text-muted">
                    +{template.fields.length - 6} more fields
                  </li>
                )}
              </ul>
            </div>
            <Button
              className="w-full"
              onClick={() => {
                setPrefillTemplate(template.id);
                onNavigate("pipelines.create");
              }}
            >
              Use this template →
            </Button>
          </article>
        ))}
      </div>
    </div>
  );
}