/**
 * Medallion (Bronze/Silver/Gold) layer support for Build (user request:
 * "refer sample project 2's bronze/silver/gold layers ... Silver and Gold
 * need transformations ... layer-wise table naming standards").
 *
 * Deliberately named `MedallionLayer` — not `Layer`/`ArtifactLayer` — to
 * avoid colliding with the unrelated §23.9 ADF-folder-deployment-order
 * concept (`types/core.ts` `ArtifactLayer`: "pipeline" | "metadata" |
 * "runtime" | ...) that `jsonArtifact`/`textArtifact` already use.
 *
 * Scope, matching the rest of this generator's established convention (see
 * `dataQualityActivities`/`dataQualityCheckQuery` in pipeline-codegen.ts):
 * everything here is **illustrative T-SQL**, generated as text into ADF
 * `Script` activities. This app never executes it — ADF does, at run time
 * (§30.3 scope boundary) — so the SQL favors clarity/readability over being
 * a fully schema-aware, dialect-correct implementation.
 *
 * - **Bronze** = today's existing behavior, completely unchanged: a plain
 *   copy from the source object into the configured target. No
 *   transformations, no new target-table naming is applied to the actual
 *   copy sink.
 * - **Silver** = the existing copy runs as before, then an appended
 *   transformation chain (conform columns / dedupe / cast types / SCD2)
 *   writes a *separate*, layer-standardized `curated.<entity>` table.
 * - **Gold** = same shape as Silver, but with join/aggregate/project
 *   transformations, writing a layer-standardized `mart.<domain>_<entity>`
 *   table.
 *
 * Layer-wise table naming standard (extends the pattern already documented
 * in `frontend/public/sample-guides/multi-source-etl-guide.html`'s "Target
 * table & file naming standardization" section): the table name is
 * *computed* from the entity/domain tokens, never freely typed — naming
 * compliance is enforced by construction instead of by a separate,
 * skippable validation rule.
 */
import type { AuditColumnOverrides, ValidationIssue } from "@/types";

export type MedallionLayer = "bronze" | "silver" | "gold";

export const MEDALLION_LAYERS: MedallionLayer[] = ["bronze", "silver", "gold"];

/** Transformation types Silver authors on the conformed entity. */
export type SilverTransformType = "conform_columns" | "dedupe" | "cast_schema" | "scd2" | "filter";
/** Transformation types Gold authors on the business mart. */
export type GoldTransformType = "join" | "aggregate" | "select_columns";
export type TransformationType = SilverTransformType | GoldTransformType;

/**
 * One transformation step. Fields are all flat strings (mirrors
 * `dataQualityRules`'s shape) so the same row type works for every
 * transformation type; only the fields a given `type` actually reads are
 * required (see `validateTransformationRules`).
 */
export interface TransformationRule {
  name: string;
  type: TransformationType;
  /** conform_columns / select_columns: "src_col:dst_col, src_col2:dst_col2" (":dst" optional). */
  columns?: string;
  /** dedupe / scd2: comma-separated natural-key column list. */
  keyColumns?: string;
  /** dedupe / scd2: column used to pick the "latest" row per key (e.g. modified_date). */
  orderColumn?: string;
  /** cast_schema: "column:SQL_TYPE, column2:SQL_TYPE2". */
  castExpression?: string;
  /** join: the other curated entity/table being joined in. */
  joinEntity?: string;
  joinType?: "INNER" | "LEFT";
  /** join: raw ON condition, e.g. "a.customer_id = b.customer_id". */
  joinCondition?: string;
  /** aggregate: comma-separated GROUP BY column list. */
  groupByColumns?: string;
  /** aggregate: comma-separated aggregate expressions, e.g. "SUM(amount) AS total_amount". */
  aggregateExpression?: string;
  /** filter (DMT-03): T-SQL WHERE predicate on the step's input rows, e.g. "status <> 'X'". */
  filterCondition?: string;
  /** scd2 (DMT-09): history column names; default `_is_current`/`_valid_from`/`_valid_to`. */
  currentFlagColumn?: string;
  validFromColumn?: string;
  validToColumn?: string;
}

