/**
 * §26 Source Onboarding — the object registry (source object → target + file
 * formats + load behaviour). This module owns the bulk-upload template, its
 * parser, per-row validation, and the empty-row factory;
 * `features/onboarding/onboarding-view.tsx` calls `api.onboarding` (backed by
 * the real `app.onboarding_objects` table, `backend/app/models.py`) for
 * persistence and owns the screen.
 *
 * DDL alignment (the point of this file existing separately from
 * `connections.ts`'s per-connection object-list template): every column
 * below is, by construction, an exact `app.onboarding_objects` column name
 * (`docs/DDL.md`) — the template *is* that table's insertable columns, minus
 * server-generated ones (`project_id`, `version`, `status`, `created_at`,
 * `updated_at`, ...). `ONBOARDING_COLUMNS[].controlTableColumn` additionally
 * documents which `control.adf_control_table` column (`docs/DDL.md` §2) each
 * one feeds; `buildOnboardingControlTableSql` (§26.5 "Generate control-table
 * artifacts") is the generator that consumes it. `onboarding.test.ts` asserts
 * every header this module emits is a real `app.onboarding_objects` column,
 * so a rename in one place fails a test instead of silently drifting from
 * the other.
 */
import type { OnboardingDqRule, OnboardingObjectEntry, RegistryObject, ValidationIssue } from "@/types";
import type { SpecDraft } from "./spec";
import { csvRow, splitDelimited, DATABASE_CONNECTORS, FILE_CONNECTORS, SERVICE_CONNECTORS } from "./connections";
import { createTableIfMissing } from "./pipeline-codegen";

// =============================================================================
// Column model — the single source of truth for the template, the parser's
// header-synonym mapping, and the DDL-alignment guardrail.
// =============================================================================

export interface OnboardingColumnDef {
  /** Both the OnboardingObjectEntry field key and the app.onboarding_objects column name. */
  key: keyof OnboardingObjectEntry;
  /** control.adf_control_table column this feeds, once §26.5's generator exists (documentation only). */
  controlTableColumn?: string;
  /** Header synonyms recognized on import, in addition to `key` itself. */
  synonyms: string[];
  required?: boolean;
  sample: [string, string, string];
}

export const ONBOARDING_COLUMNS: OnboardingColumnDef[] = [
  { key: "object_key", synonyms: ["objectkey", "key"], sample: ["", "", ""] },
  { key: "enabled", controlTableColumn: "Enabled", synonyms: ["copyenabled", "active"], sample: ["TRUE", "TRUE", "TRUE"] },
  { key: "batch_group", synonyms: ["batchgroup", "group"], sample: ["core", "core", "files"] },
  { key: "owner", synonyms: [], sample: ["data-eng", "data-eng", "data-eng"] },
  {
    key: "data_classification",
    synonyms: ["classification", "sensitivity"],
    sample: ["confidential", "internal", "internal"],
  },
  { key: "source_system", synonyms: ["sourcesystem", "system"], required: true, sample: ["erp", "erp", "erp"] },
  { key: "source_type", controlTableColumn: "SourceType", synonyms: ["sourcetype"], required: true, sample: ["table", "table", "file"] },
  { key: "source_schema", synonyms: ["schema"], sample: ["dbo", "dbo", ""] },
  {
    key: "source_object",
    controlTableColumn: "Source",
    synonyms: ["object", "table", "table_name"],
    required: true,
    sample: ["customer", "orders", "exports/invoices_*.csv"],
  },
  { key: "source_query", controlTableColumn: "SourceQuery", synonyms: ["query"], sample: ["", "", ""] },
  { key: "target_type", controlTableColumn: "TargetType", synonyms: ["targettype"], required: true, sample: ["table", "table", "file"] },
  { key: "target_schema", synonyms: [], sample: ["curated", "curated", ""] },
  { key: "target_object", controlTableColumn: "Target", synonyms: ["target", "target_table"], sample: ["customer", "orders", ""] },
  { key: "target_path", controlTableColumn: "Target", synonyms: ["path"], sample: ["", "", "curated/invoices/"] },
  { key: "target_file_name", controlTableColumn: "TargetFileName", synonyms: ["filename"], sample: ["", "", "invoices.parquet"] },
  {
    key: "target_file_format",
    synonyms: ["fileformat", "format"],
    required: true,
    sample: ["Parquet", "Parquet", "Parquet"],
  },
  { key: "write_behavior", synonyms: ["writebehavior"], sample: ["overwrite", "append", "append"] },
  { key: "upsert_keys", synonyms: ["upsertkeys", "business_keys"], sample: ["", "", ""] },
  {
    key: "loading_behavior",
    controlTableColumn: "LoadingBehavior",
    synonyms: ["load", "behavior"],
    required: true,
    sample: ["FullLoad", "DeltaLoad", "FullLoad"],
  },
  { key: "watermark_column", controlTableColumn: "WatermarkColumn", synonyms: ["watermark"], sample: ["", "modified_date", ""] },
  { key: "watermark_column_type", controlTableColumn: "WatermarkType", synonyms: ["watermarktype"], sample: ["", "datetime", ""] },
  { key: "notes", synonyms: ["comment", "comments"], sample: ["", "", ""] },
];

