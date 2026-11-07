import { describe, expect, it } from "vitest";
import { emptySpecDraft } from "./spec";
import {
  ONBOARDING_COLUMNS,
  ONBOARDING_DDL_COLUMNS,
  buildOnboardingControlTableSql,
  buildRegistryLineage,
  dqRulesToPipelineRules,
  buildOnboardingObjectsTemplate,
  buildOnboardingObjectsCsv,
  findDuplicateObjectKeys,
  newOnboardingObject,
  onboardingPipelineSettings,
  parseOnboardingObjectsFile,
  parseOnboardingObjectsWorkbook,
  validateOnboardingRow,
} from "./onboarding";
import type { OnboardingObjectEntry, RegistryObject } from "@/types";

// Coverage for the §26 Onboarding registry template/parser/validator: the
// template must round-trip through its own parser (a template the app can't
// re-import is worse than no template), every header must be a real
// `app.onboarding_objects` DDL column (docs/DDL.md) so the two never drift
// apart silently, and the validator must catch the specific edge cases
// `backend/tests/test_scenario_onboarding.py` stubs out (ONB-01/03/05).

describe("buildOnboardingObjectsTemplate + parseOnboardingObjectsFile round trip", () => {
  it("produces a template that parses back with no warnings", () => {
    const csv = buildOnboardingObjectsTemplate();
    const { objects, warnings } = parseOnboardingObjectsFile(csv, "onboarding_objects_template.csv");
    expect(warnings).toEqual([]);
    expect(objects).toHaveLength(3);
    expect(objects[0]).toMatchObject({
      object_key: "erp.customer",
      source_system: "erp",
      source_type: "table",
      source_object: "customer",
      target_type: "table",
      target_file_format: "Parquet",
      loading_behavior: "FullLoad",
    });
    // Row 2 is the delta-load example — watermark columns must survive the round trip.
    expect(objects[1]).toMatchObject({
      loading_behavior: "DeltaLoad",
      watermark_column: "modified_date",
      watermark_column_type: "datetime",
    });
    // Row 3 is the file→file example.
    expect(objects[2]).toMatchObject({ source_type: "file", target_type: "file", target_file_name: "invoices.parquet" });
  });

  it("maps columns by header name, not position", () => {
    const content = [
      "source_object,source_system,target_type,target_file_format,loading_behavior",
      "orders,erp,table,Parquet,FullLoad",
    ].join("\n");
    const { objects, warnings } = parseOnboardingObjectsFile(content, "objects.csv");
    expect(warnings).toEqual([]);
    expect(objects[0]).toMatchObject({ source_object: "orders", source_system: "erp", object_key: "erp.orders" });
  });

  it("requires a header row — a positional/headerless file is rejected with a warning, not silently mis-mapped", () => {
    // None of these cell values may collide with a real column name/synonym
    // (e.g. "table" is itself a synonym for source_object) or the header
    // sniff would — correctly — treat this line as a header instead.
    const { objects, warnings } = parseOnboardingObjectsFile("erp,cust_master,tbl,tbl,pqt,incr", "objects.csv");
    expect(objects).toEqual([]);
    expect(warnings.some((w) => w.includes("header row"))).toBe(true);
  });

  it("flags a duplicate object_key within the same file, keeping the first occurrence", () => {
    const content = [
      "source_system,source_object,target_type,target_file_format,loading_behavior",
      "erp,customer,table,Parquet,FullLoad",
      "erp,customer,table,Parquet,DeltaLoad",
    ].join("\n");
    const { objects, warnings } = parseOnboardingObjectsFile(content, "objects.csv");
    expect(objects).toHaveLength(1);
    expect(objects[0].loading_behavior).toBe("FullLoad");
    expect(warnings.some((w) => w.includes('Duplicate object_key "erp.customer"'))).toBe(true);
  });

  it("round-trips a JSON array of objects", () => {
    const content = JSON.stringify([
      { source_system: "erp", source_object: "customer", target_type: "table", target_file_format: "Parquet", loading_behavior: "FullLoad" },
    ]);
    const { objects, warnings } = parseOnboardingObjectsFile(content, "objects.json");
    expect(warnings).toEqual([]);
    expect(objects[0].object_key).toBe("erp.customer");
  });
});