/** Transformation types that write straight to the final layer target table
 *  instead of an intermediate staging table (SCD2 merges into the real table,
 *  and its close-then-insert-missing shape is already rerun-safe). Every other
 *  step stages, so the idempotent `LoadToLayerTarget` (E2E-08) always follows. */
export const TERMINAL_TRANSFORM_TYPES: Set<TransformationType> = new Set(["scd2"]);

/**
 * §33.3 widening: Silver additionally allows `join` (combining two or more
 * Bronze sources into one curated entity) and Gold additionally allows
 * `scd2` (mart/dimension tables commonly need history tracking too) — a
 * deliberate, small loosening of the previous exclusive per-layer split, made
 * so real STTM authoring (bulk-onboarded Silver/Gold transformations) isn't
 * blocked on a combination the wizard-only version never needed. Every other
 * type stays exactly where it was; `transformationRuleSql`'s chaining engine
 * is already keyed by `type`, never `layer`, so this touches only the
 * allow-lists below.
 */
export const SILVER_TRANSFORM_TYPES: TransformationType[] = ["conform_columns", "dedupe", "cast_schema", "scd2", "join", "filter"];
export const GOLD_TRANSFORM_TYPES: TransformationType[] = ["join", "aggregate", "select_columns", "scd2", "filter", "dedupe"];

export const MEDALLION_LAYER_INFO: Record<
  MedallionLayer,
  { label: string; description: string; zonePrefix: string; transformTypes: TransformationType[]; targetPatternHint: string }
> = {
  bronze: {
    label: "Bronze — raw ingestion",
    description: "Land the source object as-is. Same behavior as every existing pipeline — no transformations.",
    zonePrefix: "raw",
    transformTypes: [],
    targetPatternHint: "raw.<source>_<object>",
  },
  silver: {
    label: "Silver — conformed / curated",
    description:
      "Clean and conform the landed data into one shared curated entity (standard columns, dedupe, type casts, optional SCD2 history).",
    zonePrefix: "curated",
    transformTypes: SILVER_TRANSFORM_TYPES,
    targetPatternHint: "curated.<entity>",
  },
  gold: {
    label: "Gold — business marts",
    description: "Join and aggregate curated entities into a business-facing mart table.",
    zonePrefix: "mart",
    transformTypes: GOLD_TRANSFORM_TYPES,
    targetPatternHint: "mart.<domain>_<entity>",
  },
};

/** Layer-standardized folder-name suggestion (a Build-time hint only; the
 *  user-typed pipeline name is still what's actually validated by
 *  lib/adf/spec.ts's ADF naming-rule check). */
export const LAYER_PIPELINE_PREFIX: Record<MedallionLayer, string> = {
  bronze: "pl_raw_",
  silver: "pl_silver_",
  gold: "pl_gold_",
};

/**
 * Split on top-level commas only — commas nested inside parentheses (e.g. a
 * SQL type like `DECIMAL(18,2)` in a `cast_schema` rule) don't end the
 * current item. A plain `.split(",")` would otherwise mis-split exactly that
 * case.
 */
function splitTopLevel(value: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const ch of value) {
    if (ch === "(") depth++;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    if (ch === "," && depth === 0) {
      parts.push(current.trim());
      current = "";
    } else {
      current += ch;
    }
  }
  if (current.trim()) parts.push(current.trim());
  return parts.filter(Boolean);
}

/**
 * DMT-06: split a `src:dst` / `col:type` pair on its LAST top-level colon — one outside quotes and
 * parentheses and not part of a `::` cast — so an expression like `CASE WHEN t = '12:00' ...` survives.
 * Returns `[pair, ""]` when there is no such colon.
 */
export function splitMappingPair(pair: string): [string, string] {
  let depth = 0;
  let quote: string | null = null;
  let at = -1;
  for (let i = 0; i < pair.length; i++) {
    const ch = pair[i];
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === "'" || ch === '"') quote = ch;
    else if (ch === "(") depth++;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    else if (ch === ":" && depth === 0 && pair[i - 1] !== ":" && pair[i + 1] !== ":") at = i;
  }
  return at < 0 ? [pair.trim(), ""] : [pair.slice(0, at).trim(), pair.slice(at + 1).trim()];
}