/** Every `app.onboarding_objects` column (`docs/DDL.md`) an onboarding row can hold — server-generated
 * columns (`project_id`, `version`, `status`, timestamps, ...) are deliberately excluded since they're
 * never part of an uploaded template. */
export const ONBOARDING_DDL_COLUMNS = new Set<string>([
  "object_key", "enabled", "task_id", "load_order", "batch_group", "domain", "priority", "owner",
  "source_system", "source_connection_ref", "source_type", "source_schema", "source_object", "source_query",
  "source_filter", "source_folder_path", "source_file_pattern", "source_file_format", "source_delimiter",
  "source_quote_char", "source_escape_char", "source_first_row_as_header", "source_encoding",
  "source_compression", "source_null_value", "source_skip_line_count",
  "target_connection_ref", "target_type", "target_schema", "target_object", "target_path",
  "target_file_name", "target_file_name_prefix", "target_file_format", "target_compression_codec",
  "target_delimiter", "target_quote_char", "target_first_row_as_header", "target_encoding",
  "write_behavior", "upsert_keys", "business_keys", "partition_by", "max_rows_per_file",
  "loading_behavior", "watermark_column", "watermark_column_type", "watermark_start_value",
  "watermark_is_inclusive_upper", "hard_delete_handling", "truncate_before_load",
  "columns", "schema_file_ref", "primary_key", "data_classification", "expected_row_count",
  "sla_minutes", "notes",
]);

export const DATA_CLASSIFICATIONS: NonNullable<OnboardingObjectEntry["data_classification"]>[] = [
  "public",
  "internal",
  "confidential",
  "restricted",
];

const REQUIRED_KEYS = ONBOARDING_COLUMNS.filter((c) => c.required).map((c) => c.key);

function normalizeHeader(raw: string): string {
  return raw.trim().toLowerCase().replace(/[\s-]/g, "_");
}

function synonymsFor(column: OnboardingColumnDef): string[] {
  return [column.key, ...column.synonyms];
}

// =============================================================================
// Downloadable template
// =============================================================================

/**
 * Build the downloadable CSV template for the Onboarding registry: one
 * header row (every `ONBOARDING_COLUMNS` key, in the order above) followed
 * by 3 dummy rows exercising a full-load table, a delta-load table with a
 * watermark, and a file→file object — proving multi-row bulk upload of
 * heterogeneous objects in a single file.
 */
export function buildOnboardingObjectsTemplate(): string {
  const headers = ONBOARDING_COLUMNS.map((c) => c.key);
  const availableSourceSystems = [...DATABASE_CONNECTORS, ...FILE_CONNECTORS, ...SERVICE_CONNECTORS].sort();
  const lines = [
    "# Onboarding registry template — one row per source object → target mapping.",
    "# Required columns: source_system, source_type, source_object, target_type, target_file_format, loading_behavior.",
    "# source_system is a free-text label (not validated against a fixed list) — but it should identify one of",
    "#   the connector types your Connections registry supports, so the object can be wired to a real linked",
    `#   service later. Available source systems (${availableSourceSystems.length}): ${availableSourceSystems.join(", ")}.`,
    "# object_key (optional): defaults to \"source_system.source_object\" when left blank; must stay unique.",
    "# source_type / target_type: table | view | query | file (target_type: table | file).",
    "# loading_behavior: FullLoad or DeltaLoad — DeltaLoad requires watermark_column + watermark_column_type.",
    "# write_behavior in (upsert, merge) requires upsert_keys (pipe-separated, e.g. \"id|updated_at\").",
    "# Re-uploading this file after editing only adds new object_keys and updates matching ones — it never",
    "#   removes rows already in your registry that aren't in the file.",
    "# Delete these sample rows before uploading, or leave them and edit in place.",
    csvRow(headers),
    csvRow(ONBOARDING_COLUMNS.map((c) => c.sample[0])),
    csvRow(ONBOARDING_COLUMNS.map((c) => c.sample[1])),
    csvRow(ONBOARDING_COLUMNS.map((c) => c.sample[2])),
  ];
  return lines.join("\r\n") + "\r\n";
}

