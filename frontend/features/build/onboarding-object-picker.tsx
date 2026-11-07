"use client";

/**
 * Bronze-layer counterpart to `registry-object-picker.tsx` (§26 onboarding
 * registry, backed by the real `app.onboarding_objects` table,
 * `backend/app/models.py`, as of the Bronze-registry backend fix). Multi-select
 * instead of single-pick since a pipeline takes N source objects, not one
 * curated entity: the user checks off onboarded objects and imports them as
 * `SourceObjectEntry` rows, additive into `draft.sourceObjects` — this
 * component never owns the row list itself, it only ever appends into the
 * one the wizard's `SourceObjectsEditor` already renders (no second,
 * competing object list — same "no duplication" rule the Silver/Gold picker
 * already follows).
 *
 * Renders nothing when nothing is onboarded yet (same zero-behavior-change
 * convention as `RegistryObjectPicker`) — the manual `SourceObjectsEditor`
 * stays the only UI in that case.
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api/client";
import { MAX_PIPELINE_DQ_RULES, dqRulesToPipelineRules } from "@/lib/adf/onboarding";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { SpecDraft } from "@/lib/adf/spec";
import type { OnboardingDqRule, OnboardingObjectEntry, SourceObjectEntry } from "@/types";

interface OnboardingObjectPickerProps {
  projectId: string;
  /** Already-present object names (case-insensitive) — picked rows already in the pipeline are
   * shown as such and excluded from the import, so re-opening this never duplicates a row. */
  existingObjects: string[];
  /** Rules already on the pipeline — imported registry DQ rules only fill the remaining slots. */
  existingDqRuleCount: number;
  /** `dqRules` = the picked objects' registry-authored rules (§30.5), mapped to the pipeline shape. */
  onImport: (rows: SourceObjectEntry[], dqRules: SpecDraft["dataQualityRules"], picked: OnboardingObjectEntry[]) => string[] | void;
  /** Object keys to check on first load (Onboarding's "Build pipeline"); the user still clicks Import. */
  preselectKeys?: string[];
}

export function OnboardingObjectPicker({ projectId, existingObjects, existingDqRuleCount, onImport, preselectKeys }: OnboardingObjectPickerProps) {
  const [dqNote, setDqNote] = useState<string | null>(null);
  const [objects, setObjects] = useState<OnboardingObjectEntry[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set(preselectKeys ?? []));

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    api.onboarding
      .list(projectId)
      .then((rows) => {
        if (!cancelled) setObjects(rows);
      })
      .catch(() => {
        if (!cancelled) setObjects([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const existingKeys = useMemo(
    () => new Set(existingObjects.map((o) => o.trim().toLowerCase())),
    [existingObjects],
  );

  if (objects.length === 0) return null;

  const q = query.trim().toLowerCase();
  const filtered = q
    ? objects.filter(
        (o) =>
          o.object_key.toLowerCase().includes(q) ||
          o.source_system.toLowerCase().includes(q) ||
          o.source_object.toLowerCase().includes(q) ||
          (o.batch_group ?? "").toLowerCase().includes(q),
      )
    : objects;

  function toggle(objectKey: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(objectKey)) next.delete(objectKey);
      else next.add(objectKey);
      return next;
    });
  }

  async function importSelected() {
    const picked = objects.filter((o) => selected.has(o.object_key) && !existingKeys.has(o.source_object.trim().toLowerCase()));
    const rows: SourceObjectEntry[] = picked
      .map((o) => ({
        object: o.source_object,
        schema: o.source_schema || undefined,
        loadingBehavior: o.loading_behavior,
        watermarkColumn: o.watermark_column || undefined,
      }));
    if (rows.length === 0) return;
    // A DQ fetch failure never blocks the object import itself.
    const registryRules: OnboardingDqRule[] = (
      await Promise.all(picked.map((o) => api.onboarding.dqRules.list(projectId, o.object_key).catch(() => [])))
    ).flat();
    const { rules, skipped } = dqRulesToPipelineRules(registryRules);
    const room = Math.max(0, MAX_PIPELINE_DQ_RULES - existingDqRuleCount);
    const notes: string[] = [];
    if (rules.length) notes.push(`Imported ${Math.min(rules.length, room)} data quality rule${rules.length === 1 ? "" : "s"} from the registry.`);
    if (rules.length > room) notes.push(`${rules.length - room} didn't fit the ${MAX_PIPELINE_DQ_RULES}-rule limit.`);
    if (skipped.length) notes.push(`${skipped.length} off or incomplete rule${skipped.length === 1 ? "" : "s"} skipped.`);
    const settingNotes = onImport(rows, rules, picked) ?? [];
    notes.push(...settingNotes);
    setDqNote(notes.length ? notes.join(" ") : null);
    setSelected(new Set());
  }

  return (
    <div className="form-card">
      <h3 className="form-card-title">Import from Onboarding (Bronze registry)</h3>
      <p className="editor-hint">
        Pick from the {objects.length} object{objects.length === 1 ? "" : "s"} already onboarded (Delivery →
        Onboarding) instead of retyping them below.
      </p>
      {objects.length > 5 && (
        <Input
          placeholder="Search by object, source system, or batch group"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="mb-2"
        />
      )}
      <div className="max-h-48 overflow-y-auto rounded-md border border-line">
        {filtered.length === 0 ? (
          <p className="editor-hint p-3">No onboarded objects match this search.</p>
        ) : (
          filtered.map((o) => {
            const alreadyAdded = existingKeys.has(o.source_object.trim().toLowerCase());
            return (
              <label key={o.object_key} className="flex items-center gap-2 border-b border-line px-3 py-2 text-xs last:border-b-0">
                <input
                  type="checkbox"
                  checked={selected.has(o.object_key)}
                  disabled={alreadyAdded}
                  onChange={() => toggle(o.object_key)}
                />
                <span className="flex-1">
                  <strong>{o.source_object}</strong>
                  <span className="text-muted"> · {o.source_system}{o.batch_group ? ` · ${o.batch_group}` : ""}</span>
                </span>
                {alreadyAdded && <span className="status-pill gray">already added</span>}
              </label>
            );
          })
        )}
      </div>
      <div className="mt-2">
        <Button type="button" variant="outline" size="sm" disabled={selected.size === 0} onClick={importSelected}>
          Import {selected.size || ""} selected object{selected.size === 1 ? "" : "s"}
        </Button>
      </div>
      {dqNote ? <p className="editor-hint mt-2">{dqNote}</p> : null}
    </div>
  );
}