function toSnake(value: string | undefined): string {
  return (
    (value ?? "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "") || "object"
  );
}

export interface LayerTargetNameInput {
  /** Bronze only — the source connector type, e.g. "sql_server". */
  sourceType?: string;
  /** Bronze only — the bare object/table name being landed. */
  object?: string;
  /** Silver + Gold — the conformed business entity name, e.g. "gl_transaction". */
  entity?: string;
  /** Gold only — the mart domain, e.g. "finance". */
  domain?: string;
}

/**
 * A project's custom zone prefix per layer (Standards page, `NamingConvention.
 * layerPrefixes` — AGENTS.md §25.3), overriding `MEDALLION_LAYER_INFO[layer].
 * zonePrefix`'s built-in raw/curated/mart default.
 */
export type LayerPrefixOverrides = Partial<Record<MedallionLayer, string>>;

/**
 * Resolves the zone prefix actually used for `layer`: the project's custom
 * prefix when one is configured and valid, else the built-in default. Same
 * sanitize-to-a-safe-SQL-identifier treatment as `toSnake` — a custom prefix
 * is compiled directly into generated T-SQL table names, so it can't be left
 * as arbitrary freeform text (e.g. containing whitespace or a stray `.`).
 */
export function resolveZonePrefix(layer: MedallionLayer, overrides?: LayerPrefixOverrides): string {
  const custom = (overrides?.[layer] ?? "").trim();
  if (!custom) return MEDALLION_LAYER_INFO[layer].zonePrefix;
  const cleaned = custom.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
  return cleaned || MEDALLION_LAYER_INFO[layer].zonePrefix;
}

/**
 * The layer-wise target table naming standard, generalized from the
 * multi-source-etl sample guide's "Target table & file naming
 * standardization" section (`curated.<entity>`, `mart.<domain>_<entity>`)
 * plus a symmetrical raw-zone pattern for Bronze. Always computed — there is
 * no freeform target-table-name field to drift from the pattern. `layerPrefixes`
 * is optional so every existing caller keeps today's raw/curated/mart default;
 * pass a project's Standards-configured overrides to make the zone itself
 * follow that project's own naming standard instead.
 */
export function layerTargetTableName(
  layer: MedallionLayer,
  input: LayerTargetNameInput,
  layerPrefixes?: LayerPrefixOverrides,
): string {
  const prefix = resolveZonePrefix(layer, layerPrefixes);
  if (layer === "bronze") {
    return `${prefix}.${toSnake(input.sourceType)}_${toSnake(input.object)}`;
  }
  if (layer === "silver") {
    return `${prefix}.${toSnake(input.entity)}`;
  }
  return `${prefix}.${toSnake(input.domain)}_${toSnake(input.entity)}`;
}

const ENTITY_TOKEN_RE = /^[a-z][a-z0-9_]{1,63}$/;

/** Validates the Silver/Gold naming inputs (§ layer-wise table naming standard). */
export function validateMedallionFields(
  layer: MedallionLayer,
  fields: { curatedEntity?: string; martDomain?: string },
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (layer === "bronze") return issues;
  const entity = (fields.curatedEntity ?? "").trim();
  if (!entity) {
    issues.push({
      severity: "error",
      message: `A curated entity name is required for the ${layer} layer (used to build "${MEDALLION_LAYER_INFO[layer].targetPatternHint}").`,
      field: "curatedEntity",
    });
  } else if (!ENTITY_TOKEN_RE.test(toSnake(entity))) {
    issues.push({
      severity: "error",
      message: `Curated entity name "${entity}" must be lowercase letters, digits, and underscores.`,
      field: "curatedEntity",
    });
  }
  if (layer === "gold") {
    const domain = (fields.martDomain ?? "").trim();
    if (!domain) {
      issues.push({
        severity: "error",
        message: `A mart domain is required for the Gold layer (used to build "${MEDALLION_LAYER_INFO.gold.targetPatternHint}").`,
        field: "martDomain",
      });
    } else if (!ENTITY_TOKEN_RE.test(toSnake(domain))) {
      issues.push({
        severity: "error",
        message: `Mart domain "${domain}" must be lowercase letters, digits, and underscores.`,
        field: "martDomain",
      });
    }
  }
  return issues;
}

/** §33.3: up to 10 transformations per object (raised from 5 — real STTM authoring needs more
 *  room than the wizard-only version did). Exported so UI editors (the Build wizard's
 *  `TransformationsEditor`, the registry object drawer) and `lib/adf/sttm.ts` all cap against the
 *  same number instead of a second hardcoded literal drifting from this one. */
export const MAX_TRANSFORMATION_RULES = 10;

/**
 * Gap #32 — standard audit columns per medallion layer, stamped on the Copy activity through the
 * existing `customMetadata` → `additionalColumns` wiring (no new generator mechanism). Every value is
 * an ADF system expression or the visibly-derived layer name — never a fabricated business value
 * (DEVELOPMENT_NOTES.md rule 5). SCD2/DQ columns (`_is_current`, `_dq_status`, ...) come from the layer's own
 * transformations, not from here.
 */
export function standardAuditColumns(
  layer: MedallionLayer,
  overrides?: AuditColumnOverrides,
): Array<{ name: string; value: string }> {
  // A project-configured list (Standards page) wins for its layer; blank rows are ignored and an
  // all-blank list falls back to the built-in one below.
  const custom = (overrides?.[layer] ?? []).filter((c) => c.name.trim());
  if (custom.length) return custom.map((c) => ({ name: c.name.trim(), value: c.value }));
  const common = [
    { name: "_pipeline_run_id", value: "@pipeline().RunId" },
    { name: "_pipeline_name", value: "@pipeline().Pipeline" },
    { name: "_ingestion_ts_utc", value: "@utcnow()" },
    { name: "_medallion_layer", value: layer },
  ];
  return layer === "bronze" ? [...common, { name: "_trigger_time", value: "@pipeline().TriggerTime" }] : common;
}

/** Same cap the spec validator enforces on `customMetadata`. */
export const MAX_CUSTOM_METADATA_COLUMNS = 10;

/** True when every standard audit column for `layer` is already present (by name) in `rows`. */
export function hasStandardAuditColumns(
  layer: MedallionLayer,
  rows: Array<{ name: string }>,
  overrides?: AuditColumnOverrides,
): boolean {
  const names = new Set(rows.map((r) => r.name));
  return standardAuditColumns(layer, overrides).every((c) => names.has(c.name));
}

/**
 * Apply (or remove) the standard audit columns. Applying replaces any stale layer's audit set, keeps
 * the user's own columns, and never exceeds MAX_CUSTOM_METADATA_COLUMNS (returns null if it would).
 */
export function withStandardAuditColumns(
  layer: MedallionLayer,
  rows: Array<{ name: string; value: string }>,
  apply: boolean,
  overrides?: AuditColumnOverrides,
): Array<{ name: string; value: string }> | null {
  const auditNames = new Set(
    MEDALLION_LAYERS.flatMap((l) => [...standardAuditColumns(l), ...standardAuditColumns(l, overrides)].map((c) => c.name)),
  );
  const own = rows.filter((r) => !auditNames.has(r.name));
  if (!apply) return own;
  const next = [...own, ...standardAuditColumns(layer, overrides)];
  return next.length > MAX_CUSTOM_METADATA_COLUMNS ? null : next;
}

/** Validates the Silver/Gold transformation rule list (up to `MAX_TRANSFORMATION_RULES`). */
export function validateTransformationRules(layer: MedallionLayer, rules: TransformationRule[]): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (layer === "bronze") return issues;
  const allowedTypes = new Set<TransformationType>(MEDALLION_LAYER_INFO[layer].transformTypes);
  if (rules.length > MAX_TRANSFORMATION_RULES) {
    issues.push({
      severity: "error",
      message: `Maximum ${MAX_TRANSFORMATION_RULES} transformations allowed.`,
      field: "transformations",
    });
  }
  if (rules.length === 0) {
    issues.push({
      severity: "error",
      message: `At least one transformation is required for the ${layer} layer.`,
      field: "transformations",
    });
  }
  rules.forEach((rule, i) => {
    const label = rule.name.trim() || `Transformation ${i + 1}`;
    if (!rule.name.trim()) {
      issues.push({ severity: "error", message: `Transformation ${i + 1} needs a name.`, field: "transformations" });
    }
    if (!allowedTypes.has(rule.type)) {
      issues.push({
        severity: "error",
        message: `Transformation "${label}" type "${rule.type}" isn't valid for the ${layer} layer.`,
        field: "transformations",
      });
    }
    switch (rule.type) {
      case "conform_columns":
        if (!rule.columns?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (conform_columns) needs a column mapping.`, field: "transformations" });
        }
        break;
      case "cast_schema":
        if (!rule.castExpression?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (cast_schema) needs at least one "column:TYPE" cast.`, field: "transformations" });
        }
        break;
      case "dedupe":
        if (!rule.keyColumns?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (dedupe) needs key column(s).`, field: "transformations" });
        }
        if (!rule.orderColumn?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (dedupe) needs an order column (which row "wins").`, field: "transformations" });
        }
        break;
      case "scd2":
        if (!rule.keyColumns?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (scd2) needs natural key column(s).`, field: "transformations" });
        }
        if (!rule.orderColumn?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (scd2) needs a change-detection column.`, field: "transformations" });
        }
        if (!rule.columns?.trim()) {
          issues.push({ severity: "warning", message: `Transformation "${label}" (scd2) has no tracked columns — only the keys and change-detection column are kept in history.`, field: "transformations" });
        }
        break;
      case "join":
        if (!rule.joinEntity?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (join) needs the other entity/table name.`, field: "transformations" });
        }
        if (!rule.joinCondition?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (join) needs a join condition.`, field: "transformations" });
        }
        if (!rule.columns?.trim()) {
          issues.push({ severity: "warning", message: `Transformation "${label}" (join) has no output column list — "a.*, b.*" duplicates the join keys and SQL Server will reject it.`, field: "transformations" });
        }
        break;
      case "aggregate":
        if (!rule.groupByColumns?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (aggregate) needs GROUP BY column(s).`, field: "transformations" });
        }
        if (!rule.aggregateExpression?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (aggregate) needs at least one aggregate expression.`, field: "transformations" });
        }
        break;
      case "select_columns":
        if (!rule.columns?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (select_columns) needs the final column list.`, field: "transformations" });
        }
        break;
      case "filter":
        if (!rule.filterCondition?.trim()) {
          issues.push({ severity: "error", message: `Transformation "${label}" (filter) needs a filter condition.`, field: "transformations" });
        }
        break;
    }
  });
  issues.push(...checkTransformationChainColumns(rules));
  return issues;
}

const PLAIN_IDENT_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
const TRAILING_ALIAS_RE = /\s+AS\s+\[?[A-Za-z_][A-Za-z0-9_]*\]?\s*$/i;

/** The column a projection item produces: its alias, else its bare (de-qualified) name; "*" for a star; null for an unnamed expression. */
function projectedName(item: string): string | null {
  const alias = /\bAS\s+\[?([A-Za-z_][A-Za-z0-9_]*)\]?\s*$/i.exec(item);
  if (alias) return alias[1];
  const trimmed = item.trim();
  if (trimmed.endsWith("*")) return "*";
  const bare = /^(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)$/.exec(trimmed);
  return bare ? bare[1] : null;
}

/** A projection's output columns (lowercased), or null when it contains a star (columns unknown). */
function projectedColumns(items: string[]): Set<string> | null {
  const out = new Set<string>();
  for (const item of items) {
    const name = projectedName(item);
    if (name === "*") return null;
    if (name) out.add(name.toLowerCase());
  }
  return out.size ? out : null;
}

function plainColumns(value: string | undefined): string[] {
  return splitTopLevel(value ?? "").filter((c) => PLAIN_IDENT_RE.test(c));
}

/**
 * Soft, step-by-step column check over a transformation chain: tracks the columns each step outputs
 * (`conform_columns`/`select_columns`/`aggregate`, and `join` with a column list, project; the rest pass
 * through) and warns when a later step reads a plain column an earlier projection dropped — e.g. a
 * `dedupe` ordering on a column `conform_columns` didn't map. Before the first projection the source's
 * columns are unknown, so nothing is checked. Always a warning: this app never sees the live schema.
 */
export function checkTransformationChainColumns(rules: TransformationRule[]): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  let known: Set<string> | null = null;
  let producer = "";
  rules.forEach((rule, i) => {
    const label = rule.name.trim() || `Transformation ${i + 1}`;
    let refs: string[] = [];
    let next: Set<string> | null | undefined; // undefined = pass-through
    switch (rule.type) {
      case "conform_columns": {
        const pairs = splitTopLevel(rule.columns ?? "").map(splitMappingPair);
        refs = pairs.map(([src]) => src.trim()).filter((c) => PLAIN_IDENT_RE.test(c));
        next = projectedColumns(pairs.map(([src, dst]) => dst.trim() || src));
        break;
      }
      case "select_columns": {
        const items = splitTopLevel(rule.columns ?? "");
        refs = items.map((it) => it.replace(TRAILING_ALIAS_RE, "").trim()).filter((c) => PLAIN_IDENT_RE.test(c));
        next = projectedColumns(items);
        break;
      }
      case "cast_schema":
        refs = splitTopLevel(rule.castExpression ?? "")
          .map((p) => splitMappingPair(p)[0].trim())
          .filter((c) => PLAIN_IDENT_RE.test(c));
        break;
      case "dedupe":
        refs = [...plainColumns(rule.keyColumns), ...plainColumns(rule.orderColumn)];
        break;
      case "scd2":
        refs = [...plainColumns(rule.keyColumns), ...plainColumns(rule.orderColumn?.trim() || "modified_date"), ...plainColumns(rule.columns)];
        break;
      case "join":
        // Join references are qualified (a./b.) and b.* is the other table — only the projection is tracked.
        next = rule.columns?.trim() ? projectedColumns(splitTopLevel(rule.columns)) : null;
        break;
      case "aggregate": {
        const groupBy = splitTopLevel(rule.groupByColumns ?? "");
        refs = groupBy.filter((c) => PLAIN_IDENT_RE.test(c));
        next = projectedColumns([...groupBy, ...splitTopLevel(rule.aggregateExpression ?? "")]);
        break;
      }
    }
    if (known) {
      const seen = new Set<string>();
      for (const col of refs) {
        const key = col.toLowerCase();
        if (known.has(key) || seen.has(key)) continue;
        seen.add(key);
        issues.push({
          severity: "warning",
          message: `Transformation "${label}" (${rule.type}) uses column "${col}", which the earlier step "${producer}" doesn't output — the generated SQL would fail at run time.`,
          field: "transformations",
        });
      }
    }
    if (next !== undefined) {
      known = next;
      producer = label;
    }
  });
  return issues;
}

