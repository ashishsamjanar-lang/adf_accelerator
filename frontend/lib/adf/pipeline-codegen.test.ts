import { describe, expect, it } from "vitest";
import { buildGeneratedArtifacts } from "./pipeline-codegen";
import { emptySpecDraft } from "./spec";
import { defaultDatasetDraft, defaultLinkedServiceDraft } from "./connections";
import type { SpecDraft } from "./spec";
import type { EnvironmentConnection } from "@/types";

// Regression coverage for a real bug found 2026-09-13: `controlTableSql`'s
// CREATE TABLE / INSERT statements hardcoded the literal
// `control.adf_control_table`, while `masterPipeline`'s own Lookup query and
// `ControlTable`/`WatermarkTable` parameters already read from
// `draft.watermarkTable` — so a project/team that customized that field away
// from the default got a pipeline that queried a table this script never
// created. Its `worker/app/generator/codegen.py` twin had the same bug and
// the same fix (`worker/tests/test_control_table_sql.py`).

function draftWith(overrides: Partial<SpecDraft> = {}): SpecDraft {
  const base = emptySpecDraft();
  return {
    ...base,
    name: "customer_incremental",
    projectId: "proj-a",
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

function controlTableSql(draft: SpecDraft): string {
  return buildGeneratedArtifacts(draft).find((a) => a.fileName === "adf_control_table.sql")?.content ?? "";
}

describe("controlTableSql", () => {
  // Regression (2026-09-27): `CREATE TABLE IF NOT EXISTS` is Postgres/MySQL syntax and fails on
  // Azure SQL / SQL Server — every generated SQL artifact guards with OBJECT_ID instead.
  it("uses T-SQL OBJECT_ID guards, never CREATE TABLE IF NOT EXISTS", () => {
    const draft = draftWith({ enableCopyLog: true, connectionControlEnabled: true });
    for (const artifact of buildGeneratedArtifacts(draft).filter((a) => a.fileName.endsWith(".sql"))) {
      expect(artifact.content).not.toContain("CREATE TABLE IF NOT EXISTS");
    }
    const sql = controlTableSql(draft);
    expect(sql).toContain("IF OBJECT_ID(N'control.watermarks', N'U') IS NULL\nCREATE TABLE control.watermarks (");
    expect(sql).toContain("IF OBJECT_ID(N'control.adf_execution_log', N'U') IS NULL");
  });

  it("creates and seeds the default control table reference", () => {
    const sql = controlTableSql(draftWith());
    expect(sql).toContain("CREATE TABLE control.watermarks (");
    expect(sql).toContain("INSERT INTO control.watermarks (");
    expect(sql).not.toContain("control.adf_control_table");
  });

  it("follows a custom per-project/team schema and table", () => {
    const sql = controlTableSql(draftWith({ watermarkTable: "acme_team.control_table" }));
    expect(sql).toContain("CREATE TABLE acme_team.control_table (");
    expect(sql).toContain("INSERT INTO acme_team.control_table (");
    expect(sql).not.toContain("control.watermarks");
    expect(sql).not.toContain("control.adf_control_table");
  });

  it("matches what the master pipeline actually queries", () => {
    const draft = draftWith({ watermarkTable: "acme_team.control_table" });
    const artifacts = buildGeneratedArtifacts(draft);
    const master = JSON.parse(artifacts.find((a) => a.fileName.endsWith("_master.json"))?.content ?? "{}");
    const controlTableParam = master.properties.parameters.ControlTable.defaultValue;
    expect(controlTableParam).toBe("acme_team.control_table");
    expect(controlTableSql(draft)).toContain("CREATE TABLE acme_team.control_table (");
  });

  // Real bug found 2026-09-20: the Lookup query interpolated `controlTable` at generation time,
  // so the declared `ControlTable` pipeline parameter had no runtime effect — unlike its sibling
  // `WatermarkTable` parameter, which the child pipeline already reads via
  // `@{pipeline().parameters.WatermarkTable}`. An operator changing `ControlTable` when triggering
  // an already-deployed pipeline was silently ignored. Fixed by making the query read
  // `@{pipeline().parameters.ControlTable}` at runtime instead; a new `TaskIdFilter` parameter
  // (0 = every enabled row, the default) lets a single control-table row be restarted without
  // touching every other row's `Enabled` flag or redeploying (AGENTS.md "Operating generated
  // pipelines in production").
  it("reads ControlTable and TaskIdFilter as runtime pipeline-parameter expressions, not baked-in values", () => {
    const draft = draftWith({ watermarkTable: "acme_team.control_table" });
    const artifacts = buildGeneratedArtifacts(draft);
    const master = JSON.parse(artifacts.find((a) => a.fileName.endsWith("_master.json"))?.content ?? "{}");
    const query = master.properties.activities[0].typeProperties.source.sqlReaderQuery as string;
    expect(query).toContain("FROM @{pipeline().parameters.ControlTable}");
    expect(query).toContain("@{pipeline().parameters.TaskIdFilter} = 0 OR TaskId = @{pipeline().parameters.TaskIdFilter}");
    expect(query).not.toContain("FROM acme_team.control_table");
    const taskIdFilterParam = master.properties.parameters.TaskIdFilter;
    expect(taskIdFilterParam).toEqual({ type: "Int", defaultValue: 0 });
  });
});

describe("connection control table", () => {
  it("is not emitted by default", () => {
    expect(controlTableSql(draftWith())).not.toContain("adf_connection_control");
  });

  it("emits one row per distinct linked service, splitting secret ref from non-secret properties", () => {
    const sql = controlTableSql(
      draftWith({ connectionControlEnabled: true, connectionControlTable: "acme_team.adf_connection_control" }),
    );
    expect(sql).toContain("CREATE TABLE acme_team.adf_connection_control (");
    const sourceLine = sql.split("\n").find((line) => line.startsWith("  N'LS_sql_server_source'"))!;
    expect(sourceLine).toContain("N'sqlserver-password'"); // SecretRef
    const propertiesMatch = sourceLine.match(/, (N'\{.*\}'|NULL), 1$/);
    expect(propertiesMatch).toBeTruthy();
    const properties = JSON.parse(propertiesMatch![1].slice(2, -1));
    expect(properties).toEqual({ database: "customerdb", server: "sqlsrv01.database.windows.net", userName: "svc_reader" });
    expect(Object.values(properties)).not.toContain("sqlserver-password");
  });

  it("dedupes a linked service shared by source and target", () => {
    const draft = draftWith({ connectionControlEnabled: true });
    const shared = { ...draft.sourceLinkedService, name: "LS_shared" };
    const sql = controlTableSql({ ...draft, sourceLinkedService: shared, targetLinkedService: shared });
    expect(sql.split("INSERT INTO control.adf_connection_control (").length - 1).toBe(1);
  });
});

// §30.4 — dataQualityRules previously round-tripped through the spec with no
// generated behavior behind it (IMPLEMENTATION_PLAN.md's "Not done" note); this
// covers pipeline-codegen.ts's dataQualityActivities turning each rule into a
// real pre-copy Lookup (+ IfCondition/Fail gate when enforcement is "block").
describe("dataQualityActivities", () => {
  function childActivities(draft: SpecDraft): { name: string; type: string; dependsOn?: unknown[] }[] {
    const artifacts = buildGeneratedArtifacts(draft);
    const child = JSON.parse(artifacts.find((a) => a.fileName.endsWith("_child.json"))?.content ?? "{}");
    return child.properties.activities;
  }

  it("emits nothing when no rules are configured", () => {
    const activities = childActivities(draftWith());
    expect(activities.map((a) => a.name)).toEqual(["RouteByLoadingBehavior"]);
    expect(activities[0].dependsOn).toBeUndefined();
  });

  it("emits a Lookup + IfCondition/Fail gate for a block rule, and gates the Switch on it", () => {
    const draft = draftWith({
      dataQualityRules: [{ name: "no_nulls", column: "customer_id", rule: "IS_NOT_NULL", enforcement: "block" }],
    });
    const activities = childActivities(draft);
    expect(activities.map((a) => a.name)).toEqual(["DQ_no_nulls_Check", "DQ_no_nulls_Gate", "RouteByLoadingBehavior"]);
    const check = activities[0] as unknown as { typeProperties: { source: { sqlReaderQuery: string } }; outputVariableName?: string };
    expect(check.typeProperties.source.sqlReaderQuery).toBe(
      "SELECT COUNT(*) AS ViolationCount FROM @{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName} WHERE customer_id IS NULL",
    );
    expect(check.outputVariableName).toBeUndefined();
    const gate = activities[1] as unknown as { typeProperties: { ifTrueActivities: { type: string }[] } };
    expect(gate.typeProperties.ifTrueActivities[0].type).toBe("Fail");
    const routeSwitch = activities[2];
    expect(routeSwitch.dependsOn).toEqual([{ activity: "DQ_no_nulls_Gate", dependencyConditions: ["Succeeded"] }]);
  });

  it("gates on the pass rate when a max violation % is set (DMT-05)", () => {
    const activities = childActivities(
      draftWith({ dataQualityRules: [{ name: "mostly_set", column: "id", rule: "IS_NOT_NULL", enforcement: "block", maxViolationPct: "5" }] }),
    );
    const check = activities[0] as unknown as { typeProperties: { source: { sqlReaderQuery: string } } };
    expect(check.typeProperties.source.sqlReaderQuery).toContain("ViolationCount * 100.0 > 5 * t.TotalRows THEN 1 ELSE 0 END AS ThresholdBreached");
    const gate = activities[1] as unknown as { typeProperties: { expression: { value: string } } };
    expect(gate.typeProperties.expression.value).toBe("@equals(int(activity('DQ_mostly_set_Check').output.firstRow.ThresholdBreached), 1)");
  });

  it("emits only a Lookup (no gate) for the default 'warn' enforcement, and does not gate the Switch", () => {
    const draft = draftWith({
      dataQualityRules: [{ name: "dup_check", column: "email", rule: "UNIQUE" }],
    });
    const activities = childActivities(draft);
    expect(activities.map((a) => a.name)).toEqual(["DQ_dup_check_Check", "RouteByLoadingBehavior"]);
    expect(activities[1].dependsOn).toBeUndefined();
  });

  it("emits whole-object ROW_COUNT_MIN and FRESHNESS_HOURS checks", () => {
    const activities = childActivities(
      draftWith({
        dataQualityRules: [
          { name: "has_rows", column: "", rule: "ROW_COUNT_MIN", value: "10", enforcement: "block" },
          { name: "fresh", column: "loaded_at", rule: "FRESHNESS_HOURS", value: "24" },
        ],
      }),
    ) as unknown as Array<{ name: string; description?: string; typeProperties: { source?: { sqlReaderQuery: string } } }>;
    expect(activities.map((a) => a.name)).toEqual(["DQ_has_rows_Check", "DQ_has_rows_Gate", "DQ_fresh_Check", "RouteByLoadingBehavior"]);
    const t = "@{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}";
    expect(activities[0].typeProperties.source?.sqlReaderQuery).toBe(`SELECT CASE WHEN COUNT(*) < 10 THEN 1 ELSE 0 END AS ViolationCount FROM ${t}`);
    expect(activities[0].description).toMatch(/\(ROW_COUNT_MIN\) on [^.]+\.[^.]+\.$/);
    expect(activities[2].typeProperties.source?.sqlReaderQuery).toBe(
      `SELECT CASE WHEN MAX(loaded_at) IS NULL OR MAX(loaded_at) < DATEADD(HOUR, -24, GETUTCDATE()) THEN 1 ELSE 0 END AS ViolationCount FROM ${t}`,
    );
  });

  it("builds the right query per rule type", () => {
    const schemaTable = "@{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}";
    const queryFor = (rule: SpecDraft["dataQualityRules"][number]) => {
      const activities = childActivities(draftWith({ dataQualityRules: [rule] }));
      return (activities[0] as unknown as { typeProperties: { source: { sqlReaderQuery: string } } }).typeProperties.source.sqlReaderQuery;
    };
    expect(queryFor({ name: "len", column: "sku", rule: "LENGTH_LE", value: "20" })).toBe(
      `SELECT COUNT(*) AS ViolationCount FROM ${schemaTable} WHERE LEN(sku) > 20`,
    );
    expect(queryFor({ name: "rng", column: "age", rule: "RANGE_BETWEEN", value: "0,120" })).toBe(
      `SELECT COUNT(*) AS ViolationCount FROM ${schemaTable} WHERE age < 0 OR age > 120`,
    );
    expect(queryFor({ name: "fmt", column: "code", rule: "REGEX_MATCH", value: "AB%" })).toBe(
      `SELECT COUNT(*) AS ViolationCount FROM ${schemaTable} WHERE PATINDEX('AB%', code) = 0`,
    );
  });

  it("sanitizes a free-text rule name into a safe activity name", () => {
    const draft = draftWith({
      dataQualityRules: [{ name: "no nulls! (v2)", column: "id", rule: "IS_NOT_NULL", enforcement: "block" }],
    });
    const activities = childActivities(draft);
    expect(activities.map((a) => a.name)).toEqual(["DQ_no_nulls___v2__Check", "DQ_no_nulls___v2__Gate", "RouteByLoadingBehavior"]);
  });

  it("skips a rule missing its name or column instead of generating a broken query", () => {
    const draft = draftWith({
      dataQualityRules: [{ name: "", column: "id", rule: "IS_NOT_NULL" }, { name: "no_col", column: "", rule: "IS_NOT_NULL" }],
    });
    expect(childActivities(draft).map((a) => a.name)).toEqual(["RouteByLoadingBehavior"]);
  });

  it("caps generated checks at 5 even if the draft somehow carries more", () => {
    const rules = Array.from({ length: 7 }, (_, i) => ({ name: `r${i}`, column: "id", rule: "IS_NOT_NULL" as const }));
    const activities = childActivities(draftWith({ dataQualityRules: rules }));
    expect(activities.filter((a) => a.name.startsWith("DQ_")).length).toBe(5);
  });
});

// Multi-ADF-instance support (IMPLEMENTATION_PLAN.md A-1/A-2): picking a
// registered ADF instance fills in `globalParameters.json`'s real
// factory/environment name — and only that artifact ("parameter surfaces
// only" decision, not a general per-instance resolution of artifacts).
describe("factoryArtifact / globalParameters.json (multi-instance support)", () => {
  function globalParameters(draft: SpecDraft, environment?: EnvironmentConnection | null) {
    const artifacts = buildGeneratedArtifacts(draft, environment);
    return JSON.parse(artifacts.find((a) => a.fileName === "globalParameters.json")?.content ?? "{}");
  }

  it("stays placeholder-only when no ADF instance is resolved (backward compatible)", () => {
    const parameters = globalParameters(draftWith());
    expect(parameters.name).toBe("<FACTORY_NAME>");
    expect(parameters.properties.globalParameters.Environment.value).toBe("<ENVIRONMENT_NAME>");
  });

  it("fills in the real factory/environment name once an instance is resolved", () => {
    const environment: EnvironmentConnection = {
      name: "PROD_EU",
      storageAccount: "stgprodeu",
      sqlServer: "sql-prod-eu.database.windows.net",
      database: "prod_eu",
      keyVault: "kv-prod-eu",
      factoryName: "contoso-adf-prod-eu",
      subscriptionId: "11111111-1111-1111-1111-111111111111",
      resourceGroup: "rg-adf-prod-eu",
    };
    const parameters = globalParameters(draftWith(), environment);
    expect(parameters.name).toBe("contoso-adf-prod-eu");
    expect(parameters.properties.globalParameters.Environment.value).toBe("PROD_EU");
  });

  it("only affects globalParameters.json — every other artifact is unchanged", () => {
    const environment: EnvironmentConnection = {
      name: "PROD_EU",
      storageAccount: "stgprodeu",
      sqlServer: "sql-prod-eu.database.windows.net",
      database: "prod_eu",
      keyVault: "kv-prod-eu",
      factoryName: "contoso-adf-prod-eu",
      subscriptionId: "11111111-1111-1111-1111-111111111111",
      resourceGroup: "rg-adf-prod-eu",
    };
    const draft = draftWith();
    const withInstance = buildGeneratedArtifacts(draft, environment);
    const without = buildGeneratedArtifacts(draft);
    for (const artifact of without) {
      if (artifact.fileName === "globalParameters.json") continue;
      expect(withInstance.find((a) => a.fileName === artifact.fileName)?.content).toBe(artifact.content);
    }
  });
});

// Medallion layer (Bronze/Silver/Gold) — user request: layer-specific pipeline
// creation with Silver/Gold transformations and layer-wise table naming.

function childPipelineOf(
  draft: SpecDraft,
  layerPrefixes?: Partial<Record<SpecDraft["medallionLayer"], string>>,
): { properties: { activities: Array<Record<string, unknown>> } } {
  const artifacts = buildGeneratedArtifacts(draft, undefined, layerPrefixes);
  return JSON.parse(artifacts.find((a) => a.fileName.endsWith("_child.json"))?.content ?? "{}");
}

function switchCases(child: ReturnType<typeof childPipelineOf>) {
  const route = child.properties.activities.find((a) => a.name === "RouteByLoadingBehavior") as {
    typeProperties: { cases: Array<{ value: string; activities: Array<Record<string, unknown>> }>; defaultActivities: Array<Record<string, unknown>> };
  };
  return route.typeProperties;
}

describe("medallion layer codegen", () => {
  it("bronze (default) generates no transformation activities — unchanged existing behavior", () => {
    const draft = draftWith();
    expect(draft.medallionLayer).toBe("bronze");
    const cases = switchCases(childPipelineOf(draft));
    const fullLoadNames = cases.cases.find((c) => c.value === "FullLoad")!.activities.map((a) => a.name);
    expect(fullLoadNames).toEqual(["LogRunStart_FullLoad", "FullLoadCopy", "LogRunSucceeded_FullLoad", "LogRunFailed_FullLoad"]);
    const defaultNames = cases.defaultActivities.map((a) => a.name);
    expect(defaultNames).not.toContain("LoadToLayerTarget_DeltaLoad");
  });

  it("silver ships a rerunnable schema script for the chain; bronze doesn't (RUN-09)", () => {
    const silver = draftWith({ medallionLayer: "silver", curatedEntity: "c", transformations: [{ name: "d", type: "dedupe", keyColumns: "id" }] });
    const sql = buildGeneratedArtifacts(silver).find((a) => a.fileName === "adf_layer_schemas.sql")?.content ?? "";
    for (const schema of ["raw", "stg", "curated"]) {
      expect(sql).toContain(`IF SCHEMA_ID(N'${schema}') IS NULL EXEC(N'CREATE SCHEMA [${schema}]');`);
    }
    expect(buildGeneratedArtifacts(draftWith()).some((a) => a.fileName === "adf_layer_schemas.sql")).toBe(false);
  });

  it("the Bronze landed table uses the source-system label when set, so two systems on one connector don't collide", () => {
    const silver = { medallionLayer: "silver" as const, curatedEntity: "c", transformations: [{ name: "d", type: "dedupe" as const, keyColumns: "id" }] };
    const text = (d: SpecDraft) => buildGeneratedArtifacts(d).map((a) => a.content).join("\n");
    const unlabelled = text(draftWith(silver));
    const labelled = text(draftWith({ ...silver, sourceSystem: "Epic Clarity" }));
    expect(unlabelled).toMatch(/raw\.sql_server_\w+/);
    expect(labelled).toMatch(/raw\.epic_clarity_\w+/);
    expect(labelled).not.toMatch(/raw\.sql_server_\w+/);
  });

  it("write-only Lookups end with a SELECT and use sqlReaderQuery (RUN-02)", () => {
    const child = childPipelineOf(draftWith({ enableCopyLog: true }));
    const logStart = switchCases(child).cases.find((c) => c.value === "FullLoad")!.activities.find((a) => a.name === "LogRunStart_FullLoad") as unknown as {
      typeProperties: { source: Record<string, string> };
    };
    expect(logStart.typeProperties.source.sqlReaderQuery.endsWith("; SELECT 1 AS Done")).toBe(true);
    expect(logStart.typeProperties.source.query).toBeUndefined();
  });

  it("silver appends a transformation chain after the copy, ending with a load into curated.<entity>", () => {
    const draft = draftWith({
      medallionLayer: "silver",
      curatedEntity: "gl_transaction",
      loadTypeMode: "full",
      transformations: [
        { name: "dedupe_gl", type: "dedupe", keyColumns: "transaction_id", orderColumn: "modified_date" },
      ],
    });
    const cases = switchCases(childPipelineOf(draft));
    const fullLoad = cases.cases.find((c) => c.value === "FullLoad")!.activities;
    const names = fullLoad.map((a) => a.name);
    expect(names).toContain("Transform_dedupe_gl_FullLoad");
    expect(names).toContain("LoadToLayerTarget_FullLoad");
    // dedupe isn't terminal -> the synthesized load activity writes into the real target table.
    const load = fullLoad.find((a) => a.name === "LoadToLayerTarget_FullLoad") as { typeProperties: { scripts: Array<{ text: string }> } };
    expect(load.typeProperties.scripts[0].text).toContain("INSERT INTO curated.gl_transaction");
    // LogRunSucceeded should now wait on the transformation chain, not just the copy.
    const logSucceeded = fullLoad.find((a) => a.name === "LogRunSucceeded_FullLoad") as { dependsOn: Array<{ activity: string }> };
    expect(logSucceeded.dependsOn[0].activity).toBe("DropStagingTables_FullLoad");
    // RUN-06: per-object, per-run staging tables, dropped after the load.
    const drop = fullLoad.find((a) => a.name === "DropStagingTables_FullLoad") as { dependsOn: Array<{ activity: string }>; typeProperties: { scripts: Array<{ text: string }> } };
    expect(drop.dependsOn[0].activity).toBe("LoadToLayerTarget_FullLoad");
    const text = drop.typeProperties.scripts[0].text;
    expect(text.startsWith("DROP TABLE IF EXISTS stg.")).toBe(true);
    expect(text.endsWith("_step1_@{pipeline().parameters.TaskId}_@{replace(pipeline().RunId, '-', '')};")).toBe(true);
  });

  it("silver DQ checks run on the layer target after the load, not on the source (E2E-09)", () => {
    const rule = { name: "cust_id_nn", column: "customer_id", rule: "IS_NOT_NULL" as const, enforcement: "block" as const };
    const draft = draftWith({
      medallionLayer: "silver",
      curatedEntity: "customer",
      transformations: [{ name: "conform", type: "conform_columns", columns: "cust_id:customer_id" }],
      dataQualityRules: [rule],
    });
    const activities = childPipelineOf(draft).properties.activities;
    const names = activities.map((a) => a.name);
    expect(names.indexOf("RouteByLoadingBehavior")).toBeLessThan(names.indexOf("DQ_cust_id_nn_Check"));
    const check = activities.find((a) => a.name === "DQ_cust_id_nn_Check") as {
      dependsOn: unknown;
      typeProperties: { source: { sqlReaderQuery: string } };
    };
    expect(check.dependsOn).toEqual([{ activity: "RouteByLoadingBehavior", dependencyConditions: ["Succeeded"] }]);
    expect(check.typeProperties.source.sqlReaderQuery).toContain("FROM curated.customer WHERE customer_id IS NULL");
    expect(activities.find((a) => a.name === "RouteByLoadingBehavior")).not.toHaveProperty("dependsOn");
  });

  it("DQ checks on an SCD2 target read only current rows (RUN-13)", () => {
    const draft = draftWith({
      medallionLayer: "silver",
      curatedEntity: "customer",
      transformations: [{ name: "hist", type: "scd2", keyColumns: "customer_id", orderColumn: "modified_date", currentFlagColumn: "is_current" }],
      dataQualityRules: [{ name: "cust_uq", column: "customer_id", rule: "UNIQUE", enforcement: "block" }],
    });
    const check = childPipelineOf(draft).properties.activities.find((a) => a.name === "DQ_cust_uq_Check") as {
      typeProperties: { source: { sqlReaderQuery: string } };
    };
    expect(check.typeProperties.source.sqlReaderQuery).toContain("FROM (SELECT * FROM curated.customer WHERE is_current = 1) cur GROUP BY customer_id");
  });

  it("emits every transformation rule up to MAX_TRANSFORMATION_RULES, not just 5 (G1)", () => {
    const draft = draftWith({
      medallionLayer: "silver",
      curatedEntity: "customer",
      loadTypeMode: "full",
      transformations: Array.from({ length: 7 }, (_, i) => ({
        name: `dedupe_${i}`, type: "dedupe" as const, keyColumns: "id", orderColumn: "modified_date",
      })),
    });
    const fullLoad = switchCases(childPipelineOf(draft)).cases.find((c) => c.value === "FullLoad")!.activities;
    expect(fullLoad.filter((a) => String(a.name).startsWith("Transform_"))).toHaveLength(7);
  });

  it("gives every transformation Script activity the target linked service (G2)", () => {
    const draft = draftWith({
      medallionLayer: "silver",
      curatedEntity: "customer",
      loadTypeMode: "full",
      transformations: [{ name: "d", type: "dedupe", keyColumns: "id", orderColumn: "modified_date" }],
    });
    const scripts = switchCases(childPipelineOf(draft)).cases.find((c) => c.value === "FullLoad")!.activities.filter((a) => a.type === "Script");
    expect(scripts).toHaveLength(3); // transform, layer load, staging drop
    for (const s of scripts) {
      expect(s.linkedServiceName).toEqual({ referenceName: draft.targetLinkedService.name, type: "LinkedServiceReference" });
    }
  });

  it("omits trigger/globalParameters/parameters.json only when opted out (gap #28)", () => {
    const names = (d: SpecDraft) => buildGeneratedArtifacts(d).map((a) => a.fileName);
    const all = names(draftWith());
    expect(all).toEqual(expect.arrayContaining(["globalParameters.json", "parameters.json"]));
    expect(all.some((n) => n.startsWith("TR_"))).toBe(true);
    const none = names(draftWith({ generateTrigger: false, generateGlobalParameters: false, generateParametersFile: false }));
    expect(none).not.toContain("globalParameters.json");
    expect(none).not.toContain("parameters.json");
    expect(none.some((n) => n.startsWith("TR_"))).toBe(false);
    expect(none.length).toBe(all.length - 3);
  });

  it("emits arm-template-parameters-definition.json only when opted in", () => {
    const names = (d: SpecDraft) => buildGeneratedArtifacts(d).map((a) => a.fileName);
    expect(names(draftWith())).not.toContain("arm-template-parameters-definition.json");
    const artifact = buildGeneratedArtifacts(draftWith({ generateArmParameterDefinition: true })).find(
      (a) => a.fileName === "arm-template-parameters-definition.json",
    );
    const parsed = JSON.parse(artifact?.content ?? "{}");
    expect(parsed["Microsoft.DataFactory/factories/linkedServices"]["*"].properties.typeProperties.connectionString).toBe(
      "|:-connectionString:secureString",
    );
    expect(parsed["Microsoft.DataFactory/factories/pipelines"].properties.parameters["*"].defaultValue).toBe("=");
  });

  it("emits lineage.json only when opted in, built from draft values alone", () => {
    const names = (d: SpecDraft) => buildGeneratedArtifacts(d).map((a) => a.fileName);
    expect(names(draftWith())).not.toContain("lineage.json");
    const draft = draftWith({ generateLineage: true });
    const artifact = buildGeneratedArtifacts(draft).find((a) => a.fileName === "lineage.json");
    const parsed = JSON.parse(artifact?.content ?? "{}");
    expect(parsed.job.name).toMatch(/_master$/);
    expect(parsed.inputs[0].namespace).toBe(`${draft.sourceType}://${draft.sourceLinkedService.name}`);
    expect(parsed.outputs[0]).toEqual({ namespace: `${draft.targetType}://${draft.targetLinkedService.name}`, name: draft.targetPath });
  });

  it("stamps trimmed, de-duplicated annotations on every pipeline only when set (gap #33)", () => {
    const pipelines = (d: SpecDraft) =>
      buildGeneratedArtifacts(d)
        .filter((a) => a.artifactType === "pipeline")
        .map((a) => JSON.parse(a.content ?? "{}").properties.annotations);
    expect(pipelines(draftWith()).every((a) => a.length === 0)).toBe(true);
    const tagged = pipelines(draftWith({ annotations: [" domain:finance", "", "domain:finance", "owner:de"] }));
    expect(tagged.length).toBeGreaterThan(1);
    for (const a of tagged) expect(a).toEqual(["domain:finance", "owner:de"]);
  });

  it("sets concurrency 1 on the master pipeline but never on the child (G6)", () => {
    const artifacts = buildGeneratedArtifacts(draftWith());
    const parse = (suffix: string) => JSON.parse(artifacts.find((a) => a.fileName.endsWith(suffix))?.content ?? "{}");
    expect(parse("_master.json").properties.concurrency).toBe(1);
    expect(parse("_child.json").properties).not.toHaveProperty("concurrency");
  });

  it("silver with a terminal (scd2) rule writes directly to the target table, no synthesized load step", () => {
    const draft = draftWith({
      medallionLayer: "silver",
      curatedEntity: "gl_transaction",
      loadTypeMode: "full",
      transformations: [{ name: "scd2_gl", type: "scd2", keyColumns: "transaction_id", orderColumn: "modified_date" }],
    });
    const fullLoad = switchCases(childPipelineOf(draft)).cases.find((c) => c.value === "FullLoad")!.activities;
    const names = fullLoad.map((a) => a.name);
    expect(names).toContain("Transform_scd2_gl_FullLoad");
    expect(names).not.toContain("LoadToLayerTarget_FullLoad");
    const scd2 = fullLoad.find((a) => a.name === "Transform_scd2_gl_FullLoad") as { typeProperties: { scripts: Array<{ text: string }> } };
    expect(scd2.typeProperties.scripts[0].text).toContain("MERGE curated.gl_transaction AS tgt");
  });

  it("gold appends transformations to the delta (default) branch, writing into mart.<domain>_<entity>", () => {
    const draft = draftWith({
      medallionLayer: "gold",
      curatedEntity: "gl_fact",
      martDomain: "finance",
      transformations: [
        { name: "agg_gl", type: "aggregate", groupByColumns: "customer_id", aggregateExpression: "SUM(amount) AS total_amount" },
      ],
    });
    const defaultActivities = switchCases(childPipelineOf(draft)).defaultActivities;
    const names = defaultActivities.map((a) => a.name);
    expect(names).toContain("Transform_agg_gl_DeltaLoad");
    expect(names).toContain("LoadToLayerTarget_DeltaLoad");
    const load = defaultActivities.find((a) => a.name === "LoadToLayerTarget_DeltaLoad") as { typeProperties: { scripts: Array<{ text: string }> } };
    expect(load.typeProperties.scripts[0].text).toContain("INSERT INTO mart.finance_gl_fact");
  });

  it("§25.3 Standards 'Layer prefixes': a project's custom zone prefix threads through to the generated load activity", () => {
    const draft = draftWith({
      medallionLayer: "silver",
      curatedEntity: "gl_transaction",
      transformations: [{ name: "dedupe_gl", type: "dedupe", keyColumns: "id", orderColumn: "modified_date" }],
    });
    const activities = switchCases(childPipelineOf(draft, { silver: "cur" })).cases.find((c) => c.value === "FullLoad")?.activities ?? [];
    const load = activities.find((a) => a.name === "LoadToLayerTarget_FullLoad") as { typeProperties: { scripts: Array<{ text: string }> } };
    expect(load.typeProperties.scripts[0].text).toContain("INSERT INTO cur.gl_transaction");
  });

  it("groups generated pipelines by layer in the ADF folder tree", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ medallionLayer: "silver", curatedEntity: "gl_transaction", transformations: [{ name: "d", type: "dedupe", keyColumns: "id", orderColumn: "modified_date" }] }));
    const master = JSON.parse(artifacts.find((a) => a.fileName.endsWith("_master.json"))?.content ?? "{}");
    expect(master.properties.folder.name).toBe("metadata-driven/silver");
  });
});

