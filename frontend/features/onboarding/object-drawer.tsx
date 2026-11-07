"use client";

/**
 * Single-row editor for the Onboarding registry (§26.1's default-view +
 * progressive-disclosure split): the 6 default columns are always visible,
 * everything else sits behind "Show all columns" — same affordance as
 * `connection-editors.tsx`'s `SourceObjectsEditor`.
 */
import { useState } from "react";
import { Save, X } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { DATA_CLASSIFICATIONS, validateOnboardingRow } from "@/lib/adf/onboarding";
import type { OnboardingObjectEntry } from "@/types";

interface ObjectDrawerProps {
  initial: OnboardingObjectEntry;
  otherObjectKeys: string[];
  onCancel: () => void;
  onSave: (entry: OnboardingObjectEntry) => void;
}

function field<K extends keyof OnboardingObjectEntry>(
  draft: OnboardingObjectEntry,
  patch: (part: Partial<OnboardingObjectEntry>) => void,
  key: K,
) {
  return {
    value: (draft[key] as string | undefined) ?? "",
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
      patch({ [key]: e.target.value || undefined } as Partial<OnboardingObjectEntry>),
  };
}

export function ObjectDrawer({ initial, otherObjectKeys, onCancel, onSave }: ObjectDrawerProps) {
  const [draft, setDraft] = useState<OnboardingObjectEntry>(initial);
  const [showAll, setShowAll] = useState(false);
  const [showErrors, setShowErrors] = useState(false);

  function patch(part: Partial<OnboardingObjectEntry>) {
    setDraft((prev) => {
      const next = { ...prev, ...part };
      // object_key auto-derives from source_system.source_object until the
      // user has typed one explicitly (tracked implicitly: once it stops
      // matching the auto-derived value we leave it alone).
      const autoKey = [prev.source_system, prev.source_object].filter(Boolean).join(".");
      if (!prev.object_key || prev.object_key === autoKey) {
        next.object_key = [next.source_system, next.source_object].filter(Boolean).join(".");
      }
      return next;
    });
  }

  const issues = validateOnboardingRow(draft, "object");
  const duplicate = otherObjectKeys.includes(draft.object_key);
  const errors = [...issues.map((i) => i.message), ...(duplicate ? [`object_key "${draft.object_key}" is already in the registry.`] : [])];

  function handleSave() {
    if (errors.length) {
      setShowErrors(true);
      return;
    }
    onSave(draft);
  }

  return (
    <Dialog
      open
      onClose={onCancel}
      eyebrow="ONBOARDING · OBJECT"
      title={initial.source_object ? `Edit ${initial.object_key}` : "Add object"}
      description="One row = one source object mapped to one target, with its file format and load behaviour."
      wide
    >
      {showErrors && errors.length ? (
        <p className="mb-3 rounded-md border border-red-bg bg-red-bg px-3 py-2 text-xs text-red">{errors[0]}</p>
      ) : null}

      <div className="form-card">
        <h3 className="form-card-title">Source</h3>
        <div className="form-fields">
          <label>
            <span>Source system *</span>
            <Input {...field(draft, patch, "source_system")} placeholder="e.g. erp" />
          </label>
          <label>
            <span>Source type *</span>
            <Select value={draft.source_type} onChange={(e) => patch({ source_type: e.target.value as OnboardingObjectEntry["source_type"] })}>
              <option value="table">table</option>
              <option value="view">view</option>
              <option value="query">query</option>
              <option value="file">file</option>
            </Select>
          </label>
          <label>
            <span>Source object *</span>
            <Input {...field(draft, patch, "source_object")} placeholder="e.g. customer or exports/invoices_*.csv" />
          </label>
          {showAll ? (
            <>
              <label>
                <span>Source schema</span>
                <Input {...field(draft, patch, "source_schema")} placeholder="e.g. dbo" />
              </label>
              <label className="col-span-2">
                <span>Source query override</span>
                <Input {...field(draft, patch, "source_query")} placeholder="Optional SELECT override" />
              </label>
            </>
          ) : null}
        </div>
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Target</h3>
        <div className="form-fields">
          <label>
            <span>Target type *</span>
            <Select value={draft.target_type} onChange={(e) => patch({ target_type: e.target.value as OnboardingObjectEntry["target_type"] })}>
              <option value="table">table</option>
              <option value="file">file</option>
            </Select>
          </label>
          <label>
            <span>Target file format *</span>
            <Input {...field(draft, patch, "target_file_format")} placeholder="Parquet" />
          </label>
          {draft.target_type === "table" ? (
            <>
              <label>
                <span>Target schema</span>
                <Input {...field(draft, patch, "target_schema")} placeholder="e.g. curated" />
              </label>
              <label>
                <span>Target object</span>
                <Input {...field(draft, patch, "target_object")} placeholder="e.g. customer" />
              </label>
            </>
          ) : (
            <>
              <label>
                <span>Target path</span>
                <Input {...field(draft, patch, "target_path")} placeholder="e.g. curated/customer/" />
              </label>
              <label>
                <span>Target file name</span>
                <Input {...field(draft, patch, "target_file_name")} placeholder="Optional" />
              </label>
            </>
          )}
          {showAll ? (
            <>
              <label>
                <span>Write behavior</span>
                <Select value={draft.write_behavior ?? ""} onChange={(e) => patch({ write_behavior: (e.target.value || undefined) as OnboardingObjectEntry["write_behavior"] })}>
                  <option value="">Default (append)</option>
                  <option value="append">append</option>
                  <option value="overwrite">overwrite</option>
                  <option value="upsert">upsert</option>
                  <option value="merge">merge</option>
                </Select>
              </label>
              {draft.write_behavior === "upsert" || draft.write_behavior === "merge" ? (
                <label>
                  <span>Upsert keys *</span>
                  <Input {...field(draft, patch, "upsert_keys")} placeholder="id|updated_at" />
                </label>
              ) : null}
            </>
          ) : null}
        </div>
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Load behaviour</h3>
        <div className="form-fields">
          <label>
            <span>Loading behavior *</span>
            <Select value={draft.loading_behavior} onChange={(e) => patch({ loading_behavior: e.target.value as OnboardingObjectEntry["loading_behavior"] })}>
              <option value="FullLoad">FullLoad</option>
              <option value="DeltaLoad">DeltaLoad</option>
            </Select>
          </label>
          {draft.loading_behavior === "DeltaLoad" ? (
            <>
              <label>
                <span>Watermark column *</span>
                <Input {...field(draft, patch, "watermark_column")} placeholder="e.g. modified_date" />
              </label>
              <label>
                <span>Watermark column type *</span>
                <Select value={draft.watermark_column_type ?? ""} onChange={(e) => patch({ watermark_column_type: (e.target.value || undefined) as OnboardingObjectEntry["watermark_column_type"] })}>
                  <option value="">—</option>
                  <option value="datetime">datetime</option>
                  <option value="int">int</option>
                  <option value="bigint">bigint</option>
                  <option value="string">string</option>
                </Select>
              </label>
            </>
          ) : null}
        </div>
      </div>

      {showAll ? (
        <div className="form-card">
          <h3 className="form-card-title">Grouping &amp; notes</h3>
          <div className="form-fields">
            <label>
              <span>Batch group</span>
              <Input {...field(draft, patch, "batch_group")} placeholder="One pipeline per group" />
            </label>
            <label>
              <span>Owner</span>
              <Input {...field(draft, patch, "owner")} />
            </label>
            <label>
              <span>Data classification</span>
              <Select
                value={draft.data_classification ?? ""}
                onChange={(e) =>
                  patch({ data_classification: (e.target.value || undefined) as OnboardingObjectEntry["data_classification"] })
                }
              >
                <option value="">—</option>
                {DATA_CLASSIFICATIONS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </label>
            <label>
              <span>Enabled</span>
              <Select value={draft.enabled ? "true" : "false"} onChange={(e) => patch({ enabled: e.target.value === "true" })}>
                <option value="true">Enabled</option>
                <option value="false">Disabled</option>
              </Select>
            </label>
            <label className="col-span-2">
              <span>Notes</span>
              <Textarea rows={2} {...field(draft, patch, "notes")} />
            </label>
          </div>
        </div>
      ) : null}

      <div className="mb-4 flex items-center justify-between">
        <button type="button" className="editor-columns-toggle" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show fewer columns" : "Show all columns"}
        </button>
        <span className="text-xs text-muted">object_key: {draft.object_key || "—"}</span>
      </div>

      <div className="wizard-actions">
        <Button variant="ghost" onClick={onCancel}>
          <X className="h-3.5 w-3.5" />
          Cancel
        </Button>
        <Button onClick={handleSave}>
          <Save className="h-3.5 w-3.5" />
          Save object
        </Button>
      </div>
    </Dialog>
  );
}