/** RUN-18: CONCAT_WS skips NULLs, so `(NULL,'x')` and `('x',NULL)` would hash the same; a NULL becomes CHAR(0) instead. */
function hashPart(alias: string, column: string): string {
  return `ISNULL(CAST(${alias}.${column} AS NVARCHAR(MAX)), CHAR(0))`;
}

/**
 * The T-SQL text for one transformation rule (a single ADF `Script`
 * activity). `readFrom` is where this rule reads from (the landed object for
 * the first rule, or the previous rule's `writeTo`); `writeTo` is an
 * intermediate staging table unless the rule is terminal, in which case it's
 * `targetTable` itself (see `TERMINAL_TRANSFORM_TYPES`).
 */
export function transformationRuleSql(
  rule: TransformationRule,
  ctx: { readFrom: string; writeTo: string; targetTable: string },
): string {
  const { readFrom, writeTo, targetTable } = ctx;
  switch (rule.type) {
    case "conform_columns": {
      const mapping = splitTopLevel(rule.columns ?? "");
      const selectList =
        mapping
          .map((pair) => {
            const [src, dst] = splitMappingPair(pair);
            return dst ? `${src} AS ${dst}` : src;
          })
          .join(", ") || "*";
      return `DROP TABLE IF EXISTS ${writeTo};\nSELECT ${selectList} INTO ${writeTo} FROM ${readFrom};`;
    }
    case "dedupe": {
      const keys = rule.keyColumns ?? "";
      const order = rule.orderColumn ?? "1";
      return [
        `DROP TABLE IF EXISTS ${writeTo};`,
        `WITH Ranked AS (`,
        `  SELECT *, ROW_NUMBER() OVER (PARTITION BY ${keys} ORDER BY ${order} DESC) AS _rn`,
        `  FROM ${readFrom}`,
        `)`,
        `SELECT * INTO ${writeTo} FROM Ranked WHERE _rn = 1;`,
        `ALTER TABLE ${writeTo} DROP COLUMN _rn;`,
      ].join("\n");
    }
    case "cast_schema": {
      const casts = splitTopLevel(rule.castExpression ?? "");
      const alters = casts
        .map((pair) => {
          const [col, type] = splitMappingPair(pair);
          return col && type ? `ALTER TABLE ${writeTo} ALTER COLUMN ${col} ${type};` : "";
        })
        .filter(Boolean)
        .join("\n");
      return `DROP TABLE IF EXISTS ${writeTo};\nSELECT * INTO ${writeTo} FROM ${readFrom};\n${alters}`;
    }
    case "scd2": {
      const keys = (rule.keyColumns ?? "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const order = rule.orderColumn ?? "modified_date";
      // G3: carry the tracked business attributes (`columns`) and detect change via a row hash of them.
      const attrs = splitTopLevel(rule.columns ?? "").filter((c) => !keys.includes(c) && c !== order);
      const onClause = keys.map((k) => `tgt.${k} = src.${k}`).join(" AND ") || "1=0";
      // DMT-09: the history columns take the rule's names when set (the target DDL's own names).
      const isCurrent = rule.currentFlagColumn?.trim() || "_is_current";
      const validFrom = rule.validFromColumn?.trim() || "_valid_from";
      const validTo = rule.validToColumn?.trim() || "_valid_to";
      const insertCols = [...keys, ...attrs, order, isCurrent, validFrom, validTo].join(", ");
      const newRowSelect = [...keys.map((k) => `src.${k}`), ...attrs.map((a) => `src.${a}`), `src.${order}`, "1", "GETUTCDATE()", "NULL"].join(", ");
      const tracked = [...attrs, order];
      const changed = attrs.length
        ? `HASHBYTES('SHA2_256', CONCAT_WS('|', ${tracked.map((c) => hashPart("tgt", c)).join(", ")})) <> HASHBYTES('SHA2_256', CONCAT_WS('|', ${tracked.map((c) => hashPart("src", c)).join(", ")}))`
        : `tgt.${order} <> src.${order}`;
      // One source row per key (the latest by the change column): a source holding several versions of a
      // key — e.g. a Gold SCD2 over a Silver SCD2 history table — would otherwise match one target row
      // twice, which MERGE rejects.
      const latest = keys.length
        ? `(SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY ${keys.join(", ")} ORDER BY ${order} DESC) AS _scd2_rn FROM ${readFrom}) v WHERE _scd2_rn = 1)`
        : readFrom;
      return [
        `-- Step 1: close out changed current rows.`,
        `MERGE ${targetTable} AS tgt`,
        `USING ${latest} AS src`,
        `ON ${onClause} AND tgt.${isCurrent} = 1`,
        `WHEN MATCHED AND ${changed} THEN`,
        `  UPDATE SET tgt.${isCurrent} = 0, tgt.${validTo} = GETUTCDATE();`,
        `-- Step 2: insert new + newly-changed rows as the new current version.`,
        `INSERT INTO ${targetTable} (${insertCols})`,
        `SELECT ${newRowSelect}`,
        `FROM ${latest} src`,
        `LEFT JOIN ${targetTable} tgt ON ${onClause} AND tgt.${isCurrent} = 1`,
        `WHERE tgt.${keys[0] ?? "id"} IS NULL;`,
      ].join("\n");
    }
    case "join": {
      const joinType = rule.joinType ?? "INNER";
      const other = rule.joinEntity ?? "curated.other_entity";
      const condition = rule.joinCondition ?? "1=1";
      // G4: `a.*, b.*` duplicates the join-key columns, which SELECT ... INTO rejects — prefer the
      // rule's explicit projection (`columns`); the bare fallback is flagged by validation.
      const projection = rule.columns?.trim() || "a.*, b.*";
      return `DROP TABLE IF EXISTS ${writeTo};\nSELECT ${projection}\nINTO ${writeTo}\nFROM ${readFrom} a\n${joinType} JOIN ${other} b ON ${condition};`;
    }
    case "aggregate": {
      const groupBy = rule.groupByColumns ?? "";
      const agg = rule.aggregateExpression ?? "COUNT(*) AS row_count";
      return `DROP TABLE IF EXISTS ${writeTo};\nSELECT ${groupBy}, ${agg}\nINTO ${writeTo}\nFROM ${readFrom}\nGROUP BY ${groupBy};`;
    }
    case "select_columns": {
      const cols = (rule.columns ?? "*").trim() || "*";
      return `DROP TABLE IF EXISTS ${writeTo};\nSELECT ${cols} INTO ${writeTo} FROM ${readFrom};`;
    }
    case "filter": {
      const condition = rule.filterCondition?.trim() || "1=1";
      return `DROP TABLE IF EXISTS ${writeTo};\nSELECT * INTO ${writeTo} FROM ${readFrom} WHERE ${condition};`;
    }
    default:
      return `-- Unsupported transformation type "${(rule as { type: string }).type}".`;
  }
}

/** G5: the grain of the transformed rows — the last dedupe/scd2 key or aggregate GROUP BY in the chain, else the pipeline's upsert keys. */
export function layerLoadKeyColumns(rules: TransformationRule[], fallback?: string): string[] {
  let keys = fallback ?? "";
  for (const rule of rules) {
    if ((rule.type === "dedupe" || rule.type === "scd2") && rule.keyColumns?.trim()) keys = rule.keyColumns;
    else if (rule.type === "aggregate" && rule.groupByColumns?.trim()) keys = rule.groupByColumns;
  }
  return keys
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean);
}

