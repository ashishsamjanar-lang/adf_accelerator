"use client";

/**
 * Shared Silver/Gold transformation + data-quality rule editors (§16, §30.4).
 * Extracted 2026-09-14 (§33 STTM work) from `features/build/create-pipeline-
 * wizard.tsx`, which owned the only copy — the new §33 registry object
 * drawer (`features/onboarding/registry-object-drawer.tsx`) needs the exact
 * same editing UI for a hand-authored Silver/Gold registry object, and
 * duplicating ~200 lines of row-editing logic per DEVELOPMENT_NOTES.md's "reuse
 * components/lib helpers before adding new ones" rule would have been the
 * wrong call. Behavior is unchanged from the wizard's original inline copy.
 */
import { Fragment } from "react";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import {
  MAX_TRANSFORMATION_RULES,
  MEDALLION_LAYER_INFO,
  type MedallionLayer,
  type TransformationRule,
  type TransformationType,
} from "@/lib/adf/medallion";
import { DQ_PCT_RULE_TYPES } from "@/lib/adf/spec";

export const DATA_QUALITY_RULES: string[] = ["IS_NOT_NULL", "UNIQUE", "LENGTH_LE", "RANGE_BETWEEN", "REGEX_MATCH", "ROW_COUNT_MIN", "FRESHNESS_HOURS"];
export const MAX_DATA_QUALITY_RULES = 5; // dataQualityRules never chained — cap unaffected by §33.3's widening

/** Placeholder text for the Value column — only LENGTH_LE/RANGE_BETWEEN/REGEX_MATCH use it (§30.4). */
export const DATA_QUALITY_VALUE_HINT: Record<string, string> = {
  IS_NOT_NULL: "(not used)",
  UNIQUE: "(not used)",
  LENGTH_LE: "e.g. 100",
  RANGE_BETWEEN: "e.g. 0,100",
  REGEX_MATCH: "e.g. ^[A-Z]{3}%",
  ROW_COUNT_MIN: "min rows, e.g. 1",
  FRESHNESS_HOURS: "max age (h), e.g. 24",
};
export const DATA_QUALITY_ENFORCEMENTS: Array<{ value: "warn" | "block"; label: string }> = [
  { value: "warn", label: "Warn" },
  { value: "block", label: "Block" },
];

export function mapValue<T>(list: T[], index: number, patch: Partial<T>): T[] {
  return list.map((item, i) => (i === index ? { ...item, ...patch } : item));
}

export function removeAt<T>(list: T[], index: number): T[] {
  return list.filter((_, i) => i !== index);
}

export interface DataQualityRuleRow {
  name: string;
  column: string;
  rule: string;
  value?: string;
  enforcement?: "warn" | "block";
  maxViolationPct?: string;
}