// §33.7 — the two additive sibling control-table artifacts, generated only when built from a
// picked registry object (silverObjectRef/martObjectRef set).
describe("curatedControlTableSql / martControlTableSql (§33.7)", () => {
  const silverRules = [{ name: "conform", type: "conform_columns" as const, columns: "a:b" }];
  const goldRules = [{ name: "agg", type: "aggregate" as const, groupByColumns: "customer_id", aggregateExpression: "SUM(amount) AS total" }];

  it("emits adf_curated_control_table.sql only when silverObjectRef is set on a silver pipeline", () => {
    const withoutRef = buildGeneratedArtifacts(
      draftWith({ medallionLayer: "silver", curatedEntity: "gl_transaction", transformations: silverRules }),
    );
    expect(withoutRef.find((a) => a.fileName === "adf_curated_control_table.sql")).toBeUndefined();

    const withRef = buildGeneratedArtifacts(
      draftWith({
        medallionLayer: "silver",
        curatedEntity: "gl_transaction",
        transformations: silverRules,
        silverObjectRef: "gl_transaction",
        registryTag: "finance",
        registrySourceObjectKeys: ["erp.gl_transaction"],
      }),
    );
    const artifact = withRef.find((a) => a.fileName === "adf_curated_control_table.sql");
    expect(artifact).toBeDefined();
    expect(artifact!.content).toContain("CREATE TABLE control.adf_curated_control_table");
    expect(artifact!.content).toContain("N'gl_transaction', N'finance', N'gl_transaction'");
    expect(artifact!.content).toContain("N'curated.gl_transaction'");
    expect(artifact!.content).toContain("N'erp.gl_transaction'");
    // A silver pipeline with a ref never emits the mart-side artifact.
    expect(withRef.find((a) => a.fileName === "adf_mart_control_table.sql")).toBeUndefined();
  });

  it("emits adf_mart_control_table.sql only when martObjectRef is set on a gold pipeline", () => {
    const withoutRef = buildGeneratedArtifacts(
      draftWith({ medallionLayer: "gold", curatedEntity: "gl_fact", martDomain: "finance", transformations: goldRules }),
    );
    expect(withoutRef.find((a) => a.fileName === "adf_mart_control_table.sql")).toBeUndefined();

    const withRef = buildGeneratedArtifacts(
      draftWith({
        medallionLayer: "gold",
        curatedEntity: "gl_fact",
        martDomain: "finance",
        transformations: goldRules,
        martObjectRef: "gl_fact",
        registryTag: "finance",
        registrySourceObjectKeys: ["gl_transaction"],
      }),
    );
    const artifact = withRef.find((a) => a.fileName === "adf_mart_control_table.sql");
    expect(artifact).toBeDefined();
    expect(artifact!.content).toContain("CREATE TABLE control.adf_mart_control_table");
    expect(artifact!.content).toContain("N'mart.finance_gl_fact'");
  });

  it("bronze pipelines never emit either sibling artifact, ref or not", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ silverObjectRef: "x", martObjectRef: "y" } as Partial<SpecDraft>));
    expect(artifacts.find((a) => a.fileName === "adf_curated_control_table.sql")).toBeUndefined();
    expect(artifacts.find((a) => a.fileName === "adf_mart_control_table.sql")).toBeUndefined();
  });

  it("every other artifact is byte-identical whether or not a registry ref is set", () => {
    const base = draftWith({ medallionLayer: "silver", curatedEntity: "gl_transaction", transformations: silverRules });
    const withRef = { ...base, silverObjectRef: "gl_transaction", registryTag: "finance" };
    const a1 = buildGeneratedArtifacts(base).filter((a) => a.fileName !== "adf_curated_control_table.sql");
    const a2 = buildGeneratedArtifacts(withRef).filter((a) => a.fileName !== "adf_curated_control_table.sql");
    expect(a2.map((a) => [a.fileName, a.content])).toEqual(a1.map((a) => [a.fileName, a.content]));
  });
});

