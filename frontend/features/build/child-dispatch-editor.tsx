"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ChildDispatchCase } from "@/lib/adf/spec";

interface ChildDispatchEditorProps {
  value: ChildDispatchCase[];
  defaultChildName: string;
  /** §24 pick/reference — child pipelines already in this project (free text still allowed). */
  suggestions?: string[];
  errors?: string[];
  onChange: (next: ChildDispatchCase[] | undefined) => void;
}

/**
 * Gap #30 — optional routing of control-table rows (by `TaskGroup`) to other existing child pipelines
 * from this master. Empty = the master only ever runs its own child (today's default output).
 */
export function ChildDispatchEditor({ value, defaultChildName, suggestions = [], errors, onChange }: ChildDispatchEditorProps) {
  const update = (index: number, patch: Partial<ChildDispatchCase>) =>
    onChange(value.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  const remove = (index: number) => {
    const next = value.filter((_, i) => i !== index);
    onChange(next.length ? next : undefined);
  };
  return (
    <div className="grid gap-2" style={{ gridColumn: "1 / -1" }}>
      <span className="text-xs font-bold text-ink">Dispatch to other child pipelines (optional)</span>
      <small className="text-muted">
        Adds a Switch on the control table&apos;s <code>TaskGroup</code> column. Matching rows run the named existing child;
        every other row runs <code>{defaultChildName}</code>.
      </small>
      {value.map((c, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            aria-label={`TaskGroup value ${i + 1}`}
            value={c.matchValue}
            placeholder="TaskGroup value, e.g. finance"
            onChange={(e) => update(i, { matchValue: e.target.value })}
          />
          <Input
            aria-label={`Child pipeline ${i + 1}`}
            value={c.childPipelineName}
            placeholder="Existing child, e.g. PL_finance_child"
            list={suggestions.length ? "dispatch-child-options" : undefined}
            onChange={(e) => update(i, { childPipelineName: e.target.value })}
          />
          <Button type="button" size="sm" variant="outline" aria-label={`Remove case ${i + 1}`} onClick={() => remove(i)}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      ))}
      <div>
        <Button type="button" size="sm" variant="outline" onClick={() => onChange([...value, { matchValue: "", childPipelineName: "" }])}>
          <Plus className="h-3.5 w-3.5" />
          Add dispatch case
        </Button>
      </div>
      {suggestions.length ? (
        <datalist id="dispatch-child-options">
          {[...new Set(suggestions)].map((n) => (
            <option key={n} value={n} />
          ))}
        </datalist>
      ) : null}
      {errors?.map((msg) => (
        <small key={msg} className="text-red">{msg}</small>
      ))}
    </div>
  );
}
