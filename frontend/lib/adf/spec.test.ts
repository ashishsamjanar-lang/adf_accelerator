import { describe, expect, it } from "vitest";
import {
  buildSpec,
  emptySpecDraft,
  fieldErrors,
  looksLikeRegex,
  resolveChildPipelineName,
  resolveCopyTimeout,
  resolveMasterPipelineName,
  sourceDatasetDefaults,
  validateDraft,
} from "./spec";
import type { SpecDraft } from "./spec";

// This is the reference implementation `worker/app/generator/spec.py` is a
// port of (DEVELOPMENT_NOTES.md's "keep them in parity" note); `worker/tests/` has a
// generator-parity test that diffs the two generators' *output*, but nothing
// on the frontend side unit-tested `validateDraft`/`buildSpec` in isolation
// before this file — the frontend had no test runner at all.

function validDraft(overrides: Partial<SpecDraft> = {}): SpecDraft {
  const base = emptySpecDraft();
  return {
    ...base,
    name: "customer_incremental",
    projectId: "proj-a",
    // Connector-required fields (source: sql_server/sql auth, target: ADLS
    // Gen2/account key) — otherwise `validateLinkedServiceDraft`/
    // `validateDatasetDraft` always fail even on an "empty" draft, which a
    // bare `emptySpecDraft()` is not exempt from.
    sourceLinkedService: {
      ...base.sourceLinkedService,
      fields: { server: "sqlsrv01.database.windows.net", database: "customerdb", userName: "svc_reader", password: "sqlserver-password" },
    },
    targetLinkedService: {
      ...base.targetLinkedService,
      fields: { accountName: "stgcustomer", url: "https://stgcustomer.dfs.core.windows.net", accountKey: "storage-account-key" },
    },
    sourceDataset: { ...base.sourceDataset, linkedServiceName: base.sourceLinkedService.name },
    targetDataset: { ...base.targetDataset, linkedServiceName: base.targetLinkedService.name },
    ...overrides,
  };
}

describe("sourceDatasetDefaults — file-kind source connectors (SFTP-04-style dummy data)", () => {
  it("does not double the extension when sourceObject is already a file name/pattern", () => {
    // docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md §6 vendor-invoice scenario:
    // sourceObject for a file-kind connector is the file name/pattern itself
    // (unlike a DB source, where it's a bare table name) — appending ".csv"
    // unconditionally doubled it (regression: worker/tests/
    // test_scenario_smoke_dummy_artifacts.py::test_sftp_style_file_source_default_filename_does_not_double_its_extension).
    const draft = validDraft({ sourceType: "sftp", sourceObject: "/outbound/Acme_Invoice_09-12-2026.csv" });
    const defaults = sourceDatasetDefaults(draft);
    expect(defaults.fileName).toBe("Acme_Invoice_09-12-2026.csv");
    expect(defaults.fileName.endsWith(".csv.csv")).toBe(false);
  });

  it("still synthesizes a .csv default for a schema/table-shaped (non-file) source", () => {
    const draft = validDraft({ sourceType: "sql_server", sourceObject: "dbo.customer" });
    expect(sourceDatasetDefaults(draft).fileName).toBe("customer.csv");
  });
});