// §24.5 — independent master/child pipeline artifact-name overrides, and the "rename the master
// pipeline" / "append to an existing pipeline" Build-wizard feature they back.
describe("masterPipelineName / childPipelineName overrides", () => {
  it("falls back to PL_{name}_master/_child when unset", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ name: "orders_hub" }));
    expect(artifacts.map((a) => a.fileName)).toEqual(expect.arrayContaining(["PL_orders_hub_master.json", "PL_orders_hub_child.json"]));
  });

  it("uses the explicit override for the generated file name and internal references", () => {
    const artifacts = buildGeneratedArtifacts(
      draftWith({ name: "orders_hub", masterPipelineName: "PL_OrdersOrchestrator", childPipelineName: "PL_OrdersRouter" }),
    );
    expect(artifacts.map((a) => a.fileName)).toEqual(
      expect.arrayContaining(["PL_OrdersOrchestrator.json", "PL_OrdersRouter.json"]),
    );
    expect(artifacts.find((a) => a.fileName === "PL_orders_hub_master.json")).toBeUndefined();
    expect(artifacts.find((a) => a.fileName === "PL_orders_hub_child.json")).toBeUndefined();

    const master = JSON.parse(artifacts.find((a) => a.fileName === "PL_OrdersOrchestrator.json")?.content ?? "{}");
    expect(master.name).toBe("PL_OrdersOrchestrator");
    // The master's ExecutePipeline activity must reference the *overridden* child name, not the
    // PL_{name}_child fallback.
    const executePipeline = JSON.stringify(master).includes('"referenceName":"PL_OrdersRouter"');
    expect(executePipeline).toBe(true);

    const child = JSON.parse(artifacts.find((a) => a.fileName === "PL_OrdersRouter.json")?.content ?? "{}");
    expect(child.name).toBe("PL_OrdersRouter");
  });

  it("every non-pipeline, non-trigger artifact is byte-identical whether or not an override is set", () => {
    // TR_{name}.json legitimately changes too — its schedule references the master pipeline by
    // name, same as the master's own ExecutePipeline activity references the child — both are
    // covered by the previous test instead.
    const base = draftWith({ name: "orders_hub" });
    const withOverride = { ...base, masterPipelineName: "PL_OrdersOrchestrator", childPipelineName: "PL_OrdersRouter" };
    const strip = (list: ReturnType<typeof buildGeneratedArtifacts>) =>
      list.filter((a) => !a.fileName.startsWith("PL_") && !a.fileName.startsWith("TR_")).map((a) => [a.fileName, a.content]);
    expect(strip(buildGeneratedArtifacts(withOverride))).toEqual(strip(buildGeneratedArtifacts(base)));
  });
});