/**
 * §26.5 `objects.csv` snapshot — the registry's current rows in the exact template column order, so
 * the export re-uploads cleanly (`parseOnboardingObjectsFile` round-trips it). Booleans are written as
 * TRUE/FALSE like the template; empty/null values are blank cells.
 */
export function buildOnboardingObjectsCsv(entries: OnboardingObjectEntry[]): string {
  const headers = ONBOARDING_COLUMNS.map((c) => c.key);
  const cell = (value: unknown): string =>
    value === null || value === undefined ? "" : typeof value === "boolean" ? (value ? "TRUE" : "FALSE") : String(value);
  const lines = [csvRow(headers), ...entries.map((entry) => csvRow(headers.map((key) => cell(entry[key]))))];
  return lines.join("\r\n") + "\r\n";
}

// =============================================================================
// Parser
// =============================================================================

export interface ParsedOnboardingObjects {
  objects: OnboardingObjectEntry[];
  warnings: string[];
  /**
   * §26.3 Map step — header cells that don't match any registry column or synonym (after any
   * `headerMap` was applied). The UI lets the user map each to a column and re-parses.
   */
  unmappedHeaders: string[];
  /** `.xlsx` only: the first sheet as TSV, so a Map-step re-parse doesn't need the binary again. */
  sourceText?: string;
}

/** §26.3 Map step — raw header cell (as it appears in the file) → `ONBOARDING_COLUMNS` key. */
export type OnboardingHeaderMap = Record<string, string>;

function isRecognizedHeader(cell: string): boolean {
  return ONBOARDING_COLUMNS.some((column) => synonymsFor(column).includes(normalizeHeader(cell)));
}

function looksLikeOnboardingHeader(cells: string[]): boolean {
  return cells.some(isRecognizedHeader);
}

function applyHeaderMap(cells: string[], headerMap: OnboardingHeaderMap): string[] {
  return cells.map((cell) => headerMap[cell.trim()] || cell);
}

function entryFromOnboardingRecord(record: Record<string, string>): OnboardingObjectEntry {
  const lowered: Record<string, string> = {};
  for (const [key, value] of Object.entries(record)) lowered[normalizeHeader(key)] = value.trim();

  const values: Partial<Record<string, string>> = {};
  for (const column of ONBOARDING_COLUMNS) {
    const matchKey = synonymsFor(column).find((syn) => lowered[syn]);
    if (matchKey) values[column.key] = lowered[matchKey];
  }

  const sourceSystem = values.source_system ?? "";
  const sourceObject = values.source_object ?? "";
  const objectKey = values.object_key || (sourceSystem && sourceObject ? `${sourceSystem}.${sourceObject}` : sourceObject);

  return {
    object_key: objectKey,
    enabled: values.enabled ? !/^(false|0|no)$/i.test(values.enabled) : true,
    batch_group: values.batch_group || undefined,
    owner: values.owner || undefined,
    data_classification: (values.data_classification?.toLowerCase() as OnboardingObjectEntry["data_classification"]) || undefined,
    source_system: sourceSystem,
    source_type: (values.source_type as OnboardingObjectEntry["source_type"]) || "table",
    source_schema: values.source_schema || undefined,
    source_object: sourceObject,
    source_query: values.source_query || undefined,
    target_type: (values.target_type as OnboardingObjectEntry["target_type"]) || "table",
    target_schema: values.target_schema || undefined,
    target_object: values.target_object || undefined,
    target_path: values.target_path || undefined,
    target_file_name: values.target_file_name || undefined,
    target_file_format: values.target_file_format || "Parquet",
    write_behavior: (values.write_behavior as OnboardingObjectEntry["write_behavior"]) || undefined,
    upsert_keys: values.upsert_keys || undefined,
    loading_behavior: (values.loading_behavior as OnboardingObjectEntry["loading_behavior"]) || "FullLoad",
    watermark_column: values.watermark_column || undefined,
    watermark_column_type: (values.watermark_column_type as OnboardingObjectEntry["watermark_column_type"]) || undefined,
    notes: values.notes || undefined,
    status: "draft",
  };
}