describe("validateDraft", () => {
  it("has no errors for a fully filled-in draft", () => {
    expect(validateDraft(validDraft())).toEqual([]);
  });

  it("flags incomplete and duplicate-sink column mappings", () => {
    const columnMappings = [{ source: "a", sink: "x" }, { source: "b", sink: "X" }, { source: "c", sink: "" }];
    const errors = fieldErrors(validateDraft(validDraft({ columnMappings })), "columnMappings");
    expect(errors.some((m) => m.includes("needs both"))).toBe(true);
    expect(errors.some((m) => m.includes("more than once"))).toBe(true);
    expect(fieldErrors(validateDraft(validDraft({ columnMappings: [{ source: "a", sink: "x" }] })), "columnMappings")).toEqual([]);
  });

  it("flags incomplete/duplicate per-object mappings and warns when the pattern ignores them", () => {
    const sourceObjects = [
      { object: "dbo.customer", columnMappings: [{ source: "a", sink: "" }, { source: "b", sink: "x" }, { source: "c", sink: "X" }] },
    ];
    const errors = fieldErrors(validateDraft(validDraft({ sourceObjects })), "sourceObjects");
    expect(errors.some((m) => m.includes("needs both"))).toBe(true);
    expect(errors.some((m) => m.includes("more than once"))).toBe(true);
    const ignored = validateDraft(
      validDraft({ pattern: "independent_full", sourceObjects: [{ object: "dbo.customer", columnMappings: [{ source: "a", sink: "x" }] }] }),
    );
    expect(ignored.some((i) => i.severity === "warning" && i.message.includes("only applied"))).toBe(true);
  });

  it("requires a name", () => {
    const issues = validateDraft(validDraft({ name: "" }));
    expect(fieldErrors(issues, "name")).toEqual(["A pipeline name is required."]);
  });

  it("rejects a name that isn't lower_snake_case", () => {
    const issues = validateDraft(validDraft({ name: "CustomerLoad" }));
    expect(fieldErrors(issues, "name")[0]).toContain("must start with a lowercase letter");
  });

  it("rejects a name shorter than 3 characters", () => {
    const issues = validateDraft(validDraft({ name: "ab" }));
    expect(fieldErrors(issues, "name")).toHaveLength(1);
  });

  it("requires a project", () => {
    const issues = validateDraft(validDraft({ projectId: "" }));
    expect(fieldErrors(issues, "projectId")).toEqual(["Select a project."]);
  });

  it("requires at least one source object", () => {
    const issues = validateDraft(validDraft({ sourceObjectMode: "form", sourceObjects: [], sourceObject: "" }));
    expect(fieldErrors(issues, "sourceObjects")[0]).toContain("Add at least one source object");
  });

  it("caps source objects at 200", () => {
    const many = Array.from({ length: 201 }, (_, i) => ({ object: `dbo.t${i}` }));
    const issues = validateDraft(validDraft({ sourceObjects: many }));
    expect(fieldErrors(issues, "sourceObjects")).toContain("Maximum 200 source objects per pipeline.");
  });

  it("requires a target path", () => {
    const issues = validateDraft(validDraft({ targetPath: "  " }));
    expect(fieldErrors(issues, "targetPath")).toEqual(["Target path is required."]);
  });

  it("requires a watermark column and table for incremental loads", () => {
    const issues = validateDraft(
      validDraft({ watermarkEnabled: true, loadTypeMode: "incremental", watermarkColumns: [], watermarkTable: "" }),
    );
    expect(fieldErrors(issues, "watermarkColumns")).toContain(
      "At least one watermark column is required for incremental loads.",
    );
    expect(fieldErrors(issues, "watermarkTable")).toContain("Watermark table is required for incremental loads.");
  });

  it("does not require a watermark for a full load", () => {
    const issues = validateDraft(
      validDraft({ watermarkEnabled: true, loadTypeMode: "full", watermarkColumns: [], watermarkTable: "" }),
    );
    expect(fieldErrors(issues, "watermarkColumns")).toEqual([]);
    expect(fieldErrors(issues, "watermarkTable")).toEqual([]);
  });

  it("caps watermark columns at 5", () => {
    const columns = Array.from({ length: 6 }, (_, i) => ({ name: `col${i}`, type: "string" as const, order: i }));
    const issues = validateDraft(validDraft({ watermarkColumns: columns }));
    expect(fieldErrors(issues, "watermarkColumns")).toContain("Maximum 5 watermark columns allowed for composite watermark.");
  });

  it("keeps batchCount and parallelCopies within 1-50", () => {
    const tooLow = validateDraft(validDraft({ batchCount: 0, parallelCopies: 0 }));
    expect(fieldErrors(tooLow, "batchCount")).toHaveLength(1);
    expect(fieldErrors(tooLow, "parallelCopies")).toHaveLength(1);

    const tooHigh = validateDraft(validDraft({ batchCount: 51, parallelCopies: 51 }));
    expect(fieldErrors(tooHigh, "batchCount")).toHaveLength(1);
    expect(fieldErrors(tooHigh, "parallelCopies")).toHaveLength(1);
  });

  it("caps custom log columns and custom metadata at 10", () => {
    const tooMany = Array.from({ length: 11 }, (_, i) => ({ name: `c${i}`, value: `v${i}` }));
    const issues = validateDraft(validDraft({ customLogColumns: tooMany, customMetadata: tooMany }));
    expect(fieldErrors(issues, "customLogColumns")).toEqual(["Maximum 10 custom log columns allowed."]);
    expect(fieldErrors(issues, "customMetadata")).toEqual(["Maximum 10 custom metadata columns allowed."]);
  });
});