describe("pipeline patterns (§24.3/§24.12, gap #5)", () => {
  it("master_child (the default/unset pattern) is unaffected", () => {
    const withDefault = buildGeneratedArtifacts(draftWith());
    const withExplicit = buildGeneratedArtifacts(draftWith({ pattern: "master_child" }));
    expect(withDefault.map((a) => [a.fileName, a.content])).toEqual(withExplicit.map((a) => [a.fileName, a.content]));
    expect(withDefault.some((a) => a.fileName.endsWith("_master.json"))).toBe(true);
    expect(withDefault.some((a) => a.fileName.endsWith("_child.json"))).toBe(true);
  });

  it("child_only emits just the child pipeline, nothing else", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ pattern: "child_only", childPipelineName: "PL_existing_child" }));
    expect(artifacts).toHaveLength(1);
    expect(artifacts[0].fileName).toBe("PL_existing_child.json");
    const pipeline = JSON.parse(artifacts[0].content ?? "{}");
    expect(pipeline.name).toBe("PL_existing_child");
  });

  it("master_only emits just the master pipeline, referencing an existing child by name", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ pattern: "master_only", childPipelineName: "PL_existing_child" }));
    expect(artifacts).toHaveLength(1);
    expect(artifacts[0].fileName).toMatch(/_master\.json$/);
    const pipeline = JSON.parse(artifacts[0].content ?? "{}");
    expect(JSON.stringify(pipeline)).toContain('"referenceName":"PL_existing_child"');
  });

  it("single_pipeline has no child artifact and loops inline via ForEach", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ pattern: "single_pipeline" }));
    expect(artifacts.some((a) => a.fileName.endsWith("_master.json"))).toBe(false);
    expect(artifacts.some((a) => a.fileName.endsWith("_child.json"))).toBe(false);
    const pipeline = JSON.parse(artifacts.find((a) => a.fileName === "PL_customer_incremental.json")?.content ?? "{}");
    expect(pipeline.name).toBe("PL_customer_incremental");
    const forEach = pipeline.properties.activities.find((a: { name: string }) => a.name === "ForEachExecutedObject");
    expect(forEach.type).toBe("ForEach");
    expect(JSON.stringify(forEach)).not.toContain("ExecutePipeline");
    // Every per-row reference is item()-based, not a pipeline parameter (that's now the master's job).
    expect(JSON.stringify(forEach)).not.toContain("pipeline().parameters.LoadingBehavior");
    expect(JSON.stringify(forEach)).toContain("item().LoadingBehavior");
    // adf_control_table.sql still gets generated (single_pipeline is still control-table-driven).
    expect(artifacts.some((a) => a.fileName === "adf_control_table.sql")).toBe(true);
  });

  it("independent_full logs a literal FullLoad, never a dangling LoadingBehavior parameter reference", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ pattern: "independent_full", loadTypeMode: "full", enableCopyLog: true }));
    const pipeline = JSON.parse(artifacts.find((a) => a.fileName === "PL_customer_incremental.json")?.content ?? "{}");
    const json = JSON.stringify(pipeline);
    expect(json).toContain("'FullLoad'");
    expect(json).not.toContain("pipeline().parameters.LoadingBehavior");
  });

  it("independent_full has one Copy activity, no control table, no watermark", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ pattern: "independent_full", loadTypeMode: "full" }));
    expect(artifacts.some((a) => a.fileName === "adf_control_table.sql")).toBe(false);
    expect(artifacts.some((a) => a.fileName === "adf_watermark_table.sql")).toBe(false);
    const pipeline = JSON.parse(artifacts.find((a) => a.fileName === "PL_customer_incremental.json")?.content ?? "{}");
    const names = pipeline.properties.activities.map((a: { name: string }) => a.name);
    expect(names).toContain("FullLoadCopy");
    expect(names).not.toContain("RouteByLoadingBehavior");
    expect("WatermarkTable" in pipeline.properties.parameters).toBe(false);
  });

  it("independent_watermark has a watermark-delta chain and its own single-row watermark table", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ pattern: "independent_watermark" }));
    expect(artifacts.some((a) => a.fileName === "adf_control_table.sql")).toBe(false);
    const watermarkSql = artifacts.find((a) => a.fileName === "adf_watermark_table.sql")?.content ?? "";
    expect(watermarkSql).toContain("CREATE TABLE control.watermarks (");
    expect(watermarkSql).toContain("TaskId = 1");
    const pipeline = JSON.parse(artifacts.find((a) => a.fileName === "PL_customer_incremental.json")?.content ?? "{}");
    const names = pipeline.properties.activities.map((a: { name: string }) => a.name);
    expect(names).toEqual(expect.arrayContaining(["LookupLastWatermark", "LookupNewWatermark", "CopyDeltaData", "UpdateWatermark"]));
  });

  it("a custom pipelineName override is honored for single/independent patterns and referenced by the trigger", () => {
    const artifacts = buildGeneratedArtifacts(
      draftWith({ pattern: "independent_full", pipelineName: "PL_CustomerFullCopy", triggerEnabled: true }),
    );
    expect(artifacts.some((a) => a.fileName === "PL_CustomerFullCopy.json")).toBe(true);
    const trigger = JSON.parse(artifacts.find((a) => a.fileName.startsWith("TR_"))?.content ?? "{}");
    expect(JSON.stringify(trigger)).toContain('"referenceName":"PL_CustomerFullCopy"');
  });
});

