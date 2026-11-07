/**
 * §33 STTM-Based Silver/Gold Object Registry — client-side Excel template
 * builder (`buildSttmTemplate`) and preview parser (`parseSttmWorkbook`).
 *
 * **Not the authoritative validator.** This module exists only for instant
 * upload-preview feedback (§33.9) — the real commit path re-parses the raw
 * file server-side (`backend/app/onboarding/sttm_parser.py`, `openpyxl`),
 * independently, and wins on any disagreement. Kept in *rule* parity with
 * that module (same allow-lists, same required-field checks) but not
 * byte-identical-output parity (§18 doesn't apply here — this produces
 * preview rows, not ADF artifacts).
 *
 * **Sheet layout — MUST stay in lockstep with the backend parser's
 * `_HEADER_FIELDS`/`_TRANSFORM_COLUMNS`/`_DQ_COLUMNS` row/column numbers**
 * (`backend/app/onboarding/sttm_parser.py`'s module docstring has the same
 * table). One fixed, deterministic row layout per object sheet: column A/B
 * a "Field | Value" pair table for the object header (row 1 = header row,
 * data starts row 2), then a TRANSFORMATIONS block (max 10 rows) and a DATA
 * QUALITY RULES block (max 5 rows), each its own little table with its own
 * header row.
 */
import type { Worksheet } from "exceljs";
import { validateDataQualityRules } from "./spec";
import {
  MAX_TRANSFORMATION_RULES,
  MEDALLION_LAYER_INFO,
  layerTargetTableName,
  validateTransformationRules,
  type MedallionLayer,
  type TransformationRule,
} from "./medallion";
import type {
  RegistryObjectInput,
  SttmDataQualityRule,
  SttmLayer,
  SttmParseReport,
  SttmParsedSheet,
  SttmSheetIssue,
} from "@/types/sttm";

// =============================================================================
// Sheet layout constants — shared source of truth for build + parse.
// =============================================================================

export const STTM_TEMPLATE_VERSION: Record<SttmLayer, string> = { silver: "STTM-SILVER-v1", mart: "STTM-GOLD-v1" };

export const HEADER_FIELDS = [
  "object_key",
  "tag",
  "curated_entity",
  "mart_domain",
  "source_object_keys",
  "enabled",
  "owner",
  "batch_group",
  "domain",
  "write_behavior",
  "loading_behavior",
  "watermark_column",
  "watermark_column_type",
  "business_keys",
  "data_classification",
  "notes",
] as const;

export const TRANSFORM_COLUMNS = [
  "name",
  "type",
  "columns",
  "key_columns",
  "order_column",
  "cast_expression",
  "join_entity",
  "join_type",
  "join_condition",
  "group_by_columns",
  "aggregate_expression",
] as const;

export const DQ_COLUMNS = ["name", "column", "rule", "value", "enforcement"] as const;

export const MAX_TRANSFORMATIONS = MAX_TRANSFORMATION_RULES;
export const MAX_DQ_RULES = 5;
export const MAX_SHEETS_PER_WORKBOOK = 200; // §33.6 rule 10 guardrail — mirrors sttm_parser.py

export const HEADER_START_ROW = 2; // row 1 is "Field" | "Value"
export const TRANSFORM_SECTION_ROW = HEADER_START_ROW + HEADER_FIELDS.length + 1; // blank row, then section title
export const TRANSFORM_HEADER_ROW = TRANSFORM_SECTION_ROW + 1;
export const TRANSFORM_DATA_START_ROW = TRANSFORM_HEADER_ROW + 1;
export const DQ_SECTION_ROW = TRANSFORM_DATA_START_ROW + MAX_TRANSFORMATIONS + 1;
export const DQ_HEADER_ROW = DQ_SECTION_ROW + 1;
export const DQ_DATA_START_ROW = DQ_HEADER_ROW + 1;

const RESERVED_EXACT = new Set(["Instructions", "Available_Transformations"]);
const RESERVED_PREFIXES = ["EXAMPLE_"];

function isReservedSheet(name: string): boolean {
  return RESERVED_EXACT.has(name) || RESERVED_PREFIXES.some((p) => name.startsWith(p));
}

function snakeToCamel(key: string): string {
  const parts = key.split("_");
  return parts[0] + parts.slice(1).map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join("");
}

const ENTITY_TOKEN_RE = /^[a-z][a-z0-9_]{1,63}$/;