describe("buildSpec", () => {
  it("builds a LogicalPipelineSpec carrying the draft's core fields through", () => {
    const spec = buildSpec(validDraft());
    expect(spec.schemaVersion).toBe("2.1");
    expect(spec.pipeline.name).toBe("customer_incremental");
    expect(spec.pipeline.type).toBe("incremental_load");
    expect(spec.source.type).toBe("sql_server");
    expect(spec.source.object).toBe("dbo.customer");
    expect(spec.target.type).toBe("azure_data_lake_storage_gen2");
    expect(spec.target.path).toBe("/customer/");
    expect(spec.watermark.enabled).toBe(true);
    expect(spec.watermark.columns).toHaveLength(1);
    expect(spec.loadType.mode).toBe("incremental");
  });

  it("truncates watermark columns to 5 even if the draft carries more", () => {
    const columns = Array.from({ length: 8 }, (_, i) => ({ name: `col${i}`, type: "string" as const, order: i }));
    const spec = buildSpec(validDraft({ watermarkColumns: columns }));
    expect(spec.watermark.columns).toHaveLength(5);
  });

  it("prefers the first row of sourceObjects over the singular sourceObject field", () => {
    const spec = buildSpec(
      validDraft({ sourceObjects: [{ object: "dbo.orders" }, { object: "dbo.customer" }], sourceObject: "dbo.customer" }),
    );
    expect(spec.source.object).toBe("dbo.orders");
  });
});

describe("fieldErrors", () => {
  it("returns only the messages for the requested field", () => {
    const issues = validateDraft(validDraft({ name: "", projectId: "" }));
    expect(fieldErrors(issues, "name")).toHaveLength(1);
    expect(fieldErrors(issues, "projectId")).toHaveLength(1);
    expect(fieldErrors(issues, "targetPath")).toEqual([]);
  });
});

// §30.4 — validateDataQualityRules gates Generate on the same shape
// pipeline-codegen.ts's dataQualityActivities turns into real activities, so a
// rule that would produce a broken/no-op query is caught here first.
describe("data quality rules validation", () => {
  it("allows a fully filled-in rule of each type", () => {
    const issues = validateDraft(
      validDraft({
        dataQualityRules: [
          { name: "no_nulls", column: "id", rule: "IS_NOT_NULL", enforcement: "block" },
          { name: "dup_check", column: "email", rule: "UNIQUE" },
          { name: "len", column: "sku", rule: "LENGTH_LE", value: "20" },
          { name: "rng", column: "age", rule: "RANGE_BETWEEN", value: "0,120" },
          { name: "fmt", column: "code", rule: "REGEX_MATCH", value: "AB%" },
        ],
      }),
    );
    expect(fieldErrors(issues, "dataQualityRules")).toEqual([]);
  });

  it("rejects more than 5 rules", () => {
    const rules = Array.from({ length: 6 }, (_, i) => ({ name: `r${i}`, column: "id", rule: "IS_NOT_NULL" as const }));
    const issues = validateDraft(validDraft({ dataQualityRules: rules }));
    expect(fieldErrors(issues, "dataQualityRules")).toContain("Maximum 5 data quality rules allowed.");
  });

  it("accepts ROW_COUNT_MIN without a column but requires whole-number thresholds", () => {
    const ok = validateDraft(validDraft({ dataQualityRules: [{ name: "has_rows", column: "", rule: "ROW_COUNT_MIN", value: "1" }] }));
    expect(fieldErrors(ok, "dataQualityRules")).toEqual([]);
    const bad = validateDraft(
      validDraft({ dataQualityRules: [{ name: "fresh", column: "loaded_at", rule: "FRESHNESS_HOURS", value: "soon" }] }),
    );
    expect(fieldErrors(bad, "dataQualityRules")).toEqual([
      'Data quality rule "fresh" (FRESHNESS_HOURS) needs a whole-number maximum age in hours in Value.',
    ]);
  });

  it("rejects two rule names that produce the same activity name (DQ-06)", () => {
    const issues = validateDraft(
      validDraft({
        dataQualityRules: [
          { name: "id check", column: "id", rule: "IS_NOT_NULL" },
          { name: "ID_check", column: "id", rule: "UNIQUE" },
        ],
      }),
    );
    expect(fieldErrors(issues, "dataQualityRules")).toEqual([
      'Data quality rules "id check" and "ID_check" produce the same activity name — rename one.',
    ]);
  });

  it("requires a name and a column", () => {
    const issues = validateDraft(validDraft({ dataQualityRules: [{ name: "", column: "", rule: "IS_NOT_NULL" }] }));
    const messages = fieldErrors(issues, "dataQualityRules");
    expect(messages).toContain("Data quality rule 1 needs a name.");
    expect(messages.some((m) => m.includes("needs a column"))).toBe(true);
  });

  it("rejects an unknown enforcement value", () => {
    const issues = validateDraft(
      validDraft({ dataQualityRules: [{ name: "r", column: "id", rule: "IS_NOT_NULL", enforcement: "fail" as never }] }),
    );
    expect(fieldErrors(issues, "dataQualityRules").some((m) => m.includes('must be "warn" or "block"'))).toBe(true);
  });

  it("requires a whole-number Value for LENGTH_LE", () => {
    const issues = validateDraft(validDraft({ dataQualityRules: [{ name: "r", column: "id", rule: "LENGTH_LE", value: "abc" }] }));
    expect(fieldErrors(issues, "dataQualityRules").some((m) => m.includes("LENGTH_LE"))).toBe(true);
  });

  it("requires a valid min,max Value for RANGE_BETWEEN", () => {
    const issues = validateDraft(validDraft({ dataQualityRules: [{ name: "r", column: "id", rule: "RANGE_BETWEEN", value: "100,0" }] }));
    expect(fieldErrors(issues, "dataQualityRules").some((m) => m.includes("RANGE_BETWEEN"))).toBe(true);
  });

  it("requires a non-empty pattern Value for REGEX_MATCH", () => {
    const issues = validateDraft(validDraft({ dataQualityRules: [{ name: "r", column: "id", rule: "REGEX_MATCH", value: "" }] }));
    expect(fieldErrors(issues, "dataQualityRules").some((m) => m.includes("REGEX_MATCH"))).toBe(true);
  });
});