/**
 * §26.3 `.xlsx` upload: reads the first worksheet and parses it exactly like a
 * TSV (same header-row contract as CSV). Tabs/newlines inside a cell collapse to
 * spaces so they can't split a row. Dynamically imports `exceljs`, same reasoning
 * as `sttm.ts` — keeps it out of the main bundle.
 */
export async function parseOnboardingObjectsWorkbook(
  data: ArrayBuffer,
  fileName: string,
  headerMap: OnboardingHeaderMap = {},
): Promise<ParsedOnboardingObjects> {
  try {
    const ExcelJS = (await import("exceljs")).default;
    const wb = new ExcelJS.Workbook();
    await wb.xlsx.load(data);
    const ws = wb.worksheets[0];
    if (!ws) return { objects: [], warnings: [`${fileName} has no worksheets.`], unmappedHeaders: [] };
    const lines: string[] = [];
    ws.eachRow({ includeEmpty: false }, (row) => {
      const cells: string[] = [];
      for (let c = 1; c <= ws.columnCount; c++) cells.push(workbookCellText(row.getCell(c).value));
      if (cells.some((v) => v)) lines.push(cells.join("\t"));
    });
    const sourceText = lines.join("\n");
    const parsed = { ...parseOnboardingObjectsFile(sourceText, fileName.replace(/\.xlsx$/i, ".tsv"), headerMap), sourceText };
    if (wb.worksheets.length > 1) {
      parsed.warnings.push(`Only the first worksheet ("${ws.name}") was read; ${wb.worksheets.length - 1} other sheet(s) ignored.`);
    }
    return parsed;
  } catch (error) {
    return {
      objects: [],
      warnings: [`Could not read ${fileName}: ${error instanceof Error ? error.message : String(error)}`],
      unmappedHeaders: [],
    };
  }
}

function workbookCellText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "object" && "text" in value) return workbookCellText((value as { text: unknown }).text);
  if (typeof value === "object" && "result" in value) return workbookCellText((value as { result: unknown }).result);
  if (typeof value === "object" && "richText" in value) {
    return ((value as { richText: Array<{ text: string }> }).richText ?? []).map((r) => r.text).join("");
  }
  return String(value).replace(/[\t\r\n]+/g, " ").trim();
}

/**
 * Parse an uploaded onboarding-registry file. Unlike the per-connection
 * object-list parser (`connections.ts`'s `parseTablesFile`), this format
 * always requires a header row — there are too many optional columns for a
 * positional fallback to be meaningful. CSV/TSV (any of , ; | or tab) or a
 * JSON array of objects.
 */
export function parseOnboardingObjectsFile(
  content: string,
  fileName: string,
  headerMap: OnboardingHeaderMap = {},
): ParsedOnboardingObjects {
  const warnings: string[] = [];
  const unmapped: string[] = [];
  try {
    const objects = fileName.toLowerCase().endsWith(".json")
      ? parseOnboardingJson(content, warnings, headerMap, unmapped)
      : parseOnboardingDelimited(content, warnings, headerMap, unmapped);
    const seen = new Set<string>();
    const unique: OnboardingObjectEntry[] = [];
    for (const entry of objects) {
      if (seen.has(entry.object_key)) {
        warnings.push(`Duplicate object_key "${entry.object_key}" — kept first occurrence in this file.`);
        continue;
      }
      seen.add(entry.object_key);
      unique.push(entry);
    }
    if (!unique.length && !warnings.length) warnings.push("No usable object rows were found in the file.");
    return { objects: unique, warnings, unmappedHeaders: [...new Set(unmapped)] };
  } catch (error) {
    return {
      objects: [],
      warnings: [`Could not parse ${fileName}: ${error instanceof Error ? error.message : "unknown error"}.`],
      unmappedHeaders: [],
    };
  }
}

