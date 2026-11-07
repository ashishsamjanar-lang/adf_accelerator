"use client";

/**
 * Single-object manual editor for the §33 Silver/Gold registry — the
 * "create/edit a Silver or Gold object one at a time" DoD item (§33.12 #3).
 * Mirrors `object-drawer.tsx`'s (bronze, §26) structure and reuses the exact
 * same transformation/DQ-rule editing UI the Build wizard uses
 * (`components/rules/rule-editors.tsx`, §33.2 — no new engine, no new form).
 */
import { useState } from "react";
import { Save, X } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { DataQualityEditor, TransformationsEditor } from "@/components/rules/rule-editors";
import { layerTargetTableName, type LayerPrefixOverrides, type MedallionLayer, type TransformationRule } from "@/lib/adf/medallion";
import { validateDataQualityRules } from "@/lib/adf/spec";
import { validateSttmObjectHeader } from "@/lib/adf/sttm";
import type { RegistryObject, RegistryObjectInput, SttmDataQualityRule, SttmLayer } from "@/types";

interface RegistryObjectDrawerProps {
  layer: SttmLayer;
  initial: RegistryObject | null; // null = create
  /** Project's Standards-configured custom zone prefix per layer, for the live "Target table"
   *  preview below (§25.3). The backend recomputes `target_table` itself on save regardless. */
  layerPrefixes?: LayerPrefixOverrides;
  onCancel: () => void;
  /** Parent owns the actual API call (create vs. update, expectedVersion) — returns an error
   *  message string on failure, or null on success (drawer closes itself only on success). */
  onSave: (input: RegistryObjectInput) => Promise<string | null>;
}

function medallionLayerOf(layer: SttmLayer): MedallionLayer {
  return layer === "mart" ? "gold" : "silver";
}

function emptyDraft(layer: SttmLayer): RegistryObjectInput {
  return {
    enabled: true,
    tag: "",
    curatedEntity: "",
    martDomain: layer === "mart" ? "" : undefined,
    sourceObjectKeys: [],
    writeBehavior: "append",
    loadingBehavior: "FullLoad",
    businessKeys: [],
    transformations: [],
    dataQualityRules: [],
    notes: "",
  };
}

function fromRegistryObject(obj: RegistryObject): RegistryObjectInput {
  return {
    objectKey: obj.objectKey,
    enabled: obj.enabled,
    tag: obj.tag,
    curatedEntity: obj.curatedEntity,
    martDomain: obj.martDomain ?? undefined,
    sourceObjectKeys: obj.sourceObjectKeys,
    owner: obj.owner ?? undefined,
    batchGroup: obj.batchGroup ?? undefined,
    domain: obj.domain ?? undefined,
    writeBehavior: obj.writeBehavior,
    loadingBehavior: obj.loadingBehavior,
    watermarkColumn: obj.watermarkColumn ?? undefined,
    watermarkColumnType: obj.watermarkColumnType ?? undefined,
    businessKeys: obj.businessKeys,
    transformations: obj.transformations,
    dataQualityRules: obj.dataQualityRules,
    dataClassification: obj.dataClassification ?? undefined,
    notes: obj.notes,
  };
}