/**
 * G5: idempotent final load — truncate-and-load on a full refresh, delete-then-insert on the key
 * columns otherwise, so a rerun/restart (AGENTS.md §34) never duplicates rows.
 */
export function layerFinalLoadSql(targetTable: string, readFrom: string, opts: { fullRefresh: boolean; keys: string[] }): string {
  // RUN-09: columns are matched by name at run time, not by position — a target with a different column
  // order or extra (e.g. audit) columns loads correctly; target-only columns keep their defaults.
  const columns = `-- Insert by column name: the staged columns that also exist in the target.
DECLARE @cols NVARCHAR(MAX) = (SELECT STRING_AGG(QUOTENAME(s.name), ', ') WITHIN GROUP (ORDER BY s.column_id) FROM sys.columns s WHERE s.object_id = OBJECT_ID(N'${readFrom}') AND COL_LENGTH(N'${targetTable}', s.name) IS NOT NULL);
IF @cols IS NULL THROW 50001, N'No column of ${readFrom} exists in ${targetTable}.', 1;`;
  const insert = `EXEC(N'INSERT INTO ${targetTable} (' + @cols + N') SELECT ' + @cols + N' FROM ${readFrom};');`;
  if (opts.fullRefresh) return `${columns}
BEGIN TRANSACTION;
TRUNCATE TABLE ${targetTable};
${insert}
COMMIT;`;
  if (!opts.keys.length) {
    return `-- No key columns (dedupe/scd2 keys, aggregate GROUP BY or upsert keys): a rerun appends duplicates.
${columns}
${insert}`;
  }
  const match = opts.keys.map((k) => `tgt.${k} = src.${k}`).join(" AND ");
  return `${columns}
BEGIN TRANSACTION;
DELETE tgt FROM ${targetTable} tgt WHERE EXISTS (SELECT 1 FROM ${readFrom} src WHERE ${match});
${insert}
COMMIT;`;
}