function parseOnboardingDelimited(
  content: string,
  warnings: string[],
  headerMap: OnboardingHeaderMap,
  unmapped: string[],
): OnboardingObjectEntry[] {
  const lines = content.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  let header: string[] | null = null;
  const objects: OnboardingObjectEntry[] = [];
  lines.forEach((line, index) => {
    if (line.startsWith("#")) return;
    const cells = splitDelimited(line);
    if (!header) {
      const mapped = applyHeaderMap(cells, headerMap);
      if (!looksLikeOnboardingHeader(mapped)) {
        warnings.push(`Line ${index + 1}: expected a header row with recognized columns (e.g. "source_object") — found none.`);
        // First candidate header only — offer its cells for mapping instead of every data row's.
        if (!unmapped.length) unmapped.push(...cells.filter(Boolean));
        return;
      }
      header = mapped;
      unmapped.length = 0;
      unmapped.push(...cells.filter((cell, i) => cell && !isRecognizedHeader(mapped[i])));
      return;
    }
    const record = Object.fromEntries(header.map((key, i) => [key, cells[i] ?? ""]));
    const entry = entryFromOnboardingRecord(record);
    if (!entry.source_object) {
      warnings.push(`Line ${index + 1}: empty source_object — skipped.`);
      return;
    }
    objects.push(entry);
  });
  return objects;
}

function parseOnboardingJson(
  content: string,
  warnings: string[],
  headerMap: OnboardingHeaderMap,
  unmapped: string[],
): OnboardingObjectEntry[] {
  const parsed: unknown = JSON.parse(content);
  if (!Array.isArray(parsed)) throw new Error("JSON input must be an array of objects.");
  const objects: OnboardingObjectEntry[] = [];
  parsed.forEach((item, index) => {
    if (!item || typeof item !== "object") {
      warnings.push(`JSON row ${index + 1}: not an object — skipped.`);
      return;
    }
    const record: Record<string, string> = {};
    for (const [key, value] of Object.entries(item as Record<string, unknown>)) {
      const mappedKey = headerMap[key.trim()] || key;
      if (!isRecognizedHeader(mappedKey)) unmapped.push(key);
      record[mappedKey] = String(value ?? "");
    }
    const entry = entryFromOnboardingRecord(record);
    if (!entry.source_object) {
      warnings.push(`JSON row ${index + 1}: empty source_object — skipped.`);
      return;
    }
    objects.push(entry);
  });
  return objects;
}

// =============================================================================
// Validation (§26.4 rules that don't need a backend)
// =============================================================================

/** Validate one onboarding row. Mirrors §26.4 items 1–3 and the ONB-01/03/05
 * edge cases in `backend/tests/test_scenario_onboarding.py` (duplicate
 * object_key is checked across the whole registry by the caller, not here —
 * this only validates what's knowable from a single row). */
export function validateOnboardingRow(entry: OnboardingObjectEntry, field = "onboarding"): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const label = entry.object_key || entry.source_object || "(unnamed object)";

  for (const key of REQUIRED_KEYS) {
    if (!entry[key]) {
      const column = ONBOARDING_COLUMNS.find((c) => c.key === key)!;
      issues.push({ severity: "error", message: `"${label}": ${column.key} is required.`, field });
    }
  }
  if (entry.data_classification && !DATA_CLASSIFICATIONS.includes(entry.data_classification)) {
    issues.push({
      severity: "error",
      message: `"${label}": data_classification must be one of ${DATA_CLASSIFICATIONS.join(", ")}.`,
      field,
    });
  }
  if (entry.loading_behavior === "DeltaLoad" && (!entry.watermark_column || !entry.watermark_column_type)) {
    issues.push({
      severity: "error",
      message: `"${label}": DeltaLoad requires both watermark_column and watermark_column_type.`,
      field,
    });
  }
  if ((entry.write_behavior === "upsert" || entry.write_behavior === "merge") && !entry.upsert_keys) {
    issues.push({
      severity: "error",
      message: `"${label}": write_behavior "${entry.write_behavior}" requires upsert_keys.`,
      field,
    });
  }
  return issues;
}

