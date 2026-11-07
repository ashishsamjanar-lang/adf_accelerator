import { describe, expect, it } from "vitest";
import {
  splitMappingPair,
  hasStandardAuditColumns,
  standardAuditColumns,
  withStandardAuditColumns,
  layerTargetTableName,
  resolveZonePrefix,
  transformationRuleSql,
  layerFinalLoadSql,
  layerLoadKeyColumns,
  validateMedallionFields,
  validateTransformationRules,
  checkTransformationChainColumns,
  type TransformationRule,
} from "./medallion";

describe("layerTargetTableName", () => {
  it("builds the raw zone name for bronze", () => {
    expect(layerTargetTableName("bronze", { sourceType: "sql_server", object: "GL Transaction" })).toBe("raw.sql_server_gl_transaction");
  });

  it("builds the curated zone name for silver, from the entity only", () => {
    expect(layerTargetTableName("silver", { entity: "GL_Transaction" })).toBe("curated.gl_transaction");
  });

  it("builds the mart zone name for gold, from domain + entity", () => {
    expect(layerTargetTableName("gold", { domain: "Finance", entity: "gl_fact" })).toBe("mart.finance_gl_fact");
  });

  it("falls back to a safe token when the input is empty", () => {
    expect(layerTargetTableName("silver", {})).toBe("curated.object");
  });

  it("§25.3 Standards 'Layer prefixes': a project's custom zone prefix overrides the default, per layer", () => {
    const overrides = { bronze: "lnd", silver: "cur", gold: "pub" };
    expect(layerTargetTableName("bronze", { sourceType: "sql_server", object: "gl_transaction" }, overrides)).toBe("lnd.sql_server_gl_transaction");
    expect(layerTargetTableName("silver", { entity: "gl_transaction" }, overrides)).toBe("cur.gl_transaction");
    expect(layerTargetTableName("gold", { domain: "finance", entity: "gl_fact" }, overrides)).toBe("pub.finance_gl_fact");
  });

  it("layer prefix overrides are independent — an unset layer keeps its default", () => {
    expect(layerTargetTableName("silver", { entity: "gl_transaction" }, { bronze: "lnd" })).toBe("curated.gl_transaction");
  });
});

describe("resolveZonePrefix", () => {
  it("returns the built-in default when no override is configured", () => {
    expect(resolveZonePrefix("bronze")).toBe("raw");
    expect(resolveZonePrefix("silver", {})).toBe("curated");
    expect(resolveZonePrefix("gold", { silver: "cur" })).toBe("mart");
  });

  it("sanitizes a custom prefix into a safe SQL identifier", () => {
    expect(resolveZonePrefix("silver", { silver: "My Curated Zone!" })).toBe("my_curated_zone");
  });

  it("falls back to the default when a custom prefix is blank or sanitizes to nothing", () => {
    expect(resolveZonePrefix("silver", { silver: "   " })).toBe("curated");
    expect(resolveZonePrefix("silver", { silver: "!!!" })).toBe("curated");
  });
});

describe("validateMedallionFields", () => {
  it("bronze needs nothing", () => {
    expect(validateMedallionFields("bronze", {})).toEqual([]);
  });

  it("silver requires a curated entity", () => {
    const issues = validateMedallionFields("silver", {});
    expect(issues.some((i) => i.field === "curatedEntity")).toBe(true);
  });

  it("silver accepts a valid entity", () => {
    expect(validateMedallionFields("silver", { curatedEntity: "gl_transaction" })).toEqual([]);
  });

  it("gold requires both curated entity and mart domain", () => {
    const issues = validateMedallionFields("gold", {});
    expect(issues.some((i) => i.field === "curatedEntity")).toBe(true);
    expect(issues.some((i) => i.field === "martDomain")).toBe(true);
  });

  it("gold accepts valid entity + domain", () => {
    expect(validateMedallionFields("gold", { curatedEntity: "gl_fact", martDomain: "finance" })).toEqual([]);
  });
});

