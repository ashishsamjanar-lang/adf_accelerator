"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ColumnMapping } from "@/lib/adf/spec";

interface ColumnMappingsEditorProps {
  value: ColumnMapping[];
  errors?: string[];
  onChange: (next: ColumnMapping[] | undefined) => void;
  /** Overrides for reuse per source object (defaults describe the pipeline-level mappings). */
  title?: string;
  hint?: string;
  /** Prefixes every accessible label, so several editors on one page stay distinguishable. */
  labelPrefix?: string;
}

/**
 * Schema-drift slice — optional explicit Copy column mappings / renames (TabularTranslator `mappings`).
 * Empty = ADF's by-name auto-mapping (today's default output).
 */
export function ColumnMappingsEditor({ value, errors, onChange, title, hint, labelPrefix = "" }: ColumnMappingsEditorProps) {
  const update = (index: number, patch: Partial<ColumnMapping>) =>
    onChange(value.map((m, i) => (i === index ? { ...m, ...patch } : m)));
  const remove = (index: number) => {
    const next = value.filter((_, i) => i !== index);
    onChange(next.length ? next : undefined);
  };
  return (
    <div className="grid gap-2" style={{ gridColumn: "1 / -1" }}>
      <span className="text-xs font-bold text-ink">{title ?? "Column mappings (optional)"}</span>
      <small className="text-muted">
        {hint ??
          "Explicit source → sink column mappings for every Copy activity, e.g. to rename a column. Leave empty to map by name."}
      </small>
      {value.map((m, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            aria-label={`${labelPrefix}Source column ${i + 1}`}
            value={m.source}
            placeholder="Source column"
            onChange={(e) => update(i, { source: e.target.value })}
          />
          <Input
            aria-label={`${labelPrefix}Sink column ${i + 1}`}
            value={m.sink}
            placeholder="Sink column"
            onChange={(e) => update(i, { sink: e.target.value })}
          />
          <Button type="button" size="sm" variant="outline" aria-label={`${labelPrefix}Remove mapping ${i + 1}`} onClick={() => remove(i)}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      ))}
      <div>
        <Button type="button" size="sm" variant="outline" onClick={() => onChange([...value, { source: "", sink: "" }])}>
          <Plus className="h-3.5 w-3.5" />
          Add column mapping
        </Button>
      </div>
      {errors?.map((msg) => (
        <small key={msg} className="text-red">{msg}</small>
      ))}
    </div>
  );
}