// §24.6 "use existing, don't regenerate" (found missing 2026-09-20: picking a saved Connection only
// ever prefilled the draft's LS/DS fields — the pipeline always regenerated a fresh
// linkedService/dataset JSON regardless, so "reuse" saved typing but never actually skipped an
// artifact as AGENTS.md §24.6 describes). `sourceConnectionReused`/`targetConnectionReused` let the
// Build wizard's "already pushed to the target repo" checkbox actually omit that artifact.
describe("sourceConnectionReused / targetConnectionReused (§24.6)", () => {
  it("generates every LS/DS artifact by default (unchanged behavior)", () => {
    const artifacts = buildGeneratedArtifacts(draftWith());
    expect(artifacts.some((a) => a.artifactType === "linked_service")).toBe(true);
    expect(artifacts.some((a) => a.artifactType === "dataset")).toBe(true);
  });

  it("omits only the source LS/DS when sourceConnectionReused is set", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ sourceConnectionReused: true }));
    const lsNames = artifacts.filter((a) => a.artifactType === "linked_service").map((a) => a.fileName);
    const dsNames = artifacts.filter((a) => a.artifactType === "dataset").map((a) => a.fileName);
    expect(lsNames).not.toContain(`${emptySpecDraft().sourceLinkedService.name}.json`);
    expect(lsNames.length).toBe(1); // target LS still generated
    expect(dsNames.length).toBe(1); // target DS still generated
  });

  it("omits only the target LS/DS when targetConnectionReused is set", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ targetConnectionReused: true }));
    expect(artifacts.filter((a) => a.artifactType === "linked_service").length).toBe(1); // source LS still generated
    expect(artifacts.filter((a) => a.artifactType === "dataset").length).toBe(1); // source DS still generated
  });

  it("omits both when both are reused, but the pipeline still references them by name", () => {
    const draft = draftWith({ sourceConnectionReused: true, targetConnectionReused: true });
    const artifacts = buildGeneratedArtifacts(draft);
    expect(artifacts.some((a) => a.artifactType === "linked_service")).toBe(false);
    expect(artifacts.some((a) => a.artifactType === "dataset")).toBe(false);
    const master = JSON.parse(artifacts.find((a) => a.fileName.endsWith("_master.json"))?.content ?? "{}");
    expect(JSON.stringify(master)).toContain(draft.sourceLinkedService.name);
    expect(JSON.stringify(master)).toContain(draft.targetLinkedService.name);
  });
});

