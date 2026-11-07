/**
 * ADF pipeline code generation for the Metadata-Driven Load template.
 *
 * Everything in this module is derived from the user-populated SpecDraft —
 * no business values are injected. It produces the artifacts a developer
 * would commit to Git:
 *
 *   PL_{name}_master.json        metadata-driven master (control table -> ForEach -> child)
 *   PL_{name}_child.json         per-object child (Switch on LoadingBehavior)
 *   DS_{sourceName}.json         source dataset (user-configured parameters)
 *   DS_{targetName}.json         target dataset (user-configured parameters)
 *   {sourceLsName}.json          source linked service (name already carries the LS_ prefix)
 *   {targetLsName}.json          target linked service (name already carries the LS_ prefix)
 *   adf_control_table.sql        control-table DDL + one seed row per object
 *   TR_{name}_daily.json         scheduled trigger
 *   globalParameters.json        ADF factory global-parameters artifact (IMPLEMENTATION_PLAN.md A-2)
 *   parameters.json              runtime parameters snapshot (IMPLEMENTATION_PLAN.md A-1)
 *
 * Every fileName above is a bare name — the canonical ADF folder each
 * artifact lands in (linkedService/, dataset/, pipeline/, trigger/,
 * factory/, §24.11) is applied at push time from artifactType, not baked
 * into the generated name (see worker/app/jobs/runner.py::_artifact_blobs).
 */
import type { SpecDraft } from "./spec";
import type { ArtifactLayer, ArtifactType, ArtifactVersion, ColumnMapping, EnvironmentConnection, SourceObjectEntry } from "@/types";
import {
  buildDatasetProperties,
  buildLinkedServiceProperties,
  usesUserAssignedIdentity,
  connectorSchema,
  datasetName,
  DEFAULT_KEY_VAULT_LINKED_SERVICE,
  visibleFields,
  type DatasetDraft,
  type LinkedServiceDraft,
} from "./connections";
import {
  bronzeSourceToken,
  effectiveSourceObjects,
  completeColumnMappings,
  perObjectColumnMappingsActive,
  childDispatchCases,
  dqRuleNeedsColumn,
  DQ_PCT_RULE_TYPES,
  pipelineAnnotations,
  resolveChildPipelineName,
  resolveCopyTimeout,
  resolveSoftDeleteColumn,
  resolveMasterPipelineName,
  resolvePipelineName,
  resolveTriggerPipelineName,
  sourceDatasetDefaults,
  targetDatasetDefaults,
} from "./spec";
import { getQualifierProfile, resolveQualifiedName } from "./qualifier";
import {
  layerFinalLoadSql,
  layerLoadKeyColumns,
  layerTargetTableName,
  MAX_TRANSFORMATION_RULES,
  transformationRuleSql,
  TERMINAL_TRANSFORM_TYPES,
  type LayerPrefixOverrides,
} from "./medallion";

/** ADF's pipeline `parameters` block is a name-keyed object, not a list of `{ name, ... }`. */
function namedParameters(params: readonly unknown[]): Record<string, unknown> {
  return Object.fromEntries(
    (params as Array<{ name: string } & Record<string, unknown>>).map(({ name, ...rest }) => [name, rest]),
  );
}

function loadingBehaviorOf(draft: SpecDraft): "FullLoad" | "DeltaLoad" | "ChunkedLoad" {
  if (draft.chunkingEnabled && draft.chunkColumn?.trim()) return "ChunkedLoad";
  return draft.loadTypeMode === "full" || draft.fullLoad ? "FullLoad" : "DeltaLoad";
}

/**
 * Standard metadata/lineage columns (sample-guide parity gap 1) as ADF Copy
 * activity source `additionalColumns`. A value starting with "@" is emitted
 * as dynamic content (e.g. `@pipeline().RunId`, `@utcnow()`); everything else
 * is a literal — same convention already used by customLogColumns' defaults.
 */
function additionalColumnsOf(entries: Array<{ name: string; value: string }>): unknown[] | undefined {
  if (!entries.length) return undefined;
  return entries.slice(0, 10).map((entry) => ({
    name: entry.name,
    value: entry.value.trim().startsWith("@") ? { type: "Expression", value: entry.value.trim() } : entry.value,
  }));
}

function watermarkColumnNames(draft: SpecDraft): string[] {
  return draft.watermarkColumns.slice(0, 5).map((c) => c.name.trim()).filter(Boolean);
}

/** More than one watermark column, in composite mode — the single-column path stays untouched. */
function isCompositeWatermark(draft: SpecDraft): boolean {
  return draft.watermarkType === "composite" && watermarkColumnNames(draft).length > 1;
}

/**
 * Composite-watermark new-high-watermark lookup (sample-guide parity gap 3):
 * independent per-column MAX, one output column per watermark column.
 */
function newWatermarkLookupQuery(draft: SpecDraft): string {
  if (!isCompositeWatermark(draft)) {
    return "SELECT MAX(@{pipeline().parameters.WatermarkColumn}) AS NewWatermark FROM " +
      "@{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}";
  }
  const selects = watermarkColumnNames(draft).map((col, i) => `MAX(${col}) AS NewWatermark_${i}`).join(", ");
  return `SELECT ${selects} FROM @{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}`;
}

/**
 * Delta Copy source query. Composite watermark (>1 column, sample-guide parity
 * gap 3 — previously `watermarkColumns[0]` was the only column ever read, so a
 * genuine composite key silently lost every column past the first) builds a
 * lexicographic tuple predicate "(c0 > last0) OR (c0 = last0 AND c1 > last1) OR
 * ..." at generation time — ADF dynamic content has no runtime loop construct
 * to do this for an arbitrary column count, so the column names/count (known
 * per-pipeline, not per control-table row) are baked into the query text here
 * instead of being read generically like the single-column case is. Only the
 * lower bound is tightened to the full tuple; the upper bound stays the
 * simpler per-column MAX from `newWatermarkLookupQuery` (an accepted
 * approximation — each run's ceiling is still a true upper bound per column).
 */
function deltaCopyQuery(draft: SpecDraft): unknown {
  if (!isCompositeWatermark(draft)) {
    return {
      type: "Expression",
      value: `@concat('SELECT * FROM ', pipeline().parameters.SchemaName, '.', pipeline().parameters.TableName, ' WHERE ', pipeline().parameters.WatermarkColumn, ' > ''', activity('LookupLastWatermark').output.firstRow.WatermarkValue, ''' AND ', pipeline().parameters.WatermarkColumn, ' <= ''', activity('LookupNewWatermark').output.firstRow.NewWatermark, '''')`,
    };
  }
  const cols = watermarkColumnNames(draft);
  const lastAt = (i: number) => `split(activity('LookupLastWatermark').output.firstRow.WatermarkValue, '|')[${i}]`;
  const openQuote = (text: string) => `'${text}'''`;
  const closeQuote = (text: string) => `'''${text}'`;
  const parts: string[] = ["'SELECT * FROM '", "pipeline().parameters.SchemaName", "'.'", "pipeline().parameters.TableName", "' WHERE ('"];
  cols.forEach((col, k) => {
    for (let i = 0; i < k; i++) {
      parts.push(openQuote(`${cols[i]} = `), lastAt(i), closeQuote(" AND "));
    }
    parts.push(openQuote(`${col} > `), lastAt(k), "''''");
    if (k < cols.length - 1) parts.push("' OR ('");
  });
  parts.push("')'");
  return { type: "Expression", value: `@concat(${parts.join(", ")})` };
}

/**
 * UpdateWatermark write-back. Composite mode joins each column's new MAX with
 * "|" into one delimited string — no control-table schema change needed, the
 * existing single `WatermarkValue` column just carries a delimited tuple
 * instead of a scalar (parsed back out by `split(...)` in `deltaCopyQuery`).
 */
function newWatermarkValueExpr(draft: SpecDraft): string {
  if (!isCompositeWatermark(draft)) {
    return "activity('LookupNewWatermark').output.firstRow.NewWatermark";
  }
  const cols = watermarkColumnNames(draft);
  const fields = cols.map((_, i) => `string(activity('LookupNewWatermark').output.firstRow.NewWatermark_${i})`);
  return fields.length > 1 ? `concat(${fields.join(", '|', ")})` : fields[0];
}

/** The "control" schema prefix off the configured watermark table (e.g. "control.watermarks" -> "control"). */
function controlSchemaOf(draft: SpecDraft): string {
  const parts = (draft.watermarkTable || "control.watermarks").split(".");
  return parts.length >= 2 ? parts.slice(0, -1).join(".") : "control";
}

/** A DQ rule's activity-name-safe token — ADF activity names allow only letters/digits/underscore/hyphen/space, and
 *  a rule name is free text, so it needs the same sanitizing every other user-named token gets before use in a name. */
function dqActivityBase(rule: { name: string }, index: number): string {
  return rule.name.trim().replace(/[^A-Za-z0-9_]/g, "_") || `Rule${index + 1}`;
}

/**
 * The validation query for one §30.4 data quality rule, in the same T-SQL dialect the rest of this
 * generator's control-plane Lookups already assume (LookupLastWatermark, execution-log writes, ...).
 * REGEX_MATCH uses PATINDEX as the closest T-SQL built-in — it is a wildcard match, not a true regex
 * engine; swap in your platform's real regex support (a CLR function, Data Flow expression, etc.) if
 * you need one.
 */
function dataQualityCheckQuery(schemaTable: string, rule: { column: string; rule: string; value?: string }): string {
  const col = rule.column.trim();
  switch (rule.rule) {
    case "IS_NOT_NULL":
      return `SELECT COUNT(*) AS ViolationCount FROM ${schemaTable} WHERE ${col} IS NULL`;
    case "UNIQUE":
      return `SELECT COUNT(*) AS ViolationCount FROM (SELECT ${col} FROM ${schemaTable} GROUP BY ${col} HAVING COUNT(*) > 1) dup`;
    case "LENGTH_LE": {
      const maxLength = rule.value?.trim() || "0";
      return `SELECT COUNT(*) AS ViolationCount FROM ${schemaTable} WHERE LEN(${col}) > ${maxLength}`;
    }
    case "RANGE_BETWEEN": {
      const [min, max] = (rule.value ?? "").split(",").map((v) => v.trim());
      return `SELECT COUNT(*) AS ViolationCount FROM ${schemaTable} WHERE ${col} < ${min || "0"} OR ${col} > ${max || "0"}`;
    }
    case "ROW_COUNT_MIN":
      // One "violation" when the object holds fewer rows than the threshold — keeps the ViolationCount contract.
      return `SELECT CASE WHEN COUNT(*) < ${rule.value?.trim() || "0"} THEN 1 ELSE 0 END AS ViolationCount FROM ${schemaTable}`;
    case "FRESHNESS_HOURS":
      // One "violation" when the newest timestamp is missing or older than the threshold (UTC).
      return `SELECT CASE WHEN MAX(${col}) IS NULL OR MAX(${col}) < DATEADD(HOUR, -${rule.value?.trim() || "0"}, GETUTCDATE()) THEN 1 ELSE 0 END AS ViolationCount FROM ${schemaTable}`;
    case "REGEX_MATCH":
    default:
      return `SELECT COUNT(*) AS ViolationCount FROM ${schemaTable} WHERE PATINDEX('${rule.value?.trim() || "%"}', ${col}) = 0`;
  }
}

/**
 * §30.4 basic Data Quality checks — a pre-copy gate against the source object. Each configured rule
 * (up to 5, `DataQualityEditor` in create-pipeline-wizard.tsx) becomes a Lookup activity running a
 * validation query in the same illustrative SqlServerSource/SqlWrapper convention already used for
 * every other control-plane check this generator emits — adjust the query/linked service if your
 * source isn't SQL-queryable this way. `enforcement: "block"` additionally fails the pipeline on a
 * breach via a nested IfCondition -> Fail, whose "Succeeded" status then gates
 * RouteByLoadingBehavior; `"warn"` (default) just runs the check so its ViolationCount is visible in
 * ADF's own run history — this app never runs the check itself or sees the result (§30.3).
 */
/** DMT-05: the rule's max violation % when it applies (a row-level rule with a valid number). */
function dqPct(rule: { rule: string; maxViolationPct?: string }): string | null {
  const pct = (rule.maxViolationPct ?? "").trim();
  return pct && DQ_PCT_RULE_TYPES.has(rule.rule) && /^\d+(?:\.\d+)?$/.test(pct) ? pct : null;
}

/** The check query; with a max violation % it also returns TotalRows and ThresholdBreached. Keep in parity with codegen.py. */
function dataQualityCheckSql(schemaTable: string, rule: SpecDraft["dataQualityRules"][number]): string {
  const query = dataQualityCheckQuery(schemaTable, rule);
  const pct = dqPct(rule);
  if (!pct) return query;
  return (
    "SELECT v.ViolationCount, t.TotalRows, " +
    `CASE WHEN v.ViolationCount * 100.0 > ${pct} * t.TotalRows THEN 1 ELSE 0 END AS ThresholdBreached ` +
    `FROM (${query}) v CROSS JOIN (SELECT COUNT(*) AS TotalRows FROM ${schemaTable}) t`
  );
}

