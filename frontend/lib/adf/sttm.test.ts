import { describe, expect, it } from "vitest";
import {
  DQ_DATA_START_ROW,
  HEADER_FIELDS,
  HEADER_START_ROW,
  MAX_SHEETS_PER_WORKBOOK,
  TRANSFORM_DATA_START_ROW,
  buildSttmTemplate,
  checkDqColumnsAgainstTransformations,
  parseSttmWorkbook,
  validateSttmObjectHeader,
} from "./sttm";

// Coverage for the §33 STTM engine: the generated template must round-trip
// through its own preview parser with no errors on the worked example sheet
// (a template the app can't re-import is worse than no template — same bar
// as `onboarding.test.ts`), and the header validator must match the backend
// parser's rule set (`backend/tests/test_sttm_parser.py` tests the same
// cases server-side).

describe("buildSttmTemplate + parseSttmWorkbook round trip", () => {
  it("silver: the worked EXAMPLE_ sheet parses back valid", async () => {
    const buffer = await buildSttmTemplate("silver");
    const report = await parseSttmWorkbook("silver", buffer);
    // Instructions/Available_Transformations/EXAMPLE_* are reserved except
    // the sheet actually meant to be duplicated — but EXAMPLE_* itself is
    // reserved too (never treated as a real object), so only NEW_OBJECT_1
    // (blank) should show up as a sheet, and it should fail with missing
    // required fields rather than throwing.
    expect(report.sheetTotal).toBe(1);
    expect(report.sheets[0].sheetName).toBe("NEW_OBJECT_1");
    expect(report.sheets[0].valid).toBe(false);
    expect(report.sheets[0].issues.some((i) => i.field === "tag")).toBe(true);
  });

  it("gold: the worked EXAMPLE_ sheet is reserved, NEW_OBJECT_1 needs mart_domain", async () => {
    const buffer = await buildSttmTemplate("mart");
    const report = await parseSttmWorkbook("mart", buffer);
    expect(report.sheetTotal).toBe(1);
    expect(report.sheets[0].issues.some((i) => i.field === "martDomain")).toBe(true);
  });

  it("a renamed, filled-in sheet parses back valid for silver (incl. the §33.3 join widening)", async () => {
    const buffer = await buildSttmTemplate("silver");
    const ExcelJS = (await import("exceljs")).default;
    const wb = new ExcelJS.Workbook();
    await wb.xlsx.load(buffer);
    const blank = wb.getWorksheet("NEW_OBJECT_1")!;
    blank.name = "gl_transaction_enriched";
    HEADER_FIELDS.forEach((field, i) => {
      const values: Record<string, string> = {
        object_key: "gl_transaction_enriched",
        tag: "finance",
        curated_entity: "gl_transaction_enriched",
        source_object_keys: "sql_server.dbo.gl_transaction",
        write_behavior: "append",
        loading_behavior: "FullLoad",
      };
      if (values[field]) blank.getCell(HEADER_START_ROW + i, 2).value = values[field];
    });
    blank.getCell(TRANSFORM_DATA_START_ROW, 1).value = "join_cc";
    blank.getCell(TRANSFORM_DATA_START_ROW, 2).value = "join";
    blank.getCell(TRANSFORM_DATA_START_ROW, 7).value = "raw.cost_center";
    blank.getCell(TRANSFORM_DATA_START_ROW, 9).value = "a.cc_id = b.cc_id";
    blank.getCell(DQ_DATA_START_ROW, 1).value = "not_null_amount";
    blank.getCell(DQ_DATA_START_ROW, 2).value = "amount";
    blank.getCell(DQ_DATA_START_ROW, 3).value = "IS_NOT_NULL";

    const reBuffer = await wb.xlsx.writeBuffer();
    const report = await parseSttmWorkbook("silver", reBuffer as ArrayBuffer);
    const sheet = report.sheets.find((s) => s.sheetName === "gl_transaction_enriched");
    expect(sheet?.valid, JSON.stringify(sheet?.issues)).toBe(true);
    expect(sheet?.parsed?.transformations?.[0].type).toBe("join");
    expect(sheet?.parsed?.dataQualityRules?.[0].rule).toBe("IS_NOT_NULL");
  });
});