describe("child dispatch (gap #30)", () => {
  const childDispatch = [
    { matchValue: "finance", childPipelineName: "PL_finance_child" },
    { matchValue: "hr", childPipelineName: "PL_hr_child" },
  ];
  const masterOf = (draft: SpecDraft) =>
    JSON.parse(buildGeneratedArtifacts(draft).find((a) => a.fileName === "PL_customer_incremental_master.json")?.content ?? "{}");

  it("keeps the single ExecutePipeline when no dispatch is configured", () => {
    const master = masterOf(draftWith());
    expect(master.properties.activities[1].typeProperties.activities.map((a: { type: string }) => a.type)).toEqual(["ExecutePipeline"]);
    expect(controlTableSql(draftWith())).not.toContain("TaskGroup");
  });

  it("routes rows through a Switch on TaskGroup, defaulting to the own child", () => {
    const draft = draftWith({ childDispatch });
    const [dispatch] = masterOf(draft).properties.activities[1].typeProperties.activities;
    expect(dispatch.type).toBe("Switch");
    expect(dispatch.typeProperties.cases.map((c: { value: string }) => c.value)).toEqual(["finance", "hr"]);
    expect(dispatch.typeProperties.cases[1].activities[0].typeProperties.pipeline.referenceName).toBe("PL_hr_child");
    expect(dispatch.typeProperties.defaultActivities[0].typeProperties.pipeline.referenceName).toBe("PL_customer_incremental_child");
    expect(masterOf(draft).properties.activities[0].typeProperties.source.sqlReaderQuery).toContain(", TaskGroup FROM");
    expect(controlTableSql(draft)).toContain("TaskGroup");
  });
});