describe("validateTransformationRules", () => {
  it("bronze needs no transformations", () => {
    expect(validateTransformationRules("bronze", [])).toEqual([]);
  });

  it("silver requires at least one transformation", () => {
    const issues = validateTransformationRules("silver", []);
    expect(issues.some((i) => i.field === "transformations")).toBe(true);
  });

  it("rejects a gold-only type on the silver layer", () => {
    const rules: TransformationRule[] = [{ name: "bad", type: "aggregate", groupByColumns: "x", aggregateExpression: "COUNT(*)" }];
    const issues = validateTransformationRules("silver", rules);
    expect(issues.some((i) => i.message.includes("isn't valid for the silver layer"))).toBe(true);
  });

  it("§33.3 widening: allows join on silver (combining two or more bronze sources)", () => {
    const rules: TransformationRule[] = [
      { name: "join_orders", type: "join", joinEntity: "raw.orders", joinCondition: "a.customer_id = b.customer_id", columns: "a.*, b.order_total" },
    ];
    expect(validateTransformationRules("silver", rules)).toEqual([]);
  });

  it("§33.3 widening: allows scd2 on gold (mart/dimension history tracking)", () => {
    const rules: TransformationRule[] = [{ name: "scd", type: "scd2", keyColumns: "customer_id", orderColumn: "modified_date", columns: "name" }];
    expect(validateTransformationRules("gold", rules)).toEqual([]);
  });

  it("rejects an object still using a genuinely disallowed type (bronze-only shape)", () => {
    const rules: TransformationRule[] = [{ name: "bad", type: "cast_schema", castExpression: "x:INT" }];
    const issues = validateTransformationRules("gold", rules);
    expect(issues.some((i) => i.message.includes("isn't valid for the gold layer"))).toBe(true);
  });

  it("§33.3 widening: allows up to 10 transformations (raised from 5)", () => {
    const rules: TransformationRule[] = Array.from({ length: 10 }, (_, i) => ({
      name: `step_${i}`,
      type: "conform_columns" as const,
      columns: "a:b",
    }));
    const issues = validateTransformationRules("silver", rules);
    expect(issues.some((i) => i.field === "transformations" && i.message.includes("Maximum"))).toBe(false);
  });

  it("rejects more than 10 transformations", () => {
    const rules: TransformationRule[] = Array.from({ length: 11 }, (_, i) => ({
      name: `step_${i}`,
      type: "conform_columns" as const,
      columns: "a:b",
    }));
    const issues = validateTransformationRules("silver", rules);
    expect(issues.some((i) => i.message === "Maximum 10 transformations allowed.")).toBe(true);
  });

  it("requires dedupe's key/order columns", () => {
    const rules: TransformationRule[] = [{ name: "dedupe_customer", type: "dedupe" }];
    const issues = validateTransformationRules("silver", rules);
    expect(issues.some((i) => i.message.includes("needs key column"))).toBe(true);
    expect(issues.some((i) => i.message.includes("needs an order column"))).toBe(true);
  });

  it("accepts a fully-specified silver rule set", () => {
    const rules: TransformationRule[] = [
      { name: "conform", type: "conform_columns", columns: "cust_id:customer_id, modified_date" },
      { name: "dedupe", type: "dedupe", keyColumns: "customer_id", orderColumn: "modified_date" },
    ];
    expect(validateTransformationRules("silver", rules)).toEqual([]);
  });

  it("requires join's entity + condition, and aggregate's group-by + expression", () => {
    const issues = validateTransformationRules("gold", [
      { name: "join_customer", type: "join" },
      { name: "agg", type: "aggregate" },
    ]);
    expect(issues.some((i) => i.message.includes("(join) needs the other entity"))).toBe(true);
    expect(issues.some((i) => i.message.includes("(join) needs a join condition"))).toBe(true);
    expect(issues.some((i) => i.message.includes("(aggregate) needs GROUP BY"))).toBe(true);
    expect(issues.some((i) => i.message.includes("(aggregate) needs at least one aggregate expression"))).toBe(true);
  });

  it("accepts a fully-specified gold rule set", () => {
    const rules: TransformationRule[] = [
      { name: "join_customer", type: "join", joinEntity: "curated.customer", joinCondition: "a.customer_id = b.customer_id", columns: "a.*, b.segment" },
      { name: "agg", type: "aggregate", groupByColumns: "customer_id", aggregateExpression: "SUM(amount) AS total_amount" },
    ];
    expect(validateTransformationRules("gold", rules)).toEqual([]);
  });
});