describe("checkDqColumnsAgainstTransformations (STTM-08, mirrors backend's check_dq_columns_against_transformations)", () => {
  it("warns when a DQ rule's column doesn't appear in any transformation's output columns", () => {
    const issues = checkDqColumnsAgainstTransformations(
      [{ name: "conform", type: "conform_columns", columns: "TransactionId:transaction_id" }],
      [{ name: "not_null_amount", column: "amount", rule: "IS_NOT_NULL" }],
    );
    expect(issues).toHaveLength(1);
    expect(issues[0].severity).toBe("warning");
    expect(issues[0].message).toContain("amount");
  });

  it("does not warn when the DQ rule's column matches a transformation's output column", () => {
    const issues = checkDqColumnsAgainstTransformations(
      [{ name: "conform", type: "conform_columns", columns: "TransactionId:transaction_id" }],
      [{ name: "not_null_txn", column: "transaction_id", rule: "IS_NOT_NULL" }],
    );
    expect(issues).toEqual([]);
  });

  it("never warns when no transformation names any output columns (dedupe/scd2/join only)", () => {
    const issues = checkDqColumnsAgainstTransformations(
      [{ name: "dedupe_gl", type: "dedupe", keyColumns: "transaction_id", orderColumn: "modified_date" }],
      [{ name: "not_null_amount", column: "amount", rule: "IS_NOT_NULL" }],
    );
    expect(issues).toEqual([]);
  });

  it("checks aggregate GROUP BY columns and AS aliases as valid output columns", () => {
    const issues = checkDqColumnsAgainstTransformations(
      [{ name: "agg_gl", type: "aggregate", groupByColumns: "customer_id", aggregateExpression: "SUM(amount) AS total_amount" }],
      [
        { name: "not_null_customer", column: "customer_id", rule: "IS_NOT_NULL" },
        { name: "not_null_total", column: "total_amount", rule: "IS_NOT_NULL" },
        { name: "not_null_bogus", column: "not_a_real_column", rule: "IS_NOT_NULL" },
      ],
    );
    expect(issues).toHaveLength(1);
    expect(issues[0].message).toContain("not_a_real_column");
  });
});

describe("parseSttmWorkbook sheet-count guardrail (STTM-18)", () => {
  it("rejects a workbook exceeding MAX_SHEETS_PER_WORKBOOK instead of silently truncating", async () => {
    const ExcelJS = (await import("exceljs")).default;
    const wb = new ExcelJS.Workbook();
    for (let i = 0; i < MAX_SHEETS_PER_WORKBOOK + 1; i++) wb.addWorksheet(`obj_${i}`);
    const buffer = (await wb.xlsx.writeBuffer()) as ArrayBuffer;
    await expect(parseSttmWorkbook("silver", buffer)).rejects.toThrow(/exceeding the .*-sheet-per-workbook limit/);
  });

  it("accepts a workbook at exactly the limit", async () => {
    const ExcelJS = (await import("exceljs")).default;
    const wb = new ExcelJS.Workbook();
    for (let i = 0; i < MAX_SHEETS_PER_WORKBOOK; i++) wb.addWorksheet(`obj_${i}`);
    const buffer = (await wb.xlsx.writeBuffer()) as ArrayBuffer;
    const report = await parseSttmWorkbook("silver", buffer);
    expect(report.sheetTotal).toBe(MAX_SHEETS_PER_WORKBOOK);
  });
});

describe("validateSttmObjectHeader", () => {
  it("requires tag and curated_entity", () => {
    const issues = validateSttmObjectHeader("silver", {});
    expect(issues.some((i) => i.field === "tag")).toBe(true);
    expect(issues.some((i) => i.field === "curatedEntity")).toBe(true);
  });

  it("mart requires martDomain too", () => {
    const issues = validateSttmObjectHeader("mart", { tag: "x", curatedEntity: "gl_fact", sourceObjectKeys: ["a"] });
    expect(issues.some((i) => i.field === "martDomain")).toBe(true);
  });

  it("DeltaLoad requires watermark column + type", () => {
    const issues = validateSttmObjectHeader("silver", {
      tag: "x",
      curatedEntity: "e",
      sourceObjectKeys: ["a"],
      loadingBehavior: "DeltaLoad",
    });
    expect(issues.some((i) => i.field === "watermarkColumn")).toBe(true);
  });

  it("upsert/merge requires business keys", () => {
    const issues = validateSttmObjectHeader("silver", {
      tag: "x",
      curatedEntity: "e",
      sourceObjectKeys: ["a"],
      writeBehavior: "upsert",
    });
    expect(issues.some((i) => i.field === "businessKeys")).toBe(true);
  });

  it("accepts a fully-specified header", () => {
    expect(
      validateSttmObjectHeader("silver", { tag: "finance", curatedEntity: "gl_transaction", sourceObjectKeys: ["a"] }),
    ).toEqual([]);
  });
});
