/**
 * gaps.md DMT-11 (preview half) — reads a Data Modular `STTM_Data_Modular_Template_v2b.xlsx` in the
 * browser so the upload panel can show what the workbook holds before the server round-trip.
 *
 * This is a read-only view of the template's own cells (Section 1 attributes, Section 2 mapping
 * rows). It does NOT translate them into registry transformations/DQ rules — that stays the
 * server's authoritative job (`backend/app/onboarding/sttm_data_modular.py`, §33.9 "server wins"),
 * so there is no second translator to drift. The layout constants mirror that module's
 * `TEMPLATE_SHEET`/`RECORD_TYPE_HEADER`/`_SECTION1_FIRST_ROW`/`_HEADER_SCAN_LIMIT`.
 */
import type { Worksheet } from "exceljs";

export const DATA_MODULAR_TEMPLATE_SHEET = "STTM_Template";
const RECORD_TYPE_HEADER = "Record_Type";
const SECTION1_FIRST_ROW = 3;
const HEADER_SCAN_LIMIT = 200;

export interface DataModularPreviewRow {
  /** Worksheet row number, for matching the server's "Row N:" issue messages. */
  row: number;
  recordType: string;
  targetColumn: string;
  sourceObject: string;
  sourceColumn: string;
  transformationType: string;
  dqRuleTypes: string;
}

export interface DataModularPreview {
  tableName: string;
  layer: string;
  /** Section 1's filled `Attribute | Value` pairs, in sheet order. */
  attributes: Array<{ name: string; value: string }>;
  rows: DataModularPreviewRow[];
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") {
    const v = value as { text?: unknown; result?: unknown; richText?: Array<{ text: string }> };
    if (Array.isArray(v.richText)) return v.richText.map((r) => r.text).join("").trim();
    if (v.text !== undefined) return String(v.text).trim();
    if (v.result !== undefined) return String(v.result).trim();
    if (value instanceof Date) return value.toISOString().slice(0, 10);
  }
  return String(value).trim();
}

function headerRow(ws: Worksheet): number | null {
  for (let r = 1; r <= HEADER_SCAN_LIMIT; r += 1) {
    if (cellText(ws.getCell(r, 1).value) === RECORD_TYPE_HEADER) return r;
  }
  return null;
}

/** Section 1 + Section 2 of one template sheet; null when `ws` isn't a Data Modular template sheet. */
export function readDataModularSheet(ws: Worksheet): DataModularPreview | null {
  if (ws.name !== DATA_MODULAR_TEMPLATE_SHEET) return null;
  const header = headerRow(ws);
  if (header === null) return null;
  const attributes: DataModularPreview["attributes"] = [];
  for (let r = SECTION1_FIRST_ROW; r < header; r += 1) {
    const name = cellText(ws.getCell(r, 1).value);
    const value = cellText(ws.getCell(r, 2).value);
    if (name && value && !name.toUpperCase().startsWith("SECTION")) attributes.push({ name, value });
  }
  const headers: string[] = [];
  ws.getRow(header).eachCell({ includeEmpty: true }, (cell, col) => {
    headers[col] = cellText(cell.value);
  });
  const rows: DataModularPreviewRow[] = [];
  for (let r = header + 1; r <= ws.rowCount; r += 1) {
    const values: Record<string, string> = {};
    ws.getRow(r).eachCell((cell, col) => {
      const key = headers[col];
      const text = cellText(cell.value);
      if (key && text) values[key] = text;
    });
    if (!Object.keys(values).length) continue;
    rows.push({
      row: r,
      recordType: values.Record_Type ?? "",
      targetColumn: values.Target_Column_Name ?? "",
      sourceObject: values.Source_Object ?? "",
      sourceColumn: values.Source_Column ?? "",
      transformationType: values.Transformation_Type ?? "",
      dqRuleTypes: values.DQ_Rule_Type ?? "",
    });
  }
  const attr = (name: string) => attributes.find((a) => a.name === name)?.value ?? "";
  return { tableName: attr("Target_Table_Name") || attr("Target_Table_ID"), layer: attr("Target_Layer"), attributes, rows };
}

/** Reads an `.xlsx` in the browser; null when it holds no Data Modular template sheet (legacy layout). */
export async function readDataModularWorkbook(data: ArrayBuffer): Promise<DataModularPreview | null> {
  const ExcelJS = (await import("exceljs")).default;
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(data);
  const ws = workbook.getWorksheet(DATA_MODULAR_TEMPLATE_SHEET);
  return ws ? readDataModularSheet(ws) : null;
}