describe("validateDraft — medallion layer", () => {
  it("bronze (default) needs no curated entity or transformations", () => {
    expect(validateDraft(validDraft())).toEqual([]);
  });

  it("silver requires a curated entity and at least one transformation", () => {
    const issues = validateDraft(validDraft({ medallionLayer: "silver" }));
    expect(fieldErrors(issues, "curatedEntity").length).toBeGreaterThan(0);
    expect(fieldErrors(issues, "transformations").length).toBeGreaterThan(0);
  });

  it("silver with a valid entity + transformation passes", () => {
    const issues = validateDraft(
      validDraft({
        medallionLayer: "silver",
        curatedEntity: "gl_transaction",
        transformations: [{ name: "dedupe_gl", type: "dedupe", keyColumns: "transaction_id", orderColumn: "modified_date" }],
        targetType: "azure_sql_database",
      }),
    );
    expect(issues).toEqual([]);
  });

  it("gold requires a mart domain in addition to the curated entity", () => {
    const issues = validateDraft(
      validDraft({
        medallionLayer: "gold",
        curatedEntity: "gl_fact",
        transformations: [{ name: "agg_gl", type: "aggregate", groupByColumns: "customer_id", aggregateExpression: "SUM(amount) AS total_amount" }],
      }),
    );
    expect(fieldErrors(issues, "martDomain").length).toBeGreaterThan(0);
  });

  it("gold with entity + domain + transformation passes", () => {
    const issues = validateDraft(
      validDraft({
        medallionLayer: "gold",
        curatedEntity: "gl_fact",
        martDomain: "finance",
        transformations: [{ name: "agg_gl", type: "aggregate", groupByColumns: "customer_id", aggregateExpression: "SUM(amount) AS total_amount" }],
        targetType: "azure_sql_database",
      }),
    );
    expect(issues).toEqual([]);
  });
});

describe("master/child pipeline artifact name overrides (§24.5)", () => {
  it("resolve* fall back to PL_{name}_master/_child when unset", () => {
    const draft = validDraft({ name: "orders_hub" });
    expect(resolveMasterPipelineName(draft)).toBe("PL_orders_hub_master");
    expect(resolveChildPipelineName(draft)).toBe("PL_orders_hub_child");
  });

  it("resolve* prefer the explicit override when set", () => {
    const draft = validDraft({ masterPipelineName: "PL_OrdersOrchestrator", childPipelineName: "PL_OrdersRouter" });
    expect(resolveMasterPipelineName(draft)).toBe("PL_OrdersOrchestrator");
    expect(resolveChildPipelineName(draft)).toBe("PL_OrdersRouter");
  });

  it("a valid draft with no overrides has no errors", () => {
    expect(validateDraft(validDraft())).toEqual([]);
  });

  it("rejects an ADF-illegal override", () => {
    const issues = validateDraft(validDraft({ masterPipelineName: "PL Orders-Master!" }));
    expect(fieldErrors(issues, "masterPipelineName").length).toBeGreaterThan(0);
  });

  it("rejects master and child overrides being equal", () => {
    const issues = validateDraft(validDraft({ masterPipelineName: "PL_Same", childPipelineName: "PL_Same" }));
    expect(fieldErrors(issues, "childPipelineName").length).toBeGreaterThan(0);
  });

  it("a valid, distinct pair of overrides passes", () => {
    const issues = validateDraft(
      validDraft({ masterPipelineName: "PL_OrdersOrchestrator", childPipelineName: "PL_OrdersRouter" }),
    );
    expect(issues).toEqual([]);
  });
});