describe("transformationRuleSql", () => {
  const ctx = { readFrom: "dbo.gl_transaction", writeTo: "stg.pl1_step1", targetTable: "curated.gl_transaction" };

  it("conform_columns renames the given columns", () => {
    const sql = transformationRuleSql({ name: "r", type: "conform_columns", columns: "cust_id:customer_id, amt:amount" }, ctx);
    expect(sql).toContain("cust_id AS customer_id");
    expect(sql).toContain("amt AS amount");
    expect(sql).toContain(ctx.writeTo);
  });

  it("dedupe ranks by the order column and keeps rank 1", () => {
    const sql = transformationRuleSql({ name: "r", type: "dedupe", keyColumns: "customer_id", orderColumn: "modified_date" }, ctx);
    expect(sql).toContain("ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY modified_date DESC)");
    expect(sql).toContain("WHERE _rn = 1");
  });

  it("cast_schema emits an ALTER COLUMN per cast", () => {
    const sql = transformationRuleSql({ name: "r", type: "cast_schema", castExpression: "amount:DECIMAL(18,2), posted_date:DATETIME2" }, ctx);
    expect(sql).toContain("ALTER TABLE stg.pl1_step1 ALTER COLUMN amount DECIMAL(18,2);");
    expect(sql).toContain("ALTER TABLE stg.pl1_step1 ALTER COLUMN posted_date DATETIME2;");
  });

  it("scd2 writes to the real target table, not staging", () => {
    const sql = transformationRuleSql(
      { name: "r", type: "scd2", keyColumns: "customer_id", orderColumn: "modified_date" },
      { ...ctx, writeTo: ctx.targetTable },
    );
    expect(sql).toContain(`MERGE ${ctx.targetTable} AS tgt`);
    expect(sql).toContain(`INSERT INTO ${ctx.targetTable}`);
    expect(sql).toContain("_is_current");
  });

  it("scd2 uses the rule's history column names when set (DMT-09)", () => {
    const sql = transformationRuleSql(
      {
        name: "r", type: "scd2", keyColumns: "customer_id", orderColumn: "modified_date",
        currentFlagColumn: "is_current", validFromColumn: "eff_from", validToColumn: "eff_to",
      },
      { ...ctx, writeTo: ctx.targetTable },
    );
    expect(sql).toContain("modified_date, is_current, eff_from, eff_to)");
    expect(sql).toContain("UPDATE SET tgt.is_current = 0, tgt.eff_to = GETUTCDATE();");
    expect(sql).not.toContain("_is_current");
  });

  it("filter stages the matching rows (DMT-03)", () => {
    const sql = transformationRuleSql({ name: "f", type: "filter", filterCondition: "status <> 'X'" }, ctx);
    expect(sql).toBe(`DROP TABLE IF EXISTS ${ctx.writeTo};\nSELECT * INTO ${ctx.writeTo} FROM ${ctx.readFrom} WHERE status <> 'X';`);
  });

  it("join produces an INNER/LEFT join against the other entity", () => {
    const sql = transformationRuleSql(
      { name: "r", type: "join", joinEntity: "curated.customer", joinType: "LEFT", joinCondition: "a.customer_id = b.customer_id" },
      ctx,
    );
    expect(sql).toContain("LEFT JOIN curated.customer b ON a.customer_id = b.customer_id");
  });

  it("join uses the rule's explicit projection instead of a.*, b.* (G4)", () => {
    const rule: TransformationRule = { name: "r", type: "join", joinEntity: "curated.customer", joinCondition: "a.customer_id = b.customer_id", columns: "a.*, b.segment" };
    const sql = transformationRuleSql(rule, ctx);
    expect(sql).toContain("SELECT a.*, b.segment\nINTO");
    expect(sql).not.toContain("b.*");
    const noColumns = validateTransformationRules("silver", [{ ...rule, columns: undefined }]);
    expect(noColumns).toEqual([expect.objectContaining({ severity: "warning" })]);
  });

  it("aggregate groups and applies the given aggregate expression", () => {
    const sql = transformationRuleSql(
      { name: "r", type: "aggregate", groupByColumns: "customer_id", aggregateExpression: "SUM(amount) AS total_amount" },
      ctx,
    );
    expect(sql).toContain("SELECT customer_id, SUM(amount) AS total_amount");
    expect(sql).toContain("GROUP BY customer_id");
  });

  it("select_columns stages its projection (E2E-08: LoadToLayerTarget does the idempotent write)", () => {
    const sql = transformationRuleSql({ name: "r", type: "select_columns", columns: "customer_id, total_amount" }, ctx);
    expect(sql).toContain(`SELECT customer_id, total_amount INTO ${ctx.writeTo}`);
    expect(sql).not.toContain(`INSERT INTO ${ctx.targetTable}`);
  });
});

describe("G3/G5 — SCD2 tracked columns + idempotent final load", () => {
  it("carries tracked columns into history and detects change by row hash", () => {
    const sql = transformationRuleSql(
      { name: "h", type: "scd2", keyColumns: "customer_id", orderColumn: "modified_date", columns: "name, segment" },
      { readFrom: "stg.x", writeTo: "curated.c", targetTable: "curated.c" },
    );
    expect(sql).toContain("INSERT INTO curated.c (customer_id, name, segment, modified_date, _is_current");
    // RUN-18: each hashed column is NULL-coalesced, since CONCAT_WS skips NULLs.
    expect(sql).toContain("HASHBYTES('SHA2_256', CONCAT_WS('|', ISNULL(CAST(tgt.name AS NVARCHAR(MAX)), CHAR(0)), ISNULL(CAST(tgt.segment AS NVARCHAR(MAX)), CHAR(0)), ISNULL(CAST(tgt.modified_date AS NVARCHAR(MAX)), CHAR(0))))");
  });

  it("truncates on full refresh and deletes by key otherwise", () => {
    expect(layerFinalLoadSql("curated.c", "stg.s", { fullRefresh: true, keys: ["id"] })).toContain("TRUNCATE TABLE curated.c;");
    const delta = layerFinalLoadSql("curated.c", "stg.s", { fullRefresh: false, keys: layerLoadKeyColumns([{ name: "d", type: "dedupe", keyColumns: "id" }]) });
    expect(delta).toContain("DELETE tgt FROM curated.c tgt WHERE EXISTS (SELECT 1 FROM stg.s src WHERE tgt.id = src.id);");
  });
});