function toSnake(value: string | undefined): string {
  return (
    (value ?? "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "") || "object"
  );
}

// =============================================================================
// §33.6 header validation — a pure-TS mirror of the backend's
// `validate_object_header` (rules 1-6; uniqueness/collision (7-8) and
// cross-layer advisories (9) need DB/registry context this module doesn't
// have, guardrails (10) are enforced by `parseSttmWorkbook` itself).
// =============================================================================

export function validateSttmObjectHeader(layer: SttmLayer, data: RegistryObjectInput): SttmSheetIssue[] {
  const issues: SttmSheetIssue[] = [];
  const tag = (data.tag ?? "").trim();
  if (!tag) {
    issues.push({ severity: "error", message: "tag is required (the mandatory classification column).", field: "tag" });
  }

  const entity = (data.curatedEntity ?? "").trim();
  if (!entity) {
    issues.push({ severity: "error", message: "curated_entity is required.", field: "curatedEntity" });
  } else if (!ENTITY_TOKEN_RE.test(toSnake(entity))) {
    issues.push({
      severity: "error",
      message: `curated_entity "${entity}" must be lowercase letters, digits, and underscores.`,
      field: "curatedEntity",
    });
  }

  if (layer === "mart") {
    const domain = (data.martDomain ?? "").trim();
    if (!domain) {
      issues.push({ severity: "error", message: "mart_domain is required for Gold objects.", field: "martDomain" });
    } else if (!ENTITY_TOKEN_RE.test(toSnake(domain))) {
      issues.push({
        severity: "error",
        message: `mart_domain "${domain}" must be lowercase letters, digits, and underscores.`,
        field: "martDomain",
      });
    }
  }

  if (!data.sourceObjectKeys || data.sourceObjectKeys.length === 0) {
    issues.push({
      severity: "error",
      message: "source_object_keys is required (at least one upstream object).",
      field: "sourceObjectKeys",
    });
  }

  if (data.loadingBehavior === "DeltaLoad" && (!data.watermarkColumn?.trim() || !data.watermarkColumnType?.trim())) {
    issues.push({
      severity: "error",
      message: "watermark_column and watermark_column_type are required when loading_behavior=DeltaLoad.",
      field: "watermarkColumn",
    });
  }

  if ((data.writeBehavior === "upsert" || data.writeBehavior === "merge") && (!data.businessKeys || data.businessKeys.length === 0)) {
    issues.push({
      severity: "error",
      message: "business_keys is required when write_behavior is upsert or merge.",
      field: "businessKeys",
    });
  }

  return issues;
}

function medallionLayerOf(layer: SttmLayer): MedallionLayer {
  return layer === "mart" ? "gold" : "silver";
}

// =============================================================================
// §33.6 rule 6 soft cross-check — mirrors the backend's
// `check_dq_columns_against_transformations`/`_transformation_output_columns`
// (`backend/app/onboarding/sttm.py`) field-for-field. Always a `warning`,
// never blocks — see that module's docstring for the full rationale.
// =============================================================================

function transformationOutputColumns(transformations: TransformationRule[]): Set<string> {
  const columns = new Set<string>();
  for (const rule of transformations) {
    const rtype = (rule as unknown as { type?: string }).type ?? "";
    const asRecord = rule as unknown as Record<string, string | undefined>;
    if (rtype === "conform_columns" || rtype === "select_columns") {
      for (const part of (asRecord.columns ?? "").split(",")) {
        const trimmed = part.trim();
        if (!trimmed) continue;
        columns.add(trimmed.includes(":") ? trimmed.split(":").pop()!.trim() : trimmed);
      }
    } else if (rtype === "cast_schema") {
      for (const part of (asRecord.castExpression ?? "").split(",")) {
        const trimmed = part.trim();
        if (trimmed.includes(":")) columns.add(trimmed.split(":")[0].trim());
      }
    } else if (rtype === "aggregate") {
      for (const part of (asRecord.groupByColumns ?? "").split(",")) {
        const trimmed = part.trim();
        if (trimmed) columns.add(trimmed);
      }
      for (const part of (asRecord.aggregateExpression ?? "").split(",")) {
        const match = /\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$/i.exec(part.trim());
        if (match) columns.add(match[1]);
      }
    }
  }
  return columns;
}

export function checkDqColumnsAgainstTransformations(
  transformations: TransformationRule[],
  dqRules: SttmDataQualityRule[],
): SttmSheetIssue[] {
  const known = transformationOutputColumns(transformations);
  if (known.size === 0) return [];
  const issues: SttmSheetIssue[] = [];
  dqRules.forEach((rule, i) => {
    const column = (rule.column ?? "").trim();
    if (!column || known.has(column)) return;
    const label = (rule.name ?? "").trim() || `Rule ${i + 1}`;
    issues.push({
      severity: "warning",
      message: `Data quality rule "${label}" checks column "${column}", which doesn't appear in any transformation's output columns.`,
      field: "dataQualityRules",
    });
  });
  return issues;
}

// =============================================================================
// Template builder (§33.5) — exceljs, generated client-side, two separate
// workbooks (Silver/Gold kept apart, not one combined file — disjoint
// mandatory fields and, even after §33.3's widening, still-disjoint-enough
// transformation vocabularies).
// =============================================================================

interface ExampleRow {
  header: Partial<Record<(typeof HEADER_FIELDS)[number], string>>;
  transformations: string[][];
  dqRules: string[][];
}

const EXAMPLE_ROWS: Record<SttmLayer, { sheetName: string; row: ExampleRow }> = {
  silver: {
    sheetName: "EXAMPLE_gl_transaction",
    row: {
      header: {
        object_key: "gl_transaction",
        tag: "finance",
        curated_entity: "gl_transaction",
        source_object_keys: "sql_server.dbo.gl_transaction",
        enabled: "TRUE",
        owner: "data-eng",
        batch_group: "finance-core",
        write_behavior: "append",
        loading_behavior: "FullLoad",
        data_classification: "internal",
        notes: "Illustrative example — duplicate NEW_OBJECT_1 and rename for your own entity.",
      },
      transformations: [
        ["conform_gl_columns", "conform_columns", "TransactionId:transaction_id, Amount:amount", "", "", "", "", "", "", "", ""],
        ["dedupe_gl", "dedupe", "", "transaction_id", "modified_date", "", "", "", "", "", ""],
      ],
      dqRules: [["not_null_amount", "amount", "IS_NOT_NULL", "", "warn"]],
    },
  },
  mart: {
    sheetName: "EXAMPLE_gl_fact",
    row: {
      header: {
        object_key: "gl_fact",
        tag: "finance",
        mart_domain: "finance",
        curated_entity: "gl_fact",
        source_object_keys: "gl_transaction",
        enabled: "TRUE",
        owner: "data-eng",
        write_behavior: "overwrite",
        loading_behavior: "FullLoad",
        data_classification: "internal",
        notes: "Illustrative example — duplicate NEW_OBJECT_1 and rename for your own mart.",
      },
      transformations: [
        ["agg_gl", "aggregate", "", "", "", "", "", "", "", "customer_id, posted_month", "SUM(amount) AS total_amount"],
      ],
      dqRules: [["range_amount", "total_amount", "RANGE_BETWEEN", "0,1000000000", "warn"]],
    },
  },
};

function writeObjectSheet(ws: Worksheet, header: ExampleRow["header"], transformations: string[][], dqRules: string[][]): void {
  ws.getCell(1, 1).value = "Field";
  ws.getCell(1, 2).value = "Value";
  ws.getCell(1, 1).font = { bold: true };
  ws.getCell(1, 2).font = { bold: true };
  HEADER_FIELDS.forEach((field, i) => {
    ws.getCell(HEADER_START_ROW + i, 1).value = field;
    ws.getCell(HEADER_START_ROW + i, 2).value = header[field] ?? "";
  });

  ws.getCell(TRANSFORM_SECTION_ROW, 1).value = `TRANSFORMATIONS (max ${MAX_TRANSFORMATIONS})`;
  ws.getCell(TRANSFORM_SECTION_ROW, 1).font = { bold: true };
  TRANSFORM_COLUMNS.forEach((col, i) => {
    ws.getCell(TRANSFORM_HEADER_ROW, i + 1).value = col;
    ws.getCell(TRANSFORM_HEADER_ROW, i + 1).font = { bold: true };
  });
  transformations.slice(0, MAX_TRANSFORMATIONS).forEach((row, r) => {
    row.forEach((val, c) => {
      if (val) ws.getCell(TRANSFORM_DATA_START_ROW + r, c + 1).value = val;
    });
  });

  ws.getCell(DQ_SECTION_ROW, 1).value = `DATA QUALITY RULES (max ${MAX_DQ_RULES})`;
  ws.getCell(DQ_SECTION_ROW, 1).font = { bold: true };
  DQ_COLUMNS.forEach((col, i) => {
    ws.getCell(DQ_HEADER_ROW, i + 1).value = col;
    ws.getCell(DQ_HEADER_ROW, i + 1).font = { bold: true };
  });
  dqRules.slice(0, MAX_DQ_RULES).forEach((row, r) => {
    row.forEach((val, c) => {
      if (val) ws.getCell(DQ_DATA_START_ROW + r, c + 1).value = val;
    });
  });

  for (let col = 1; col <= 11; col++) ws.getColumn(col).width = 22;
}

/** Builds one STTM workbook (`.xlsx`, as an `ArrayBuffer`) for the given
 * layer — Instructions + Available_Transformations + one worked EXAMPLE_*
 * sheet + one blank NEW_OBJECT_1 skeleton. Dynamically imports `exceljs`
 * (a ~1MB library, no reason to ship it in the main bundle for a
 * download-button-only feature). */
export async function buildSttmTemplate(layer: SttmLayer): Promise<ArrayBuffer> {
  const ExcelJS = (await import("exceljs")).default;
  const wb = new ExcelJS.Workbook();
  wb.creator = "ADF Pipeline Accelerator";
  wb.created = new Date();

  const medallionLayer = medallionLayerOf(layer);
  const info = MEDALLION_LAYER_INFO[medallionLayer];

  const instructions = wb.addWorksheet("Instructions");
  instructions.getColumn(1).width = 100;
  const lines = [
    `STTM ${layer === "mart" ? "Gold" : "Silver"} Onboarding Template`,
    "",
    `Layer target table pattern: ${info.targetPatternHint}`,
    "",
    "How to use this workbook:",
    '1. Duplicate the "NEW_OBJECT_1" sheet once per curated entity you want to onboard, and rename the',
    "   copy to your object's key (letters/digits/underscores only — this becomes the object_key).",
    '2. Fill in the "Field | Value" table at the top of your sheet. "tag" is mandatory on every object —',
    "   it is the classification column used for filtering and governance.",
    "3. Fill in up to 10 transformation rows and up to 5 data quality rows — see the",
    '   "Available_Transformations" sheet for the exact fields each type reads.',
    '4. Leave "Instructions", "Available_Transformations", and any "EXAMPLE_*" sheet as-is — they are',
    "   never read as objects.",
    "5. Upload the finished workbook on the Onboarding page. Re-uploading is additive/upsert-by-",
    "   object_key — it never destructively replaces objects you already onboarded.",
    "",
    `Template_Version: ${STTM_TEMPLATE_VERSION[layer]}`,
  ];
  lines.forEach((line, i) => {
    instructions.getCell(i + 1, 1).value = line;
  });
  instructions.getCell(1, 1).font = { bold: true, size: 14 };
  // Hidden forward-compat marker cell (§33.5) — kept separate from the
  // human-readable line above so a future parser can read it without regex.
  const versionCell = instructions.getCell(1, 10);
  versionCell.value = STTM_TEMPLATE_VERSION[layer];
  instructions.getColumn(10).hidden = true;

  const reference = wb.addWorksheet("Available_Transformations");
  const refHeader = ["Layer", "Type", "Applies_To", "Required_Params", "Optional_Params", "Description", "Example"];
  refHeader.forEach((h, i) => {
    reference.getCell(1, i + 1).value = h;
    reference.getCell(1, i + 1).font = { bold: true };
  });
  const TRANSFORM_DOCS: Record<string, { required: string; optional: string; description: string; example: string }> = {
    conform_columns: { required: "columns", optional: "", description: "Rename/select columns.", example: "TransactionId:transaction_id" },
    dedupe: { required: "key_columns, order_column", optional: "", description: "Keep one row per key, latest by order_column.", example: "key_columns=customer_id" },
    cast_schema: { required: "cast_expression", optional: "", description: "ALTER COLUMN type casts.", example: "amount:DECIMAL(18,2)" },
    scd2: { required: "key_columns, order_column", optional: "", description: "Slowly Changing Dimension type 2 history.", example: "key_columns=customer_id" },
    join: { required: "join_entity, join_condition", optional: "join_type (INNER|LEFT, default INNER)", description: "Join in another entity/table.", example: "join_entity=curated.customer" },
    aggregate: { required: "group_by_columns, aggregate_expression", optional: "", description: "GROUP BY aggregation.", example: "SUM(amount) AS total_amount" },
    select_columns: { required: "columns", optional: "", description: "Final column projection (terminal step).", example: "customer_id, total_amount" },
  };
  let row = 2;
  info.transformTypes.forEach((type) => {
    const doc = TRANSFORM_DOCS[type];
    reference.getCell(row, 1).value = layer === "mart" ? "Gold" : "Silver";
    reference.getCell(row, 2).value = type;
    reference.getCell(row, 3).value = "transformation";
    reference.getCell(row, 4).value = doc?.required ?? "";
    reference.getCell(row, 5).value = doc?.optional ?? "";
    reference.getCell(row, 6).value = doc?.description ?? "";
    reference.getCell(row, 7).value = doc?.example ?? "";
    row++;
  });
  const DQ_DOCS: Record<string, string> = {
    IS_NOT_NULL: "Column must never be null.",
    UNIQUE: "Column must be unique.",
    LENGTH_LE: "String length must be <= Value (a whole number).",
    RANGE_BETWEEN: 'Numeric value must fall within "min,max" (Value).',
    REGEX_MATCH: "Value must match the regex pattern in Value.",
    ROW_COUNT_MIN: "Object must hold at least Value rows (column not used).",
    FRESHNESS_HOURS: "Newest value of the timestamp column must be at most Value hours old.",
  };
  (["IS_NOT_NULL", "UNIQUE", "LENGTH_LE", "RANGE_BETWEEN", "REGEX_MATCH", "ROW_COUNT_MIN", "FRESHNESS_HOURS"] as const).forEach((type) => {
    reference.getCell(row, 1).value = layer === "mart" ? "Gold" : "Silver";
    reference.getCell(row, 2).value = type;
    reference.getCell(row, 3).value = "data_quality";
    reference.getCell(row, 4).value = "column, rule";
    reference.getCell(row, 5).value = "value, enforcement (warn|block, default warn)";
    reference.getCell(row, 6).value = DQ_DOCS[type];
    reference.getCell(row, 7).value = "";
    row++;
  });
  refHeader.forEach((_, i) => reference.getColumn(i + 1).width = 28);

  const example = wb.addWorksheet(EXAMPLE_ROWS[layer].sheetName);
  writeObjectSheet(example, EXAMPLE_ROWS[layer].row.header, EXAMPLE_ROWS[layer].row.transformations, EXAMPLE_ROWS[layer].row.dqRules);

  const blank = wb.addWorksheet("NEW_OBJECT_1");
  writeObjectSheet(blank, {}, [], []);

  const buffer = await wb.xlsx.writeBuffer();
  return buffer as ArrayBuffer;
}

// =============================================================================
// Client-side preview parser (§33.9 — instant feedback only, never
// authoritative; see this file's header comment).
// =============================================================================

function cellString(ws: Worksheet, row: number, col: number): string {
  const value = ws.getCell(row, col).value;
  if (value === null || value === undefined) return "";
  if (typeof value === "object" && "text" in value) return String((value as { text: unknown }).text ?? "");
  if (typeof value === "object" && "result" in value) return String((value as { result: unknown }).result ?? "");
  return String(value).trim();
}

function parseObjectSheet(ws: Worksheet, layer: SttmLayer): SttmParsedSheet {
  const header: Record<string, string> = {};
  HEADER_FIELDS.forEach((field, i) => {
    header[field] = cellString(ws, HEADER_START_ROW + i, 2);
  });

  const transformations: TransformationRule[] = [];
  for (let r = 0; r < MAX_TRANSFORMATIONS; r++) {
    const rowNo = TRANSFORM_DATA_START_ROW + r;
    const values = TRANSFORM_COLUMNS.map((_, c) => cellString(ws, rowNo, c + 1));
    if (values.every((v) => !v)) continue;
    const rule: Record<string, string> = {};
    TRANSFORM_COLUMNS.forEach((col, i) => {
      if (values[i]) rule[snakeToCamel(col)] = values[i];
    });
    if (Object.keys(rule).length) transformations.push(rule as unknown as TransformationRule);
  }

  const dqRules: SttmDataQualityRule[] = [];
  for (let r = 0; r < MAX_DQ_RULES; r++) {
    const rowNo = DQ_DATA_START_ROW + r;
    const values = DQ_COLUMNS.map((_, c) => cellString(ws, rowNo, c + 1));
    if (values.every((v) => !v)) continue;
    const rule: Record<string, string> = {};
    DQ_COLUMNS.forEach((col, i) => {
      if (values[i]) rule[col] = values[i];
    });
    if (Object.keys(rule).length) dqRules.push(rule as unknown as SttmDataQualityRule);
  }

  const objectKey = header.object_key || toSnake(header.curated_entity);
  const parsed: RegistryObjectInput = {
    objectKey,
    enabled: (header.enabled || "TRUE").toUpperCase() !== "FALSE",
    tag: header.tag,
    curatedEntity: header.curated_entity,
    martDomain: header.mart_domain || undefined,
    sourceObjectKeys: header.source_object_keys ? header.source_object_keys.split("|").map((s) => s.trim()).filter(Boolean) : [],
    owner: header.owner || undefined,
    batchGroup: header.batch_group || undefined,
    domain: header.domain || undefined,
    writeBehavior: (header.write_behavior || "append") as RegistryObjectInput["writeBehavior"],
    loadingBehavior: (header.loading_behavior || "FullLoad") as RegistryObjectInput["loadingBehavior"],
    watermarkColumn: header.watermark_column || undefined,
    watermarkColumnType: header.watermark_column_type || undefined,
    businessKeys: header.business_keys ? header.business_keys.split(",").map((s) => s.trim()).filter(Boolean) : [],
    transformations,
    dataQualityRules: dqRules,
    dataClassification: (header.data_classification || undefined) as RegistryObjectInput["dataClassification"],
    notes: header.notes || "",
  };

  const medallionLayer = medallionLayerOf(layer);
  const issues: SttmSheetIssue[] = [
    ...validateSttmObjectHeader(layer, parsed),
    ...validateTransformationRules(medallionLayer, transformations),
    ...validateDataQualityRules(dqRules),
    ...checkDqColumnsAgainstTransformations(transformations, dqRules),
  ];
  const hasErrors = issues.some((i) => i.severity === "error");

  return {
    sheetName: ws.name,
    objectKey: objectKey || null,
    curatedEntity: parsed.curatedEntity || null,
    valid: !hasErrors,
    issues,
    parsed: hasErrors ? null : parsed,
  };
}

/** Client-side preview parse of an uploaded `.xlsx` — same guardrails as the
 * backend (§33.6 rule 10: cap at 200 sheets/workbook) and the same
 * duplicate-`object_key`-across-sheets check (§33.6 rule 7). Dynamically
 * imports `exceljs`, same reasoning as `buildSttmTemplate`. */
export async function parseSttmWorkbook(layer: SttmLayer, data: ArrayBuffer): Promise<SttmParseReport> {
  const ExcelJS = (await import("exceljs")).default;
  const wb = new ExcelJS.Workbook();
  await wb.xlsx.load(data);

  const names = wb.worksheets.map((ws) => ws.name).filter((n) => !isReservedSheet(n));
  if (names.length > MAX_SHEETS_PER_WORKBOOK) {
    // §33.6 rule 10: reject outright rather than silently parsing the first
    // MAX_SHEETS_PER_WORKBOOK and dropping the rest unannounced.
    throw new Error(
      `Workbook has ${names.length} object sheets, exceeding the ${MAX_SHEETS_PER_WORKBOOK}-sheet-per-workbook limit. Split it into multiple uploads.`,
    );
  }

  const seen = new Map<string, string>();
  const sheets: SttmParsedSheet[] = [];
  for (const name of names) {
    const ws = wb.getWorksheet(name);
    if (!ws) continue;
    const parsed = parseObjectSheet(ws, layer);
    if (parsed.objectKey) {
      const firstSheet = seen.get(parsed.objectKey);
      if (firstSheet) {
        parsed.valid = false;
        parsed.parsed = null;
        parsed.issues.push({
          severity: "error",
          message: `Sheet "${name}" duplicates object_key "${parsed.objectKey}" already used by sheet "${firstSheet}".`,
          field: "objectKey",
        });
      } else {
        seen.set(parsed.objectKey, name);
      }
    }
    sheets.push(parsed);
  }

  const sheetValid = sheets.filter((s) => s.valid).length;
  return {
    fileHash: "", // computed server-side only (§33.9) — the preview parser has no reason to hash
    sheetTotal: sheets.length,
    sheetValid,
    sheetError: sheets.length - sheetValid,
    sheets,
  };
}

export { layerTargetTableName };