describe("gap #29/#33 — sourceSystem default names + copyTimeout", () => {
  it("keeps PL_{name}_master/child when sourceSystem is unset", () => {
    const d = { ...emptySpecDraft(), name: "cust" };
    expect(resolveMasterPipelineName(d)).toBe("PL_cust_master");
    expect(resolveChildPipelineName(d)).toBe("PL_cust_child");
  });
  it("uses pl_master / pl_<source_system>_child when sourceSystem is set; overrides still win", () => {
    const d = { ...emptySpecDraft(), name: "cust", sourceSystem: "SAP ECC" };
    expect(resolveMasterPipelineName(d)).toBe("pl_master");
    expect(resolveChildPipelineName(d)).toBe("pl_sap_ecc_child");
    expect(resolveChildPipelineName({ ...d, childPipelineName: "PL_x" })).toBe("PL_x");
  });
  it("defaults the copy timeout and validates its format", () => {
    expect(resolveCopyTimeout(emptySpecDraft())).toBe("0.02:00:00.000");
    expect(resolveCopyTimeout({ ...emptySpecDraft(), copyTimeout: "0.04:00:00" })).toBe("0.04:00:00");
    const issues = validateDraft({ ...emptySpecDraft(), copyTimeout: "2 hours" });
    expect(issues.some((i) => i.field === "copyTimeout")).toBe(true);
  });
});

describe("hard-delete handling validation", () => {
  it("is silent when unset and needs key columns when on", () => {
    expect(fieldErrors(validateDraft(validDraft()), "upsertKeyColumns")).toEqual([]);
    expect(fieldErrors(validateDraft(validDraft({ deleteHandling: "soft_delete" })), "upsertKeyColumns")).toEqual([
      "Delete handling needs key column(s) — set the upsert key columns.",
    ]);
    expect(validateDraft(validDraft({ deleteHandling: "soft_delete", upsertKeyColumns: "id" }))).toEqual([]);
  });

  it("rejects a bad soft-delete column and CDC on a non-SQL source or composite watermark", () => {
    expect(fieldErrors(validateDraft(validDraft({ deleteHandling: "soft_delete", upsertKeyColumns: "id", softDeleteColumn: "is deleted" })), "softDeleteColumn")).toHaveLength(1);
    expect(fieldErrors(validateDraft(validDraft({ deleteHandling: "cdc", upsertKeyColumns: "id", sourceType: "oracle" })), "deleteHandling")).toHaveLength(1);
    expect(
      fieldErrors(validateDraft(validDraft({ deleteHandling: "cdc", upsertKeyColumns: "id", watermarkColumns: [{ name: "id", type: "int", order: 1 }] })), "watermarkColumns"),
    ).toEqual(["CDC delete detection maps the watermark window to LSNs — use one datetime watermark column."]);
  });
});

describe("REGEX_MATCH pattern check (gaps.md RUN-17)", () => {
  it("flags regex-only syntax but not LIKE patterns", () => {
    expect(looksLikeRegex("^[^@]+@.+$")).toBe(true);
    expect(looksLikeRegex("[0-9]\\d")).toBe(true);
    for (const like of ["%_@_%._%", "[A-Z][A-Z]%", "AB%"]) expect(looksLikeRegex(like)).toBe(false);
  });
});

describe("Full Load row-count warning (gaps.md SQL-05)", () => {
  const issues = (overrides: Partial<SpecDraft>) =>
    validateDraft({ ...emptySpecDraft(), loadTypeMode: "full", fullLoad: true, ...overrides }).filter((i) => i.field === "estimatedSourceRows");

  it("warns on a large unchunked Full Load and stays silent otherwise", () => {
    const [issue] = issues({ estimatedSourceRows: 200_000_000 });
    expect(issue.severity).toBe("warning");
    expect(issue.message).toContain("200,000,000");
    expect(issues({})).toEqual([]);
    expect(issues({ estimatedSourceRows: 500_000 })).toEqual([]);
    expect(issues({ estimatedSourceRows: 200_000_000, chunkingEnabled: true, chunkColumn: "claim_id" })).toEqual([]);
    expect(issues({ estimatedSourceRows: 200_000_000, loadTypeMode: "incremental", fullLoad: false })).toEqual([]);
  });
});