function dataQualityActivities(
  draft: SpecDraft,
  schema: string,
  table: string,
  postLoad?: { table: string; dependsOn: string } | null,
): { activities: unknown[]; gateDependsOn: Array<{ activity: string; dependencyConditions: string[] }> } {
  const rules = draft.dataQualityRules.slice(0, 5).filter((r) => r.name.trim() && (r.column.trim() || !dqRuleNeedsColumn(r.rule)));
  const schemaTable = postLoad ? dqReadFrom(draft, postLoad.table) : "@{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}";
  const checkedObject = postLoad ? postLoad.table : `${schema}.${table}`;
  const activities: unknown[] = [];
  const gateDependsOn: Array<{ activity: string; dependencyConditions: string[] }> = [];
  rules.forEach((rule, i) => {
    const base = dqActivityBase(rule, i);
    const checkName = `DQ_${base}_Check`;
    activities.push({
      name: checkName,
      description: `Data quality check "${rule.name.trim()}" (${rule.rule}) on ${checkedObject}${rule.column.trim() ? `.${rule.column.trim()}` : ""}.`,
      type: "Lookup",
      ...(postLoad ? { dependsOn: [{ activity: postLoad.dependsOn, dependencyConditions: ["Succeeded"] }] } : {}),
      policy: { timeout: "0.00:10:00.000", retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
      typeProperties: {
        source: {
          type: "SqlServerSource",
          sqlReaderQuery: dataQualityCheckSql(schemaTable, rule),
        },
      },
    });
    if ((rule.enforcement ?? "warn") === "block") {
      const gateName = `DQ_${base}_Gate`;
      const pct = dqPct(rule);
      activities.push({
        name: gateName,
        type: "IfCondition",
        dependsOn: [{ activity: checkName, dependencyConditions: ["Succeeded"] }],
        typeProperties: {
          expression: {
            type: "Expression",
            value: pct
              ? `@equals(int(activity('${checkName}').output.firstRow.ThresholdBreached), 1)`
              : `@greater(int(activity('${checkName}').output.firstRow.ViolationCount), 0)`,
          },
          ifTrueActivities: [
            {
              name: `DQ_${base}_Fail`,
              type: "Fail",
              typeProperties: {
                message: {
                  type: "Expression",
                  value: `@concat('Data quality rule "${rule.name.trim()}" (${rule.rule}) failed${rule.column.trim() ? ` on column ${rule.column.trim()}` : ""}: ', string(activity('${checkName}').output.firstRow.ViolationCount), ' violating row(s)${pct ? ` (more than ${pct}% of rows)` : ""}.')`,
                },
                errorCode: "DataQualityRuleFailed",
              },
            },
          ],
        },
      });
      gateDependsOn.push({ activity: gateName, dependencyConditions: ["Succeeded"] });
    }
  });
  return { activities, gateDependsOn };
}

/** RUN-13: on an SCD2 target the post-load checks read only the current version of each key. */
function dqReadFrom(draft: SpecDraft, target: string): string {
  const scd2 = draft.transformations.slice(0, MAX_TRANSFORMATION_RULES).find((r) => r.name.trim() && r.type === "scd2");
  if (!scd2) return target;
  const flag = scd2.currentFlagColumn?.trim() || "_is_current";
  return `(SELECT * FROM ${target} WHERE ${flag} = 1) cur`;
}

/**
 * E2E-09: a Silver/Gold rule names a *target-layer* column, so when a transformation chain runs the
 * checks read the layer target table after the load instead of gating the copy on the source object
 * (where a renamed column doesn't exist). Null = keep the pre-copy source check (Bronze, or no chain).
 */
function postLoadDqTable(draft: SpecDraft, table: string, layerPrefixes?: LayerPrefixOverrides): string | null {
  if (draft.medallionLayer === "bronze") return null;
  if (!draft.transformations.slice(0, MAX_TRANSFORMATION_RULES).some((r) => r.name.trim())) return null;
  return layerTargetTableName(
    draft.medallionLayer,
    { sourceType: bronzeSourceToken(draft), object: table, entity: draft.curatedEntity, domain: draft.martDomain },
    layerPrefixes,
  );
}

function transformActivityBase(rule: { name: string }, index: number): string {
  return rule.name.trim().replace(/[^A-Za-z0-9_]/g, "_") || `Step${index + 1}`;
}

/**
 * Silver/Gold medallion transformations (`lib/adf/medallion.ts`) — a chain of
 * ADF `Script` activities appended after the copy activity names, each
 * running one configured transformation rule's illustrative T-SQL, ending
 * with a write into the layer's standardized target table
 * (`curated.<entity>` / `mart.<domain>_<entity>`). Bronze (the default) and a
 * layer with no configured rules both return no extra activities — the
 * existing copy-only behavior is completely unaffected.
 *
 * `dependsOnActivity` is the name of the activity whose completion means the
 * data is landed and ready to transform (`FullLoadCopy` for the FullLoad
 * branch, `UpdateWatermark` — the last step of the delta branch — for the
 * default/incremental branch, keeping both branches' chains equally simple
 * rather than racing the transform against watermark bookkeeping).
 */
function layerTransformationActivities(
  draft: SpecDraft,
  schema: string,
  table: string,
  dependsOnActivity: string,
  layerPrefixes?: LayerPrefixOverrides,
  fullRefresh = false,
  nameSuffix = "",
): { activities: unknown[]; finalActivityName: string } {
  if (draft.medallionLayer === "bronze") return { activities: [], finalActivityName: dependsOnActivity };
  const rules = draft.transformations.slice(0, MAX_TRANSFORMATION_RULES).filter((r) => r.name.trim());
  if (!rules.length) return { activities: [], finalActivityName: dependsOnActivity };

  const targetTable = layerTargetTableName(
    draft.medallionLayer,
    { sourceType: bronzeSourceToken(draft), object: table, entity: draft.curatedEntity, domain: draft.martDomain },
    layerPrefixes,
  );
  // G2: ADF requires a linked service on every Script activity — the SQL runs on the target store.
  const targetLsRef = { referenceName: draft.targetLinkedService.name, type: "LinkedServiceReference" };
  const activities: unknown[] = [];
  // G7: the chain reads the Bronze landed table (raw.<source>_<object>) on the target store,
  // never the source object's own name — that table only exists on the source system.
  let readFrom = layerTargetTableName("bronze", { sourceType: bronzeSourceToken(draft), object: table }, layerPrefixes);
  let prevName = dependsOnActivity;
  let lastTerminal = false;
  const stagingTables: string[] = [];
  rules.forEach((rule, i) => {
    // RUN-04: `nameSuffix` keeps names unique across the child's Switch branches.
    const name = `Transform_${transformActivityBase(rule, i)}${nameSuffix}`;
    const terminal = TERMINAL_TRANSFORM_TYPES.has(rule.type);
    // RUN-06: staging tables are per object and per run, so parallel ForEach iterations (and
    // overlapping runs) never share one; DropStagingTables removes them after a successful chain.
    const writeTo = terminal ? targetTable : `stg.${draft.name}_step${i + 1}${STAGING_RUN_SUFFIX}`;
    if (!terminal) stagingTables.push(writeTo);
    activities.push({
      name,
      description: `${draft.medallionLayer} layer transformation "${rule.name.trim()}" (${rule.type}).`,
      type: "Script",
      linkedServiceName: targetLsRef,
      dependsOn: [{ activity: prevName, dependencyConditions: ["Succeeded"] }],
      policy: { timeout: "0.01:00:00.000", retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
      typeProperties: { scripts: [{ type: "Query", text: transformationRuleSql(rule, { readFrom, writeTo, targetTable }) }] },
    });
    readFrom = writeTo;
    prevName = name;
    lastTerminal = terminal;
  });
  if (!lastTerminal) {
    const loadName = `LoadToLayerTarget${nameSuffix}`;
    activities.push({
      name: loadName,
      description: `Load transformed data into the ${draft.medallionLayer} layer target ${targetTable}.`,
      type: "Script",
      linkedServiceName: targetLsRef,
      dependsOn: [{ activity: prevName, dependencyConditions: ["Succeeded"] }],
      policy: { timeout: "0.00:30:00.000", retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
      typeProperties: {
        scripts: [
          {
            type: "Query",
            text: layerFinalLoadSql(targetTable, readFrom, { fullRefresh, keys: layerLoadKeyColumns(rules, draft.layerKeyColumns?.trim() || draft.upsertKeyColumns) }),
          },
        ],
      },
    });
    prevName = loadName;
  }
  if (stagingTables.length) {
    const dropName = `DropStagingTables${nameSuffix}`;
    activities.push({
      name: dropName,
      description: "Drop this run's staging tables (a failed run keeps them for diagnosis).",
      type: "Script",
      linkedServiceName: targetLsRef,
      dependsOn: [{ activity: prevName, dependencyConditions: ["Succeeded"] }],
      policy: { timeout: "0.00:10:00.000", retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
      typeProperties: { scripts: [{ type: "NonQuery", text: stagingTables.map((t) => `DROP TABLE IF EXISTS ${t};`).join("\n") }] },
    });
    prevName = dropName;
  }
  return { activities, finalActivityName: prevName };
}

/** RUN-06: suffix that makes a staging table unique per object (TaskId) and per run (RunId, no hyphens). */
const STAGING_RUN_SUFFIX = "_@{pipeline().parameters.TaskId}_@{replace(pipeline().RunId, '-', '')}";

/**
 * Connector-aware replacement for the old blind `split(".")` — resolves the
 * source object through `lib/adf/qualifier.ts` so 3-part sources (e.g. SAP
 * HANA with a catalog) and schema-less sources (SAP ECC/OData, Salesforce,
 * ...) get the right identifier instead of a mis-split or a hard-coded
 * "dbo". Case-conformed per the connector's real convention (Oracle/SAP
 * HANA uppercase, Postgres lowercase, ...) so the object resolves correctly
 * regardless of the case it was onboarded in.
 */
function splitObjectName(objectName: string, connectorType: SpecDraft["sourceType"]): { schema: string; table: string } {
  const profile = getQualifierProfile(connectorType);
  const resolved = resolveQualifiedName({ object: objectName }, profile);
  return { schema: resolved.schema ?? "dbo", table: resolved.object || "source_table" };
}

/** Effective dataset name for a side of the pipeline. */
function resolveDatasetName(draft: SpecDraft, kind: "source" | "target"): string {
  const ds: DatasetDraft = kind === "source" ? draft.sourceDataset : draft.targetDataset;
  const defaults = kind === "source" ? sourceDatasetDefaults(draft) : targetDatasetDefaults(draft);
  return datasetName(ds, defaults);
}

const WRITE_SQL = /\b(?:INSERT|UPDATE|DELETE|MERGE)\b/i;
export const LOOKUP_DONE_SELECT = "; SELECT 1 AS Done";

/** RUN-02: a Lookup fails when its query returns no result set, so a Lookup that only writes (execution log, watermark, chunk plan) ends with a one-row SELECT. */
function ensureLookupResultSets(node: unknown): void {
  if (Array.isArray(node)) {
    node.forEach(ensureLookupResultSets);
    return;
  }
  if (!node || typeof node !== "object") return;
  const record = node as Record<string, unknown>;
  if (record.type === "Lookup") {
    const source = (record.typeProperties as { source?: Record<string, unknown> } | undefined)?.source;
    const query = source?.sqlReaderQuery;
    if (source && typeof query === "string" && WRITE_SQL.test(query) && !query.endsWith(LOOKUP_DONE_SELECT)) {
      source.sqlReaderQuery = query + LOOKUP_DONE_SELECT;
    }
  }
  Object.values(record).forEach(ensureLookupResultSets);
}

function jsonArtifact(
  draft: SpecDraft,
  fileName: string,
  artifactType: ArtifactType,
  layer: ArtifactLayer,
  value: unknown,
): ArtifactVersion {
  if (artifactType === "pipeline") ensureLookupResultSets(value);
  const content = JSON.stringify(value, null, 2) + "\n";
  return {
    id: `art-${fileName}-${Date.now()}`,
    pipelineId: `pipeline-${draft.name}`,
    pipelineName: draft.name,
    fileName,
    fileType: "json",
    artifactType,
    layer,
    version: "v1.0.0",
    state: "generated",
    bytes: content.length,
    content,
  };
}

function textArtifact(
  draft: SpecDraft,
  fileName: string,
  artifactType: ArtifactType,
  layer: ArtifactLayer,
  content: string,
): ArtifactVersion {
  return {
    id: `art-${fileName}-${Date.now()}`,
    pipelineId: `pipeline-${draft.name}`,
    pipelineName: draft.name,
    fileName,
    fileType: fileName.endsWith(".sql") ? "sql" : "config",
    artifactType,
    layer,
    version: "v1.0.0",
    state: "generated",
    bytes: content.length,
    content,
  };
}

const CHILD_PARAMETER_KEYS = [
  "TaskId", "SourceType", "Source", "SourceQuery", "TargetType", "Target", "TargetFileName",
  "LoadingBehavior", "WatermarkColumn", "WatermarkValue",
] as const;

function executeChildActivity(name: string, childPipelineName: string, withColumnMapping = false): unknown {
  // Per-object column mappings go to this pipeline's own child only — a dispatch-case child is an
  // existing pipeline that may not declare a ColumnMapping parameter.
  const keys: readonly string[] = withColumnMapping ? [...CHILD_PARAMETER_KEYS, "ColumnMapping"] : CHILD_PARAMETER_KEYS;
  return {
    name,
    type: "ExecutePipeline",
    typeProperties: {
      parameters: Object.fromEntries(keys.map((key) => [key, { value: `@item().${key}`, type: "Expression" }])),
      pipeline: { referenceName: childPipelineName, type: "PipelineReference" },
      waitOnCompletion: true,
    },
  };
}

/**
 * The master ForEach's inner activities: one `ExecutePipeline` of this pipeline's own child, or — with
 * gap #30's `childDispatch` — a `Switch` on the row's `TaskGroup` whose cases each run a different
 * named child, unmatched/NULL rows falling through to the own child.
 */
function forEachDispatchActivities(draft: SpecDraft): unknown[] {
  const ownChild = executeChildActivity("ExecuteChildPipeline", resolveChildPipelineName(draft), perObjectColumnMappingsActive(draft));
  const cases = childDispatchCases(draft);
  if (!cases.length) return [ownChild];
  return [
    {
      name: "DispatchToChildPipeline",
      description: "Route each control-table row to its child pipeline by TaskGroup; unmatched rows run the default child.",
      type: "Switch",
      typeProperties: {
        on: { value: "@coalesce(item().TaskGroup, '')", type: "Expression" },
        cases: cases.map((c, i) => ({
          value: c.matchValue,
          activities: [executeChildActivity(`ExecuteChildCase${i + 1}`, c.childPipelineName)],
        })),
        defaultActivities: [ownChild],
      },
    },
  ];
}

function masterPipeline(draft: SpecDraft): unknown {
  const loadingBehavior = loadingBehaviorOf(draft);
  const controlTable = draft.watermarkTable || "control.adf_control_table";
  return {
    name: resolveMasterPipelineName(draft),
    type: "Microsoft.DataFactory/factories/pipelines",
    properties: {
      // G6: top-level runs must not overlap (shared staging tables + watermark). Never set on the child — the master fans children out in parallel.
      concurrency: 1,
      description: `Metadata-driven master pipeline for ${draft.sourceObject}. Reads one row per object from the control table and delegates each object (${loadingBehavior}) to the child pipeline.`,
      activities: [
        {
          name: "ReadObjectsFromControlTable",
          description: "Read all enabled objects to copy from the metadata/control table.",
          type: "Lookup",
          policy: {
            timeout: "0.00:10:00.000",
            retry: draft.retryCount,
            retryIntervalInSeconds: draft.retryIntervalInSeconds,
          },
          typeProperties: {
            source: {
              type: "SqlServerSource",
              // ControlTable/TaskIdFilter are read live via @{pipeline().parameters...} (not
              // interpolated at generation time) so an operator can retarget this already-deployed
              // pipeline at trigger time: point ControlTable at a different table/environment, or
              // set TaskIdFilter to the one TaskId to restart (0 = every enabled row, the default —
              // see AGENTS.md's "Operating generated pipelines in production" section).
              sqlReaderQuery: `SELECT TaskId, SourceType, Source, SourceQuery, TargetType, Target, TargetFileName, LoadingBehavior, WatermarkColumn, WatermarkType, WatermarkValue, Enabled${childDispatchCases(draft).length ? ", TaskGroup" : ""}${perObjectColumnMappingsActive(draft) ? ", ColumnMapping" : ""} FROM @{pipeline().parameters.ControlTable} WHERE Enabled = 1 AND (@{pipeline().parameters.TaskIdFilter} = 0 OR TaskId = @{pipeline().parameters.TaskIdFilter}) ORDER BY TaskId`,
            },
            // RUN-05: every control-table row, read as output.value by the ForEach.
            firstRowOnly: false,
          },
        },
        {
          name: "ForEachExecutedObject",
          description: "Batch the control-table rows and delegate each object to the child pipeline.",
          type: "ForEach",
          dependsOn: [{ activity: "ReadObjectsFromControlTable", dependencyConditions: ["Succeeded"] }],
          typeProperties: {
            items: {
              value: "@activity('ReadObjectsFromControlTable').output.value",
              type: "Expression",
            },
            isSequential: draft.isSequential,
            batchCount: draft.batchCount,
            activities: forEachDispatchActivities(draft),
          },
        },
      ],

      parameters: namedParameters([
        { name: "ControlTable", type: "String", defaultValue: controlTable },
        { name: "TaskIdFilter", type: "Int", defaultValue: 0 },
        { name: "SourceLinkedService", type: "String", defaultValue: draft.sourceLinkedService.name },
        { name: "TargetLinkedService", type: "String", defaultValue: draft.targetLinkedService.name },
        { name: "BatchCount", type: "Int", defaultValue: draft.batchCount },
        { name: "ParallelCopies", type: "Int", defaultValue: draft.parallelCopies },
        { name: "RetryCount", type: "Int", defaultValue: draft.retryCount },
      ]),
      folder: { name: `metadata-driven/${draft.medallionLayer}` },
      annotations: pipelineAnnotations(draft),
    },
  };
}

/**
 * Chunked historical/initial-load fan-out (sample-guide parity gap 4): a
 * persisted chunk-plan control table gives restart-from-failure semantics a
 * plain parallelCopies/sourcePartitionOption Copy doesn't — a re-run only
 * re-processes PENDING/FAILED chunks instead of restarting the whole extract.
 * This ForEach fans out over row-range CHUNKS of one object (bounded by
 * `maxParallelChunks`), never over objects — that fan-out is the master
 * pipeline's own ForEach, unrelated and untouched here.
 *
 * The chunk-plan seed query buckets `chunkColumn` into fixed-size ranges with
 * T-SQL (matching this app's existing SQL-Server-flavored control-table
 * convention, regardless of the source connector) — a very unevenly
 * distributed column gets uneven-sized chunks, not uneven row counts; that's
 * an accepted limitation of a generic, connector-agnostic seed strategy.
 */
function chunkedLoadActivities(
  draft: SpecDraft,
  copySink: () => unknown,
  chunkPlanTable: string,
  logTable: string,
  logSuffix = "",
): unknown[] {
  const col = draft.chunkColumn!.trim();
  const chunkSize = draft.chunkSizeRows || 1000000;

  const chunkCopySource = (): unknown => {
    const source = storeSource(
      draft,
      expr(
        `@concat('SELECT * FROM ', pipeline().parameters.SchemaName, '.', pipeline().parameters.TableName, ' WHERE ${col} >= ''', item().RangeStart, ''' AND ${col} <= ''', item().RangeEnd, '''')`,
      ),
    );
    if (connectorSchema(draft.sourceType).kind === "database") source.partitionOption = "None";
    const additionalColumns = additionalColumnsOf(draft.customMetadata);
    if (additionalColumns) source.additionalColumns = additionalColumns;
    return source;
  };
  const chunkTuning: Record<string, unknown> = {
    parallelCopies: draft.parallelCopies,
    ...(draft.dataIntegrationUnits ? { dataIntegrationUnits: draft.dataIntegrationUnits } : {}),
    enableSkipIncompatibleRow: draft.redirectIncompatibleRows,
    ...(draft.redirectIncompatibleRows && draft.rejectsLinkedService.trim()
      ? {
          redirectIncompatibleRowSettings: {
            linkedServiceName: { referenceName: draft.rejectsLinkedService.trim(), type: "LinkedServiceReference" },
            path: draft.rejectsPath || "rejects/",
          },
        }
      : {}),
  };

  const generateChunkPlan = {
    name: "GenerateChunkPlan",
    description: `Idempotently seed ${chunkPlanTable} with fixed-size ${col} ranges for this object, if not already planned.`,
    type: "Lookup",
    policy: { timeout: "0.00:15:00.000", retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
    typeProperties: {
      source: {
        type: "SqlServerSource",
        sqlReaderQuery: [
          ";WITH bounds AS (",
          `  SELECT MIN(${col}) AS lo, MAX(${col}) AS hi FROM @{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}`,
          ")",
          `INSERT INTO ${chunkPlanTable} (TaskId, ChunkId, RangeStart, RangeEnd, Status)`,
          "SELECT @{pipeline().parameters.TaskId}, n.n,",
          `  CAST(bounds.lo + n.n * ${chunkSize} AS NVARCHAR(128)),`,
          `  CAST(CASE WHEN bounds.lo + (n.n + 1) * ${chunkSize} - 1 > bounds.hi THEN bounds.hi ELSE bounds.lo + (n.n + 1) * ${chunkSize} - 1 END AS NVARCHAR(128)),`,
          "  'PENDING'",
          "FROM bounds",
          "CROSS APPLY (",
          `  SELECT TOP (CAST(CEILING((bounds.hi - bounds.lo + 1) * 1.0 / ${chunkSize}) AS INT)) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS n`,
          "  FROM sys.all_objects",
          ") AS n",
          `WHERE NOT EXISTS (SELECT 1 FROM ${chunkPlanTable} WHERE TaskId = @{pipeline().parameters.TaskId})`,
        ].join(" "),
      },
    },
  };
  const lookupPendingChunks = {
    name: "LookupPendingChunks",
    description: "Read chunks not yet succeeded (PENDING or FAILED) — a re-run skips already-SUCCEEDED chunks.",
    type: "Lookup",
    dependsOn: [{ activity: "GenerateChunkPlan", dependencyConditions: ["Succeeded"] }],
    policy: { timeout: "0.00:10:00.000", retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
    typeProperties: {
      source: {
        type: "SqlServerSource",
        sqlReaderQuery: `SELECT ChunkId, RangeStart, RangeEnd FROM ${chunkPlanTable} WHERE TaskId = @{pipeline().parameters.TaskId} AND Status IN ('PENDING', 'FAILED') ORDER BY ChunkId`,
      },
      firstRowOnly: false,
    },
  };
  const forEachChunk = {
    name: "ForEachChunk",
    description: "Copy each pending chunk, tracking per-chunk restart status in the chunk-plan control table.",
    type: "ForEach",
    dependsOn: [{ activity: "LookupPendingChunks", dependencyConditions: ["Succeeded"] }],
    typeProperties: {
      items: { value: "@activity('LookupPendingChunks').output.value", type: "Expression" },
      isSequential: false,
      batchCount: draft.maxParallelChunks,
      activities: [
        {
          name: "MarkChunkRunning",
          type: "Lookup",
          policy: { timeout: "0.00:05:00.000", retry: 0, retryIntervalInSeconds: 30 },
          typeProperties: {
            source: {
              type: "SqlServerSource",
              sqlReaderQuery: `UPDATE ${chunkPlanTable} SET Status = 'RUNNING', StartedAt = GETUTCDATE() WHERE TaskId = @{pipeline().parameters.TaskId} AND ChunkId = @{item().ChunkId}`,
            },
          },
        },
        copyActivity(draft, "CopyChunk", chunkCopySource(), copySink(), chunkTuning, {
          dependsOn: [{ activity: "MarkChunkRunning", dependencyConditions: ["Succeeded"] }],
        }),
        {
          name: "MarkChunkSucceeded",
          type: "Lookup",
          dependsOn: [{ activity: "CopyChunk", dependencyConditions: ["Succeeded"] }],
          policy: { timeout: "0.00:05:00.000", retry: 0, retryIntervalInSeconds: 30 },
          typeProperties: {
            source: {
              type: "SqlServerSource",
              sqlReaderQuery: `UPDATE ${chunkPlanTable} SET Status = 'SUCCEEDED', RowsCopied = @{activity('CopyChunk').output.rowsCopied}, CompletedAt = GETUTCDATE() WHERE TaskId = @{pipeline().parameters.TaskId} AND ChunkId = @{item().ChunkId}`,
            },
          },
        },
        {
          name: "MarkChunkFailed",
          type: "Lookup",
          dependsOn: [{ activity: "CopyChunk", dependencyConditions: ["Failed"] }],
          policy: { timeout: "0.00:05:00.000", retry: 0, retryIntervalInSeconds: 30 },
          typeProperties: {
            source: {
              type: "SqlServerSource",
              sqlReaderQuery: `UPDATE ${chunkPlanTable} SET Status = 'FAILED', ErrorMessage = '@{replace(activity('CopyChunk').Error.message, '''', '')}', CompletedAt = GETUTCDATE() WHERE TaskId = @{pipeline().parameters.TaskId} AND ChunkId = @{item().ChunkId}`,
            },
          },
        },
      ],
    },
  };
  const verifyChunksComplete = {
    name: "VerifyAllChunksSucceeded",
    description: "Fail the pipeline if any chunk did not reach SUCCEEDED — makes an incomplete chunked load visible instead of silently partial.",
    type: "Lookup",
    dependsOn: [{ activity: "ForEachChunk", dependencyConditions: ["Succeeded"] }],
    policy: { timeout: "0.00:05:00.000", retry: 0, retryIntervalInSeconds: 30 },
    typeProperties: {
      source: {
        type: "SqlServerSource",
        sqlReaderQuery: `SELECT COUNT(*) AS Remaining FROM ${chunkPlanTable} WHERE TaskId = @{pipeline().parameters.TaskId} AND Status <> 'SUCCEEDED'`,
      },
    },
  };
  const failIfIncomplete = {
    name: "FailIfChunksIncomplete",
    type: "IfCondition",
    dependsOn: [{ activity: "VerifyAllChunksSucceeded", dependencyConditions: ["Succeeded"] }],
    typeProperties: {
      expression: { type: "Expression", value: "@greater(activity('VerifyAllChunksSucceeded').output.firstRow.Remaining, 0)" },
      ifTrueActivities: [
        {
          name: "FailChunkedLoad",
          type: "Fail",
          typeProperties: {
            message: {
              type: "Expression",
              value:
                "@concat('Chunked load incomplete: ', string(activity('VerifyAllChunksSucceeded').output.firstRow.Remaining), ' chunk(s) not SUCCEEDED. Re-run the pipeline to retry only those chunks.')",
            },
            errorCode: "ChunkedLoadIncomplete",
          },
        },
      ],
    },
  };

  if (!draft.enableCopyLog) {
    return [generateChunkPlan, lookupPendingChunks, forEachChunk, verifyChunksComplete, failIfIncomplete];
  }
  const logStart = {
    name: `LogRunStart${logSuffix}`,
    description: "Record the start of this object's run in the shared execution-log control table.",
    type: "Lookup",
    policy: { timeout: "0.00:05:00.000", retry: 0, retryIntervalInSeconds: 30 },
    typeProperties: {
      source: {
        type: "SqlServerSource",
        sqlReaderQuery: `INSERT INTO ${logTable} (RunId, PipelineName, SourceObject, TargetObject, LoadType, Status, StartedAt) VALUES ('@{pipeline().RunId}', '@{pipeline().Pipeline}', '@{pipeline().parameters.Source}', '@{pipeline().parameters.Target}', 'ChunkedLoad', 'RUNNING', GETUTCDATE())`,
      },
    },
  };
  return [
    logStart,
    { ...generateChunkPlan, dependsOn: [{ activity: `LogRunStart${logSuffix}`, dependencyConditions: ["Succeeded"] }] },
    lookupPendingChunks,
    forEachChunk,
    verifyChunksComplete,
    failIfIncomplete,
    {
      name: `LogRunSucceeded${logSuffix}`,
      type: "Lookup",
      dependsOn: [{ activity: "VerifyAllChunksSucceeded", dependencyConditions: ["Succeeded"] }],
      policy: { timeout: "0.00:05:00.000", retry: 0, retryIntervalInSeconds: 30 },
      typeProperties: {
        source: {
          type: "SqlServerSource",
          sqlReaderQuery: `UPDATE ${logTable} SET Status = 'SUCCEEDED', CompletedAt = GETUTCDATE() WHERE RunId = '@{pipeline().RunId}' AND SourceObject = '@{pipeline().parameters.Source}'`,
        },
      },
    },
    {
      name: `LogRunFailed${logSuffix}`,
      type: "Lookup",
      dependsOn: [{ activity: "ForEachChunk", dependencyConditions: ["Failed"] }],
      policy: { timeout: "0.00:05:00.000", retry: 0, retryIntervalInSeconds: 30 },
      typeProperties: {
        source: {
          type: "SqlServerSource",
          sqlReaderQuery: `UPDATE ${logTable} SET Status = 'FAILED', ErrorMessage = 'One or more chunks failed', CompletedAt = GETUTCDATE() WHERE RunId = '@{pipeline().RunId}' AND SourceObject = '@{pipeline().parameters.Source}'`,
        },
      },
    },
  ];
}

/** The shared execution-log control table name for a draft (sample-guide parity gap 2). */
function logTableOf(draft: SpecDraft): string {
  return `${controlSchemaOf(draft)}.adf_execution_log`;
}

/**
 * Standard execution logging (sample-guide parity gap 2) — a start row per run. `loadType`, when given,
 * is a literal value (e.g. "FullLoad"/"DeltaLoad" — the independent patterns, §24.12, have no
 * `LoadingBehavior` pipeline parameter to read dynamically, unlike `childPipeline`'s Switch-routed
 * shape); omitted, it reads `@pipeline().parameters.LoadingBehavior` exactly as before.
 */
function logStartActivityOf(logTable: string, loadType?: string, logSuffix = ""): unknown {
  const loadTypeValue = loadType ? `'${loadType}'` : "'@{pipeline().parameters.LoadingBehavior}'";
  return {
    name: `LogRunStart${logSuffix}`,
    description: "Record the start of this object's run in the shared execution-log control table.",
    type: "Lookup",
    policy: { timeout: "0.00:05:00.000", retry: 0, retryIntervalInSeconds: 30 },
    typeProperties: {
      source: {
        type: "SqlServerSource",
        sqlReaderQuery: `INSERT INTO ${logTable} (RunId, PipelineName, SourceObject, TargetObject, LoadType, Status, StartedAt) VALUES ('@{pipeline().RunId}', '@{pipeline().Pipeline}', '@{pipeline().parameters.Source}', '@{pipeline().parameters.Target}', ${loadTypeValue}, 'RUNNING', GETUTCDATE())`,
      },
    },
  };
}
/** Success/failure write-back for the row `logStartActivityOf` inserted. */
function logEndActivityOf(
  logTable: string,
  name: string,
  dependsOnActivity: string,
  condition: "Succeeded" | "Failed",
  copyActivityName: string,
): unknown {
  return {
    name,
    description:
      condition === "Succeeded"
        ? "Mark this run succeeded in the execution-log control table, with rows read/written from the Copy activity."
        : "Mark this run failed in the execution-log control table, with the failing activity's error message.",
    type: "Lookup",
    policy: { timeout: "0.00:05:00.000", retry: 0, retryIntervalInSeconds: 30 },
    dependsOn: [{ activity: dependsOnActivity, dependencyConditions: [condition] }],
    typeProperties: {
      source: {
        type: "SqlServerSource",
        sqlReaderQuery:
          condition === "Succeeded"
            ? `UPDATE ${logTable} SET Status = 'SUCCEEDED', RowsRead = @{activity('${copyActivityName}').output.rowsRead}, RowsWritten = @{activity('${copyActivityName}').output.rowsCopied}, CompletedAt = GETUTCDATE() WHERE RunId = '@{pipeline().RunId}' AND SourceObject = '@{pipeline().parameters.Source}'`
            : `UPDATE ${logTable} SET Status = 'FAILED', ErrorMessage = '@{replace(activity('${copyActivityName}').Error.message, '''', '')}', CompletedAt = GETUTCDATE() WHERE RunId = '@{pipeline().RunId}' AND SourceObject = '@{pipeline().parameters.Source}'`,
      },
    },
  };
}
/** Wrap a load-path's activities with LogRunStart/success/failure when logging is enabled (gap 2). Shared by
 *  `childPipeline` and the independent (single-object) pipeline strategies — §24.12. `logSuffix` keeps the
 *  log activity names unique across the child's Switch branches (ADF requires pipeline-wide unique names). */
function withExecutionLogOf(
  draft: SpecDraft,
  logTable: string,
  activities: unknown[],
  finalActivityName: string,
  copyActivityName: string,
  loadType?: string,
  logSuffix = "",
): unknown[] {
  if (!draft.enableCopyLog) return activities;
  return [
    logStartActivityOf(logTable, loadType, logSuffix),
    ...activities,
    logEndActivityOf(logTable, `LogRunSucceeded${logSuffix}`, finalActivityName, "Succeeded", copyActivityName),
    logEndActivityOf(logTable, `LogRunFailed${logSuffix}`, copyActivityName, "Failed", copyActivityName),
  ];
}

/** ADF Copy activity tuning shared by every load path (staging, DIUs, reject routing, row-count check). */
function copyTuningOf(draft: SpecDraft): Record<string, unknown> {
  return {
    enableStaging: draft.enableStaging,
    ...(draft.enableStaging
      ? {
          stagingSettings: {
            linkedServiceName: {
              referenceName: draft.stagingLinkedService,
              type: "LinkedServiceReference",
            },
            path: draft.stagingPath || "staging/",
            enableCompression: draft.stagingEnableCompression,
          },
        }
      : {}),
    parallelCopies: draft.parallelCopies,
    ...(draft.dataIntegrationUnits ? { dataIntegrationUnits: draft.dataIntegrationUnits } : {}),
    enableSkipIncompatibleRow: draft.redirectIncompatibleRows,
    // Sample-guide parity gap 6 — enableSkipIncompatibleRow alone just discards
    // rejected rows; ADF also needs a destination to actually land them.
    ...(draft.redirectIncompatibleRows && draft.rejectsLinkedService.trim()
      ? {
          redirectIncompatibleRowSettings: {
            linkedServiceName: { referenceName: draft.rejectsLinkedService.trim(), type: "LinkedServiceReference" },
            path: draft.rejectsPath || "rejects/",
          },
        }
      : {}),
    // §30.4 — row-count validation was a UI/parameters-file toggle with no generated behaviour
    // behind it (RowCountValidationEnabled was written to parameters.json but nothing read
    // it). ADF's Copy activity has a native data-consistency check for exactly this; wire the flag
    // to it instead of leaving it a no-op. This app still never runs the check itself — ADF does,
    // at execution time (§30.3 scope boundary).
    validateDataConsistency: draft.rowCountValidationEnabled,
    // Schema-drift slice — opt-in by-name auto-mapping with type conversion, so a source column
    // type change converts instead of failing the copy. Unset = no translator, unchanged output.
    ...copyTranslatorOf(draft),
  };
}

/** Schema-drift slice — `translator` from the type-conversion toggle and/or explicit column mappings. */
function copyTranslatorOf(draft: SpecDraft): { translator?: Record<string, unknown> } {
  const mappings = completeColumnMappings(draft.columnMappings);
  if (!draft.copyTypeConversion && !mappings.length) return {};
  return { translator: translatorFor(draft, mappings) };
}

function translatorFor(draft: SpecDraft, mappings: ColumnMapping[]): Record<string, unknown> {
  const base = draft.copyTypeConversion ? { ...COPY_TYPE_CONVERSION_TRANSLATOR } : { type: "TabularTranslator" };
  const entries = mappings.map((m) => ({ source: { name: m.source }, sink: { name: m.sink } }));
  return entries.length ? { ...base, mappings: entries } : base;
}

/**
 * Per-object column mappings — the translator JSON stored in one control-table row's `ColumnMapping`
 * column: the object's own mappings, else the pipeline-level ones (never empty — a bare
 * `TabularTranslator` maps by name, today's behavior).
 */
function objectTranslatorJson(draft: SpecDraft, entry?: SourceObjectEntry): string {
  const own = completeColumnMappings(entry?.columnMappings);
  return JSON.stringify(translatorFor(draft, own.length ? own : completeColumnMappings(draft.columnMappings)));
}

/** The child Copy's translator when per-object mappings are live: read from the row's `ColumnMapping`. */
const PER_OBJECT_TRANSLATOR = { value: "@json(pipeline().parameters.ColumnMapping)", type: "Expression" };

const COPY_TYPE_CONVERSION_TRANSLATOR = {
  type: "TabularTranslator",
  typeConversion: true,
  typeConversionSettings: { allowDataTruncation: false, treatBooleanAsNumber: false },
};

// RUN-01: a Copy activity names its datasets in activity-level inputs/outputs and carries a store-typed
// source/sink (SqlServerSource, AzureSqlSink, ParquetSink, ...). The store type is derived from the
// connector's dataset type; the SQL family reads `sqlReaderQuery`, other databases read `query`.
// Keep in parity with codegen.py's copy_store_type & co.
const STORE_TYPE_SUFFIX = /(?:Table|Object|Collection|Resource|Dataset|Format)$/;
const STORE_TYPE_OVERRIDES: Record<string, string> = { AzureSqlDW: "SqlDW", AzureSqlMI: "SqlMI" };
const SQL_FAMILY_STORES = new Set(["SqlServer", "AzureSql", "SqlMI", "SqlDW", "AmazonRdsForSqlServer"]);
const FILE_SINK_FORMATS = new Set(["DelimitedText", "Parquet", "Json", "Avro", "Orc"]);

/** ADF Copy source/sink type prefix for a connector (append "Source"/"Sink"). */
export function copyStoreType(connectorType: string): string {
  const base = connectorSchema(connectorType).datasetType.replace(STORE_TYPE_SUFFIX, "");
  return STORE_TYPE_OVERRIDES[base] ?? base;
}

const expr = (value: string) => ({ value, type: "Expression" });

function datasetRef(referenceName: string, parameters?: Record<string, unknown> | null): Record<string, unknown> {
  return { referenceName, type: "DatasetReference", ...(parameters && Object.keys(parameters).length ? { parameters } : {}) };
}

/** A parameterized relational source dataset is pointed at the run's SchemaName/TableName. */
function sourceDatasetParameters(draft: SpecDraft): Record<string, unknown> | null {
  if (connectorSchema(draft.sourceType).kind !== "database" || !draft.sourceDataset.parameterized) return null;
  return { SchemaName: expr("@pipeline().parameters.SchemaName"), TableName: expr("@pipeline().parameters.TableName") };
}

/** A parameterized file target dataset is pointed at the run's Target folder / TargetFileName. */
function targetDatasetParameters(draft: SpecDraft): Record<string, unknown> | null {
  if (connectorSchema(draft.targetType).kind !== "file" || !draft.targetDataset.parameterized) return null;
  return { FolderPath: expr("@pipeline().parameters.Target"), FileName: expr("@pipeline().parameters.TargetFileName") };
}

/** One ADF Copy activity (RUN-01). `opts.targetParams` overrides the default output parameters. */
function copyActivity(
  draft: SpecDraft,
  name: string,
  source: unknown,
  sink: unknown,
  tuning: Record<string, unknown>,
  opts: { description?: string; dependsOn?: unknown[]; targetParams?: Record<string, unknown> } = {},
): Record<string, unknown> {
  return {
    name,
    ...(opts.description ? { description: opts.description } : {}),
    type: "Copy",
    ...(opts.dependsOn?.length ? { dependsOn: opts.dependsOn } : {}),
    policy: { timeout: resolveCopyTimeout(draft), retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
    inputs: [datasetRef(resolveDatasetName(draft, "source"), sourceDatasetParameters(draft))],
    outputs: [datasetRef(resolveDatasetName(draft, "target"), opts.targetParams ?? targetDatasetParameters(draft))],
    typeProperties: { source, sink, ...tuning },
  };
}

/** A store-typed Copy source reading `query` (a database source) or the dataset itself. */
function storeSource(draft: SpecDraft, query: unknown): Record<string, unknown> {
  const schema = connectorSchema(draft.sourceType);
  const store = copyStoreType(draft.sourceType);
  const source: Record<string, unknown> = { type: `${store}Source` };
  if (schema.kind === "database") source[SQL_FAMILY_STORES.has(store) ? "sqlReaderQuery" : "query"] = query;
  else if (schema.kind === "file") {
    const location = schema.datasetLocationType || "AzureBlobFSLocation";
    source.storeSettings = { type: location.replace("Location", "ReadSettings"), recursive: true };
  }
  return source;
}

/** ADF Copy activity source (store type + partitioning + the full/delta query). */
function copySourceOf(draft: SpecDraft, mode: "full" | "delta"): unknown {
  const source = storeSource(draft, mode === "full" ? expr("@pipeline().parameters.SourceQuery") : deltaCopyQuery(draft));
  if (connectorSchema(draft.sourceType).kind === "database") {
    source.partitionOption = draft.sourcePartitionOption;
    if (draft.sourcePartitionOption === "DynamicRange" && draft.sourcePartitionColumn?.trim()) {
      source.partitionSettings = { partitionColumnName: draft.sourcePartitionColumn.trim() };
    }
    if (draft.sourceIsolationLevel && SQL_FAMILY_STORES.has(copyStoreType(draft.sourceType))) {
      source.isolationLevel = draft.sourceIsolationLevel;
    }
  }
  if (draft.queryTimeout?.trim()) source.queryTimeout = draft.queryTimeout.trim();
  // Standard metadata/lineage columns (sample-guide parity gap 1). Real ADF
  // location: Copy activity source.additionalColumns.
  const additionalColumns = additionalColumnsOf(draft.customMetadata);
  if (additionalColumns) source.additionalColumns = additionalColumns;
  return source;
}

/** A database target's `schema.table` from the target dataset (RUN-15/RUN-16); null for a file/service target. Keep in parity with codegen.py. */
function targetTableName(draft: SpecDraft): string | null {
  if (connectorSchema(draft.targetType).kind !== "database") return null;
  const defaults = targetDatasetDefaults(draft);
  const ds = draft.targetDataset;
  return `${ds.schema.trim() || defaults.schema}.${ds.table.trim() || defaults.table}`;
}

/**
 * A store-typed Copy sink (RUN-01): a file target gets store + format write settings, a database target
 * gets write behaviour / upsert keys / pre-copy truncate, never the other kind's settings.
 */
function copySinkOf(draft: SpecDraft, fullLoad = false): unknown {
  const schema = connectorSchema(draft.targetType);
  if (schema.kind === "file") {
    const fmt = FILE_SINK_FORMATS.has(draft.sinkFileFormat) ? draft.sinkFileFormat : "Parquet";
    const location = schema.datasetLocationType || "AzureBlobFSLocation";
    return {
      type: `${fmt}Sink`,
      storeSettings: { type: location.replace("Location", "WriteSettings"), copyBehavior: draft.copyBehavior },
      formatSettings: {
        type: `${fmt}WriteSettings`,
        ...(fmt === "DelimitedText" ? { fileExtension: ".csv" } : {}),
        ...(draft.maxRowsPerFile ? { maxRowsPerFile: draft.maxRowsPerFile } : {}),
      },
    };
  }
  const store = copyStoreType(draft.targetType);
  // RUN-16: a FullLoad with "truncate target" empties a database target before the copy (ADF has no
  // Copy-level `truncate` property), so source deletes don't survive a full reload.
  const truncateTable = fullLoad && draft.truncateTarget ? targetTableName(draft) : null;
  return {
    type: `${store}Sink`,
    ...(SQL_FAMILY_STORES.has(store)
      ? {
          writeBehavior: draft.writeBehavior === "Upsert" ? "upsert" : "insert",
          ...(draft.writeBehavior === "Upsert" && draft.upsertKeyColumns.trim()
            ? {
                upsertSettings: {
                  useTempDB: true,
                  keys: draft.upsertKeyColumns.split(",").map((k) => k.trim()).filter(Boolean),
                },
              }
            : {}),
        }
      : {}),
    ...(truncateTable ? { preCopyScript: `TRUNCATE TABLE ${truncateTable}` } : {}),
    ...(draft.writeBatchSize ? { writeBatchSize: draft.writeBatchSize } : {}),
  };
}

/**
 * Hard-delete handling (`SpecDraft.deleteHandling`) for the DeltaLoad branch — a watermark copy only
 * sees inserts/updates, so deleted source rows would otherwise live on in the target forever. Runs
 * between the delta copy and `UpdateWatermark` (the watermark only advances once deletes are applied)
 * and marks, never physically deletes, rows on the Bronze landed table:
 * - `soft_delete` (key reconciliation): stage every current source key, flag landed rows whose key is
 *   gone, un-flag rows whose key came back.
 * - `cdc`: stage the latest SQL Server CDC operation per key inside the watermark window (mapped to
 *   LSNs), flag keys whose last operation is a delete (1), un-flag inserts/updates (2/4).
 * `none`/unset returns no activities — unchanged output. Keep in parity with codegen.py.
 */
function deleteHandlingActivities(
  draft: SpecDraft,
  table: string,
  afterActivity: string,
  sourceDatasetRef: string,
  targetDatasetRef: string,
  layerPrefixes?: LayerPrefixOverrides,
): { activities: unknown[]; finalActivityName: string } {
  const mode = draft.deleteHandling ?? "none";
  const keys = draft.upsertKeyColumns.split(",").map((k) => k.trim()).filter(Boolean);
  if (mode === "none" || !keys.length) return { activities: [], finalActivityName: afterActivity };
  const landed = layerTargetTableName("bronze", { sourceType: bronzeSourceToken(draft), object: table }, layerPrefixes);
  const stage = `stg.${landed.split(".").pop()}_delete_keys`;
  const flag = resolveSoftDeleteColumn(draft);
  const keyList = keys.join(", ");
  const match = keys.map((k) => `k.${k} = tgt.${k}`).join(" AND ");
  const sourceTable = "@{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}";
  let stageName: string;
  let stageQuery: string;
  let applySql: string;
  if (mode === "cdc") {
    const ci = draft.cdcCaptureInstance?.trim() || "@{pipeline().parameters.SchemaName}_@{pipeline().parameters.TableName}";
    stageName = "StageCdcChanges";
    stageQuery = [
      `DECLARE @min_lsn binary(10) = sys.fn_cdc_get_min_lsn('${ci}');`,
      "DECLARE @from_lsn binary(10) = sys.fn_cdc_map_time_to_lsn('smallest greater than', '@{activity('LookupLastWatermark').output.firstRow.WatermarkValue}');",
      "DECLARE @to_lsn binary(10) = sys.fn_cdc_map_time_to_lsn('largest less than or equal', '@{activity('LookupNewWatermark').output.firstRow.NewWatermark}');",
      "IF @from_lsn IS NOT NULL AND @from_lsn < @min_lsn SET @from_lsn = @min_lsn;",
      `IF @from_lsn IS NULL OR @to_lsn IS NULL OR @from_lsn > @to_lsn SELECT TOP 0 ${keyList}, 0 AS _cdc_operation FROM ${sourceTable};`,
      `ELSE SELECT ${keyList}, _cdc_operation FROM (SELECT ${keyList}, __$operation AS _cdc_operation, ROW_NUMBER() OVER (PARTITION BY ${keyList} ORDER BY __$start_lsn DESC, __$seqval DESC) AS _rn FROM cdc.fn_cdc_get_all_changes_${ci}(@from_lsn, @to_lsn, 'all')) c WHERE _rn = 1;`,
    ].join("\n");
    applySql = [
      `UPDATE tgt SET tgt.${flag} = 1, tgt._deleted_at_utc = SYSUTCDATETIME() FROM ${landed} tgt JOIN ${stage} k ON ${match} WHERE k._cdc_operation = 1 AND tgt.${flag} = 0;`,
      `UPDATE tgt SET tgt.${flag} = 0, tgt._deleted_at_utc = NULL FROM ${landed} tgt JOIN ${stage} k ON ${match} WHERE k._cdc_operation IN (2, 4) AND tgt.${flag} = 1;`,
    ].join("\n");
  } else {
    stageName = "StageSourceKeys";
    stageQuery = `SELECT ${keyList} FROM ${sourceTable}`;
    applySql = [
      `UPDATE tgt SET tgt.${flag} = 1, tgt._deleted_at_utc = SYSUTCDATETIME() FROM ${landed} tgt WHERE tgt.${flag} = 0 AND NOT EXISTS (SELECT 1 FROM ${stage} k WHERE ${match});`,
      `UPDATE tgt SET tgt.${flag} = 0, tgt._deleted_at_utc = NULL FROM ${landed} tgt WHERE tgt.${flag} = 1 AND EXISTS (SELECT 1 FROM ${stage} k WHERE ${match});`,
    ].join("\n");
  }
  const applyName = "ApplySoftDeletes";
  return {
    activities: [
      copyActivity(
        draft,
        stageName,
        storeSource(draft, stageQuery),
        {
          type: `${copyStoreType(draft.targetType)}Sink`,
          tableOption: "autoCreate",
          preCopyScript: `IF OBJECT_ID('${stage}') IS NOT NULL TRUNCATE TABLE ${stage}`,
        },
        {},
        {
          description:
            mode === "cdc"
              ? `Stage the latest CDC operation per key (${keyList}) inside this run's watermark window.`
              : `Stage every current source key (${keyList}) for delete reconciliation.`,
          dependsOn: [{ activity: afterActivity, dependencyConditions: ["Succeeded"] }],
          // The stage table is the parameterized target dataset pointed at `stage`.
          targetParams: { SchemaName: stage.split(".")[0], TableName: stage.slice(stage.indexOf(".") + 1) },
        },
      ),
      {
        name: applyName,
        description: `Mark deleted rows on ${landed} via ${flag} (+ _deleted_at_utc); rows that reappear are un-flagged.`,
        type: "Script",
        linkedServiceName: { referenceName: draft.targetLinkedService.name, type: "LinkedServiceReference" },
        dependsOn: [{ activity: stageName, dependencyConditions: ["Succeeded"] }],
        policy: { timeout: "0.00:30:00.000", retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
        typeProperties: {
          scripts: [
            // Separate batch: SQL Server can't reference a column added in the same batch.
            {
              type: "NonQuery",
              text: `IF COL_LENGTH('${landed}', '${flag}') IS NULL ALTER TABLE ${landed} ADD ${flag} BIT NOT NULL DEFAULT 0;\nIF COL_LENGTH('${landed}', '_deleted_at_utc') IS NULL ALTER TABLE ${landed} ADD _deleted_at_utc DATETIME2 NULL;`,
            },
            { type: "NonQuery", text: applySql },
          ],
        },
      },
    ],
    finalActivityName: applyName,
  };
}

function childPipeline(draft: SpecDraft, layerPrefixes?: LayerPrefixOverrides): unknown {
  const watermarkColumn = draft.watermarkColumns[0]?.name ?? "WatermarkValue";
  const watermarkColumnList = draft.watermarkColumns.map((c) => c.name).join(", ");
  const { schema, table } = splitObjectName(draft.sourceObject, draft.sourceType);
  const sourceDatasetRef = resolveDatasetName(draft, "source");
  const targetDatasetRef = resolveDatasetName(draft, "target");
  const controlSchema = controlSchemaOf(draft);
  const logTable = logTableOf(draft);
  const chunkPlanTable = `${controlSchema}.adf_chunk_plan`;
  const isChunked = draft.chunkingEnabled && !!draft.chunkColumn?.trim();
  const withExecutionLog = (
    activities: unknown[],
    finalActivityName: string,
    copyActivityName: string,
    logSuffix: string,
  ): unknown[] => withExecutionLogOf(draft, logTable, activities, finalActivityName, copyActivityName, undefined, logSuffix);
  const perObjectMappings = perObjectColumnMappingsActive(draft);
  const copyTuning = perObjectMappings ? { ...copyTuningOf(draft), translator: PER_OBJECT_TRANSLATOR } : copyTuningOf(draft);
  const copySource = (mode: "full" | "delta"): unknown => copySourceOf(draft, mode);
  const copySink = (): unknown => copySinkOf(draft);
  const postLoadDq = postLoadDqTable(draft, table, layerPrefixes);
  const { activities: dqActivities, gateDependsOn: dqGateDependsOn } = dataQualityActivities(
    draft,
    schema,
    table,
    postLoadDq ? { table: postLoadDq, dependsOn: "RouteByLoadingBehavior" } : null,
  );
  const fullLoadTransform = layerTransformationActivities(draft, schema, table, "FullLoadCopy", layerPrefixes, true, "_FullLoad");
  const deltaTransform = layerTransformationActivities(draft, schema, table, "UpdateWatermark", layerPrefixes, false, "_DeltaLoad");
  const deltaDeletes = deleteHandlingActivities(draft, table, "CopyDeltaData", sourceDatasetRef, targetDatasetRef, layerPrefixes);
  return {
    name: resolveChildPipelineName(draft),
    type: "Microsoft.DataFactory/factories/pipelines",
    properties: {
      description: `Per-object child pipeline. Routes on LoadingBehavior: FullLoad performs a snapshot copy; DeltaLoad runs watermark lookup -> delta copy -> watermark write-back; ChunkedLoad fans out over row-range chunks with persisted restart status.`,
      activities: [
        ...(postLoadDq ? [] : dqActivities),
        {
          name: "RouteByLoadingBehavior",
          type: "Switch",
          ...(!postLoadDq && dqGateDependsOn.length ? { dependsOn: dqGateDependsOn } : {}),
          typeProperties: {
            on: { value: "@pipeline().parameters.LoadingBehavior", type: "Expression" },
            cases: [
              {
                value: "FullLoad",
                activities: withExecutionLog(
                  [
                    copyActivity(draft, "FullLoadCopy", copySource("full"), copySinkOf(draft, true), copyTuning, {
                      description: `Truncate-and-load full snapshot of ${draft.sourceObject}.`,
                    }),
                    ...fullLoadTransform.activities,
                  ],
                  fullLoadTransform.finalActivityName,
                  "FullLoadCopy",
                  "_FullLoad",
                ),
              },
              {
                value: "ChunkedLoad",
                // High-volume initial/historical seed (sample-guide parity gap 4) — a
                // chunk-plan control table gives restart-from-failure semantics that
                // parallelCopies/sourcePartitionOption alone don't: a re-run reads only
                // PENDING/FAILED chunks, so a network blip mid-extract doesn't restart
                // the whole object. This ForEach fans out over row-range CHUNKS of one
                // object, not over objects (that fan-out is the master pipeline above) —
                // not the orchestration patterns intentionally out of scope here.
                activities: isChunked ? chunkedLoadActivities(draft, copySink, chunkPlanTable, logTable, "_ChunkedLoad") : [],
              },
            ],
            defaultActivities: withExecutionLog(
              [
                {
                  name: "LookupLastWatermark",
                  description: "Read the previous watermark for this object from the control table.",
                  type: "Lookup",
                  policy: {
                    timeout: "0.00:10:00.000",
                    retry: draft.retryCount,
                    retryIntervalInSeconds: draft.retryIntervalInSeconds,
                  },
                  typeProperties: {
                    source: {
                      type: "SqlServerSource",
                      sqlReaderQuery: `SELECT WatermarkValue FROM @{pipeline().parameters.WatermarkTable} WHERE TaskId = @{pipeline().parameters.TaskId}`,
                    },
                  },
                },
                {
                  name: "LookupNewWatermark",
                  description: "Compute the current maximum watermark from the source object.",
                  type: "Lookup",
                  // Runs after LookupLastWatermark so both lookups are guaranteed complete
                  // before CopyDeltaData's dynamic-content query reads their output —
                  // activities without dependsOn execute in parallel in ADF.
                  dependsOn: [{ activity: "LookupLastWatermark", dependencyConditions: ["Succeeded"] }],
                  policy: {
                    timeout: "0.00:10:00.000",
                    retry: draft.retryCount,
                    retryIntervalInSeconds: draft.retryIntervalInSeconds,
                  },
                  typeProperties: {
                    source: {
                      type: "SqlServerSource",
                      sqlReaderQuery: newWatermarkLookupQuery(draft),
                    },
                  },
                },
                copyActivity(draft, "CopyDeltaData", copySource("delta"), copySink(), copyTuning, {
                  description: "Copy rows changed since the last watermark (watermark column > old AND <= new).",
                  dependsOn: [{ activity: "LookupNewWatermark", dependencyConditions: ["Succeeded"] }],
                }),
                ...deltaDeletes.activities,
                {
                  name: "UpdateWatermark",
                  description: "Write the new watermark back to the control table.",
                  type: "Lookup",
                  dependsOn: [{ activity: deltaDeletes.finalActivityName, dependencyConditions: ["Succeeded"] }],
                  policy: {
                    timeout: "0.00:10:00.000",
                    retry: draft.retryCount,
                    retryIntervalInSeconds: draft.retryIntervalInSeconds,
                  },
                  typeProperties: {
                    source: {
                      type: "SqlServerSource",
                      sqlReaderQuery: `UPDATE @{pipeline().parameters.WatermarkTable} SET WatermarkValue = '@{${newWatermarkValueExpr(draft)}}', LastRunTimestamp = GETDATE() WHERE TaskId = @{pipeline().parameters.TaskId}`,
                    },
                  },
                },
                ...deltaTransform.activities,
              ],
              deltaTransform.finalActivityName,
              "CopyDeltaData",
              "_DeltaLoad",
            ),

          },
        },
        // E2E-09: Silver/Gold checks run on the loaded layer target, after the Switch.
        ...(postLoadDq ? dqActivities : []),
      ],
      parameters: namedParameters([
        { name: "TaskId", type: "Int", defaultValue: 1 },
        { name: "SourceType", type: "String", defaultValue: draft.sourceType },
        { name: "Source", type: "String", defaultValue: draft.sourceObject },
        { name: "SourceQuery", type: "String", defaultValue: draft.sourceQuery ?? "" },
        { name: "SchemaName", type: "String", defaultValue: schema },
        { name: "TableName", type: "String", defaultValue: table },
        { name: "TargetType", type: "String", defaultValue: draft.targetType },
        { name: "Target", type: "String", defaultValue: draft.targetPath },
        { name: "TargetFileName", type: "String", defaultValue: draft.targetFileName ?? "" },
        { name: "LoadingBehavior", type: "String", defaultValue: loadingBehaviorOf(draft) },
        { name: "WatermarkColumn", type: "String", defaultValue: watermarkColumn },
        { name: "WatermarkColumnList", type: "String", defaultValue: watermarkColumnList },
        { name: "WatermarkValue", type: "String", defaultValue: draft.watermarkInitialValue || "1900-01-01" },
        { name: "WatermarkTable", type: "String", defaultValue: draft.watermarkTable || "control.adf_control_table" },
        { name: "BatchCount", type: "Int", defaultValue: draft.batchCount },
        { name: "ParallelCopies", type: "Int", defaultValue: draft.parallelCopies },
        ...(perObjectMappings ? [{ name: "ColumnMapping", type: "String", defaultValue: objectTranslatorJson(draft) }] : []),
      ]),
      folder: { name: `metadata-driven/${draft.medallionLayer}` },
      annotations: pipelineAnnotations(draft),
    },
  };
}

/**
 * §24.3/§24.12 "Independent (single-object)" pattern: one source, one target, no control table — the
 * missing capability the metadata-driven-only generator never offered (a plain full or watermark copy
 * pipeline, not scaled out over a control-table object list). Reuses the exact same Copy/log/DQ/
 * transformation building blocks `childPipeline` uses for its `FullLoad`/default (delta) Switch cases,
 * just as the pipeline's only top-level activities instead of one branch of a per-object Switch.
 */
function independentPipeline(draft: SpecDraft, mode: "full" | "delta", layerPrefixes?: LayerPrefixOverrides): unknown {
  const { schema, table } = splitObjectName(draft.sourceObject, draft.sourceType);
  const sourceDatasetRef = resolveDatasetName(draft, "source");
  const targetDatasetRef = resolveDatasetName(draft, "target");
  const logTable = logTableOf(draft);
  const postLoadDq = postLoadDqTable(draft, table, layerPrefixes);
  const copyTuning = copyTuningOf(draft);

  let rawActivities: unknown[];
  let finalActivityName: string;
  let copyActivityName: string;

  if (mode === "full") {
    copyActivityName = "FullLoadCopy";
    const transform = layerTransformationActivities(draft, schema, table, copyActivityName, layerPrefixes, true);
    rawActivities = [
      copyActivity(draft, copyActivityName, copySourceOf(draft, "full"), copySinkOf(draft, true), copyTuning, {
        description: `Truncate-and-load full snapshot of ${draft.sourceObject}.`,
      }),
      ...transform.activities,
    ];
    finalActivityName = transform.finalActivityName;
  } else {
    copyActivityName = "CopyDeltaData";
    const transform = layerTransformationActivities(draft, schema, table, "UpdateWatermark", layerPrefixes);
    const deletes = deleteHandlingActivities(draft, table, copyActivityName, sourceDatasetRef, targetDatasetRef, layerPrefixes);
    rawActivities = [
      {
        name: "LookupLastWatermark",
        description: "Read the previous watermark for this object from the watermark table.",
        type: "Lookup",
        policy: { timeout: "0.00:10:00.000", retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
        typeProperties: {
          source: {
            type: "SqlServerSource",
            sqlReaderQuery: `SELECT WatermarkValue FROM @{pipeline().parameters.WatermarkTable} WHERE TaskId = @{pipeline().parameters.TaskId}`,
          },
        },
      },
      {
        name: "LookupNewWatermark",
        description: "Compute the current maximum watermark from the source object.",
        type: "Lookup",
        dependsOn: [{ activity: "LookupLastWatermark", dependencyConditions: ["Succeeded"] }],
        policy: { timeout: "0.00:10:00.000", retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
        typeProperties: {
          source: {
            type: "SqlServerSource",
            sqlReaderQuery: newWatermarkLookupQuery(draft),
          },
        },
      },
      copyActivity(draft, copyActivityName, copySourceOf(draft, "delta"), copySinkOf(draft), copyTuning, {
        description: "Copy rows changed since the last watermark (watermark column > old AND <= new).",
        dependsOn: [{ activity: "LookupNewWatermark", dependencyConditions: ["Succeeded"] }],
      }),
      ...deletes.activities,
      {
        name: "UpdateWatermark",
        description: "Write the new watermark back to the watermark table.",
        type: "Lookup",
        dependsOn: [{ activity: deletes.finalActivityName, dependencyConditions: ["Succeeded"] }],
        policy: { timeout: "0.00:10:00.000", retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
        typeProperties: {
          source: {
            type: "SqlServerSource",
            sqlReaderQuery: `UPDATE @{pipeline().parameters.WatermarkTable} SET WatermarkValue = '@{${newWatermarkValueExpr(draft)}}', LastRunTimestamp = GETDATE() WHERE TaskId = @{pipeline().parameters.TaskId}`,
          },
        },
      },
      ...transform.activities,
    ];
    finalActivityName = transform.finalActivityName;
  }

  const wrapped = withExecutionLogOf(
    draft,
    logTable,
    rawActivities,
    finalActivityName,
    copyActivityName,
    mode === "full" ? "FullLoad" : "DeltaLoad",
  );
  const { activities: dqActivities, gateDependsOn: dqGateDependsOn } = dataQualityActivities(
    draft,
    schema,
    table,
    postLoadDq ? { table: postLoadDq, dependsOn: finalActivityName } : null,
  );
  // Equivalent to `childPipeline`'s DQ gate on its Switch: here there's no Switch to hang it off, so it
  // gates whichever activity actually runs first (LogRunStart when logging is on, else the copy path's
  // own first activity). E2E-09: Silver/Gold checks instead run after the layer load.
  if (!postLoadDq && dqGateDependsOn.length && wrapped.length) {
    const first = wrapped[0] as { dependsOn?: unknown[] };
    wrapped[0] = { ...first, dependsOn: [...(first.dependsOn ?? []), ...dqGateDependsOn] };
  }
  const activities = postLoadDq ? [...wrapped, ...dqActivities] : [...dqActivities, ...wrapped];

  const watermarkColumn = draft.watermarkColumns[0]?.name ?? "WatermarkValue";
  const watermarkColumnList = draft.watermarkColumns.map((c) => c.name).join(", ");
  const parameters: unknown[] = [
    { name: "TaskId", type: "Int", defaultValue: 1 },
    { name: "SourceType", type: "String", defaultValue: draft.sourceType },
    { name: "Source", type: "String", defaultValue: draft.sourceObject },
    { name: "SourceQuery", type: "String", defaultValue: draft.sourceQuery ?? "" },
    { name: "SchemaName", type: "String", defaultValue: schema },
    { name: "TableName", type: "String", defaultValue: table },
    { name: "TargetType", type: "String", defaultValue: draft.targetType },
    { name: "Target", type: "String", defaultValue: draft.targetPath },
    { name: "TargetFileName", type: "String", defaultValue: draft.targetFileName ?? "" },
  ];
  if (mode === "delta") {
    parameters.push(
      { name: "WatermarkColumn", type: "String", defaultValue: watermarkColumn },
      { name: "WatermarkColumnList", type: "String", defaultValue: watermarkColumnList },
      { name: "WatermarkValue", type: "String", defaultValue: draft.watermarkInitialValue || "1900-01-01" },
      { name: "WatermarkTable", type: "String", defaultValue: draft.watermarkTable || "control.watermarks" },
    );
  }
  parameters.push(
    { name: "BatchCount", type: "Int", defaultValue: draft.batchCount },
    { name: "ParallelCopies", type: "Int", defaultValue: draft.parallelCopies },
  );

  return {
    name: resolvePipelineName(draft),
    type: "Microsoft.DataFactory/factories/pipelines",
    properties: {
      // G6: top-level runs must not overlap (shared staging tables + watermark). Never set on the child — the master fans children out in parallel.
      concurrency: 1,
      description:
        mode === "full"
          ? `Independent single-object full-load pipeline for ${draft.sourceObject}. One source, one target — no control table, no child pipeline.`
          : `Independent single-object watermark-delta pipeline for ${draft.sourceObject}. One source, one target, its own watermark table — no control table, no child pipeline.`,
      activities,
      parameters: namedParameters(parameters),
      folder: { name: `metadata-driven/${draft.medallionLayer}` },
      annotations: pipelineAnnotations(draft),
    },
  };
}

/**
 * §24.3/§24.12 "Independent" watermark-delta pipeline's own watermark table — a single tracked row
 * (`TaskId = 1`), distinct from `controlTableSql`'s multi-object `adf_control_table` (this pattern has
 * no control table at all).
 */
function watermarkTableSql(draft: SpecDraft): string {
  const table = draft.watermarkTable.trim() || "control.watermarks";
  return [
    "-- Watermark table for this independent (single-object) watermark-delta pipeline.",
    "-- One row (TaskId = 1) tracks this pipeline's own last-processed watermark value.",
    "",
    ...createTableIfMissing(table),
    "  TaskId            INT           NOT NULL PRIMARY KEY,",
    "  WatermarkValue    NVARCHAR(256) NULL,",
    "  LastRunTimestamp  DATETIME2     NULL",
    ");",
    "",
    `IF NOT EXISTS (SELECT 1 FROM ${table} WHERE TaskId = 1)`,
    `  INSERT INTO ${table} (TaskId, WatermarkValue) VALUES (1, N'${(draft.watermarkInitialValue || "1900-01-01").replace(/'/g, "''")}');`,
    "",
  ].join("\n");
}

/**
 * §24.3/§24.12 "Single pipeline (metadata-driven)" pattern: the same control-table-driven fan-out as
 * `masterPipeline`'s `ForEachExecutedObject`, but looping `childPipeline`'s own Switch inline instead of
 * delegating to a nested `ExecutePipeline` — no child pipeline artifact at all. Built by taking the
 * child's activities verbatim and rewriting every `@pipeline().parameters.X` reference for the set of
 * parameters the master's `ExecutePipeline` call today overrides per row (`TaskId`, `SourceType`,
 * `Source`, `SourceQuery`, `TargetType`, `Target`, `TargetFileName`, `LoadingBehavior`,
 * `WatermarkColumn`, `WatermarkValue` — see `masterPipeline`) into `@item().X`, since those values now
 * come from the `ForEach` loop's current control-table row instead of a delegated pipeline's own
 * parameters. Every other parameter (`SchemaName`, `TableName`, `WatermarkColumnList`, `WatermarkTable`,
 * ...) stays a fixed, build-time pipeline parameter, matching `masterPipeline`/`childPipeline`'s existing
 * (unchanged) semantics exactly — this pattern doesn't alter what those fields mean, only how the
 * per-object routing itself is wired.
 */
const FOREACH_ITEM_PARAM_NAMES = [
  "TaskId",
  "SourceType",
  "SourceQuery",
  "Source",
  "TargetFileName",
  "TargetType",
  "Target",
  "LoadingBehavior",
  "WatermarkColumn",
  "WatermarkValue",
  "ColumnMapping",
];
const FOREACH_ITEM_PARAM_RE = new RegExp(`pipeline\\(\\)\\.parameters\\.(${FOREACH_ITEM_PARAM_NAMES.join("|")})\\b`, "g");

function singlePipeline(draft: SpecDraft, layerPrefixes?: LayerPrefixOverrides): unknown {
  const child = childPipeline(draft, layerPrefixes) as { properties: { activities: unknown[] } };
  const itemActivities: unknown[] = JSON.parse(
    JSON.stringify(child.properties.activities).replace(FOREACH_ITEM_PARAM_RE, "item().$1"),
  );
  const controlTable = draft.watermarkTable || "control.adf_control_table";
  const { schema, table } = splitObjectName(draft.sourceObject, draft.sourceType);
  return {
    name: resolvePipelineName(draft),
    type: "Microsoft.DataFactory/factories/pipelines",
    properties: {
      // G6: top-level runs must not overlap (shared staging tables + watermark). Never set on the child — the master fans children out in parallel.
      concurrency: 1,
      description: `Single metadata-driven pipeline for ${draft.sourceObject}. Reads one row per object from the control table and loops it inline — no child pipeline.`,
      activities: [
        {
          name: "ReadObjectsFromControlTable",
          description: "Read all enabled objects to copy from the metadata/control table.",
          type: "Lookup",
          policy: { timeout: "0.00:10:00.000", retry: draft.retryCount, retryIntervalInSeconds: draft.retryIntervalInSeconds },
          typeProperties: {
            source: {
              type: "SqlServerSource",
              // See masterPipeline's identical comment: ControlTable/TaskIdFilter are runtime
              // expressions, not baked in at generation time.
              sqlReaderQuery: `SELECT TaskId, SourceType, Source, SourceQuery, TargetType, Target, TargetFileName, LoadingBehavior, WatermarkColumn, WatermarkType, WatermarkValue, Enabled${perObjectColumnMappingsActive(draft) ? ", ColumnMapping" : ""} FROM @{pipeline().parameters.ControlTable} WHERE Enabled = 1 AND (@{pipeline().parameters.TaskIdFilter} = 0 OR TaskId = @{pipeline().parameters.TaskIdFilter}) ORDER BY TaskId`,
            },
            // RUN-05: every control-table row, read as output.value by the ForEach.
            firstRowOnly: false,
          },
        },
        {
          name: "ForEachExecutedObject",
          description: "Batch the control-table rows and process each object inline.",
          type: "ForEach",
          dependsOn: [{ activity: "ReadObjectsFromControlTable", dependencyConditions: ["Succeeded"] }],
          typeProperties: {
            items: { value: "@activity('ReadObjectsFromControlTable').output.value", type: "Expression" },
            isSequential: draft.isSequential,
            batchCount: draft.batchCount,
            activities: itemActivities,
          },
        },
      ],
      parameters: namedParameters([
        { name: "ControlTable", type: "String", defaultValue: controlTable },
        { name: "TaskIdFilter", type: "Int", defaultValue: 0 },
        { name: "SourceLinkedService", type: "String", defaultValue: draft.sourceLinkedService.name },
        { name: "TargetLinkedService", type: "String", defaultValue: draft.targetLinkedService.name },
        { name: "SchemaName", type: "String", defaultValue: schema },
        { name: "TableName", type: "String", defaultValue: table },
        { name: "WatermarkColumnList", type: "String", defaultValue: draft.watermarkColumns.map((c) => c.name).join(", ") },
        { name: "WatermarkTable", type: "String", defaultValue: draft.watermarkTable || "control.adf_control_table" },
        { name: "BatchCount", type: "Int", defaultValue: draft.batchCount },
        { name: "ParallelCopies", type: "Int", defaultValue: draft.parallelCopies },
        { name: "RetryCount", type: "Int", defaultValue: draft.retryCount },
      ]),
      folder: { name: `metadata-driven/${draft.medallionLayer}` },
      annotations: pipelineAnnotations(draft),
    },
  };
}

const capitalize = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

function datasetArtifact(draft: SpecDraft, kind: "source" | "target"): ArtifactVersion {
  const ls = kind === "source" ? draft.sourceLinkedService : draft.targetLinkedService;
  const ds = kind === "source" ? draft.sourceDataset : draft.targetDataset;
  const defaults = kind === "source" ? sourceDatasetDefaults(draft) : targetDatasetDefaults(draft);
  const value = {
    name: datasetName(ds, defaults),
    type: "Microsoft.DataFactory/factories/datasets",
    // §25 docs.description — a visibly-derived description, so generated output passes its own advisory.
    properties: {
      description: `${capitalize(kind)} dataset for pipeline ${draft.name} (${ls.connectorType}).`,
      ...buildDatasetProperties(ds, ls, defaults),
    },
  };
  return jsonArtifact(draft, `${value.name}.json`, "dataset", "data", value);
}

function linkedServiceArtifact(draft: SpecDraft, kind: "source" | "target"): ArtifactVersion {
  const ls = kind === "source" ? draft.sourceLinkedService : draft.targetLinkedService;
  const value = {
    name: ls.name,
    type: "Microsoft.DataFactory/factories/linkedServices",
    properties: {
      description: `${capitalize(kind)} connection (${ls.connectorType}) for pipeline ${draft.name}.`,
      ...buildLinkedServiceProperties(ls),
    },
  };
  // `ls.name` already carries the `LS_` convention prefix (see
  // `defaultLinkedServiceDraft` in connections.ts) — the file name must match the
  // declared `name` property exactly, or ADF's Git-integration import sees a
  // mismatch between the resource name and its file.
  return jsonArtifact(draft, `${ls.name}.json`, "linked_service", "connection", value);
}

/** §32.5/§32.7 — one `credentials` artifact per distinct user-assigned identity (source and target
 * may share one). Mirrors worker/app/generator/codegen.py's `_credential_artifacts_for`. */
function credentialArtifacts(draft: SpecDraft): ArtifactVersion[] {
  const seen = new Map<string, ArtifactVersion>();
  for (const ls of [draft.sourceLinkedService, draft.targetLinkedService]) {
    if (!usesUserAssignedIdentity(ls)) continue;
    const name = ls.credentialName!.trim();
    if (seen.has(name)) continue;
    const value = {
      name,
      type: "Microsoft.DataFactory/factories/credentials",
      properties: {
        type: "ManagedIdentity",
        typeProperties: { resourceId: (ls.credentialResourceId ?? "").trim() },
      },
    };
    seen.set(name, jsonArtifact(draft, `${name}.json`, "credential", "connection", value));
  }
  return [...seen.values()];
}

function controlTableSql(draft: SpecDraft): string {
  const objects = effectiveSourceObjects(draft);
  const defaultWatermark = draft.watermarkColumns[0]?.name ?? "WatermarkValue";
  const loadingBehavior = loadingBehaviorOf(draft);
  const escapedQuery = (draft.sourceQuery ?? "").replace(/'/g, "''").trim();
  // The pipeline's own Lookup ("ReadObjectsFromControlTable" in masterPipeline)
  // and its ControlTable/WatermarkTable parameters read from exactly this
  // reference (draft.watermarkTable) — the DDL below must create/seed the
  // *same* table, or a project/team that customizes it away from the default
  // gets a pipeline that queries a table this script never creates.
  const controlTable = draft.watermarkTable.trim() || "control.adf_control_table";
  const perObjectMappings = perObjectColumnMappingsActive(draft);
  const lines: string[] = [
    "-- Metadata / control table for the metadata-driven Copy Data pattern.",
    "-- One row per object to copy. Add, disable, or re-configure objects here",
    "-- without ever rebuilding the pipeline.",
    "",
    ...createTableIfMissing(controlTable),
    "  TaskId            INT IDENTITY(1,1) PRIMARY KEY,",
    "  SourceType        NVARCHAR(128)  NOT NULL,",
    "  Source            NVARCHAR(512)  NOT NULL,",
    "  SourceQuery       NVARCHAR(MAX)  NULL,",
    "  TargetType        NVARCHAR(128)  NOT NULL,",
    "  Target            NVARCHAR(512)  NOT NULL,",
    "  TargetFileName    NVARCHAR(512)  NULL,",
    "  LoadingBehavior   NVARCHAR(16)   NOT NULL DEFAULT 'DeltaLoad', -- FullLoad | DeltaLoad",
    "  WatermarkColumn   NVARCHAR(128)  NULL,",
    "  WatermarkType     NVARCHAR(16)   NULL,                        -- single | composite",
    "  WatermarkValue    NVARCHAR(256)  NULL,",
    "  Enabled           BIT            NOT NULL DEFAULT 1,",
    ...(childDispatchCases(draft).length
      ? ["  TaskGroup         NVARCHAR(128)  NULL,                        -- master Switch dispatch key (NULL = default child)"]
      : []),
    ...(perObjectMappings
      ? ["  ColumnMapping     NVARCHAR(MAX)  NULL,                        -- per-object Copy translator JSON"]
      : []),
    "  LastRunTimestamp  DATETIME2      NULL,",
    "  CreatedAt         DATETIME2      NOT NULL DEFAULT GETDATE()",
    ");",
    "",
    `-- Seed rows for the pipeline created in this wizard (${draft.name}).`,
  ];
  // RUN-15: a database target's Target is its schema.table, not the (file) target path.
  const tableTarget = targetTableName(draft);
  // Several objects into one lake folder would overwrite/mix each other's files: each gets its own subfolder.
  const perObjectFolder = !tableTarget && objects.length > 1;
  for (const entry of objects) {
    const behavior = entry.loadingBehavior ?? loadingBehavior;
    const targetValue =
      tableTarget ??
      (perObjectFolder
        ? `${draft.targetPath.replace(/\/+$/, "")}/${splitObjectName(entry.object, draft.sourceType).table.toLowerCase().replace(/[^a-z0-9_]/g, "_")}/`
        : draft.targetPath);
    // A FullLoad row reads no watermark — never seed the form's default column name for it.
    // (Empty, not NULL: the master passes it into the child's String parameter either way.)
    const watermark = entry.watermarkColumn?.trim() || (behavior === "FullLoad" ? "" : defaultWatermark);
    lines.push(
      // RUN-15: re-running the script must not duplicate a row (same Source -> Target).
      `IF NOT EXISTS (SELECT 1 FROM ${controlTable} WHERE Source = N'${entry.object.replace(/'/g, "''")}' AND Target = N'${targetValue}')`,
      `INSERT INTO ${controlTable} (`,
      "  SourceType, Source, SourceQuery, TargetType, Target, TargetFileName,",
      `  LoadingBehavior, WatermarkColumn, WatermarkType, WatermarkValue, Enabled${perObjectMappings ? ", ColumnMapping" : ""}`,
      ") VALUES (",
      `  N'${draft.sourceType}', N'${entry.object.replace(/'/g, "''")}', N'${escapedQuery}',`,
      `  N'${draft.targetType}', N'${targetValue}', N'${draft.targetFileName ?? ""}',`,
      `  N'${behavior}', N'${watermark}', N'${draft.watermarkType}', N'${draft.watermarkInitialValue || "1900-01-01"}', 1` +
        (perObjectMappings ? `, N'${objectTranslatorJson(draft, entry).replace(/'/g, "''")}'` : ""),
      ");",
      "",
    );
  }
  const controlSchema = controlSchemaOf(draft);
  if (draft.enableCopyLog) {
    // Standard execution logging (sample-guide parity gap 2) — the one shared
    // on-call/audit surface every generated pipeline's LogRunStart/Succeeded/
    // Failed activities (see childPipeline) write rows into.
    lines.push(
      "-- Shared execution-log control table. Every generated pipeline writes one row per run here",
      "-- (LogRunStart on start, then LogRunSucceeded/LogRunFailed on completion) so run history,",
      "-- row counts, and failures are queryable in one place instead of only in ADF's own run history.",
      ...createTableIfMissing(`${controlSchema}.adf_execution_log`),
      "  LogId          INT IDENTITY(1,1) PRIMARY KEY,",
      "  RunId          NVARCHAR(64)   NOT NULL,",
      "  PipelineName   NVARCHAR(256)  NOT NULL,",
      "  SourceObject   NVARCHAR(512)  NOT NULL,",
      "  TargetObject   NVARCHAR(512)  NULL,",
      "  LoadType       NVARCHAR(16)   NULL,       -- FullLoad | DeltaLoad | ChunkedLoad",
      "  RowsRead       BIGINT         NULL,",
      "  RowsWritten    BIGINT         NULL,",
      "  RowsRejected   BIGINT         NULL,",
      "  Status         NVARCHAR(16)   NOT NULL,   -- RUNNING | SUCCEEDED | FAILED",
      "  ErrorMessage   NVARCHAR(MAX)  NULL,",
      "  StartedAt      DATETIME2      NOT NULL,",
      "  CompletedAt    DATETIME2      NULL",
      ");",
      "",
    );
  }
  if (draft.chunkingEnabled && draft.chunkColumn?.trim()) {
    // Chunked historical/initial-load control table (sample-guide parity gap 4).
    // Persisted per-chunk status is what makes a chunked load restartable — a
    // re-run's LookupPendingChunks (see childPipeline) only reads PENDING/FAILED
    // rows, so a network blip mid-extract doesn't restart the whole object.
    lines.push(
      "-- Chunk-plan control table for the chunked historical/initial-load strategy.",
      "-- GenerateChunkPlan seeds one row per chunk (idempotent, per TaskId); ForEachChunk",
      "-- updates Status per chunk so a re-run only retries PENDING/FAILED chunks.",
      ...createTableIfMissing(`${controlSchema}.adf_chunk_plan`),
      "  TaskId         INT            NOT NULL,",
      "  ChunkId        INT            NOT NULL,",
      "  RangeStart     NVARCHAR(128)  NOT NULL,",
      `  RangeEnd       NVARCHAR(128)  NOT NULL,   -- bounds on ${draft.chunkColumn.trim()}`,
      "  Status         NVARCHAR(16)   NOT NULL DEFAULT 'PENDING', -- PENDING | RUNNING | SUCCEEDED | FAILED",
      "  RowsCopied     BIGINT         NULL,",
      "  StartedAt      DATETIME2      NULL,",
      "  CompletedAt    DATETIME2      NULL,",
      "  ErrorMessage   NVARCHAR(MAX)  NULL,",
      "  PRIMARY KEY (TaskId, ChunkId)",
      ");",
      "",
    );
  }
  if (draft.connectionControlEnabled) {
    lines.push(...connectionControlSql(draft));
  }
  return lines.join("\n");
}

/**
 * §33.7 — the two additive, sibling control-table artifacts generated
 * **only** when this pipeline's `medallionLayer` matches `kind` **and** the
 * matching `silverObjectRef`/`martObjectRef` is set (i.e. built from a
 * picked registry object, not a manually-authored Silver/Gold pipeline).
 * Deliberately **not** an extension of `controlTableSql`/`adf_control_table`
 * — bronze's control table is copy-activity-shaped, these are
 * transformation-shaped; forcing both into one schema would add
 * permanently-NULL columns to every bronze row and risk the
 * byte-identical-output guarantee bronze pipelines already depend on
 * (§18's generator-parity suite). Full DDL: `docs/DDL.md` §2.3.
 */
function registryControlTableSql(draft: SpecDraft, kind: "curated" | "mart", layerPrefixes?: LayerPrefixOverrides): string {
  const objectKey = (kind === "curated" ? draft.silverObjectRef : draft.martObjectRef) ?? "";
  const table = kind === "curated" ? "control.adf_curated_control_table" : "control.adf_mart_control_table";
  const targetTable = layerTargetTableName(draft.medallionLayer, { entity: draft.curatedEntity, domain: draft.martDomain }, layerPrefixes);
  const tag = draft.registryTag ?? "";
  const sourceKeys = (draft.registrySourceObjectKeys ?? []).join("|");
  const transformCount = draft.transformations.length;
  const dqCount = draft.dataQualityRules.length;
  const loadingBehavior = loadingBehaviorOf(draft);
  const esc = (v: string) => v.replace(/'/g, "''");

  if (kind === "curated") {
    return [
      "-- Curated (Silver) control table — auto-captured on publish from the registry object this",
      "-- pipeline was built from (AGENTS.md §33.7). Sibling to adf_control_table.sql, not an extension of it.",
      ...createTableIfMissing(table),
      "  ObjectId              INT IDENTITY(1,1) PRIMARY KEY,",
      "  ObjectKey             NVARCHAR(256)  NOT NULL,",
      "  Tag                   NVARCHAR(128)  NOT NULL,",
      "  CuratedEntity         NVARCHAR(256)  NOT NULL,",
      "  TargetTable           NVARCHAR(512)  NOT NULL,",
      "  SourceObjectKeys      NVARCHAR(MAX)  NULL,",
      "  TransformationCount   INT            NOT NULL DEFAULT 0,",
      "  DataQualityRuleCount  INT            NOT NULL DEFAULT 0,",
      "  WriteBehavior         NVARCHAR(16)   NULL,",
      "  LoadingBehavior       NVARCHAR(16)   NULL,",
      "  Enabled               BIT            NOT NULL DEFAULT 1,",
      "  CreatedAt             DATETIME2      NOT NULL DEFAULT GETDATE()",
      ");",
      "",
      `IF NOT EXISTS (SELECT 1 FROM ${table} WHERE ObjectKey = N'${esc(objectKey)}')`,
      `INSERT INTO ${table} (`,
      "  ObjectKey, Tag, CuratedEntity, TargetTable, SourceObjectKeys, TransformationCount,",
      "  DataQualityRuleCount, WriteBehavior, LoadingBehavior, Enabled",
      ") VALUES (",
      `  N'${esc(objectKey)}', N'${esc(tag)}', N'${esc(draft.curatedEntity ?? "")}',`,
      `  N'${targetTable}', ${sqlStringOrNull(sourceKeys)}, ${transformCount},`,
      `  ${dqCount}, ${sqlStringOrNull(draft.writeBehavior)}, ${sqlStringOrNull(loadingBehavior)}, 1`,
      ");",
      "",
    ].join("\n");
  }
  return [
    "-- Mart (Gold) control table — auto-captured on publish from the registry object this pipeline",
    "-- was built from (AGENTS.md §33.7). Sibling to adf_control_table.sql, not an extension of it.",
    ...createTableIfMissing(table),
    "  ObjectId                  INT IDENTITY(1,1) PRIMARY KEY,",
    "  ObjectKey                 NVARCHAR(256) NOT NULL,",
    "  Tag                       NVARCHAR(128) NOT NULL,",
    "  MartDomain                NVARCHAR(128) NOT NULL,",
    "  CuratedEntity             NVARCHAR(256) NOT NULL,",
    "  TargetTable               NVARCHAR(512) NOT NULL,",
    "  SourceCuratedEntities     NVARCHAR(MAX) NULL,",
    "  TransformationCount       INT           NOT NULL DEFAULT 0,",
    "  DataQualityRuleCount      INT           NOT NULL DEFAULT 0,",
    "  WriteBehavior             NVARCHAR(16)  NULL,",
    "  LoadingBehavior           NVARCHAR(16)  NULL,",
    "  Enabled                   BIT           NOT NULL DEFAULT 1,",
    "  CreatedAt                 DATETIME2     NOT NULL DEFAULT GETDATE()",
    ");",
    "",
    `IF NOT EXISTS (SELECT 1 FROM ${table} WHERE ObjectKey = N'${esc(objectKey)}')`,
    `INSERT INTO ${table} (`,
    "  ObjectKey, Tag, MartDomain, CuratedEntity, TargetTable, SourceCuratedEntities,",
    "  TransformationCount, DataQualityRuleCount, WriteBehavior, LoadingBehavior, Enabled",
    ") VALUES (",
    `  N'${esc(objectKey)}', N'${esc(tag)}', N'${esc(draft.martDomain ?? "")}',`,
    `  N'${esc(draft.curatedEntity ?? "")}', N'${targetTable}', ${sqlStringOrNull(sourceKeys)},`,
    `  ${transformCount}, ${dqCount}, ${sqlStringOrNull(draft.writeBehavior)}, ${sqlStringOrNull(loadingBehavior)}, 1`,
    ");",
    "",
  ].join("\n");
}

/** `null` unless this is a Silver pipeline built from a picked `app.silver_objects` row. */
export function curatedControlTableSql(draft: SpecDraft, layerPrefixes?: LayerPrefixOverrides): string | null {
  if (draft.medallionLayer !== "silver" || !draft.silverObjectRef) return null;
  return registryControlTableSql(draft, "curated", layerPrefixes);
}

/**
 * RUN-09 slice: the schemas a Silver/Gold chain reads and writes (Bronze landed table, staging, layer
 * target), guarded so the script is rerunnable. The tables themselves are not created. `null` for Bronze
 * or an empty chain.
 */
export function layerSchemasSql(draft: SpecDraft, layerPrefixes?: LayerPrefixOverrides): string | null {
  if (draft.medallionLayer === "bronze" || !draft.transformations.slice(0, MAX_TRANSFORMATION_RULES).some((r) => r.name.trim())) {
    return null;
  }
  const { table } = splitObjectName(draft.sourceObject, draft.sourceType);
  const tables = [
    layerTargetTableName("bronze", { sourceType: bronzeSourceToken(draft), object: table }, layerPrefixes),
    "stg.",
    layerTargetTableName(
      draft.medallionLayer,
      { sourceType: bronzeSourceToken(draft), object: table, entity: draft.curatedEntity, domain: draft.martDomain },
      layerPrefixes,
    ),
  ];
  const schemas = [...new Set(tables.filter((t) => t.includes(".")).map((t) => t.split(".")[0]))];
  const lines = ["-- Schemas the transformation chain reads and writes. Run once on the target store before the first run.", ""];
  lines.push(...schemas.map((s) => `IF SCHEMA_ID(N'${s}') IS NULL EXEC(N'CREATE SCHEMA [${s}]');`));
  return lines.join("\n") + "\n";
}

/** `null` unless this is a Gold pipeline built from a picked `app.mart_objects` row. */
export function martControlTableSql(draft: SpecDraft, layerPrefixes?: LayerPrefixOverrides): string | null {
  if (draft.medallionLayer !== "gold" || !draft.martObjectRef) return null;
  return registryControlTableSql(draft, "mart", layerPrefixes);
}

/**
 * T-SQL has no `CREATE TABLE IF NOT EXISTS` (that is Postgres/MySQL syntax), so guard the create with
 * `OBJECT_ID` instead — the generated scripts target Azure SQL / SQL Server and must re-run cleanly.
 */
export function createTableIfMissing(table: string): string[] {
  return [`IF OBJECT_ID(N'${table.replace(/'/g, "''")}', N'U') IS NULL`, `CREATE TABLE ${table} (`];
}

/** SQL literal, or NULL when blank — avoids inserting an empty string for an optional column. */
function sqlStringOrNull(value: string | undefined | null): string {
  const v = (value ?? "").trim();
  return v ? `N'${v.replace(/'/g, "''")}'` : "NULL";
}

/**
 * Connection control table (AGENTS.md §26.5/§26.6, docs/DDL.md §2.1): one row
 * per distinct linked service this pipeline references, decoupling its
 * connector/Key Vault/property detail from the pipeline JSON so ops can
 * repoint a connection per environment/team by editing this table instead of
 * regenerating artifacts. Scoped to source + target — staging/rejects/
 * trigger-storage slots are plain linked-service-name strings on SpecDraft
 * with no connector-type/auth metadata attached, so they aren't represented.
 */
function connectionControlSql(draft: SpecDraft): string[] {
  const connTable = draft.connectionControlTable.trim() || "control.adf_connection_control";
  const rows = new Map<string, LinkedServiceDraft>();
  for (const ls of [draft.sourceLinkedService, draft.targetLinkedService]) {
    if (ls.name.trim()) rows.set(ls.name, ls);
  }
  const lines: string[] = [
    "-- Connection control table: one row per linked service this pipeline uses,",
    "-- decoupling its connector/Key Vault/property detail from the pipeline JSON",
    "-- so ops can repoint a connection per environment without regenerating artifacts.",
    ...createTableIfMissing(connTable),
    "  ConnectionId      INT IDENTITY(1,1) PRIMARY KEY,",
    "  ConnectionName    NVARCHAR(128)  NOT NULL UNIQUE,",
    "  ConnectorType     NVARCHAR(64)   NOT NULL,",
    "  LinkedServiceName NVARCHAR(256)  NOT NULL,",
    "  KeyVaultName      NVARCHAR(256)  NULL,",
    "  SecretRef         NVARCHAR(256)  NULL,",
    "  Properties        NVARCHAR(MAX)  NULL,",
    "  Enabled           BIT            NOT NULL DEFAULT 1,",
    "  CreatedAt         DATETIME2      NOT NULL DEFAULT GETDATE()",
    ");",
    "",
  ];
  for (const [name, ls] of rows) {
    const fieldDefs = visibleFields(connectorSchema(ls.connectorType), ls.authType);
    const secretKey = fieldDefs.find((f) => f.isSecret && (ls.fields[f.key] ?? "").trim())?.key;
    const secretRef = secretKey ? ls.fields[secretKey] : undefined;
    const nonSecret = Object.fromEntries(
      Object.entries(ls.fields).filter(([key, value]) => key !== secretKey && (value ?? "").trim()),
    );
    lines.push(
      `IF NOT EXISTS (SELECT 1 FROM ${connTable} WHERE ConnectionName = N'${name.replace(/'/g, "''")}')`,
      `INSERT INTO ${connTable} (`,
      "  ConnectionName, ConnectorType, LinkedServiceName, KeyVaultName, SecretRef, Properties, Enabled",
      ") VALUES (",
      `  N'${name.replace(/'/g, "''")}', N'${ls.connectorType}', N'${ls.name.replace(/'/g, "''")}', ` +
        `${sqlStringOrNull(ls.keyVaultLinkedService)}, ${sqlStringOrNull(secretRef)}, ` +
        `${sqlStringOrNull(JSON.stringify(nonSecret))}, 1`,
      ");",
      "",
    );
  }
  return lines;
}

function scheduleRecurrence(draft: SpecDraft) {
  const pipelineName = resolveTriggerPipelineName(draft);
  return {
    startTime: draft.triggerStartTime?.trim() || "2026-01-01T00:00:00Z",
    timeZone: draft.triggerTimeZone?.trim() || "UTC",
    interval: Number.isFinite(draft.triggerInterval) && draft.triggerInterval > 0 ? draft.triggerInterval : 1,
    pipelineReference: {
      pipelineReference: { referenceName: pipelineName, type: "PipelineReference" },
      parameters: {},
    },
  };
}

/**
 * File-arrival trigger safety net (sample-guide parity gap 5): both sample
 * guides pair every storage-event trigger with a schedule trigger fallback in
 * case an event is missed, rather than relying solely on eventing.
 */
function fallbackScheduleTriggerArtifact(draft: SpecDraft): unknown {
  const { startTime, timeZone, interval, pipelineReference } = scheduleRecurrence(draft);
  return {
    name: `TR_${draft.name}_fallback`,
    type: "Microsoft.DataFactory/factories/triggers",
    properties: {
      annotations: ["fallback-for-missed-file-arrival-events"],
      runtimeState: draft.triggerEnabled ? "Started" : "Stopped",
      type: "ScheduleTrigger",
      typeProperties: { recurrence: { frequency: draft.triggerFrequency, interval, startTime, timeZone } },
      pipelines: [pipelineReference],
    },
  };
}

function triggerArtifact(draft: SpecDraft): unknown {
  const { startTime, timeZone, interval, pipelineReference } = scheduleRecurrence(draft);
  if (draft.triggerType === "blobEvents") {
    // File-arrival / storage-event trigger (sample-guide parity gap 5) — both
    // guides trigger every file-based source the moment a file lands, instead
    // of only ever polling on a fixed clock.
    const container = draft.triggerContainerName?.trim() || "landing";
    const beginsWith = draft.triggerBlobPathBeginsWith?.trim() || "";
    const endsWith = draft.triggerBlobPathEndsWith?.trim() || "";
    return {
      name: `TR_${draft.name}`,
      type: "Microsoft.DataFactory/factories/triggers",
      properties: {
        annotations: [`storageLinkedService:${draft.triggerStorageLinkedService?.trim() || ""}`],
        runtimeState: draft.triggerEnabled ? "Started" : "Stopped",
        type: "BlobEventsTrigger",
        typeProperties: {
          blobPathBeginsWith: `/${container}/blobs/${beginsWith}`,
          ...(endsWith ? { blobPathEndsWith: endsWith } : {}),
          ignoreEmptyBlobs: true,
          // Filled in per-factory at deploy time (§20 scope boundary, same
          // convention as globalParameters.json's <FACTORY_NAME> placeholder) —
          // this app never deploys and doesn't know the real storage account's ARM resource ID.
          scope: "<STORAGE_ACCOUNT_RESOURCE_ID>",
          events: ["Microsoft.Storage.BlobCreated"],
        },
        pipelines: [pipelineReference],
      },
    };
  }
  if (draft.triggerType === "tumbling") {
    return {
      name: `TR_${draft.name}`,
      type: "Microsoft.DataFactory/factories/triggers",
      properties: {
        annotations: [],
        runtimeState: draft.triggerEnabled ? "Started" : "Stopped",
        type: "TumblingWindowTrigger",
        typeProperties: {
          frequency: draft.triggerFrequency === "Minute" ? "Minute" : "Hour",
          interval,
          startTime,
          delay: "00:00:00",
          maxConcurrency: draft.parallelCopies,
          retryPolicy: { count: draft.retryCount, intervalInSeconds: draft.retryIntervalInSeconds },
        },
        pipeline: pipelineReference,
      },
    };
  }
  return {
    name: `TR_${draft.name}`,
    type: "Microsoft.DataFactory/factories/triggers",
    properties: {
      annotations: [],
      runtimeState: draft.triggerEnabled ? "Started" : "Stopped",
      type: "ScheduleTrigger",
      typeProperties: {
        recurrence: {
          frequency: draft.triggerFrequency,
          interval,
          startTime,
          timeZone,
        },
      },
      pipelines: [pipelineReference],
    },
  };
}

/**
 * ADF global-parameters artifact (§24.11 `factory/<factoryName>.json`, gap A-2).
 *
 * This app never deploys and, absent a selected ADF instance, does not know
 * the real target factory's name (that's owned by the customer's own
 * ARM/Bicep pipeline, §20 scope boundary) — so `name`/`Environment` stay
 * placeholder tokens, not literal resource names, exactly as before.
 *
 * When the caller resolves `draft.targetEnvironmentName` against the
 * `environments` catalog (multi-instance support) and passes the matched
 * `environment`, this — and only this parameter-surface file — is filled in
 * with real values: `name` becomes the real factory name and the
 * `Environment` global parameter becomes the real instance name. Nothing
 * else changes: pipeline/dataset/linkedService JSON and the trigger's
 * `<STORAGE_ACCOUNT_RESOURCE_ID>` placeholder stay parameterized as always —
 * this is deliberately "fill parameter surfaces only", not a general
 * per-instance resolution of generated artifacts.
 */
/**
 * ADF custom parameterization template (`arm-template-parameters-definition.json`) — tells ADF's
 * own ARM export which linked-service/dataset/pipeline/trigger/global-parameter properties become
 * ARM template parameters, so the customer's own dev→QA→PROD release can override them per
 * environment. A Git artifact only (§20 scope boundary: no deploy step here). Static and
 * draft-independent; opt-in via `generateArmParameterDefinition`. Keep in parity with
 * `_arm_parameter_definition` in `worker/app/generator/codegen.py`.
 */
function armParameterDefinition(): unknown {
  return {
    "Microsoft.DataFactory/factories": {
      "properties": {
        "globalParameters": {
          "*": {
            "value": "="
          }
        }
      },
      "location": "="
    },
    "Microsoft.DataFactory/factories/pipelines": {
      "properties": {
        "parameters": {
          "*": {
            "defaultValue": "="
          }
        }
      }
    },
    "Microsoft.DataFactory/factories/triggers": {
      "properties": {
        "typeProperties": {
          "recurrence": {
            "interval": "=",
            "frequency": "="
          },
          "scope": "="
        },
        "pipelines": [
          {
            "parameters": {
              "*": "="
            }
          }
        ]
      }
    },
    "Microsoft.DataFactory/factories/linkedServices": {
      "*": {
        "properties": {
          "typeProperties": {
            "accountName": "=",
            "baseUrl": "=",
            "connectionString": "|:-connectionString:secureString",
            "database": "=",
            "dataLakeStoreUri": "=",
            "environmentUrl": "=",
            "fullyQualifiedDomainName": "=",
            "host": "=",
            "password": {
              "secretName": "="
            },
            "resourceGroupName": "=",
            "server": "=",
            "serviceEndpoint": "=",
            "servicePrincipalId": "=",
            "subscriptionId": "=",
            "tenant": "=",
            "url": "=",
            "userName": "=",
            "username": "="
          }
        }
      }
    },
    "Microsoft.DataFactory/factories/datasets": {
      "*": {
        "properties": {
          "typeProperties": {
            "folderPath": "=",
            "fileName": "=",
            "fileSystem": "=",
            "container": "=",
            "schema": "=",
            "table": "="
          }
        }
      }
    },
    "Microsoft.DataFactory/factories/credentials": {
      "properties": {
        "typeProperties": {
          "resourceId": "="
        }
      }
    }
  };
}

function factoryArtifact(draft: SpecDraft, environment?: EnvironmentConnection | null): unknown {
  const keyVaultLinkedService =
    draft.sourceLinkedService.keyVaultLinkedService ||
    draft.targetLinkedService.keyVaultLinkedService ||
    DEFAULT_KEY_VAULT_LINKED_SERVICE;
  return {
    name: environment?.factoryName || "<FACTORY_NAME>",
    type: "Microsoft.DataFactory/factories",
    properties: {
      globalParameters: {
        Environment: { type: "String", value: environment?.name || "<ENVIRONMENT_NAME>" },
        KeyVaultLinkedServiceName: { type: "String", value: keyVaultLinkedService },
      },
    },
  };
}

function parametersFile(draft: SpecDraft, layerPrefixes?: LayerPrefixOverrides): unknown {
  return {
    BatchCount: draft.batchCount,
    ParallelCopies: draft.parallelCopies,
    DataIntegrationUnits: draft.dataIntegrationUnits ?? 2,
    EnableStaging: draft.enableStaging,
    StagingLinkedService: draft.stagingLinkedService,
    StagingPath: draft.stagingPath,
    SourcePartitionOption: draft.sourcePartitionOption,
    SourcePartitionColumn: draft.sourcePartitionColumn ?? "",
    QueryTimeout: draft.queryTimeout,
    SinkFileFormat: draft.sinkFileFormat,
    CopyBehavior: draft.copyBehavior,
    WriteBehavior: draft.writeBehavior,
    UpsertKeyColumns: draft.upsertKeyColumns,
    TriggerEnabled: draft.triggerEnabled,
    TriggerType: draft.triggerType,
    TriggerFrequency: draft.triggerFrequency,
    TriggerInterval: draft.triggerInterval,
    RetryCount: draft.retryCount,
    RetryIntervalInSeconds: draft.retryIntervalInSeconds,
    RedirectIncompatibleRows: draft.redirectIncompatibleRows,
    LogLevel: draft.logLevel,
    EnableAzureMonitor: draft.enableAzureMonitor,
    EnableCopyLog: draft.enableCopyLog,
    RowCountValidationEnabled: draft.rowCountValidationEnabled,
    NotifyOnSuccess: draft.notifyOnSuccess,
    NotifyOnFailure: draft.notifyOnFailure,
    NotificationEmails: draft.notificationEmails,
    WatermarkTable: draft.watermarkTable || "control.adf_control_table",
    WatermarkInitialValue: draft.watermarkInitialValue || "1900-01-01",
    RejectsLinkedService: draft.rejectsLinkedService,
    RejectsPath: draft.rejectsPath,
    ChunkingEnabled: draft.chunkingEnabled,
    ChunkColumn: draft.chunkColumn ?? "",
    ChunkSizeRows: draft.chunkSizeRows,
    MaxParallelChunks: draft.maxParallelChunks,
    TriggerStorageLinkedService: draft.triggerStorageLinkedService ?? "",
    TriggerContainerName: draft.triggerContainerName ?? "",
    TriggerBlobPathBeginsWith: draft.triggerBlobPathBeginsWith ?? "",
    TriggerBlobPathEndsWith: draft.triggerBlobPathEndsWith ?? "",
    TriggerFallbackSchedule: draft.triggerFallbackSchedule,
    MedallionLayer: draft.medallionLayer,
    LayerTargetTable:
      draft.medallionLayer === "bronze"
        ? ""
        : layerTargetTableName(draft.medallionLayer, { entity: draft.curatedEntity, domain: draft.martDomain }, layerPrefixes),
  };
}

/**
 * Generate the complete set of ADF artifacts for a Metadata-Driven Load draft.
 * Each artifact carries its source `content` so the UI can render/copy it.
 *
 * `environment` is the `EnvironmentConnection` matching `draft.targetEnvironmentName`
 * (resolved by the caller against `GET /api/v1/environments` — multi-instance
 * support), or omitted/null when no ADF instance was selected. It only
 * affects `globalParameters.json` (see `factoryArtifact`); every other
 * artifact is byte-identical either way.
 */
/**
 * `layerPrefixes` is the project's Standards-configured custom zone prefix
 * per medallion layer (`NamingConvention.layerPrefixes` — omitted/undefined
 * keeps today's raw/curated/mart default); it only affects Silver/Gold
 * artifacts that reference a `layerTargetTableName()`-computed table name.
 */
/**
 * Static source → target lineage for this pipeline (opt-in via `generateLineage`). OpenLineage
 * job/dataset naming (`namespace` + `name`) so a catalog (Purview, Marquez, ...) can ingest it, but no
 * run facets or `eventTime` — the generator stays a pure function of the draft. Keep in parity with
 * `_lineage_document` in `worker/app/generator/codegen.py`.
 */
function lineageDocument(draft: SpecDraft, layerPrefixes?: LayerPrefixOverrides): unknown {
  const sourceNs = `${draft.sourceType}://${draft.sourceLinkedService.name}`;
  const targetNs = `${draft.targetType}://${draft.targetLinkedService.name}`;
  const outputs: Array<{ namespace: string; name: string }> = [];
  if (draft.targetPath.trim()) outputs.push({ namespace: targetNs, name: draft.targetPath });
  if (draft.medallionLayer === "silver" || draft.medallionLayer === "gold") {
    outputs.push({
      namespace: targetNs,
      name: layerTargetTableName(draft.medallionLayer, { entity: draft.curatedEntity, domain: draft.martDomain }, layerPrefixes),
    });
  }
  return {
    lineageVersion: "1.0",
    producer: "adf-accelerator",
    job: { namespace: "adf", name: resolveTriggerPipelineName(draft) },
    medallionLayer: draft.medallionLayer,
    inputs: effectiveSourceObjects(draft).map((entry) => ({ namespace: sourceNs, name: entry.object })),
    outputs,
  };
}

/**
 * Silver/Gold on a database target land each object in the Bronze landed table the transformation chain
 * reads (`raw.<source>_<object>`), so the Copy sink, the control-table `Target` and the full-load truncate
 * all point there — the target dataset's own schema/table (default `dbo.data`) is never read by the chain.
 * Bronze and file targets are unchanged. Keep in parity with codegen.py's `_with_layer_landing`.
 */
function withLayerLanding(draft: SpecDraft, layerPrefixes?: LayerPrefixOverrides): SpecDraft {
  if ((draft.medallionLayer ?? "bronze") === "bronze" || connectorSchema(draft.targetType).kind !== "database") return draft;
  const { table } = splitObjectName(draft.sourceObject, draft.sourceType);
  const landed = layerTargetTableName("bronze", { sourceType: bronzeSourceToken(draft), object: table }, layerPrefixes);
  const dot = landed.indexOf(".");
  return { ...draft, targetDataset: { ...draft.targetDataset, schema: landed.slice(0, dot), table: landed.slice(dot + 1) } };
}

export function buildGeneratedArtifacts(
  inputDraft: SpecDraft,
  environment?: EnvironmentConnection | null,
  layerPrefixes?: LayerPrefixOverrides,
): ArtifactVersion[] {
  const draft = withLayerLanding(inputDraft, layerPrefixes);
  const name = draft.name;
  const pattern = draft.pattern ?? "master_child";

  // §24.12 "child_only"/"master_only": just the one pipeline + nothing else — it references an
  // already-existing master/child (and its LS/DS/control table) by name, so regenerating those would
  // either duplicate or silently diverge from the artifact the user is attaching to.
  if (pattern === "child_only") {
    return [jsonArtifact(draft, `${resolveChildPipelineName(draft)}.json`, "pipeline", "pipeline", childPipeline(draft, layerPrefixes))];
  }
  if (pattern === "master_only") {
    return [jsonArtifact(draft, `${resolveMasterPipelineName(draft)}.json`, "pipeline", "pipeline", masterPipeline(draft))];
  }

  const artifacts: ArtifactVersion[] = [];
  if (pattern === "single_pipeline") {
    artifacts.push(jsonArtifact(draft, `${resolvePipelineName(draft)}.json`, "pipeline", "pipeline", singlePipeline(draft, layerPrefixes)));
  } else if (pattern === "independent_full") {
    artifacts.push(jsonArtifact(draft, `${resolvePipelineName(draft)}.json`, "pipeline", "pipeline", independentPipeline(draft, "full", layerPrefixes)));
  } else if (pattern === "independent_watermark") {
    artifacts.push(jsonArtifact(draft, `${resolvePipelineName(draft)}.json`, "pipeline", "pipeline", independentPipeline(draft, "delta", layerPrefixes)));
  } else {
    // master_child — current shape, byte-identical to before this pattern field existed.
    artifacts.push(jsonArtifact(draft, `${resolveMasterPipelineName(draft)}.json`, "pipeline", "pipeline", masterPipeline(draft)));
    artifacts.push(jsonArtifact(draft, `${resolveChildPipelineName(draft)}.json`, "pipeline", "pipeline", childPipeline(draft, layerPrefixes)));
  }

  // §24.6 "use existing, don't regenerate" — omit the LS/DS artifact for a slot whose connection
  // is marked reused; the pipeline's own LinkedServiceReference/DatasetReference (built from
  // draft.source*/target* unconditionally, elsewhere in this file) is unaffected either way.
  artifacts.push(...credentialArtifacts(draft));
  if (!draft.sourceConnectionReused) {
    artifacts.push(datasetArtifact(draft, "source"));
    artifacts.push(linkedServiceArtifact(draft, "source"));
  }
  if (!draft.targetConnectionReused) {
    artifacts.push(datasetArtifact(draft, "target"));
    artifacts.push(linkedServiceArtifact(draft, "target"));
  }

  // "Independent" patterns have no multi-object control table by definition (§24.12); the watermark
  // variant gets its own single-row watermark table instead.
  if (pattern === "independent_watermark") {
    artifacts.push(textArtifact(draft, "adf_watermark_table.sql", "parameter", "metadata", watermarkTableSql(draft)));
  } else if (pattern !== "independent_full") {
    artifacts.push(textArtifact(draft, "adf_control_table.sql", "parameter", "metadata", controlTableSql(draft)));
  }

  // Gap #28 — per-build opt-outs (each undefined/true = unchanged output).
  if (draft.generateTrigger !== false) {
    artifacts.push(jsonArtifact(draft, `TR_${name}.json`, "trigger", "runtime", triggerArtifact(draft)));
  }
  if (draft.generateGlobalParameters !== false) {
    artifacts.push(jsonArtifact(draft, "globalParameters.json", "factory", "runtime", factoryArtifact(draft, environment)));
  }
  if (draft.generateParametersFile !== false) {
    artifacts.push(jsonArtifact(draft, "parameters.json", "parameter", "runtime", parametersFile(draft, layerPrefixes)));
  }
  if (draft.generateArmParameterDefinition === true) {
    artifacts.push(
      jsonArtifact(draft, "arm-template-parameters-definition.json", "parameter", "runtime", armParameterDefinition()),
    );
  }
  if (draft.generateLineage === true) {
    artifacts.push(jsonArtifact(draft, "lineage.json", "parameter", "metadata", lineageDocument(draft, layerPrefixes)));
  }

  // File-arrival trigger safety net (sample-guide parity gap 5) — an extra trigger artifact,
  // only emitted when both a blob-events trigger and its fallback are actually configured.
  if (draft.generateTrigger !== false && draft.triggerType === "blobEvents" && draft.triggerFallbackSchedule) {
    artifacts.push(jsonArtifact(draft, `TR_${name}_fallback.json`, "trigger", "runtime", fallbackScheduleTriggerArtifact(draft)));
  }
  // §33.7 — additive sibling control tables, only when built from a picked registry object.
  const curatedSql = curatedControlTableSql(draft, layerPrefixes);
  if (curatedSql) {
    artifacts.push(textArtifact(draft, "adf_curated_control_table.sql", "parameter", "metadata", curatedSql));
  }
  const martSql = martControlTableSql(draft, layerPrefixes);
  if (martSql) {
    artifacts.push(textArtifact(draft, "adf_mart_control_table.sql", "parameter", "metadata", martSql));
  }
  const schemasSql = layerSchemasSql(draft, layerPrefixes);
  if (schemasSql) {
    artifacts.push(textArtifact(draft, "adf_layer_schemas.sql", "parameter", "metadata", schemasSql));
  }
  return artifacts;
}