/** Duplicate-`object_key` check across an entire registry (§26.4 item 1 / ONB-01) — a bulk import
 * already dedupes within itself (`parseOnboardingObjectsFile`); this also catches a new row colliding
 * with one already in the grid. */
export function findDuplicateObjectKeys(entries: OnboardingObjectEntry[]): string[] {
  const seen = new Map<string, number>();
  for (const entry of entries) seen.set(entry.object_key, (seen.get(entry.object_key) ?? 0) + 1);
  return [...seen.entries()].filter(([, count]) => count > 1).map(([key]) => key);
}

// =============================================================================
// §26.5 "Generate control-table artifacts" — control.adf_control_table DDL + seed
// =============================================================================

const DEFAULT_ONBOARDING_CONTROL_TABLE = "control.adf_control_table";

function sqlEscape(value: string): string {
  return value.replace(/'/g, "''");
}

/** The physical source identifier a control-table row should carry — schema-qualified when the
 *  registry row has one (table/view/query sources), the bare object otherwise (file sources). */
function onboardingSourceRef(entry: OnboardingObjectEntry): string {
  const object = entry.source_object.trim();
  const schema = entry.source_schema?.trim();
  return schema ? `${schema}.${object}` : object;
}

/** The physical target identifier — `target_path` for a file target (`target_object`/`target_schema`
 *  are table-target-only fields), else the schema-qualified table name. */
function onboardingTargetRef(entry: OnboardingObjectEntry): string {
  if (entry.target_type === "file") return (entry.target_path ?? "").trim();
  const object = (entry.target_object ?? entry.source_object).trim();
  const schema = entry.target_schema?.trim();
  return schema ? `${schema}.${object}` : object;
}

/**
 * §26.5 "Generate control-table artifacts": `adf_control_table.sql` (DDL + one seed row per enabled
 * registry object) built directly from the Onboarding object registry — independent of any pipeline
 * draft, unlike `pipeline-codegen.ts`'s `controlTableSql` (which seeds from a single pipeline's own
 * `sourceObjects` list). Same table shape (`docs/DDL.md` §2) so a pipeline generated against this table
 * via Build's control-table field reads the exact rows this produced. Column mapping is
 * `ONBOARDING_COLUMNS[].controlTableColumn`, documented alongside each column above.
 */
export function buildOnboardingControlTableSql(entries: OnboardingObjectEntry[], tableName?: string): string {
  const table = tableName?.trim() || DEFAULT_ONBOARDING_CONTROL_TABLE;
  const enabled = entries.filter((e) => e.enabled !== false);
  const lines: string[] = [
    "-- Control table generated from the Onboarding registry (AGENTS.md §26.5).",
    "-- One row per enabled object. Re-generate after editing the registry to keep this file in sync —",
    "-- this script does not track which rows it previously seeded, so re-running it after disabling an",
    "-- object will not retract that object's earlier row; disable it in the target database directly too.",
    "",
    ...createTableIfMissing(table),
    "  TaskId            INT IDENTITY(1,1) PRIMARY KEY,",
    "  SourceType        NVARCHAR(128)  NOT NULL,",
    "  Source            NVARCHAR(512)  NOT NULL,",
    "  SourceQuery       NVARCHAR(MAX)  NULL,",
    "  TargetType        NVARCHAR(128)  NOT NULL,",
    "  Target            NVARCHAR(512)  NOT NULL,",
    "  TargetFileName    NVARCHAR(512)  NULL,",
    "  LoadingBehavior   NVARCHAR(16)   NOT NULL DEFAULT 'DeltaLoad', -- FullLoad | DeltaLoad",
    "  WatermarkColumn   NVARCHAR(128)  NULL,",
    "  WatermarkType     NVARCHAR(16)   NULL,",
    "  WatermarkValue    NVARCHAR(256)  NULL,",
    "  Enabled           BIT            NOT NULL DEFAULT 1,",
    "  LastRunTimestamp  DATETIME2      NULL,",
    "  CreatedAt         DATETIME2      NOT NULL DEFAULT GETDATE()",
    ");",
    "",
    `-- Seed rows from the onboarding registry (${enabled.length} enabled object${enabled.length === 1 ? "" : "s"} of ${entries.length} total).`,
  ];
  for (const entry of enabled) {
    lines.push(
      `INSERT INTO ${table} (`,
      "  SourceType, Source, SourceQuery, TargetType, Target, TargetFileName,",
      "  LoadingBehavior, WatermarkColumn, WatermarkType, WatermarkValue, Enabled",
      ") VALUES (",
      `  N'${sqlEscape(entry.source_type)}', N'${sqlEscape(onboardingSourceRef(entry))}', N'${sqlEscape(entry.source_query ?? "")}',`,
      `  N'${sqlEscape(entry.target_type)}', N'${sqlEscape(onboardingTargetRef(entry))}', N'${sqlEscape(entry.target_file_name ?? "")}',`,
      `  N'${sqlEscape(entry.loading_behavior)}', N'${sqlEscape(entry.watermark_column ?? "")}', N'${sqlEscape(entry.watermark_column_type ?? "")}', N'', 1`,
      ");",
      "",
    );
  }
  return lines.join("\n");
}

/** A blank row for the "Add object" drawer — `object_key` is left empty so the
 * backend derives `source_system.source_object` on save (§26.2). */
export function newOnboardingObject(): OnboardingObjectEntry {
  return {
    object_key: "",
    enabled: true,
    source_system: "",
    source_type: "table",
    source_object: "",
    target_type: "table",
    target_file_format: "Parquet",
    loading_behavior: "FullLoad",
    status: "draft",
  };
}

type PipelineDqRule = SpecDraft["dataQualityRules"][number];

/** Same 5-rule cap `validateDataQualityRules` enforces on a pipeline. */
export const MAX_PIPELINE_DQ_RULES = 5;

/**
 * Maps registry-authored `app.dq_rules` rows (§30.5) onto the pipeline-level `dataQualityRules`
 * shape the generator already emits as Lookup/Fail activities (§30.4) — the AQ2 "authored on the
 * object, generated on the pipeline" bridge. `row_count` → ROW_COUNT_MIN (whole object) and
 * `freshness` → FRESHNESS_HOURS (needs a timestamp column); `off` rules, and rules missing the
 * column/threshold they need, come back in `skipped` rather than being approximated.
 */
export function dqRulesToPipelineRules(rules: OnboardingDqRule[]): { rules: PipelineDqRule[]; skipped: OnboardingDqRule[] } {
  const out: PipelineDqRule[] = [];
  const skipped: OnboardingDqRule[] = [];
  for (const r of rules) {
    const column = (r.config.column ?? "").trim();
    const enforcement = r.enforcement === "block" ? "block" : "warn";
    const name = `${r.objectKey.split(".").pop() ?? r.objectKey}_${column}_${r.checkType}`.replace(/[^A-Za-z0-9_]/g, "_");
    const threshold = (r.config.threshold ?? "").trim();
    if (r.checkType === "row_count" && r.enforcement !== "off" && threshold) {
      out.push({ name: `${r.objectKey.split(".").pop() ?? r.objectKey}_row_count`.replace(/[^A-Za-z0-9_]/g, "_"), column: "", rule: "ROW_COUNT_MIN", value: threshold, enforcement });
    } else if (r.enforcement === "off" || !column) {
      skipped.push(r);
    } else if (r.checkType === "not_null") {
      out.push({ name, column, rule: "IS_NOT_NULL", enforcement });
    } else if (r.checkType === "unique") {
      out.push({ name, column, rule: "UNIQUE", enforcement });
    } else if (r.checkType === "freshness" && threshold) {
      out.push({ name, column, rule: "FRESHNESS_HOURS", value: threshold, enforcement });
    } else if (r.checkType === "range") {
      out.push({ name, column, rule: "RANGE_BETWEEN", value: `${(r.config.min ?? "").trim()},${(r.config.max ?? "").trim()}`, enforcement });
    } else {
      skipped.push(r);
    }
  }
  return { rules: out, skipped };
}

/**
 * The pipeline-level settings the picked Bronze onboarding rows carry (write behavior, upsert keys,
 * target folder/file/table) — the Build picker used to drop them. A setting is applied only when every
 * picked row that states it agrees; a per-object target (table, file name) only when one row is picked,
 * since one pipeline writes through one target dataset. Disagreements come back as `notes`.
 */
export function onboardingPipelineSettings(
  picked: OnboardingObjectEntry[],
  prev: SpecDraft,
): { patch: Partial<SpecDraft>; notes: string[] } {
  const patch: Partial<SpecDraft> = {};
  const notes: string[] = [];
  const agreed = (values: (string | undefined)[], label: string): string | undefined => {
    const set = new Set(values.map((v) => (v ?? "").trim()).filter(Boolean));
    if (set.size > 1) notes.push(`The picked objects disagree on ${label} — kept the form's value.`);
    return set.size === 1 ? [...set][0] : undefined;
  };
  const write = agreed(picked.map((o) => o.write_behavior), "write behavior");
  if (write) {
    patch.writeBehavior = write === "upsert" || write === "merge" ? "Upsert" : "Insert";
    if (write === "overwrite") patch.truncateTarget = true;
  }
  const keys = agreed(picked.map((o) => o.upsert_keys), "upsert keys");
  if (keys) patch.upsertKeyColumns = keys.split("|").map((k) => k.trim()).filter(Boolean).join(", ");
  const path = agreed(picked.map((o) => (o.target_type === "file" ? o.target_path : undefined)), "target path");
  if (path) patch.targetPath = path;
  if (picked.length === 1) {
    const [o] = picked;
    if (o.target_type === "file" && o.target_file_name?.trim()) patch.targetFileName = o.target_file_name.trim();
    if (o.target_type === "table" && (o.target_schema?.trim() || o.target_object?.trim())) {
      patch.targetDataset = {
        ...prev.targetDataset,
        schema: o.target_schema?.trim() || prev.targetDataset.schema,
        table: o.target_object?.trim() || prev.targetDataset.table,
      };
    }
  } else if (picked.some((o) => o.target_object?.trim() || o.target_file_name?.trim())) {
    notes.push("Per-object target tables/file names weren't applied — several objects share one target dataset.");
  }
  return { patch, notes };
}

/**
 * Registry-wide lineage (the cross-pipeline half of the "No lineage export" gap): every enabled
 * Bronze onboarding object's source → Bronze target edge, plus each Silver/Gold registry object's
 * Bronze-object-keys → curated/mart table edge. Same OpenLineage `namespace`/`name` dataset naming as
 * the per-pipeline `lineage.json` (`pipeline-codegen.ts::lineageDocument`); static, no run facets.
 * Silver/Gold inputs are resolved to the referenced Bronze object's target when it's in the
 * registry, otherwise kept as the raw object key so a dangling reference stays visible.
 */
export function buildRegistryLineage(
  bronze: OnboardingObjectEntry[],
  silver: RegistryObject[],
  gold: RegistryObject[],
): unknown {
  const bronzeTarget = (o: OnboardingObjectEntry) => ({
    namespace: "bronze://registry",
    name:
      o.target_type === "file"
        ? [o.target_path, o.target_file_name].filter(Boolean).join("/")
        : [o.target_schema, o.target_object].filter(Boolean).join("."),
  });
  const byKey = new Map(bronze.map((o) => [o.object_key, o]));
  const jobs: unknown[] = bronze
    .filter((o) => o.enabled)
    .map((o) => ({
      job: { namespace: "adf-registry", name: `bronze.${o.object_key}` },
      medallionLayer: "bronze",
      inputs: [
        { namespace: `source://${o.source_system}`, name: [o.source_schema, o.source_object].filter(Boolean).join(".") },
      ],
      outputs: [bronzeTarget(o)],
    }));
  for (const [layer, objs] of [["silver", silver], ["gold", gold]] as const) {
    for (const o of objs.filter((x) => x.enabled)) {
      jobs.push({
        job: { namespace: "adf-registry", name: `${layer}.${o.objectKey}` },
        medallionLayer: layer,
        inputs: o.sourceObjectKeys.map((key) => {
          const src = byKey.get(key);
          return src ? bronzeTarget(src) : { namespace: "unresolved://onboarding", name: key };
        }),
        outputs: [{ namespace: `${layer}://registry`, name: o.targetTable }],
      });
    }
  }
  return { lineageVersion: "1.0", producer: "adf-accelerator", scope: "registry", jobs };
}