export function RegistryObjectDrawer({ layer, initial, layerPrefixes, onCancel, onSave }: RegistryObjectDrawerProps) {
  const [draft, setDraft] = useState<RegistryObjectInput>(initial ? fromRegistryObject(initial) : emptyDraft(layer));
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);

  function patch(part: Partial<RegistryObjectInput>) {
    setDraft((prev) => ({ ...prev, ...part }));
  }

  // Client-side check is header + data-quality-rule shape only — full transformation-rule
  // validation (required fields per type, allowed-type-per-layer) happens server-side on save,
  // same "backend wins on disagreement" posture as the STTM import path (§33.9); the editor
  // itself already constrains which types are selectable per layer.
  const medallionLayer = medallionLayerOf(layer);
  const headerIssues = validateSttmObjectHeader(layer, draft);
  const dqIssues = validateDataQualityRules((draft.dataQualityRules ?? []) as SttmDataQualityRule[]);
  const errors = [...headerIssues, ...dqIssues].filter((i) => i.severity === "error");

  const targetTable = draft.curatedEntity
    ? layerTargetTableName(medallionLayer, { entity: draft.curatedEntity, domain: draft.martDomain ?? undefined }, layerPrefixes)
    : "—";

  async function handleSave() {
    if (errors.length) {
      setShowErrors(true);
      return;
    }
    setSaving(true);
    setServerError(null);
    const error = await onSave(draft);
    setSaving(false);
    if (error) setServerError(error);
  }

  return (
    <Dialog
      open
      onClose={onCancel}
      eyebrow={`${layer === "mart" ? "GOLD" : "SILVER"} REGISTRY · OBJECT`}
      title={initial ? `Edit ${initial.objectKey}` : `Add ${layer === "mart" ? "mart" : "curated"} object`}
      description={`Target table: ${targetTable} — computed from ${layer === "mart" ? "mart_domain + curated_entity" : "curated_entity"}, never freely typed.`}
      wide
    >
      {showErrors && errors.length ? (
        <p className="mb-3 rounded-md border border-red-bg bg-red-bg px-3 py-2 text-xs text-red">{errors[0].message}</p>
      ) : null}
      {serverError ? (
        <p className="mb-3 rounded-md border border-red-bg bg-red-bg px-3 py-2 text-xs text-red">{serverError}</p>
      ) : null}

      <div className="form-card">
        <h3 className="form-card-title">Identity</h3>
        <div className="form-fields">
          <label>
            <span>Tag * (mandatory classification)</span>
            <Input value={draft.tag ?? ""} onChange={(e) => patch({ tag: e.target.value })} placeholder="e.g. finance" />
          </label>
          <label>
            <span>Curated entity *</span>
            <Input value={draft.curatedEntity ?? ""} onChange={(e) => patch({ curatedEntity: e.target.value })} placeholder="e.g. gl_transaction" />
          </label>
          {layer === "mart" ? (
            <label>
              <span>Mart domain *</span>
              <Input value={draft.martDomain ?? ""} onChange={(e) => patch({ martDomain: e.target.value })} placeholder="e.g. finance" />
            </label>
          ) : null}
          <label className="col-span-2">
            <span>Source object keys * (pipe-separated {layer === "mart" ? "Silver curated entities" : "bronze onboarding object keys"})</span>
            <Input
              value={(draft.sourceObjectKeys ?? []).join("|")}
              onChange={(e) => patch({ sourceObjectKeys: e.target.value.split("|").map((s) => s.trim()).filter(Boolean) })}
              placeholder={layer === "mart" ? "gl_transaction|customer" : "erp.gl_transaction"}
            />
          </label>
          <label>
            <span>Domain (free-text classification)</span>
            <Input value={draft.domain ?? ""} onChange={(e) => patch({ domain: e.target.value })} />
          </label>
          <label>
            <span>Owner</span>
            <Input value={draft.owner ?? ""} onChange={(e) => patch({ owner: e.target.value })} placeholder="data-eng" />
          </label>
          <label>
            <span>Batch group</span>
            <Input value={draft.batchGroup ?? ""} onChange={(e) => patch({ batchGroup: e.target.value })} />
          </label>
          <div className="flex items-end pb-1">
            <Checkbox checked={draft.enabled ?? true} onChange={(next) => patch({ enabled: next })} label="Enabled" />
          </div>
        </div>
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Load behaviour</h3>
        <div className="form-fields">
          <label>
            <span>Write behavior</span>
            <Select value={draft.writeBehavior ?? "append"} onChange={(e) => patch({ writeBehavior: e.target.value as RegistryObjectInput["writeBehavior"] })}>
              <option value="append">append</option>
              <option value="overwrite">overwrite</option>
              <option value="upsert">upsert</option>
              <option value="merge">merge</option>
            </Select>
          </label>
          {draft.writeBehavior === "upsert" || draft.writeBehavior === "merge" ? (
            <label>
              <span>Business keys * (comma-separated)</span>
              <Input
                value={(draft.businessKeys ?? []).join(",")}
                onChange={(e) => patch({ businessKeys: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
              />
            </label>
          ) : null}
          <label>
            <span>Loading behavior</span>
            <Select value={draft.loadingBehavior ?? "FullLoad"} onChange={(e) => patch({ loadingBehavior: e.target.value as RegistryObjectInput["loadingBehavior"] })}>
              <option value="FullLoad">FullLoad</option>
              <option value="DeltaLoad">DeltaLoad</option>
            </Select>
          </label>
          {draft.loadingBehavior === "DeltaLoad" ? (
            <>
              <label>
                <span>Watermark column *</span>
                <Input value={draft.watermarkColumn ?? ""} onChange={(e) => patch({ watermarkColumn: e.target.value })} />
              </label>
              <label>
                <span>Watermark column type *</span>
                <Select value={draft.watermarkColumnType ?? ""} onChange={(e) => patch({ watermarkColumnType: e.target.value })}>
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

      <div className="form-card">
        <h3 className="form-card-title">Transformations</h3>
        <TransformationsEditor
          layer={medallionLayer}
          rows={(draft.transformations ?? []) as TransformationRule[]}
          onChange={(next) => patch({ transformations: next })}
        />
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Data quality</h3>
        <DataQualityEditor
          rows={(draft.dataQualityRules ?? []) as SttmDataQualityRule[]}
          onChange={(next) => patch({ dataQualityRules: next as SttmDataQualityRule[] })}
        />
      </div>

      <div className="form-card">
        <h3 className="form-card-title">Governance</h3>
        <div className="form-fields">
          <label>
            <span>Data classification</span>
            <Select value={draft.dataClassification ?? ""} onChange={(e) => patch({ dataClassification: (e.target.value || undefined) as RegistryObjectInput["dataClassification"] })}>
              <option value="">—</option>
              <option value="public">public</option>
              <option value="internal">internal</option>
              <option value="confidential">confidential</option>
              <option value="restricted">restricted</option>
            </Select>
          </label>
          <label className="col-span-2">
            <span>Notes</span>
            <Textarea rows={2} value={draft.notes ?? ""} onChange={(e) => patch({ notes: e.target.value })} />
          </label>
        </div>
      </div>

      <div className="wizard-actions">
        <Button variant="ghost" onClick={onCancel} disabled={saving}>
          <X className="h-3.5 w-3.5" />
          Cancel
        </Button>
        <Button onClick={handleSave} disabled={saving}>
          <Save className="h-3.5 w-3.5" />
          {saving ? "Saving…" : "Save object"}
        </Button>
      </div>
    </Dialog>
  );
}