describe("buildOnboardingObjectsCsv (§26.5 objects.csv snapshot)", () => {
  it("round-trips the registry rows through the upload parser", () => {
    const template = parseOnboardingObjectsFile(buildOnboardingObjectsTemplate(), "t.csv").objects;
    const snapshot = buildOnboardingObjectsCsv(template);
    expect(snapshot.split("\r\n")[0]).toBe(ONBOARDING_COLUMNS.map((c) => c.key).join(","));
    const { objects, warnings } = parseOnboardingObjectsFile(snapshot, "objects.csv");
    expect(warnings).toEqual([]);
    expect(objects).toEqual(template);
  });

  it("emits only the header row for an empty registry", () => {
    expect(buildOnboardingObjectsCsv([]).trim().split("\r\n")).toHaveLength(1);
  });
});

describe("DDL alignment guardrail", () => {
  it("every template column is a real app.onboarding_objects column (docs/DDL.md)", () => {
    for (const column of ONBOARDING_COLUMNS) {
      expect(ONBOARDING_DDL_COLUMNS.has(column.key)).toBe(true);
    }
  });
});

describe("validateOnboardingRow (§26.4 / ONB-01, ONB-03, ONB-05)", () => {
  const base: OnboardingObjectEntry = {
    object_key: "erp.customer",
    enabled: true,
    source_system: "erp",
    source_type: "table",
    source_object: "customer",
    target_type: "table",
    target_file_format: "Parquet",
    loading_behavior: "FullLoad",
  };

  it("passes a fully-specified full-load row", () => {
    expect(validateOnboardingRow(base)).toEqual([]);
  });

  it("ONB-05: flags DeltaLoad with no watermark_column/watermark_column_type", () => {
    const issues = validateOnboardingRow({ ...base, loading_behavior: "DeltaLoad" });
    expect(issues.some((i) => i.message.includes("DeltaLoad requires"))).toBe(true);
  });

  it("passes DeltaLoad once both watermark fields are set", () => {
    const issues = validateOnboardingRow({
      ...base,
      loading_behavior: "DeltaLoad",
      watermark_column: "modified_date",
      watermark_column_type: "datetime",
    });
    expect(issues).toEqual([]);
  });

  it("flags upsert write_behavior with no upsert_keys", () => {
    const issues = validateOnboardingRow({ ...base, write_behavior: "upsert" });
    expect(issues.some((i) => i.message.includes("requires upsert_keys"))).toBe(true);
  });

  it("flags a missing required field", () => {
    const issues = validateOnboardingRow({ ...base, source_object: "" });
    expect(issues.some((i) => i.message.includes("source_object is required"))).toBe(true);
  });

  it("ONB-01: findDuplicateObjectKeys flags two rows resolving to the same key", () => {
    const dup = findDuplicateObjectKeys([base, { ...base }, { ...base, object_key: "erp.orders" }]);
    expect(dup).toEqual(["erp.customer"]);
  });
});

describe("buildOnboardingControlTableSql (§26.5 generate control-table artifacts)", () => {
  const tableRow: OnboardingObjectEntry = {
    object_key: "erp.customer",
    enabled: true,
    source_system: "erp",
    source_type: "table",
    source_schema: "dbo",
    source_object: "customer",
    target_type: "table",
    target_schema: "curated",
    target_object: "customer",
    target_file_format: "Parquet",
    loading_behavior: "DeltaLoad",
    watermark_column: "modified_date",
    watermark_column_type: "datetime",
  };
  const fileRow: OnboardingObjectEntry = {
    object_key: "sftp.invoices",
    enabled: true,
    source_system: "sftp",
    source_type: "file",
    source_object: "exports/invoices_*.csv",
    target_type: "file",
    target_path: "curated/invoices/",
    target_file_name: "invoices.parquet",
    target_file_format: "Parquet",
    loading_behavior: "FullLoad",
  };
  const disabledRow: OnboardingObjectEntry = { ...tableRow, object_key: "erp.legacy", enabled: false };

  it("emits the standard control-table DDL", () => {
    const sql = buildOnboardingControlTableSql([tableRow]);
    expect(sql).toContain("CREATE TABLE control.adf_control_table (");
    expect(sql).toContain("TaskId            INT IDENTITY(1,1) PRIMARY KEY,");
  });

  it("seeds a schema-qualified table source/target from a table row", () => {
    const sql = buildOnboardingControlTableSql([tableRow]);
    expect(sql).toContain("N'dbo.customer'");
    expect(sql).toContain("N'curated.customer'");
    expect(sql).toContain("N'DeltaLoad'");
    expect(sql).toContain("N'modified_date'");
  });

  it("seeds target_path (not target_object/schema) for a file-target row", () => {
    const sql = buildOnboardingControlTableSql([fileRow]);
    expect(sql).toContain("N'curated/invoices/'");
    expect(sql).toContain("N'invoices.parquet'");
  });

  it("skips disabled rows entirely", () => {
    const sql = buildOnboardingControlTableSql([tableRow, disabledRow]);
    expect(sql).not.toContain("erp.legacy");
    expect(sql).toContain("1 enabled object of 2 total");
  });

  it("honors a custom table name", () => {
    const sql = buildOnboardingControlTableSql([tableRow], "acme_team.control_table");
    expect(sql).toContain("CREATE TABLE acme_team.control_table (");
    expect(sql).not.toContain("control.adf_control_table");
  });

  it("produces no seed rows for an empty registry, but still emits the DDL", () => {
    const sql = buildOnboardingControlTableSql([]);
    expect(sql).toContain("CREATE TABLE control.adf_control_table (");
    expect(sql).not.toContain("INSERT INTO");
  });

  it("newOnboardingObject() produces a row buildOnboardingControlTableSql can safely skip (disabled by default is false, so it's included but empty-safe)", () => {
    const blank = newOnboardingObject();
    expect(() => buildOnboardingControlTableSql([blank])).not.toThrow();
  });
});