describe("copyTypeConversion (schema-drift slice)", () => {
  function copyTranslators(draft: SpecDraft): unknown[] {
    const out: unknown[] = [];
    const walk = (node: unknown): void => {
      if (Array.isArray(node)) return node.forEach(walk);
      if (node && typeof node === "object") {
        const rec = node as Record<string, unknown>;
        if (rec.type === "Copy") out.push((rec.typeProperties as Record<string, unknown>).translator);
        Object.values(rec).forEach(walk);
      }
    };
    for (const a of buildGeneratedArtifacts(draft)) if (a.fileName.endsWith(".json")) walk(JSON.parse(a.content ?? "{}"));
    return out;
  }

  it("emits no translator by default", () => {
    const t = copyTranslators(draftWith());
    expect(t.length).toBeGreaterThan(0);
    expect(t.every((x) => x === undefined)).toBe(true);
  });

  it("adds a type-converting TabularTranslator to every Copy when enabled", () => {
    const t = copyTranslators(draftWith({ copyTypeConversion: true })) as Array<Record<string, unknown>>;
    expect(t.length).toBeGreaterThan(0);
    for (const x of t) {
      expect(x).toMatchObject({ type: "TabularTranslator", typeConversion: true, typeConversionSettings: { allowDataTruncation: false } });
    }
  });

  it("emits explicit column mappings, alone or alongside type conversion", () => {
    const columnMappings = [{ source: "CUST_NM", sink: "customer_name" }, { source: " ", sink: "" }];
    const mapped = [{ source: { name: "CUST_NM" }, sink: { name: "customer_name" } }];
    for (const x of copyTranslators(draftWith({ columnMappings }))) {
      expect(x).toEqual({ type: "TabularTranslator", mappings: mapped });
    }
    for (const x of copyTranslators(draftWith({ columnMappings, copyTypeConversion: true }))) {
      expect(x).toMatchObject({ typeConversion: true, mappings: mapped });
    }
  });
});

describe("per-object column mappings", () => {
  const rows = [
    { object: "dbo.customer", columnMappings: [{ source: "CUST_NM", sink: "customer_name" }] },
    { object: "dbo.orders" },
  ];

  it("stores each row's translator in the control table and reads it in the child Copy", () => {
    const artifacts = buildGeneratedArtifacts(
      draftWith({ sourceObjects: rows, columnMappings: [{ source: "ORD_NO", sink: "order_number" }] }),
    );
    const sql = artifacts.find((a) => a.fileName === "adf_control_table.sql")?.content ?? "";
    expect(sql).toContain("ColumnMapping     NVARCHAR(MAX)");
    expect(sql).toContain('"mappings":[{"source":{"name":"CUST_NM"},"sink":{"name":"customer_name"}}]');
    expect(sql).toContain('"mappings":[{"source":{"name":"ORD_NO"},"sink":{"name":"order_number"}}]');
    const master = artifacts.find((a) => a.fileName.endsWith("_master.json"))?.content ?? "";
    expect(master).toContain(", ColumnMapping FROM");
    const child = artifacts.find((a) => a.fileName.endsWith("_child.json"))?.content ?? "";
    expect(child.split("@json(pipeline().parameters.ColumnMapping)").length - 1).toBe(2);
  });

  it("reads @item().ColumnMapping in the single-pipeline pattern", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ pattern: "single_pipeline", sourceObjects: rows }));
    const pipeline = artifacts.find((a) => a.artifactType === "pipeline")?.content ?? "";
    expect(pipeline).toContain("@json(item().ColumnMapping)");
    expect(pipeline).not.toContain("pipeline().parameters.ColumnMapping");
  });

  it("leaves output unchanged when no object has its own mappings", () => {
    const artifacts = buildGeneratedArtifacts(draftWith({ sourceObjects: [{ object: "dbo.customer" }, { object: "dbo.orders" }] }));
    expect(artifacts.every((a) => !(a.content ?? "").includes("ColumnMapping"))).toBe(true);
  });
});

describe("hard-delete handling", () => {
  function childActivities(draft: SpecDraft): Array<Record<string, unknown>> {
    const child = JSON.parse(buildGeneratedArtifacts(draft).find((a) => a.fileName.endsWith("_child.json"))?.content ?? "{}");
    const route = child.properties.activities.find((a: { name: string }) => a.name === "RouteByLoadingBehavior");
    return route.typeProperties.defaultActivities ?? route.typeProperties.default ?? [];
  }

  it("emits nothing extra when off", () => {
    const names = childActivities(draftWith({ upsertKeyColumns: "id" })).map((a) => a.name);
    expect(names).not.toContain("ApplySoftDeletes");
  });

  it("soft_delete stages keys and flags missing rows before the watermark advances", () => {
    const acts = childActivities(draftWith({ deleteHandling: "soft_delete", upsertKeyColumns: "id" }));
    const names = acts.map((a) => a.name);
    expect(names.indexOf("StageSourceKeys")).toBeGreaterThan(names.indexOf("CopyDeltaData"));
    const update = acts.find((a) => a.name === "UpdateWatermark") as { dependsOn: Array<{ activity: string }> };
    expect(update.dependsOn[0].activity).toBe("ApplySoftDeletes");
    const apply = JSON.stringify(acts.find((a) => a.name === "ApplySoftDeletes"));
    expect(apply).toContain("_is_deleted = 1");
    expect(apply).toContain("NOT EXISTS");
  });

  it("cdc reads cdc.fn_cdc_get_all_changes for the default per-object capture instance", () => {
    const acts = childActivities(draftWith({ deleteHandling: "cdc", upsertKeyColumns: "id", softDeleteColumn: "is_removed" }));
    const stage = JSON.stringify(acts.find((a) => a.name === "StageCdcChanges"));
    expect(stage).toContain("cdc.fn_cdc_get_all_changes_@{pipeline().parameters.SchemaName}_@{pipeline().parameters.TableName}");
    expect(JSON.stringify(acts.find((a) => a.name === "ApplySoftDeletes"))).toContain("is_removed = 1");
  });
});