describe("standard audit columns (gap #32)", () => {
  it("adds the layer's set, keeps user columns, and swaps sets on layer change", () => {
    const own = [{ name: "domain", value: "finance" }];
    const bronze = withStandardAuditColumns("bronze", own, true)!;
    expect(bronze[0]).toEqual(own[0]);
    expect(hasStandardAuditColumns("bronze", bronze)).toBe(true);
    const silver = withStandardAuditColumns("silver", bronze, true)!;
    expect(silver.find((c) => c.name === "_medallion_layer")?.value).toBe("silver");
    expect(silver.some((c) => c.name === "_trigger_time")).toBe(false);
    expect(withStandardAuditColumns("silver", silver, false)).toEqual(own);
  });

  it("refuses to exceed the 10-column cap", () => {
    const full = Array.from({ length: 8 }, (_, i) => ({ name: `c${i}`, value: "x" }));
    expect(withStandardAuditColumns("bronze", full, true)).toBeNull();
  });
});

describe("project-configured audit columns (gap #32)", () => {
  const overrides = { silver: [{ name: "_batch_id", value: "@pipeline().RunId" }, { name: " ", value: "ignored" }] };

  it("uses the project's list for its layer and the built-in list elsewhere", () => {
    expect(standardAuditColumns("silver", overrides)).toEqual([{ name: "_batch_id", value: "@pipeline().RunId" }]);
    expect(standardAuditColumns("gold", overrides)).toEqual(standardAuditColumns("gold"));
  });

  it("swaps a built-in set for the project's set when the layer changes", () => {
    const bronze = withStandardAuditColumns("bronze", [{ name: "domain", value: "x" }], true, overrides)!;
    const silver = withStandardAuditColumns("silver", bronze, true, overrides)!;
    expect(silver.map((c) => c.name)).toEqual(["domain", "_batch_id"]);
    expect(hasStandardAuditColumns("silver", silver, overrides)).toBe(true);
  });
});

describe("DMT-06 — splitMappingPair", () => {
  it("splits on the last top-level colon only", () => {
    expect(splitMappingPair("cust_id:customer_id")).toEqual(["cust_id", "customer_id"]);
    expect(splitMappingPair("CASE WHEN t = '12:00' THEN 1 END:is_noon")).toEqual(["CASE WHEN t = '12:00' THEN 1 END", "is_noon"]);
    expect(splitMappingPair("amount::int")).toEqual(["amount::int", ""]);
    expect(splitMappingPair("'12:00'")).toEqual(["'12:00'", ""]);
    expect(splitMappingPair("price:DECIMAL(10,2)")).toEqual(["price", "DECIMAL(10,2)"]);
  });
});

describe("checkTransformationChainColumns", () => {
  it("warns when a later step uses a column an earlier projection dropped", () => {
    const issues = checkTransformationChainColumns([
      { name: "conform", type: "conform_columns", columns: "PAT_ID:patient_id" },
      { name: "dedupe", type: "dedupe", keyColumns: "patient_id", orderColumn: "UPDATE_DATE" },
    ]);
    expect(issues).toHaveLength(1);
    expect(issues[0].severity).toBe("warning");
    expect(issues[0].message).toContain('"UPDATE_DATE"');
  });

  it("is silent before the first projection, after a star, and for mapped columns", () => {
    expect(
      checkTransformationChainColumns([
        { name: "d0", type: "dedupe", keyColumns: "x", orderColumn: "y" },
        { name: "conform", type: "conform_columns", columns: "PAT_ID:patient_id, UPDATE_DATE:updated_at" },
        { name: "d1", type: "dedupe", keyColumns: "PATIENT_ID", orderColumn: "updated_at" },
        { name: "join", type: "join", joinEntity: "curated.x", joinCondition: "a.id = b.id", columns: "a.*, b.extra" },
        { name: "final", type: "select_columns", columns: "anything" },
      ]),
    ).toEqual([]);
  });

  it("tracks aggregate output columns", () => {
    const issues = checkTransformationChainColumns([
      { name: "agg", type: "aggregate", groupByColumns: "region", aggregateExpression: "SUM(amount) AS total" },
      { name: "final", type: "select_columns", columns: "region, total, amount" },
    ]);
    expect(issues.map((i) => i.message.includes('"amount"'))).toEqual([true]);
  });
});