describe("parseOnboardingObjectsWorkbook (.xlsx upload)", () => {
  it("reads the first worksheet by header name, like a CSV, and warns about extra sheets", async () => {
    const ExcelJS = (await import("exceljs")).default;
    const wb = new ExcelJS.Workbook();
    const ws = wb.addWorksheet("objects");
    ws.addRow(["source_object", "source_system", "target_type", "target_file_format", "loading_behavior"]);
    ws.addRow(["orders", "erp", "table", "Parquet", "FullLoad"]);
    wb.addWorksheet("notes").addRow(["ignored"]);
    const buffer = (await wb.xlsx.writeBuffer()) as ArrayBuffer;
    const { objects, warnings } = await parseOnboardingObjectsWorkbook(buffer, "objects.xlsx");
    expect(objects).toHaveLength(1);
    expect(objects[0]).toMatchObject({ source_object: "orders", source_system: "erp", object_key: "erp.orders" });
    expect(warnings).toEqual([expect.stringContaining("Only the first worksheet")]);
  });

  it("returns a warning instead of throwing on a non-workbook file", async () => {
    const { objects, warnings } = await parseOnboardingObjectsWorkbook(new TextEncoder().encode("nope").buffer as ArrayBuffer, "bad.xlsx");
    expect(objects).toEqual([]);
    expect(warnings[0]).toContain("Could not read bad.xlsx");
  });
});

describe("dqRulesToPipelineRules", () => {
  const base = { id: "dq_1", projectId: "p1", objectKey: "erp.customers", createdBy: null, createdAt: null };
  it("maps not_null/unique/range/row_count/freshness and skips off or incomplete rules", () => {
    const { rules, skipped } = dqRulesToPipelineRules([
      { ...base, checkType: "not_null", config: { column: "id" }, enforcement: "block" },
      { ...base, checkType: "unique", config: { column: "email" }, enforcement: "warn" },
      { ...base, checkType: "range", config: { column: "age", min: "0", max: "120" }, enforcement: "warn" },
      { ...base, checkType: "row_count", config: { threshold: "1" }, enforcement: "warn" },
      { ...base, checkType: "freshness", config: { column: "loaded_at", threshold: "24" }, enforcement: "block" },
      { ...base, checkType: "freshness", config: { threshold: "24" }, enforcement: "warn" },
      { ...base, checkType: "not_null", config: { column: "x" }, enforcement: "off" },
    ]);
    expect(rules).toEqual([
      { name: "customers_id_not_null", column: "id", rule: "IS_NOT_NULL", enforcement: "block" },
      { name: "customers_email_unique", column: "email", rule: "UNIQUE", enforcement: "warn" },
      { name: "customers_age_range", column: "age", rule: "RANGE_BETWEEN", value: "0,120", enforcement: "warn" },
      { name: "customers_row_count", column: "", rule: "ROW_COUNT_MIN", value: "1", enforcement: "warn" },
      { name: "customers_loaded_at_freshness", column: "loaded_at", rule: "FRESHNESS_HOURS", value: "24", enforcement: "block" },
    ]);
    expect(skipped.map((s) => s.checkType)).toEqual(["freshness", "not_null"]);
  });
});

