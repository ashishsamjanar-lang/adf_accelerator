"use client";

/**
 * §33.7/§33.8 Build wizard integration: picking a Silver/Gold registry
 * object copies its `curatedEntity`/`martDomain`/`transformations`/
 * `dataQualityRules` straight into the draft and records the pick as
 * `silverObjectRef`/`martObjectRef` (display/traceability + gates the two
 * additive control-table artifacts, §33.7). **Falls back automatically to
 * nothing (renders null) whenever the project has zero registry objects for
 * this layer** — the existing manual `curatedEntity`/`martDomain` `<Input>`s
 * in `create-pipeline-wizard.tsx` stay the only UI in that case, so this is
 * zero behavior change until someone actually onboards an object (§33.8).
 *
 * **Tag/domain/source/status filters (§33.8, added to close the STTM-10
 * gap)** — a registry with more than a handful of objects had no way to
 * narrow the plain dropdown. Filters run server-side via the same
 * `api.registry.list` query params `registry-tab.tsx`'s Onboarding catalog
 * grid already uses, kept against a separately-fetched *unfiltered* row set
 * (`allRows`) so "does this project have any registry objects at all"
 * (the render-nothing fallback) and "what does the current filter match"
 * never get confused with each other — a filter that matches nothing must
 * show an empty-state message pointing at Onboarding, never a silently empty
 * dropdown indistinguishable from "no filters applied, zero results."
 *
 * Deliberately its own small "picked" summary instead of reusing the
 * wizard's local (unexported) `InheritedField` component — that component's
 * copy ("Set in Project Settings") doesn't fit a registry pick, and
 * duplicating ~15 lines of presentational JSX was cheaper than exporting a
 * component out of a 2000+ line file for one caller.
 */
import { useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import type { RegistryObject, WatermarkColumn } from "@/types";
import type { ViewId } from "@/components/shell/navigation";
import type { SpecDraft } from "@/lib/adf/spec";

const WATERMARK_TYPES: WatermarkColumn["type"][] = ["datetime", "int", "bigint", "string"];

/**
 * E2E-06: the registry object's load settings, as a `SpecDraft` patch, so picking an object no longer
 * leaves whatever the Build form already held (the generated control-table row then contradicted the
 * registry). Only fields the object actually carries are set; a DeltaLoad keeps a composite/initial mode.
 */
export function registryLoadSettings(obj: RegistryObject, prev: SpecDraft): Partial<SpecDraft> {
  // The object's writeBehavior describes the *layer-table* load (keyed by its business keys below), not
  // the Bronze landing Copy — mapping merge/upsert onto the Copy left an Upsert with no source-side keys.
  const patch: Partial<SpecDraft> = {};
  // RUN-14: business keys are target-layer names, so they key the layer load, never the Bronze Copy's upsert.
  if (obj.businessKeys.length > 0) patch.layerKeyColumns = obj.businessKeys.join(", ");
  if (obj.loadingBehavior === "FullLoad") {
    Object.assign(patch, { loadTypeMode: "full", watermarkEnabled: false, fullLoad: true });
  } else {
    const mode = prev.loadTypeMode === "full" ? "incremental" : prev.loadTypeMode;
    Object.assign(patch, { loadTypeMode: mode, watermarkEnabled: true, fullLoad: false });
    const name = obj.watermarkColumn?.trim();
    if (name) {
      const type = WATERMARK_TYPES.find((t) => t === obj.watermarkColumnType) ?? prev.watermarkColumns[0]?.type ?? "datetime";
      patch.watermarkColumns = [{ name, type, order: 1 }];
    }
  }
  return patch;
}

interface RegistryObjectPickerProps {
  /** The Build wizard's `MedallionLayer` ("silver" | "gold" — never called for "bronze"). */
  layer: "silver" | "gold";
  projectId: string;
  pickedObjectKey: string | undefined;
  onPick: (obj: RegistryObject) => void;
  onClear: () => void;
  /** Optional — only used for the "no matches" empty-state's link to Onboarding. */
  onNavigate?: (view: ViewId) => void;
}

export function RegistryObjectPicker({ layer, projectId, pickedObjectKey, onPick, onClear, onNavigate }: RegistryObjectPickerProps) {
  const sttmLayer = layer === "gold" ? "mart" : "silver";
  const label = layer === "gold" ? "Gold" : "Silver";
  // Unfiltered — decides only whether this component renders the picker or the
  // empty/error guidance below (§33.8: an empty registry falls back to guidance
  // text, not a silently vanished component — a failed fetch must never look
  // identical to a genuinely empty registry, so `fetchStatus` tracks the two
  // separately instead of collapsing both into the same `[]`).
  const [allRows, setAllRows] = useState<RegistryObject[]>([]);
  const [fetchStatus, setFetchStatus] = useState<"loading" | "error" | "ready">("loading");
  // Filtered — what the dropdown itself actually offers.
  const [rows, setRows] = useState<RegistryObject[]>([]);
  const [rowsLoading, setRowsLoading] = useState(false);
  const [tagFilter, setTagFilter] = useState("");
  const [domainFilter, setDomainFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  useEffect(() => {
    if (!projectId) {
      setAllRows([]);
      setFetchStatus("ready");
      return;
    }
    let cancelled = false;
    setFetchStatus("loading");
    api.registry
      .list(sttmLayer, projectId)
      .then((data) => {
        if (!cancelled) {
          setAllRows(data);
          setFetchStatus("ready");
        }
      })
      .catch(() => {
        if (!cancelled) setFetchStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [sttmLayer, projectId]);

  useEffect(() => {
    if (!projectId || !allRows || allRows.length === 0) {
      setRows([]);
      return;
    }
    let cancelled = false;
    setRowsLoading(true);
    api.registry
      .list(sttmLayer, projectId, {
        tag: tagFilter || undefined,
        domain: domainFilter || undefined,
        sourceSystem: sourceFilter || undefined,
        status: statusFilter || undefined,
      })
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch(() => {
        if (!cancelled) setRows([]);
      })
      .finally(() => {
        if (!cancelled) setRowsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sttmLayer, projectId, allRows, tagFilter, domainFilter, sourceFilter, statusFilter]);

// Still loading (brief, first render) — nothing useful to show yet.
  if (fetchStatus === "loading") return null;

  // A failed fetch is never allowed to look like "nothing onboarded" — say so
  // explicitly instead of silently falling back to the manual field with no
  // explanation (the bug this component previously had: any offline/demo-mode
  // fetch failure was indistinguishable from a genuinely empty registry).
  if (fetchStatus === "error") {
    return (
      <div className="form-fields">
        <p className="editor-hint text-red">
          Couldn&apos;t load the {label} registry — try again, or enter the {label.toLowerCase()} details
          manually below.
        </p>
      </div>
    );
  }

  const picked = pickedObjectKey ? allRows.find((r) => r.objectKey === pickedObjectKey) : undefined;

  // Genuinely empty — never disappear silently. The curated entity name below
  // is required (§ layer naming standard), so leaving the user with no
  // explanation of *why* is exactly the reported dead end; point at STTM
  // onboarding as the intended source.
  if (allRows.length === 0 && !pickedObjectKey) {
    return (
      <div className="form-fields">
        <p className="editor-hint">
          No {label.toLowerCase()} objects onboarded yet for this project — enter one manually below, or go
          build the STTM workbook in Onboarding → {label}.
          {onNavigate && (
            <>
              {" "}
              <Button type="button" variant="ghost" size="sm" onClick={() => onNavigate("delivery.onboarding")}>
                Go to Onboarding
              </Button>
            </>
          )}
        </p>
      </div>
    );
  }

  if (pickedObjectKey) {
    return (
      <div className="inherited-field">
        <span className="inherited-field-label">Registry object</span>
        <div className="inherited-field-row">
          <span className="status-pill gray">
            <i />
            {picked?.curatedEntity ?? pickedObjectKey}
          </span>
          <Button type="button" variant="ghost" size="sm" onClick={onClear}>
            <RotateCcw className="h-3.5 w-3.5" />
            Enter manually instead
          </Button>
        </div>
        <small>
          Prefilled from the {label} registry
          {picked?.updatedAt ? ` as of ${new Date(picked.updatedAt).toLocaleDateString()}` : ""} — editing the registry
          object later won&apos;t change this draft; re-pick it here to refresh.
        </small>
      </div>
    );
  }

  const hasActiveFilter = Boolean(tagFilter || domainFilter || sourceFilter || statusFilter);
  // A disabled object (e.g. an unapproved Data Modular mapping, DMT-10) is never offered for a new pipeline.
  const offered = rows.filter((r) => r.enabled !== false);
  const disabledCount = rows.length - offered.length;

  return (
    <div className="form-fields">
      <label>
        <span>Pick from the {label} registry (optional)</span>
        {allRows.length > 5 && (
          <div className="flex flex-wrap items-center gap-2 pb-1">
            <Input className="w-auto" placeholder="Tag" value={tagFilter} onChange={(e) => setTagFilter(e.target.value)} />
            <Input className="w-auto" placeholder="Domain" value={domainFilter} onChange={(e) => setDomainFilter(e.target.value)} />
            <Input className="w-auto" placeholder="Source" value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)} />
            <Select className="w-auto" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">Any status</option>
              <option value="draft">draft</option>
              <option value="valid">valid</option>
              <option value="invalid">invalid</option>
            </Select>
          </div>
        )}
        {!rowsLoading && offered.length === 0 ? (
          <p className="editor-hint">
            No enabled {label.toLowerCase()} registry objects match{" "}
            {hasActiveFilter ? "these filters" : disabledCount ? "— enable one in Onboarding first" : "— nothing onboarded yet"}.
            {onNavigate && (
              <>
                {" "}
                <Button type="button" variant="ghost" size="sm" onClick={() => onNavigate("delivery.onboarding")}>
                  Go to Onboarding
                </Button>
              </>
            )}
          </p>
        ) : (
          <Select
            value=""
            onChange={(e) => {
              const obj = offered.find((r) => r.objectKey === e.target.value);
              if (obj) onPick(obj);
            }}
          >
            <option value="">— Enter manually below instead —</option>
            {offered.map((r) => (
              <option key={r.objectKey} value={r.objectKey}>
                {r.curatedEntity}
                {r.tag ? ` (${r.tag})` : ""}
              </option>
            ))}
          </Select>
        )}
        {disabledCount > 0 && offered.length > 0 && (
          <small>
            {disabledCount} disabled object{disabledCount === 1 ? "" : "s"} hidden — enable in Onboarding to build from
            {disabledCount === 1 ? " it" : " them"}.
          </small>
        )}
      </label>
    </div>
  );
}