export function DataQualityEditor({ rows, onChange }: { rows: DataQualityRuleRow[]; onChange: (next: DataQualityRuleRow[]) => void }) {
  return (
    <div className="editor-block">
      <div className="editor-head">
        <strong>Data quality rules</strong>
        <small>{rows.length}/{MAX_DATA_QUALITY_RULES}</small>
      </div>
      <p className="editor-hint">
        Generated as pipeline activities that check the data as it loads. &quot;Warn&quot; logs the
        violation count in ADF run history; &quot;Block&quot; fails the run.
      </p>
      {rows.length === 0 ? (
        <p className="editor-empty">No data quality rules configured.</p>
      ) : (
        <div className="editor-rows">
          {rows.map((rule, i) => (
            <Fragment key={i}>
            <div className="editor-row five">
              <Input
                value={rule.name}
                placeholder="e.g. no_nulls"
                onChange={(e) => onChange(mapValue(rows, i, { name: e.target.value }))}
              />
              <Input
                value={rule.column}
                placeholder="e.g. customer_id"
                onChange={(e) => onChange(mapValue(rows, i, { column: e.target.value }))}
              />
              <Select value={rule.rule} onChange={(e) => onChange(mapValue(rows, i, { rule: e.target.value }))}>
                {DATA_QUALITY_RULES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </Select>
              <Input
                value={rule.value ?? ""}
                placeholder={DATA_QUALITY_VALUE_HINT[rule.rule] ?? ""}
                onChange={(e) => onChange(mapValue(rows, i, { value: e.target.value }))}
              />
              <Select
                value={rule.enforcement ?? "warn"}
                onChange={(e) => onChange(mapValue(rows, i, { enforcement: e.target.value as "warn" | "block" }))}
              >
                {DATA_QUALITY_ENFORCEMENTS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </Select>
              <button type="button" className="editor-remove" title="Remove rule" onClick={() => onChange(removeAt(rows, i))}>
                ×
              </button>
            </div>
            {DQ_PCT_RULE_TYPES.has(rule.rule) && (
              <Input
                aria-label={`Max violation % for ${rule.name || `rule ${i + 1}`}`}
                value={rule.maxViolationPct ?? ""}
                placeholder="Max violating rows, % (optional — blank fails on any violating row)"
                onChange={(e) => onChange(mapValue(rows, i, { maxViolationPct: e.target.value }))}
              />
            )}
            </Fragment>
          ))}
        </div>
      )}
      <button
        type="button"
        className="editor-add"
        disabled={rows.length >= MAX_DATA_QUALITY_RULES}
        onClick={() => onChange([...rows, { name: "", column: "", rule: "IS_NOT_NULL", value: "", enforcement: "warn" }])}
      >
        + Add rule
      </button>
    </div>
  );
}

export const TRANSFORM_TYPE_LABELS: Record<TransformationType, string> = {
  conform_columns: "Conform columns",
  dedupe: "Dedupe",
  cast_schema: "Cast schema",
  scd2: "SCD2 history",
  join: "Join",
  aggregate: "Aggregate",
  select_columns: "Select columns",
  filter: "Filter rows",
};

/** Which of a `TransformationRule`'s flat fields are relevant per type, for a dynamic mini-form per row. */
export const TRANSFORM_FIELD_CONFIG: Record<
  TransformationType,
  Array<{ key: keyof TransformationRule; label: string; placeholder: string }>
> = {
  conform_columns: [{ key: "columns", label: "Column mapping", placeholder: "src_col:dst_col, src_col2:dst_col2" }],
  dedupe: [
    { key: "keyColumns", label: "Key column(s)", placeholder: "customer_id" },
    { key: "orderColumn", label: "Order column (latest wins)", placeholder: "modified_date" },
  ],
  cast_schema: [{ key: "castExpression", label: "Casts", placeholder: "amount:DECIMAL(18,2), posted_date:DATETIME2" }],
  scd2: [
    { key: "keyColumns", label: "Natural key column(s)", placeholder: "customer_id" },
    { key: "orderColumn", label: "Change-detection column", placeholder: "modified_date" },
    { key: "columns", label: "Tracked columns", placeholder: "name, segment, address" },
    { key: "currentFlagColumn", label: "Current-flag column (optional)", placeholder: "_is_current" },
    { key: "validFromColumn", label: "Valid-from column (optional)", placeholder: "_valid_from" },
    { key: "validToColumn", label: "Valid-to column (optional)", placeholder: "_valid_to" },
  ],
  join: [
    { key: "joinEntity", label: "Join entity/table", placeholder: "curated.customer" },
    { key: "joinType", label: "Join type (INNER/LEFT)", placeholder: "INNER" },
    { key: "joinCondition", label: "Join condition", placeholder: "a.customer_id = b.customer_id" },
    { key: "columns", label: "Output columns", placeholder: "a.customer_id, a.amount, b.segment" },
  ],
  aggregate: [
    { key: "groupByColumns", label: "Group by column(s)", placeholder: "customer_id, posted_month" },
    { key: "aggregateExpression", label: "Aggregate expression(s)", placeholder: "SUM(amount) AS total_amount" },
  ],
  select_columns: [{ key: "columns", label: "Final column list", placeholder: "customer_id, total_amount" }],
  filter: [{ key: "filterCondition", label: "Filter condition (T-SQL WHERE)", placeholder: "status <> 'X'" }],
};

/** Silver/Gold transformation-rule editor — up to `MAX_TRANSFORMATION_RULES` rows (§33.3). */
export function TransformationsEditor({
  layer,
  rows,
  onChange,
}: {
  layer: MedallionLayer;
  rows: TransformationRule[];
  onChange: (next: TransformationRule[]) => void;
}) {
  const types = MEDALLION_LAYER_INFO[layer].transformTypes;
  if (layer === "bronze" || types.length === 0) return null;
  return (
    <div className="editor-block">
      <div className="editor-head">
        <strong>{layer === "silver" ? "Silver" : "Gold"} transformations</strong>
        <small>{rows.length}/{MAX_TRANSFORMATION_RULES}</small>
      </div>
      <p className="editor-hint">
        {layer === "silver"
          ? "Generated as pipeline activities that run after the copy, before the data lands in the curated table — conform column names, dedupe, cast types, join in another entity, or maintain SCD2 history."
          : "Generated as pipeline activities that run after the copy, before the data lands in the mart table — join in another curated entity, aggregate, project the final mart columns, or maintain SCD2 history."}
      </p>
      {rows.length === 0 ? (
        <p className="editor-empty">No transformations configured yet — at least one is required to generate this layer's pipeline.</p>
      ) : (
        <div className="editor-rows">
          {rows.map((rule, i) => (
            <div className="transform-row" key={i}>
              <div className="transform-row-head">
                <Input
                  value={rule.name}
                  placeholder="e.g. conform_gl_columns"
                  onChange={(e) => onChange(mapValue(rows, i, { name: e.target.value }))}
                />
                <Select value={rule.type} onChange={(e) => onChange(mapValue(rows, i, { type: e.target.value as TransformationType }))}>
                  {types.map((t) => (
                    <option key={t} value={t}>
                      {TRANSFORM_TYPE_LABELS[t]}
                    </option>
                  ))}
                </Select>
                <button type="button" className="editor-remove" title="Remove transformation" onClick={() => onChange(removeAt(rows, i))}>
                  ×
                </button>
              </div>
              <div className="transform-row-fields">
                {TRANSFORM_FIELD_CONFIG[rule.type].map((f) => (
                  <label key={f.key}>
                    <span>{f.label}</span>
                    <Input
                      value={(rule[f.key] as string) ?? ""}
                      placeholder={f.placeholder}
                      onChange={(e) => onChange(mapValue(rows, i, { [f.key]: e.target.value } as Partial<TransformationRule>))}
                    />
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        className="editor-add"
        disabled={rows.length >= MAX_TRANSFORMATION_RULES}
        onClick={() => onChange([...rows, { name: "", type: types[0] }])}
      >
        + Add transformation
      </button>
    </div>
  );
}