describe("buildRegistryLineage", () => {
  it("links bronze sources to targets and resolves silver inputs to bronze targets", () => {
    const bronze = {
      ...newOnboardingObject(),
      object_key: "erp.customers",
      source_system: "erp",
      source_type: "table",
      source_schema: "dbo",
      source_object: "customers",
      target_type: "table",
      target_schema: "raw",
      target_object: "erp_customers",
      enabled: true,
    } as OnboardingObjectEntry;
    const silver = { objectKey: "customer", enabled: true, sourceObjectKeys: ["erp.customers", "crm.missing"], targetTable: "curated.customer" } as RegistryObject;
    const doc = buildRegistryLineage([bronze], [silver], []) as { jobs: Array<{ inputs: unknown[]; outputs: unknown[] }> };
    expect(doc.jobs).toHaveLength(2);
    expect(doc.jobs[0].inputs).toEqual([{ namespace: "source://erp", name: "dbo.customers" }]);
    expect(doc.jobs[1].inputs).toEqual([
      { namespace: "bronze://registry", name: "raw.erp_customers" },
      { namespace: "unresolved://onboarding", name: "crm.missing" },
    ]);
    expect(doc.jobs[1].outputs).toEqual([{ namespace: "silver://registry", name: "curated.customer" }]);
  });
});

describe("§26.3 Map step — header remapping", () => {
  const content = ["Origin,Table Name,target_type,target_file_format,loading_behavior", "erp,orders,table,Parquet,FullLoad"].join("\n");
  const unknownHeader = ["A,B", "erp,orders"].join("\n");

  it("reports headers it can't recognize", () => {
    const { unmappedHeaders } = parseOnboardingObjectsFile(content, "objects.csv");
    expect(unmappedHeaders).toContain("Origin");
  });

  it("applies a user header map and re-parses", () => {
    const first = parseOnboardingObjectsFile(content, "objects.csv");
    const mapped = parseOnboardingObjectsFile(
      content,
      "objects.csv",
      Object.fromEntries(first.unmappedHeaders.map((h) => [h, h === "Origin" ? "source_system" : "source_object"])),
    );
    expect(mapped.unmappedHeaders).toEqual([]);
    expect(mapped.objects[0]).toMatchObject({ source_system: "erp", source_object: "orders", object_key: "erp.orders" });
  });

  it("offers a wholly unrecognized header row for mapping", () => {
    const { objects, unmappedHeaders } = parseOnboardingObjectsFile(unknownHeader, "objects.csv");
    expect(objects).toEqual([]);
    expect(unmappedHeaders).toEqual(["A", "B"]);
    const mapped = parseOnboardingObjectsFile(unknownHeader, "objects.csv", { A: "source_system", B: "source_object" });
    expect(mapped.objects[0]).toMatchObject({ source_system: "erp", source_object: "orders" });
  });

  it("maps JSON keys too", () => {
    const mapped = parseOnboardingObjectsFile('[{"sys":"erp","obj":"orders"}]', "objects.json", { sys: "source_system", obj: "source_object" });
    expect(mapped.unmappedHeaders).toEqual([]);
    expect(mapped.objects[0]).toMatchObject({ source_system: "erp", source_object: "orders" });
  });
});

describe("onboardingPipelineSettings (Build's Bronze picker)", () => {
  const row = (over: Partial<ReturnType<typeof newOnboardingObject>>) => ({ ...newOnboardingObject(), source_system: "epic", source_object: "dbo.PATIENT", ...over });

  it("carries one object's write behavior, upsert keys and target table", () => {
    const prev = emptySpecDraft();
    const { patch, notes } = onboardingPipelineSettings(
      [row({ write_behavior: "merge", upsert_keys: "PAT_ID|CSN", target_type: "table", target_schema: "raw", target_object: "epic_patient" })],
      prev,
    );
    expect(patch).toMatchObject({ writeBehavior: "Upsert", upsertKeyColumns: "PAT_ID, CSN" });
    expect(patch.targetDataset).toMatchObject({ schema: "raw", table: "epic_patient" });
    expect(notes).toEqual([]);
  });

  it("applies only what several picked objects agree on", () => {
    const { patch, notes } = onboardingPipelineSettings(
      [
        row({ write_behavior: "append", upsert_keys: "A", target_type: "file", target_path: "/bronze/epic/", target_file_name: "a.parquet" }),
        row({ write_behavior: "upsert", upsert_keys: "B", target_type: "file", target_path: "/bronze/epic/" }),
      ],
      emptySpecDraft(),
    );
    expect(patch).toEqual({ targetPath: "/bronze/epic/" });
    expect(notes.length).toBe(3);
  });
});