describe("ADF envelope shape (gaps.md RUN-03/05/07/15)", () => {
  const artifacts = buildGeneratedArtifacts(draftWith());
  const parse = (suffix: string, type?: string) =>
    JSON.parse(artifacts.find((a) => a.fileName.endsWith(suffix) && (!type || a.artifactType === type))?.content ?? "{}");

  it("child Switch uses on/defaultActivities; master ForEach reads every control row", () => {
    const route = parse("_child.json").properties.activities.find((a: { name: string }) => a.name === "RouteByLoadingBehavior");
    expect(route.typeProperties.on).toEqual({ value: "@pipeline().parameters.LoadingBehavior", type: "Expression" });
    expect(route.typeProperties).not.toHaveProperty("default");
    const [lookup, forEach] = parse("_master.json").properties.activities;
    expect(lookup.typeProperties.firstRowOnly).toBe(false);
    expect(forEach.typeProperties.items.value).toBe("@activity('ReadObjectsFromControlTable').output.value");
    expect(forEach.dependsOn[0].activity).toBe("ReadObjectsFromControlTable");
    expect(forEach).not.toHaveProperty("activities");
  });

  it("table dataset keeps schema/table in typeProperties and guards control-table seeds", () => {
    const props = parse("_source.json", "dataset").properties;
    expect(Object.keys(props.typeProperties)).toEqual(expect.arrayContaining(["schema", "table"]));
    expect(props).not.toHaveProperty("sqlTableType");
    const sql = artifacts.find((a) => a.fileName.endsWith("adf_control_table.sql"))?.content ?? "";
    expect(sql).toMatch(/IF NOT EXISTS \(SELECT 1 FROM [^\n]+\)\nINSERT INTO/);
  });
});

describe("database target (gaps.md RUN-15/16)", () => {
  const sqlDraft = (overrides: Partial<SpecDraft> = {}) => {
    const base = draftWith();
    return draftWith({
      targetType: "azure_sql_database",
      targetDataset: { ...base.targetDataset, schema: "raw", table: "customer" },
      ...overrides,
    });
  };
  const fullLoadSink = (draft: SpecDraft) => {
    const child = JSON.parse(buildGeneratedArtifacts(draft).find((a) => a.fileName.endsWith("_child.json"))?.content ?? "{}");
    const route = child.properties.activities.find((a: { name: string }) => a.name === "RouteByLoadingBehavior");
    const copy = route.typeProperties.cases
      .flatMap((c: { activities: { name: string }[] }) => c.activities)
      .find((a: { name: string }) => a.name === "FullLoadCopy");
    expect(copy.typeProperties).not.toHaveProperty("truncate");
    return copy.typeProperties.sink;
  };

  it("seeds the control table's Target with schema.table, not the folder path", () => {
    const sql = buildGeneratedArtifacts(sqlDraft()).find((a) => a.fileName.endsWith("adf_control_table.sql"))?.content ?? "";
    expect(sql).toContain("Target = N'raw.customer'");
  });

  it("truncates a database target with preCopyScript on FullLoad only when asked", () => {
    expect(fullLoadSink(sqlDraft({ truncateTarget: true })).preCopyScript).toBe("TRUNCATE TABLE raw.customer");
    expect(fullLoadSink(sqlDraft())).not.toHaveProperty("preCopyScript");
    expect(fullLoadSink(draftWith({ truncateTarget: true }))).not.toHaveProperty("preCopyScript");
  });
});

describe("healthcare medallion findings (IMPLEMENTATION_PLAN.md cross-cutting #9)", () => {
  const silverDb = (overrides: Partial<SpecDraft> = {}) =>
    draftWith({
      targetType: "azure_sql_database",
      targetLinkedService: defaultLinkedServiceDraft("target", "azure_sql_database"),
      targetDataset: { ...defaultDatasetDraft("target", "azure_sql_database"), schema: "", table: "" },
      medallionLayer: "silver",
      curatedEntity: "patient",
      sourceSystem: "epic",
      sourceObject: "dbo.PATIENT",
      sourceObjects: [{ object: "dbo.PATIENT" }],
      transformations: [{ name: "d", type: "dedupe", keyColumns: "id", orderColumn: "modified_date" }],
      ...overrides,
    });
  const file = (draft: SpecDraft, suffix: string) => buildGeneratedArtifacts(draft).find((a) => a.fileName.endsWith(suffix))?.content ?? "";

  it("lands a Silver database-target copy in the Bronze table its chain reads, not dbo.data", () => {
    const draft = silverDb({ truncateTarget: true });
    const ds = JSON.parse(file(draft, "_target.json"));
    expect(ds.properties.parameters.SchemaName.defaultValue).toBe("raw");
    expect(ds.properties.parameters.TableName.defaultValue).toBe("epic_patient");
    expect(controlTableSql(draft)).toContain("N'raw.epic_patient'");
    expect(controlTableSql(draft)).not.toContain("dbo.data");
    expect(file(draft, "_child.json")).toContain("TRUNCATE TABLE raw.epic_patient");
  });

  it("keeps a Bronze database target's own dataset table", () => {
    const draft = silverDb({ medallionLayer: "bronze", transformations: [], targetDataset: { ...defaultDatasetDraft("target", "azure_sql_database"), schema: "stage", table: "patient" } });
    expect(controlTableSql(draft)).toContain("N'stage.patient'");
  });

  it("feeds scd2 one row per key, so a source with history can't double-match a target row", () => {
    const draft = silverDb({ transformations: [{ name: "h", type: "scd2", keyColumns: "PAT_ID", orderColumn: "UPDATE_DATE", columns: "PAT_NAME" }] });
    expect(file(draft, "_child.json")).toContain(
      "USING (SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY PAT_ID ORDER BY UPDATE_DATE DESC) AS _scd2_rn FROM raw.epic_patient) v WHERE _scd2_rn = 1) AS src",
    );
  });

  it("seeds a FullLoad row without the form's default watermark column", () => {
    const sql = controlTableSql(draftWith({ sourceObjects: [{ object: "dbo.a", loadingBehavior: "FullLoad" }, { object: "dbo.b" }] }));
    expect(sql).toContain("N'FullLoad', N'', ");
    expect(sql).toContain("N'DeltaLoad', N'modified_date', ");
  });

  it("gives each object its own folder when several land in one lake path", () => {
    const sql = controlTableSql(draftWith({ targetPath: "/bronze/epic/", sourceObjects: [{ object: "dbo.PATIENT" }, { object: "dbo.ENCOUNTER" }] }));
    expect(sql).toContain("N'/bronze/epic/patient/'");
    expect(sql).toContain("N'/bronze/epic/encounter/'");
  });
});
