"""``SpecDraft`` -> the 10-file ADF artifact set (AGENTS.md §23.4a).

Direct port of ``frontend/lib/adf/pipeline-codegen.ts``. Every value is derived
from the draft — nothing injected (§22.30). ``build_generated_artifacts``
emits, in §23.9 layer order:

  {credentialName}.json                     credential     / connection (§32.5/§32.7, only when a
                                             linked service's identity_kind == "user")
  {sourceLS}.json / {targetLS}.json         linked_service / connection (name already carries LS_)
  DS_{source}.json   / DS_{target}.json     dataset        / data
  PL_{name}_master.json / PL_{name}_child.json   pipeline   / pipeline
  TR_{name}.json                            trigger        / runtime
  globalParameters.json                     factory        / runtime  (IMPLEMENTATION_PLAN.md A-2)
  parameters.json                           parameter      / runtime  (IMPLEMENTATION_PLAN.md A-1)
  adf_control_table.sql                     parameter      / metadata

Every ``file_name`` here is a bare name (§17.6's always-on rule rejects a
path); the canonical ADF folder each artifact lands in
(``linkedService/``, ``dataset/``, ``pipeline/``, ``trigger/``, ``factory/``,
§24.11) is applied at push time from ``artifact_type``, not baked into the
generated name — see ``worker/app/jobs/runner.py::_artifact_blobs``.

``pipeline-codegen.ts`` currently emits the master/child metadata-driven shape
for **every** template; this port keeps that behaviour for parity (§23.12 gap 5
tracks adding single-pipeline / independent strategies).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any, Literal

from app.connectors import (
    build_dataset_properties,
    build_linked_service_properties,
    connector_schema,
    dataset_name,
    visible_fields,
)
from app.connectors.drafts import DEFAULT_KEY_VAULT_LINKED_SERVICE, LinkedServiceDraft

from .qualifier import QualifierEntryInput, get_qualifier_profile, resolve_qualified_name
from .medallion import (
    MAX_TRANSFORMATION_RULES,
    TERMINAL_TRANSFORM_TYPES,
    layer_final_load_sql,
    layer_load_key_columns,
    layer_target_table_name,
    transformation_rule_sql,
)
from .spec import (
    bronze_source_token,
    ColumnMapping,
    DQ_PCT_RULE_TYPES,
    DataQualityRule,
    SourceObjectEntry,
    SpecDraft,
    dq_rule_needs_column,
    effective_source_objects,
    complete_column_mappings,
    per_object_column_mappings_active,
    child_dispatch_cases,
    pipeline_annotations,
    resolve_child_pipeline_name,
    resolve_copy_timeout,
    resolve_soft_delete_column,
    resolve_master_pipeline_name,
    resolve_pipeline_name,
    resolve_trigger_pipeline_name,
    source_dataset_defaults,
    target_dataset_defaults,
)

ArtifactType = Literal["pipeline", "dataset", "linked_service", "parameter", "trigger", "factory", "credential"]
ArtifactLayer = Literal["infrastructure", "connection", "data", "pipeline", "runtime", "metadata"]


@dataclass
class GeneratedArtifact:
    file_name: str
    file_type: str  # json | sql | config
    artifact_type: str
    layer: str
    content: str

    @property
    def bytes(self) -> int:
        return len(self.content.encode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "fileName": self.file_name,
            "fileType": self.file_type,
            "artifactType": self.artifact_type,
            "layer": self.layer,
            "version": "v1.0.0",
            "state": "generated",
            "bytes": self.bytes,
            "content": self.content,
        }


def _json_artifact(file_name: str, artifact_type: str, layer: str, value: Any) -> GeneratedArtifact:
    if artifact_type == "pipeline":
        _ensure_lookup_result_sets(value)
    content = json.dumps(value, indent=2) + "\n"
    return GeneratedArtifact(file_name, "json", artifact_type, layer, content)


_WRITE_SQL = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE)\b", re.I)
LOOKUP_DONE_SELECT = "; SELECT 1 AS Done"


def _ensure_lookup_result_sets(node: Any) -> None:
    """RUN-02: a Lookup fails when its query returns no result set, so a Lookup that only writes
    (execution log, watermark, chunk plan) ends with a one-row SELECT. Port of ensureLookupResultSets."""
    if isinstance(node, list):
        for item in node:
            _ensure_lookup_result_sets(item)
    elif isinstance(node, dict):
        if node.get("type") == "Lookup":
            source = node.get("typeProperties", {}).get("source", {})
            query = source.get("sqlReaderQuery")
            if isinstance(query, str) and _WRITE_SQL.search(query) and not query.endswith(LOOKUP_DONE_SELECT):
                source["sqlReaderQuery"] = query + LOOKUP_DONE_SELECT
        for value in node.values():
            _ensure_lookup_result_sets(value)


def _text_artifact(file_name: str, artifact_type: str, layer: str, content: str) -> GeneratedArtifact:
    file_type = "sql" if file_name.endswith(".sql") else "config"
    return GeneratedArtifact(file_name, file_type, artifact_type, layer, content)


def _named_parameters(params: list[dict[str, Any]]) -> dict[str, Any]:
    """ADF's pipeline `parameters` block is a name-keyed object, not a list of {name, ...}."""
    return {p["name"]: {k: v for k, v in p.items() if k != "name"} for p in params}


def _loading_behavior_of(draft: SpecDraft) -> str:
    if draft.chunking_enabled and (draft.chunk_column or "").strip():
        return "ChunkedLoad"
    return "FullLoad" if draft.load_type_mode == "full" or draft.full_load else "DeltaLoad"


def _additional_columns_of(entries: list[Any]) -> list[dict[str, Any]] | None:
    """Standard metadata/lineage columns (sample-guide parity gap 1) as ADF Copy
    activity source ``additionalColumns``. A value starting with "@" is emitted
    as dynamic content; everything else is a literal — same convention already
    used by custom_log_columns' defaults."""
    if not entries:
        return None
    out = []
    for entry in entries[:10]:
        value = entry.value.strip()
        out.append({"name": entry.name, "value": {"type": "Expression", "value": value} if value.startswith("@") else entry.value})
    return out


def _control_schema_of(draft: SpecDraft) -> str:
    parts = (draft.watermark_table or "control.watermarks").split(".")
    return ".".join(parts[:-1]) if len(parts) >= 2 else "control"


def _dq_activity_base(rule: DataQualityRule, index: int) -> str:
    """A DQ rule's activity-name-safe token — direct port of dqActivityBase in
    pipeline-codegen.ts."""
    return re.sub(r"[^A-Za-z0-9_]", "_", rule.name.strip()) or f"Rule{index + 1}"


def _dq_check_query(schema_table: str, rule: DataQualityRule) -> str:
    """Direct port of dataQualityCheckQuery in pipeline-codegen.ts — see that
    docstring for the T-SQL/PATINDEX rationale."""
    col = rule.column.strip()
    if rule.rule == "IS_NOT_NULL":
        return f"SELECT COUNT(*) AS ViolationCount FROM {schema_table} WHERE {col} IS NULL"
    if rule.rule == "UNIQUE":
        return f"SELECT COUNT(*) AS ViolationCount FROM (SELECT {col} FROM {schema_table} GROUP BY {col} HAVING COUNT(*) > 1) dup"
    if rule.rule == "LENGTH_LE":
        max_length = rule.value.strip() or "0"
        return f"SELECT COUNT(*) AS ViolationCount FROM {schema_table} WHERE LEN({col}) > {max_length}"
    if rule.rule == "RANGE_BETWEEN":
        parts = [p.strip() for p in rule.value.split(",")]
        lo = parts[0] if len(parts) > 0 and parts[0] else "0"
        hi = parts[1] if len(parts) > 1 and parts[1] else "0"
        return f"SELECT COUNT(*) AS ViolationCount FROM {schema_table} WHERE {col} < {lo} OR {col} > {hi}"
    if rule.rule == "ROW_COUNT_MIN":
        threshold = rule.value.strip() or "0"
        return f"SELECT CASE WHEN COUNT(*) < {threshold} THEN 1 ELSE 0 END AS ViolationCount FROM {schema_table}"
    if rule.rule == "FRESHNESS_HOURS":
        hours = rule.value.strip() or "0"
        return (
            f"SELECT CASE WHEN MAX({col}) IS NULL OR MAX({col}) < DATEADD(HOUR, -{hours}, GETUTCDATE()) "
            f"THEN 1 ELSE 0 END AS ViolationCount FROM {schema_table}"
        )
    # REGEX_MATCH (and any unrecognized future rule)
    pattern = rule.value.strip() or "%"
    return f"SELECT COUNT(*) AS ViolationCount FROM {schema_table} WHERE PATINDEX('{pattern}', {col}) = 0"


def _dq_pct(rule: DataQualityRule) -> str | None:
    """DMT-05: the rule's max violation % when it applies (a row-level rule with a valid number)."""
    pct = rule.max_violation_pct.strip()
    return pct if pct and rule.rule in DQ_PCT_RULE_TYPES and re.fullmatch(r"\d+(?:\.\d+)?", pct) else None


def _dq_check_sql(schema_table: str, rule: DataQualityRule) -> str:
    """The check query; with a max violation % it also returns TotalRows and ThresholdBreached."""
    query = _dq_check_query(schema_table, rule)
    pct = _dq_pct(rule)
    if not pct:
        return query
    return (
        "SELECT v.ViolationCount, t.TotalRows, "
        f"CASE WHEN v.ViolationCount * 100.0 > {pct} * t.TotalRows THEN 1 ELSE 0 END AS ThresholdBreached "
        f"FROM ({query}) v CROSS JOIN (SELECT COUNT(*) AS TotalRows FROM {schema_table}) t"
    )


def _data_quality_activities(
    draft: SpecDraft, schema: str, table: str, post_load: tuple[str, str] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """§30.4 basic Data Quality checks — direct port of dataQualityActivities in
    pipeline-codegen.ts; see that docstring for the full design rationale
    (pre-copy gate, warn vs. block, the SqlServerSource/SqlWrapper convention)."""
    rules = [r for r in draft.data_quality_rules[:5] if r.name.strip() and (r.column.strip() or not dq_rule_needs_column(r.rule))]
    schema_table = _dq_read_from(draft, post_load[0]) if post_load else "@{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}"
    checked_object = post_load[0] if post_load else f"{schema}.{table}"
    activities: list[dict[str, Any]] = []
    gate_depends_on: list[dict[str, Any]] = []
    for i, rule in enumerate(rules):
        base = _dq_activity_base(rule, i)
        check_name = f"DQ_{base}_Check"
        check: dict[str, Any] = {
            "name": check_name,
            "description": f'Data quality check "{rule.name.strip()}" ({rule.rule}) on {checked_object}{"." + rule.column.strip() if rule.column.strip() else ""}.',
            "type": "Lookup",
        }
        if post_load:
            check["dependsOn"] = [{"activity": post_load[1], "dependencyConditions": ["Succeeded"]}]
        activities.append(
            {
                **check,
                "policy": {"timeout": "0.00:10:00.000", "retry": draft.retry_count, "retryIntervalInSeconds": draft.retry_interval_in_seconds},
                "typeProperties": {
                    "source": {
                        "type": "SqlServerSource",
                        "sqlReaderQuery": _dq_check_sql(schema_table, rule),
                    },
                },
            }
        )
        if (rule.enforcement or "warn") == "block":
            gate_name = f"DQ_{base}_Gate"
            pct = _dq_pct(rule)
            breached = (
                f"@equals(int(activity('{check_name}').output.firstRow.ThresholdBreached), 1)"
                if pct
                else f"@greater(int(activity('{check_name}').output.firstRow.ViolationCount), 0)"
            )
            activities.append(
                {
                    "name": gate_name,
                    "type": "IfCondition",
                    "dependsOn": [{"activity": check_name, "dependencyConditions": ["Succeeded"]}],
                    "typeProperties": {
                        "expression": {
                            "type": "Expression",
                            "value": breached,
                        },
                        "ifTrueActivities": [
                            {
                                "name": f"DQ_{base}_Fail",
                                "type": "Fail",
                                "typeProperties": {
                                    "message": {
                                        "type": "Expression",
                                        "value": (
                                            f"@concat('Data quality rule \"{rule.name.strip()}\" ({rule.rule}) "
                                            f"failed{' on column ' + rule.column.strip() if rule.column.strip() else ''}: ', "
                                            f"string(activity('{check_name}').output.firstRow.ViolationCount), "
                                            f"' violating row(s){f' (more than {pct}% of rows)' if pct else ''}.')"
                                        ),
                                    },
                                    "errorCode": "DataQualityRuleFailed",
                                },
                            }
                        ],
                    },
                }
            )
            gate_depends_on.append({"activity": gate_name, "dependencyConditions": ["Succeeded"]})
    return activities, gate_depends_on


def _dq_read_from(draft: SpecDraft, target: str) -> str:
    """RUN-13 — direct port of dqReadFrom in pipeline-codegen.ts: on an SCD2 target the post-load
    checks read only the current version of each key, not the closed history rows."""
    scd2 = next((r for r in draft.transformations[:MAX_TRANSFORMATION_RULES] if r.name.strip() and r.type == "scd2"), None)
    if not scd2:
        return target
    flag = (scd2.current_flag_column or "").strip() or "_is_current"
    return f"(SELECT * FROM {target} WHERE {flag} = 1) cur"


def _post_load_dq_table(draft: SpecDraft, table: str, layer_prefixes: dict[str, str] | None = None) -> str | None:
    """E2E-09 — direct port of postLoadDqTable in pipeline-codegen.ts: with a Silver/Gold
    transformation chain the DQ checks read the layer target after the load, not the source."""
    if draft.medallion_layer == "bronze":
        return None
    if not any(r.name.strip() for r in draft.transformations[:MAX_TRANSFORMATION_RULES]):
        return None
    return layer_target_table_name(
        draft.medallion_layer,
        source_type=bronze_source_token(draft),
        object_name=table,
        entity=draft.curated_entity,
        domain=draft.mart_domain,
        layer_prefixes=layer_prefixes,
    )


def _transform_activity_base(rule: Any, index: int) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", rule.name.strip()) or f"Step{index + 1}"


# RUN-06: suffix that makes a staging table unique per object (TaskId) and per run (RunId, no hyphens).
_STAGING_RUN_SUFFIX = "_@{pipeline().parameters.TaskId}_@{replace(pipeline().RunId, '-', '')}"


def _layer_transformation_activities(
    draft: SpecDraft,
    schema: str,
    table: str,
    depends_on_activity: str,
    layer_prefixes: dict[str, str] | None = None,
    full_refresh: bool = False,
    name_suffix: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """Silver/Gold medallion transformations (app.generator.medallion) — direct
    port of layerTransformationActivities in pipeline-codegen.ts. See that
    docstring for the full design rationale."""
    if draft.medallion_layer == "bronze":
        return [], depends_on_activity
    rules = [r for r in draft.transformations[:MAX_TRANSFORMATION_RULES] if r.name.strip()]
    if not rules:
        return [], depends_on_activity

    target_table = layer_target_table_name(
        draft.medallion_layer,
        source_type=bronze_source_token(draft),
        object_name=table,
        entity=draft.curated_entity,
        domain=draft.mart_domain,
        layer_prefixes=layer_prefixes,
    )
    # G2: ADF requires a linked service on every Script activity — the SQL runs on the target store.
    target_ls_ref = {"referenceName": draft.target_linked_service.name, "type": "LinkedServiceReference"}
    activities: list[dict[str, Any]] = []
    # G7: the chain reads the Bronze landed table (raw.<source>_<object>) on the target store,
    # never the source object's own name — that table only exists on the source system.
    read_from = layer_target_table_name("bronze", source_type=bronze_source_token(draft), object_name=table, layer_prefixes=layer_prefixes)
    prev_name = depends_on_activity
    last_terminal = False
    staging_tables: list[str] = []
    for i, rule in enumerate(rules):
        # RUN-04: `name_suffix` keeps names unique across the child's Switch branches.
        name = f"Transform_{_transform_activity_base(rule, i)}{name_suffix}"
        terminal = rule.type in TERMINAL_TRANSFORM_TYPES
        # RUN-06: staging tables are per object and per run, so parallel ForEach iterations (and
        # overlapping runs) never share one; DropStagingTables removes them after a successful chain.
        write_to = target_table if terminal else f"stg.{draft.name}_step{i + 1}{_STAGING_RUN_SUFFIX}"
        if not terminal:
            staging_tables.append(write_to)
        activities.append(
            {
                "name": name,
                "description": f'{draft.medallion_layer} layer transformation "{rule.name.strip()}" ({rule.type}).',
                "type": "Script",
                "linkedServiceName": target_ls_ref,
                "dependsOn": [{"activity": prev_name, "dependencyConditions": ["Succeeded"]}],
                "policy": {"timeout": "0.01:00:00.000", "retry": draft.retry_count, "retryIntervalInSeconds": draft.retry_interval_in_seconds},
                "typeProperties": {
                    "scripts": [{"type": "Query", "text": transformation_rule_sql(rule, read_from=read_from, write_to=write_to, target_table=target_table)}]
                },
            }
        )
        read_from = write_to
        prev_name = name
        last_terminal = terminal
    if not last_terminal:
        load_name = f"LoadToLayerTarget{name_suffix}"
        activities.append(
            {
                "name": load_name,
                "description": f"Load transformed data into the {draft.medallion_layer} layer target {target_table}.",
                "type": "Script",
                "linkedServiceName": target_ls_ref,
                "dependsOn": [{"activity": prev_name, "dependencyConditions": ["Succeeded"]}],
                "policy": {"timeout": "0.00:30:00.000", "retry": draft.retry_count, "retryIntervalInSeconds": draft.retry_interval_in_seconds},
                "typeProperties": {
                    "scripts": [
                        {
                            "type": "Query",
                            "text": layer_final_load_sql(
                                target_table,
                                read_from,
                                full_refresh=full_refresh,
                                keys=layer_load_key_columns(rules, draft.layer_key_columns.strip() or draft.upsert_key_columns),
                            ),
                        }
                    ]
                },
            }
        )
        prev_name = load_name
    if staging_tables:
        drop_name = f"DropStagingTables{name_suffix}"
        activities.append(
            {
                "name": drop_name,
                "description": "Drop this run's staging tables (a failed run keeps them for diagnosis).",
                "type": "Script",
                "linkedServiceName": target_ls_ref,
                "dependsOn": [{"activity": prev_name, "dependencyConditions": ["Succeeded"]}],
                "policy": {"timeout": "0.00:10:00.000", "retry": draft.retry_count, "retryIntervalInSeconds": draft.retry_interval_in_seconds},
                "typeProperties": {
                    "scripts": [{"type": "NonQuery", "text": "\n".join(f"DROP TABLE IF EXISTS {t};" for t in staging_tables)}]
                },
            }
        )
        prev_name = drop_name
    return activities, prev_name


def _watermark_column_names(draft: SpecDraft) -> list[str]:
    return [c.name.strip() for c in draft.watermark_columns[:5] if c.name.strip()]


def _is_composite_watermark(draft: SpecDraft) -> bool:
    return draft.watermark_type == "composite" and len(_watermark_column_names(draft)) > 1


def _new_watermark_lookup_query(draft: SpecDraft) -> str:
    """Composite-watermark new-high-watermark lookup (sample-guide parity gap 3):
    independent per-column MAX, one output column per watermark column."""
    if not _is_composite_watermark(draft):
        return (
            "SELECT MAX(@{pipeline().parameters.WatermarkColumn}) AS NewWatermark FROM "
            "@{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}"
        )
    cols = _watermark_column_names(draft)
    selects = ", ".join(f"MAX({col}) AS NewWatermark_{i}" for i, col in enumerate(cols))
    return f"SELECT {selects} FROM @{{pipeline().parameters.SchemaName}}.@{{pipeline().parameters.TableName}}"


def _delta_copy_query(draft: SpecDraft) -> dict[str, Any]:
    """Delta Copy source query. Composite watermark (>1 column, sample-guide
    parity gap 3) builds a lexicographic tuple predicate "(c0 > last0) OR (c0 =
    last0 AND c1 > last1) OR ..." at generation time — see the matching
    TypeScript in frontend/lib/adf/pipeline-codegen.ts for the full rationale;
    this is a direct port. Only the lower bound is tightened to the full
    tuple; the upper bound stays the simpler per-column MAX."""
    if not _is_composite_watermark(draft):
        return {
            "type": "Expression",
            "value": (
                "@concat('SELECT * FROM ', pipeline().parameters.SchemaName, '.', "
                "pipeline().parameters.TableName, ' WHERE ', pipeline().parameters.WatermarkColumn, "
                "' > ''', activity('LookupLastWatermark').output.firstRow.WatermarkValue, ''' AND ', "
                "pipeline().parameters.WatermarkColumn, ' <= ''', "
                "activity('LookupNewWatermark').output.firstRow.NewWatermark, '''')"
            ),
        }
    cols = _watermark_column_names(draft)

    def last_at(i: int) -> str:
        return f"split(activity('LookupLastWatermark').output.firstRow.WatermarkValue, '|')[{i}]"

    def open_quote(text: str) -> str:
        return f"'{text}'''"

    def close_quote(text: str) -> str:
        return f"'''{text}'"

    parts: list[str] = [
        "'SELECT * FROM '", "pipeline().parameters.SchemaName", "'.'",
        "pipeline().parameters.TableName", "' WHERE ('",
    ]
    n = len(cols)
    for k, col in enumerate(cols):
        for i in range(k):
            parts.extend([open_quote(f"{cols[i]} = "), last_at(i), close_quote(" AND ")])
        parts.extend([open_quote(f"{col} > "), last_at(k), "''''"])
        if k < n - 1:
            parts.append("' OR ('")
    parts.append("')'")
    return {"type": "Expression", "value": f"@concat({', '.join(parts)})"}


def _new_watermark_value_expr(draft: SpecDraft) -> str:
    """UpdateWatermark write-back. Composite mode joins each column's new MAX
    with "|" into one delimited string — no control-table schema change
    needed, the existing single WatermarkValue column just carries a
    delimited tuple instead of a scalar."""
    if not _is_composite_watermark(draft):
        return "activity('LookupNewWatermark').output.firstRow.NewWatermark"
    cols = _watermark_column_names(draft)
    fields = [f"string(activity('LookupNewWatermark').output.firstRow.NewWatermark_{i})" for i in range(len(cols))]
    if len(fields) > 1:
        joined = ", '|', ".join(fields)
        return f"concat({joined})"
    return fields[0]


def _split_object_name(object_name: str, connector_type: str) -> tuple[str, str]:
    """Connector-aware replacement for the old blind `split(".")` — resolves the
    source object through `generator/qualifier.py` so 3-part sources (e.g. SAP
    HANA with a catalog) and schema-less sources (SAP ECC/OData, Salesforce,
    ...) get the right identifier instead of a mis-split or a hard-coded
    "dbo". Case-conformed per the connector's real convention (Oracle/SAP
    HANA uppercase, Postgres lowercase, ...) so the object resolves correctly
    regardless of the case it was onboarded in."""
    profile = get_qualifier_profile(connector_type)
    resolved = resolve_qualified_name(QualifierEntryInput(object=object_name), profile)
    return resolved.schema or "dbo", resolved.object or "source_table"


def _resolve_dataset_name(draft: SpecDraft, kind: str) -> str:
    if kind == "source":
        return dataset_name(draft.source_dataset, source_dataset_defaults(draft))
    return dataset_name(draft.target_dataset, target_dataset_defaults(draft))


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


_CHILD_PARAMETER_KEYS = (
    "TaskId", "SourceType", "Source", "SourceQuery", "TargetType",
    "Target", "TargetFileName", "LoadingBehavior", "WatermarkColumn", "WatermarkValue",
)


def _execute_child_activity(name: str, child_pipeline_name: str, with_column_mapping: bool = False) -> dict[str, Any]:
    # Per-object column mappings go to this pipeline's own child only — a dispatch-case child is an
    # existing pipeline that may not declare a ColumnMapping parameter.
    keys = (*_CHILD_PARAMETER_KEYS, "ColumnMapping") if with_column_mapping else _CHILD_PARAMETER_KEYS
    return {
        "name": name,
        "type": "ExecutePipeline",
        "typeProperties": {
            "parameters": {key: {"value": f"@item().{key}", "type": "Expression"} for key in keys},
            "pipeline": {"referenceName": child_pipeline_name, "type": "PipelineReference"},
            "waitOnCompletion": True,
        },
    }


def _for_each_dispatch_activities(draft: SpecDraft) -> list[dict[str, Any]]:
    """Port of pipeline-codegen.ts's forEachDispatchActivities (gap #30)."""
    own_child = _execute_child_activity(
        "ExecuteChildPipeline", resolve_child_pipeline_name(draft), per_object_column_mappings_active(draft)
    )
    cases = child_dispatch_cases(draft)
    if not cases:
        return [own_child]
    return [
        {
            "name": "DispatchToChildPipeline",
            "description": "Route each control-table row to its child pipeline by TaskGroup; unmatched rows run the default child.",
            "type": "Switch",
            "typeProperties": {
                "on": {"value": "@coalesce(item().TaskGroup, '')", "type": "Expression"},
                "cases": [
                    {
                        "value": c.match_value,
                        "activities": [_execute_child_activity(f"ExecuteChildCase{i + 1}", c.child_pipeline_name)],
                    }
                    for i, c in enumerate(cases)
                ],
                "defaultActivities": [own_child],
            },
        }
    ]


def _master_pipeline(draft: SpecDraft) -> dict[str, Any]:
    loading_behavior = _loading_behavior_of(draft)
    control_table = draft.watermark_table or "control.adf_control_table"
    return {
        "name": resolve_master_pipeline_name(draft),
        "type": "Microsoft.DataFactory/factories/pipelines",
        "properties": {
            # G6: top-level runs must not overlap (shared staging tables + watermark). Never on the child.
            "concurrency": 1,
            "description": (
                f"Metadata-driven master pipeline for {draft.source_object}. Reads one row per object "
                f"from the control table and delegates each object ({loading_behavior}) to the child pipeline."
            ),
            "activities": [
                {
                    "name": "ReadObjectsFromControlTable",
                    "description": "Read all enabled objects to copy from the metadata/control table.",
                    "type": "Lookup",
                    "policy": {
                        "timeout": "0.00:10:00.000",
                        "retry": draft.retry_count,
                        "retryIntervalInSeconds": draft.retry_interval_in_seconds,
                    },
                    "typeProperties": {
                        "source": {
                            "type": "SqlServerSource",
                            # ControlTable/TaskIdFilter are read live via @{pipeline().parameters...}
                            # (not interpolated at generation time) so an operator can retarget this
                            # already-deployed pipeline at trigger time: point ControlTable at a
                            # different table/environment, or set TaskIdFilter to the one TaskId to
                            # restart (0 = every enabled row, the default — see AGENTS.md's
                            # "Operating generated pipelines in production" section).
                            "sqlReaderQuery": (
                                "SELECT TaskId, SourceType, Source, SourceQuery, TargetType, Target, "
                                "TargetFileName, LoadingBehavior, WatermarkColumn, WatermarkType, "
                                f"WatermarkValue, Enabled{', TaskGroup' if child_dispatch_cases(draft) else ''}"
                                f"{', ColumnMapping' if per_object_column_mappings_active(draft) else ''} "
                                "FROM @{pipeline().parameters.ControlTable} "
                                "WHERE Enabled = 1 AND (@{pipeline().parameters.TaskIdFilter} = 0 OR "
                                "TaskId = @{pipeline().parameters.TaskIdFilter}) ORDER BY TaskId"
                            ),
                        },
                        # RUN-05: every control-table row, read as output.value by the ForEach.
                        "firstRowOnly": False,
                    },
                },
                {
                    "name": "ForEachExecutedObject",
                    "description": "Batch the control-table rows and delegate each object to the child pipeline.",
                    "type": "ForEach",
                    "dependsOn": [{"activity": "ReadObjectsFromControlTable", "dependencyConditions": ["Succeeded"]}],
                    "typeProperties": {
                        "items": {
                            "value": "@activity('ReadObjectsFromControlTable').output.value",
                            "type": "Expression",
                        },
                        "isSequential": draft.is_sequential,
                        "batchCount": draft.batch_count,
                        "activities": _for_each_dispatch_activities(draft),
                    },
                },
            ],
            "parameters": _named_parameters([
                {"name": "ControlTable", "type": "String", "defaultValue": control_table},
                {"name": "TaskIdFilter", "type": "Int", "defaultValue": 0},
                {"name": "SourceLinkedService", "type": "String", "defaultValue": draft.source_linked_service.name},
                {"name": "TargetLinkedService", "type": "String", "defaultValue": draft.target_linked_service.name},
                {"name": "BatchCount", "type": "Int", "defaultValue": draft.batch_count},
                {"name": "ParallelCopies", "type": "Int", "defaultValue": draft.parallel_copies},
                {"name": "RetryCount", "type": "Int", "defaultValue": draft.retry_count},
            ]),
            "folder": {"name": f"metadata-driven/{draft.medallion_layer}"},
            "annotations": pipeline_annotations(draft),
        },
    }


def _copy_tuning(draft: SpecDraft) -> dict[str, Any]:
    tuning: dict[str, Any] = {"enableStaging": draft.enable_staging}
    if draft.enable_staging:
        tuning["stagingSettings"] = {
            "linkedServiceName": {
                "referenceName": draft.staging_linked_service,
                "type": "LinkedServiceReference",
            },
            "path": draft.staging_path or "staging/",
            "enableCompression": draft.staging_enable_compression,
        }
    tuning["parallelCopies"] = draft.parallel_copies
    if draft.data_integration_units:
        tuning["dataIntegrationUnits"] = draft.data_integration_units
    tuning["enableSkipIncompatibleRow"] = draft.redirect_incompatible_rows
    # Sample-guide parity gap 6 — enableSkipIncompatibleRow alone just discards
    # rejected rows; ADF also needs a destination to actually land them.
    if draft.redirect_incompatible_rows and draft.rejects_linked_service.strip():
        tuning["redirectIncompatibleRowSettings"] = {
            "linkedServiceName": {"referenceName": draft.rejects_linked_service.strip(), "type": "LinkedServiceReference"},
            "path": draft.rejects_path or "rejects/",
        }
    # §30.4 — row-count validation was a UI/parameters-file toggle with no generated behaviour
    # behind it (RowCountValidationEnabled was written to parameters.json but nothing read it).
    # ADF's Copy activity has a native data-consistency check for exactly this; wire the flag to it
    # instead of leaving it a no-op. This app still never runs the check itself — ADF does, at
    # execution time (§30.3 scope boundary).
    tuning["validateDataConsistency"] = draft.row_count_validation_enabled
    # Schema-drift slice — opt-in by-name auto-mapping with type conversion (see copyTuningOf).
    # Explicit column mappings (renames) ride on the same translator (see copyTranslatorOf).
    mappings = complete_column_mappings(draft.column_mappings)
    if draft.copy_type_conversion or mappings:
        tuning["translator"] = _translator_for(draft, mappings)
    return tuning


def _translator_for(draft: SpecDraft, mappings: list[ColumnMapping]) -> dict[str, Any]:
    """Port of pipeline-codegen.ts's translatorFor."""
    translator: dict[str, Any] = {"type": "TabularTranslator"}
    if draft.copy_type_conversion:
        translator["typeConversion"] = True
        translator["typeConversionSettings"] = {"allowDataTruncation": False, "treatBooleanAsNumber": False}
    if mappings:
        translator["mappings"] = [{"source": {"name": m.source}, "sink": {"name": m.sink}} for m in mappings]
    return translator


def _object_translator_json(draft: SpecDraft, entry: SourceObjectEntry | None = None) -> str:
    """Port of pipeline-codegen.ts's objectTranslatorJson — one control-table row's ColumnMapping value.
    Compact separators + ensure_ascii=False match JSON.stringify byte for byte."""
    own = complete_column_mappings(entry.column_mappings if entry else None)
    translator = _translator_for(draft, own or complete_column_mappings(draft.column_mappings))
    return json.dumps(translator, separators=(",", ":"), ensure_ascii=False)


# The child Copy's translator when per-object mappings are live: read from the row's ColumnMapping.
_PER_OBJECT_TRANSLATOR = {"value": "@json(pipeline().parameters.ColumnMapping)", "type": "Expression"}


# RUN-01: a Copy activity names its datasets in activity-level inputs/outputs and carries a store-typed
# source/sink (SqlServerSource, AzureSqlSink, ParquetSink, ...). The store type is derived from the
# connector's dataset type; the SQL family reads `sqlReaderQuery`, other databases read `query`.
# Port of copyStoreType & co. in pipeline-codegen.ts.
_STORE_TYPE_SUFFIX = re.compile(r"(?:Table|Object|Collection|Resource|Dataset|Format)$")
_STORE_TYPE_OVERRIDES = {"AzureSqlDW": "SqlDW", "AzureSqlMI": "SqlMI"}
_SQL_FAMILY_STORES = {"SqlServer", "AzureSql", "SqlMI", "SqlDW", "AmazonRdsForSqlServer"}


def copy_store_type(connector_type: str) -> str:
    """ADF Copy source/sink type prefix for a connector (append "Source"/"Sink")."""
    base = _STORE_TYPE_SUFFIX.sub("", connector_schema(connector_type).dataset_type)
    return _STORE_TYPE_OVERRIDES.get(base, base)


def _expr(value: str) -> dict[str, str]:
    return {"value": value, "type": "Expression"}


def _dataset_ref(name: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    ref: dict[str, Any] = {"referenceName": name, "type": "DatasetReference"}
    if parameters:
        ref["parameters"] = parameters
    return ref


def _source_dataset_parameters(draft: SpecDraft) -> dict[str, Any] | None:
    """A parameterized relational source dataset is pointed at the run's SchemaName/TableName."""
    if connector_schema(draft.source_type).kind != "database" or not draft.source_dataset.parameterized:
        return None
    return {"SchemaName": _expr("@pipeline().parameters.SchemaName"), "TableName": _expr("@pipeline().parameters.TableName")}


def _target_dataset_parameters(draft: SpecDraft) -> dict[str, Any] | None:
    """A parameterized file target dataset is pointed at the run's Target folder / TargetFileName."""
    if connector_schema(draft.target_type).kind != "file" or not draft.target_dataset.parameterized:
        return None
    return {"FolderPath": _expr("@pipeline().parameters.Target"), "FileName": _expr("@pipeline().parameters.TargetFileName")}


def _copy_activity(
    draft: SpecDraft,
    name: str,
    source: dict[str, Any],
    sink: dict[str, Any],
    tuning: dict[str, Any],
    *,
    description: str | None = None,
    depends_on: list[dict[str, Any]] | None = None,
    target_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One ADF Copy activity (RUN-01). `target_params` overrides the default output parameters."""
    activity: dict[str, Any] = {"name": name}
    if description:
        activity["description"] = description
    activity["type"] = "Copy"
    if depends_on:
        activity["dependsOn"] = depends_on
    activity["policy"] = {
        "timeout": resolve_copy_timeout(draft),
        "retry": draft.retry_count,
        "retryIntervalInSeconds": draft.retry_interval_in_seconds,
    }
    activity["inputs"] = [_dataset_ref(_resolve_dataset_name(draft, "source"), _source_dataset_parameters(draft))]
    activity["outputs"] = [
        _dataset_ref(
            _resolve_dataset_name(draft, "target"),
            target_params if target_params is not None else _target_dataset_parameters(draft),
        )
    ]
    activity["typeProperties"] = {"source": source, "sink": sink, **tuning}
    return activity


def _store_source(draft: SpecDraft, query: Any) -> dict[str, Any]:
    """A store-typed Copy source reading `query` (a database source) or the dataset itself."""
    schema = connector_schema(draft.source_type)
    store = copy_store_type(draft.source_type)
    source: dict[str, Any] = {"type": f"{store}Source"}
    if schema.kind == "database":
        source["sqlReaderQuery" if store in _SQL_FAMILY_STORES else "query"] = query
    elif schema.kind == "file":
        location = schema.dataset_location_type or "AzureBlobFSLocation"
        source["storeSettings"] = {"type": location.replace("Location", "ReadSettings"), "recursive": True}
    return source


def _copy_source(draft: SpecDraft, mode: str) -> dict[str, Any]:
    query = _expr("@pipeline().parameters.SourceQuery") if mode == "full" else _delta_copy_query(draft)
    source = _store_source(draft, query)
    if connector_schema(draft.source_type).kind == "database":
        source["partitionOption"] = draft.source_partition_option
        if draft.source_partition_option == "DynamicRange" and (draft.source_partition_column or "").strip():
            source["partitionSettings"] = {"partitionColumnName": draft.source_partition_column.strip()}
        if draft.source_isolation_level and copy_store_type(draft.source_type) in _SQL_FAMILY_STORES:
            source["isolationLevel"] = draft.source_isolation_level
    if (draft.query_timeout or "").strip():
        source["queryTimeout"] = draft.query_timeout.strip()
    # Standard metadata/lineage columns (sample-guide parity gap 1). Real ADF
    # location: Copy activity source.additionalColumns.
    additional_columns = _additional_columns_of(draft.custom_metadata)
    if additional_columns:
        source["additionalColumns"] = additional_columns
    return source


def _target_table_name(draft: SpecDraft) -> str | None:
    """A database target's `schema.table` from the target dataset (RUN-15/RUN-16); None for a
    file/service target. Direct port of targetTableName in pipeline-codegen.ts."""
    if connector_schema(draft.target_type).kind != "database":
        return None
    defaults = target_dataset_defaults(draft)
    ds = draft.target_dataset
    return f"{ds.schema.strip() or defaults.schema}.{ds.table.strip() or defaults.table}"


_FILE_SINK_FORMATS = {"DelimitedText", "Parquet", "Json", "Avro", "Orc"}


def _copy_sink(draft: SpecDraft, full_load: bool = False) -> dict[str, Any]:
    """A store-typed Copy sink (RUN-01): a file target gets store + format write settings, a database
    target gets write behaviour / upsert keys / pre-copy truncate, never the other kind's settings."""
    schema = connector_schema(draft.target_type)
    if schema.kind == "file":
        fmt = draft.sink_file_format if draft.sink_file_format in _FILE_SINK_FORMATS else "Parquet"
        location = schema.dataset_location_type or "AzureBlobFSLocation"
        format_settings: dict[str, Any] = {"type": f"{fmt}WriteSettings"}
        if fmt == "DelimitedText":
            format_settings["fileExtension"] = ".csv"
        if draft.max_rows_per_file:
            format_settings["maxRowsPerFile"] = draft.max_rows_per_file
        return {
            "type": f"{fmt}Sink",
            "storeSettings": {"type": location.replace("Location", "WriteSettings"), "copyBehavior": draft.copy_behavior},
            "formatSettings": format_settings,
        }
    store = copy_store_type(draft.target_type)
    sink: dict[str, Any] = {"type": f"{store}Sink"}
    if store in _SQL_FAMILY_STORES:
        sink["writeBehavior"] = "upsert" if draft.write_behavior == "Upsert" else "insert"
        if draft.write_behavior == "Upsert" and draft.upsert_key_columns.strip():
            sink["upsertSettings"] = {
                "useTempDB": True,
                "keys": [k.strip() for k in draft.upsert_key_columns.split(",") if k.strip()],
            }
    # RUN-16: a FullLoad with "truncate target" empties a database target before the copy (ADF has
    # no Copy-level `truncate` property), so source deletes don't survive a full reload.
    truncate_table = _target_table_name(draft) if full_load and draft.truncate_target else None
    if truncate_table:
        sink["preCopyScript"] = f"TRUNCATE TABLE {truncate_table}"
    if draft.write_batch_size:
        sink["writeBatchSize"] = draft.write_batch_size
    return sink


def _lookup_activity(
    draft: SpecDraft,
    name: str,
    description: str,
    query: str,
    depends_on: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    activity: dict[str, Any] = {
        "name": name,
        "description": description,
        "type": "Lookup",
        "policy": {
            "timeout": "0.00:10:00.000",
            "retry": draft.retry_count,
            "retryIntervalInSeconds": draft.retry_interval_in_seconds,
        },
        "typeProperties": {
            "source": {
                "type": "SqlServerSource",
                "sqlReaderQuery": query,
            },
        },
    }
    if depends_on:
        activity["dependsOn"] = [{"activity": a, "dependencyConditions": [c]} for a, c in depends_on]
    return activity


def _log_start_activity(log_table: str, load_type: str | None = None, log_suffix: str = "") -> dict[str, Any]:
    """Standard execution logging (sample-guide parity gap 2) — a start row per run."""
    load_type_value = f"'{load_type}'" if load_type else "'@{pipeline().parameters.LoadingBehavior}'"
    return {
        "name": f"LogRunStart{log_suffix}",
        "description": "Record the start of this object's run in the shared execution-log control table.",
        "type": "Lookup",
        "policy": {"timeout": "0.00:05:00.000", "retry": 0, "retryIntervalInSeconds": 30},
        "typeProperties": {
            "source": {
                "type": "SqlServerSource",
                "sqlReaderQuery": (
                    f"INSERT INTO {log_table} (RunId, PipelineName, SourceObject, TargetObject, LoadType, Status, StartedAt) "
                    "VALUES ('@{pipeline().RunId}', '@{pipeline().Pipeline}', '@{pipeline().parameters.Source}', "
                    f"'@{{pipeline().parameters.Target}}', {load_type_value}, 'RUNNING', GETUTCDATE())"
                ),
            },
        },
    }


def _log_end_activity(
    name: str, depends_on_activity: str, condition: str, copy_activity_name: str, log_table: str
) -> dict[str, Any]:
    """Success/failure write-back for the row `_log_start_activity` inserted."""
    if condition == "Succeeded":
        query = (
            f"UPDATE {log_table} SET Status = 'SUCCEEDED', "
            f"RowsRead = @{{activity('{copy_activity_name}').output.rowsRead}}, "
            f"RowsWritten = @{{activity('{copy_activity_name}').output.rowsCopied}}, "
            "CompletedAt = GETUTCDATE() WHERE RunId = '@{pipeline().RunId}' "
            "AND SourceObject = '@{pipeline().parameters.Source}'"
        )
        description = "Mark this run succeeded in the execution-log control table, with rows read/written from the Copy activity."
    else:
        query = (
            f"UPDATE {log_table} SET Status = 'FAILED', "
            f"ErrorMessage = '@{{replace(activity('{copy_activity_name}').Error.message, '''', '')}}', "
            "CompletedAt = GETUTCDATE() WHERE RunId = '@{pipeline().RunId}' "
            "AND SourceObject = '@{pipeline().parameters.Source}'"
        )
        description = "Mark this run failed in the execution-log control table, with the failing activity's error message."
    return {
        "name": name,
        "description": description,
        "type": "Lookup",
        "policy": {"timeout": "0.00:05:00.000", "retry": 0, "retryIntervalInSeconds": 30},
        "dependsOn": [{"activity": depends_on_activity, "dependencyConditions": [condition]}],
        "typeProperties": {
            "source": {"type": "SqlServerSource", "sqlReaderQuery": query},
        },
    }


def _with_execution_log(
    draft: SpecDraft,
    activities: list[dict[str, Any]],
    final_activity_name: str,
    copy_activity_name: str,
    log_table: str,
    load_type: str | None = None,
    log_suffix: str = "",
) -> list[dict[str, Any]]:
    """Wrap a load-path's activities with LogRunStart/success/failure when logging is enabled (gap 2).
    `log_suffix` keeps the log activity names unique across the child's Switch branches (ADF requires
    pipeline-wide unique activity names).
    `load_type`, when given, is a literal value ("FullLoad"/"DeltaLoad" — the independent patterns,
    §24.12, have no `LoadingBehavior` pipeline parameter, unlike `_child_pipeline`'s Switch-routed
    shape); omitted, `_log_start_activity` reads `@pipeline().parameters.LoadingBehavior` as before."""
    if not draft.enable_copy_log:
        return activities
    return [
        _log_start_activity(log_table, load_type, log_suffix),
        *activities,
        _log_end_activity(f"LogRunSucceeded{log_suffix}", final_activity_name, "Succeeded", copy_activity_name, log_table),
        _log_end_activity(f"LogRunFailed{log_suffix}", copy_activity_name, "Failed", copy_activity_name, log_table),
    ]


def _chunked_load_activities(
    draft: SpecDraft, copy_sink: dict[str, Any], chunk_plan_table: str, log_table: str, log_suffix: str = ""
) -> list[dict[str, Any]]:
    """Chunked historical/initial-load fan-out (sample-guide parity gap 4): a
    persisted chunk-plan control table gives restart-from-failure semantics a
    plain parallelCopies/sourcePartitionOption Copy doesn't. Direct port of
    ``chunkedLoadActivities`` in frontend/lib/adf/pipeline-codegen.ts — see
    that docstring for the full design rationale."""
    col = (draft.chunk_column or "").strip()
    chunk_size = draft.chunk_size_rows or 1000000
    additional_columns = _additional_columns_of(draft.custom_metadata)

    chunk_copy_source = _store_source(
        draft,
        _expr(
            "@concat('SELECT * FROM ', pipeline().parameters.SchemaName, '.', "
            f"pipeline().parameters.TableName, ' WHERE {col} >= ''', item().RangeStart, "
            f"''' AND {col} <= ''', item().RangeEnd, '''')"
        ),
    )
    if connector_schema(draft.source_type).kind == "database":
        chunk_copy_source["partitionOption"] = "None"
    if additional_columns:
        chunk_copy_source["additionalColumns"] = additional_columns
    chunk_tuning: dict[str, Any] = {"parallelCopies": draft.parallel_copies}
    if draft.data_integration_units:
        chunk_tuning["dataIntegrationUnits"] = draft.data_integration_units
    chunk_tuning["enableSkipIncompatibleRow"] = draft.redirect_incompatible_rows
    if draft.redirect_incompatible_rows and draft.rejects_linked_service.strip():
        chunk_tuning["redirectIncompatibleRowSettings"] = {
            "linkedServiceName": {"referenceName": draft.rejects_linked_service.strip(), "type": "LinkedServiceReference"},
            "path": draft.rejects_path or "rejects/",
        }

    generate_chunk_plan = {
        "name": "GenerateChunkPlan",
        "description": f"Idempotently seed {chunk_plan_table} with fixed-size {col} ranges for this object, if not already planned.",
        "type": "Lookup",
        "policy": {"timeout": "0.00:15:00.000", "retry": draft.retry_count, "retryIntervalInSeconds": draft.retry_interval_in_seconds},
        "typeProperties": {
            "source": {
                "type": "SqlServerSource",
                "sqlReaderQuery": " ".join(
                    [
                        ";WITH bounds AS (",
                        f"  SELECT MIN({col}) AS lo, MAX({col}) AS hi FROM @{{pipeline().parameters.SchemaName}}.@{{pipeline().parameters.TableName}}",
                        ")",
                        f"INSERT INTO {chunk_plan_table} (TaskId, ChunkId, RangeStart, RangeEnd, Status)",
                        "SELECT @{pipeline().parameters.TaskId}, n.n,",
                        f"  CAST(bounds.lo + n.n * {chunk_size} AS NVARCHAR(128)),",
                        f"  CAST(CASE WHEN bounds.lo + (n.n + 1) * {chunk_size} - 1 > bounds.hi THEN bounds.hi ELSE bounds.lo + (n.n + 1) * {chunk_size} - 1 END AS NVARCHAR(128)),",
                        "  'PENDING'",
                        "FROM bounds",
                        "CROSS APPLY (",
                        f"  SELECT TOP (CAST(CEILING((bounds.hi - bounds.lo + 1) * 1.0 / {chunk_size}) AS INT)) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS n",
                        "  FROM sys.all_objects",
                        ") AS n",
                        f"WHERE NOT EXISTS (SELECT 1 FROM {chunk_plan_table} WHERE TaskId = @{{pipeline().parameters.TaskId}})",
                    ]
                ),
            },
        },
    }
    lookup_pending_chunks = {
        "name": "LookupPendingChunks",
        "description": "Read chunks not yet succeeded (PENDING or FAILED) — a re-run skips already-SUCCEEDED chunks.",
        "type": "Lookup",
        "dependsOn": [{"activity": "GenerateChunkPlan", "dependencyConditions": ["Succeeded"]}],
        "policy": {"timeout": "0.00:10:00.000", "retry": draft.retry_count, "retryIntervalInSeconds": draft.retry_interval_in_seconds},
        "typeProperties": {
            "source": {
                "type": "SqlServerSource",
                "sqlReaderQuery": (
                    f"SELECT ChunkId, RangeStart, RangeEnd FROM {chunk_plan_table} "
                    "WHERE TaskId = @{pipeline().parameters.TaskId} AND Status IN ('PENDING', 'FAILED') ORDER BY ChunkId"
                ),
            },
            "firstRowOnly": False,
        },
    }
    for_each_chunk = {
        "name": "ForEachChunk",
        "description": "Copy each pending chunk, tracking per-chunk restart status in the chunk-plan control table.",
        "type": "ForEach",
        "dependsOn": [{"activity": "LookupPendingChunks", "dependencyConditions": ["Succeeded"]}],
        "typeProperties": {
            "items": {"value": "@activity('LookupPendingChunks').output.value", "type": "Expression"},
            "isSequential": False,
            "batchCount": draft.max_parallel_chunks,
            "activities": [
                {
                    "name": "MarkChunkRunning",
                    "type": "Lookup",
                    "policy": {"timeout": "0.00:05:00.000", "retry": 0, "retryIntervalInSeconds": 30},
                    "typeProperties": {
                        "source": {
                            "type": "SqlServerSource",
                            "sqlReaderQuery": (
                                f"UPDATE {chunk_plan_table} SET Status = 'RUNNING', StartedAt = GETUTCDATE() "
                                "WHERE TaskId = @{pipeline().parameters.TaskId} AND ChunkId = @{item().ChunkId}"
                            ),
                        },
                    },
                },
                _copy_activity(
                    draft,
                    "CopyChunk",
                    chunk_copy_source,
                    copy_sink,
                    chunk_tuning,
                    depends_on=[{"activity": "MarkChunkRunning", "dependencyConditions": ["Succeeded"]}],
                ),
                {
                    "name": "MarkChunkSucceeded",
                    "type": "Lookup",
                    "dependsOn": [{"activity": "CopyChunk", "dependencyConditions": ["Succeeded"]}],
                    "policy": {"timeout": "0.00:05:00.000", "retry": 0, "retryIntervalInSeconds": 30},
                    "typeProperties": {
                        "source": {
                            "type": "SqlServerSource",
                            "sqlReaderQuery": (
                                f"UPDATE {chunk_plan_table} SET Status = 'SUCCEEDED', "
                                "RowsCopied = @{activity('CopyChunk').output.rowsCopied}, CompletedAt = GETUTCDATE() "
                                "WHERE TaskId = @{pipeline().parameters.TaskId} AND ChunkId = @{item().ChunkId}"
                            ),
                        },
                    },
                },
                {
                    "name": "MarkChunkFailed",
                    "type": "Lookup",
                    "dependsOn": [{"activity": "CopyChunk", "dependencyConditions": ["Failed"]}],
                    "policy": {"timeout": "0.00:05:00.000", "retry": 0, "retryIntervalInSeconds": 30},
                    "typeProperties": {
                        "source": {
                            "type": "SqlServerSource",
                            "sqlReaderQuery": (
                                f"UPDATE {chunk_plan_table} SET Status = 'FAILED', "
                                "ErrorMessage = '@{replace(activity('CopyChunk').Error.message, '''', '')}', "
                                "CompletedAt = GETUTCDATE() WHERE TaskId = @{pipeline().parameters.TaskId} AND ChunkId = @{item().ChunkId}"
                            ),
                        },
                    },
                },
            ],
        },
    }
    verify_chunks_complete = {
        "name": "VerifyAllChunksSucceeded",
        "description": "Fail the pipeline if any chunk did not reach SUCCEEDED — makes an incomplete chunked load visible instead of silently partial.",
        "type": "Lookup",
        "dependsOn": [{"activity": "ForEachChunk", "dependencyConditions": ["Succeeded"]}],
        "policy": {"timeout": "0.00:05:00.000", "retry": 0, "retryIntervalInSeconds": 30},
        "typeProperties": {
            "source": {
                "type": "SqlServerSource",
                "sqlReaderQuery": (
                    f"SELECT COUNT(*) AS Remaining FROM {chunk_plan_table} "
                    "WHERE TaskId = @{pipeline().parameters.TaskId} AND Status <> 'SUCCEEDED'"
                ),
            },
        },
    }
    fail_if_incomplete = {
        "name": "FailIfChunksIncomplete",
        "type": "IfCondition",
        "dependsOn": [{"activity": "VerifyAllChunksSucceeded", "dependencyConditions": ["Succeeded"]}],
        "typeProperties": {
            "expression": {"type": "Expression", "value": "@greater(activity('VerifyAllChunksSucceeded').output.firstRow.Remaining, 0)"},
            "ifTrueActivities": [
                {
                    "name": "FailChunkedLoad",
                    "type": "Fail",
                    "typeProperties": {
                        "message": {
                            "type": "Expression",
                            "value": (
                                "@concat('Chunked load incomplete: ', "
                                "string(activity('VerifyAllChunksSucceeded').output.firstRow.Remaining), "
                                "' chunk(s) not SUCCEEDED. Re-run the pipeline to retry only those chunks.')"
                            ),
                        },
                        "errorCode": "ChunkedLoadIncomplete",
                    },
                }
            ],
        },
    }

    if not draft.enable_copy_log:
        return [generate_chunk_plan, lookup_pending_chunks, for_each_chunk, verify_chunks_complete, fail_if_incomplete]
    log_start = _log_start_activity(log_table, load_type="ChunkedLoad", log_suffix=log_suffix)
    generate_chunk_plan_chained = {
        **generate_chunk_plan,
        "dependsOn": [{"activity": f"LogRunStart{log_suffix}", "dependencyConditions": ["Succeeded"]}],
    }
    return [
        log_start,
        generate_chunk_plan_chained,
        lookup_pending_chunks,
        for_each_chunk,
        verify_chunks_complete,
        fail_if_incomplete,
        {
            "name": f"LogRunSucceeded{log_suffix}",
            "type": "Lookup",
            "dependsOn": [{"activity": "VerifyAllChunksSucceeded", "dependencyConditions": ["Succeeded"]}],
            "policy": {"timeout": "0.00:05:00.000", "retry": 0, "retryIntervalInSeconds": 30},
            "typeProperties": {
                "source": {
                    "type": "SqlServerSource",
                    "sqlReaderQuery": (
                        f"UPDATE {log_table} SET Status = 'SUCCEEDED', CompletedAt = GETUTCDATE() "
                        "WHERE RunId = '@{pipeline().RunId}' AND SourceObject = '@{pipeline().parameters.Source}'"
                    ),
                },
            },
        },
        {
            "name": f"LogRunFailed{log_suffix}",
            "type": "Lookup",
            "dependsOn": [{"activity": "ForEachChunk", "dependencyConditions": ["Failed"]}],
            "policy": {"timeout": "0.00:05:00.000", "retry": 0, "retryIntervalInSeconds": 30},
            "typeProperties": {
                "source": {
                    "type": "SqlServerSource",
                    "sqlReaderQuery": (
                        f"UPDATE {log_table} SET Status = 'FAILED', ErrorMessage = 'One or more chunks failed', "
                        "CompletedAt = GETUTCDATE() WHERE RunId = '@{pipeline().RunId}' AND SourceObject = '@{pipeline().parameters.Source}'"
                    ),
                },
            },
        },
    ]


def _delete_handling_activities(
    draft: SpecDraft,
    table: str,
    after_activity: str,
    source_ref: str,
    target_ref: str,
    layer_prefixes: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Hard-delete handling on the DeltaLoad branch — direct port of deleteHandlingActivities in
    pipeline-codegen.ts (see that docstring). none/unset returns no activities."""
    mode = draft.delete_handling or "none"
    keys = [k.strip() for k in draft.upsert_key_columns.split(",") if k.strip()]
    if mode == "none" or not keys:
        return [], after_activity
    landed = layer_target_table_name("bronze", source_type=bronze_source_token(draft), object_name=table, layer_prefixes=layer_prefixes)
    stage = f"stg.{landed.split('.')[-1]}_delete_keys"
    flag = resolve_soft_delete_column(draft)
    key_list = ", ".join(keys)
    match = " AND ".join(f"k.{k} = tgt.{k}" for k in keys)
    source_table = "@{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}"
    if mode == "cdc":
        ci = (draft.cdc_capture_instance or "").strip() or "@{pipeline().parameters.SchemaName}_@{pipeline().parameters.TableName}"
        stage_name = "StageCdcChanges"
        stage_query = "\n".join(
            [
                f"DECLARE @min_lsn binary(10) = sys.fn_cdc_get_min_lsn('{ci}');",
                "DECLARE @from_lsn binary(10) = sys.fn_cdc_map_time_to_lsn('smallest greater than', "
                "'@{activity('LookupLastWatermark').output.firstRow.WatermarkValue}');",
                "DECLARE @to_lsn binary(10) = sys.fn_cdc_map_time_to_lsn('largest less than or equal', "
                "'@{activity('LookupNewWatermark').output.firstRow.NewWatermark}');",
                "IF @from_lsn IS NOT NULL AND @from_lsn < @min_lsn SET @from_lsn = @min_lsn;",
                f"IF @from_lsn IS NULL OR @to_lsn IS NULL OR @from_lsn > @to_lsn SELECT TOP 0 {key_list}, 0 AS _cdc_operation FROM {source_table};",
                f"ELSE SELECT {key_list}, _cdc_operation FROM (SELECT {key_list}, __$operation AS _cdc_operation, "
                f"ROW_NUMBER() OVER (PARTITION BY {key_list} ORDER BY __$start_lsn DESC, __$seqval DESC) AS _rn "
                f"FROM cdc.fn_cdc_get_all_changes_{ci}(@from_lsn, @to_lsn, 'all')) c WHERE _rn = 1;",
            ]
        )
        apply_sql = "\n".join(
            [
                f"UPDATE tgt SET tgt.{flag} = 1, tgt._deleted_at_utc = SYSUTCDATETIME() FROM {landed} tgt JOIN {stage} k ON {match} "
                f"WHERE k._cdc_operation = 1 AND tgt.{flag} = 0;",
                f"UPDATE tgt SET tgt.{flag} = 0, tgt._deleted_at_utc = NULL FROM {landed} tgt JOIN {stage} k ON {match} "
                f"WHERE k._cdc_operation IN (2, 4) AND tgt.{flag} = 1;",
            ]
        )
        description = f"Stage the latest CDC operation per key ({key_list}) inside this run's watermark window."
    else:
        stage_name = "StageSourceKeys"
        stage_query = f"SELECT {key_list} FROM {source_table}"
        apply_sql = "\n".join(
            [
                f"UPDATE tgt SET tgt.{flag} = 1, tgt._deleted_at_utc = SYSUTCDATETIME() FROM {landed} tgt WHERE tgt.{flag} = 0 "
                f"AND NOT EXISTS (SELECT 1 FROM {stage} k WHERE {match});",
                f"UPDATE tgt SET tgt.{flag} = 0, tgt._deleted_at_utc = NULL FROM {landed} tgt WHERE tgt.{flag} = 1 "
                f"AND EXISTS (SELECT 1 FROM {stage} k WHERE {match});",
            ]
        )
        description = f"Stage every current source key ({key_list}) for delete reconciliation."
    apply_name = "ApplySoftDeletes"
    activities = [
        _copy_activity(
            draft,
            stage_name,
            _store_source(draft, stage_query),
            {
                "type": f"{copy_store_type(draft.target_type)}Sink",
                "tableOption": "autoCreate",
                "preCopyScript": f"IF OBJECT_ID('{stage}') IS NOT NULL TRUNCATE TABLE {stage}",
            },
            {},
            description=description,
            depends_on=[{"activity": after_activity, "dependencyConditions": ["Succeeded"]}],
            # The stage table is the parameterized target dataset pointed at `stage`.
            target_params={
                "SchemaName": stage.split(".", 1)[0],
                "TableName": stage.split(".", 1)[-1],
            },
        ),
        {
            "name": apply_name,
            "description": f"Mark deleted rows on {landed} via {flag} (+ _deleted_at_utc); rows that reappear are un-flagged.",
            "type": "Script",
            "linkedServiceName": {"referenceName": draft.target_linked_service.name, "type": "LinkedServiceReference"},
            "dependsOn": [{"activity": stage_name, "dependencyConditions": ["Succeeded"]}],
            "policy": {
                "timeout": "0.00:30:00.000",
                "retry": draft.retry_count,
                "retryIntervalInSeconds": draft.retry_interval_in_seconds,
            },
            "typeProperties": {
                "scripts": [
                    # Separate batch: SQL Server can't reference a column added in the same batch.
                    {
                        "type": "NonQuery",
                        "text": f"IF COL_LENGTH('{landed}', '{flag}') IS NULL ALTER TABLE {landed} ADD {flag} BIT NOT NULL DEFAULT 0;\n"
                        f"IF COL_LENGTH('{landed}', '_deleted_at_utc') IS NULL ALTER TABLE {landed} ADD _deleted_at_utc DATETIME2 NULL;",
                    },
                    {"type": "NonQuery", "text": apply_sql},
                ],
            },
        },
    ]
    return activities, apply_name


def _child_pipeline(draft: SpecDraft, layer_prefixes: dict[str, str] | None = None) -> dict[str, Any]:
    watermark_column = draft.watermark_columns[0].name if draft.watermark_columns else "WatermarkValue"
    watermark_column_list = ", ".join(c.name for c in draft.watermark_columns)
    schema, table = _split_object_name(draft.source_object, draft.source_type)
    source_ref = _resolve_dataset_name(draft, "source")
    target_ref = _resolve_dataset_name(draft, "target")
    per_object_mappings = per_object_column_mappings_active(draft)
    tuning = _copy_tuning(draft)
    if per_object_mappings:
        tuning = {**tuning, "translator": _PER_OBJECT_TRANSLATOR}
    control_schema = _control_schema_of(draft)
    log_table = f"{control_schema}.adf_execution_log"
    chunk_plan_table = f"{control_schema}.adf_chunk_plan"
    is_chunked = draft.chunking_enabled and bool((draft.chunk_column or "").strip())

    full_load_transform_activities, full_load_final_name = _layer_transformation_activities(
        draft, schema, table, "FullLoadCopy", layer_prefixes, full_refresh=True, name_suffix="_FullLoad"
    )
    delta_transform_activities, delta_final_name = _layer_transformation_activities(
        draft, schema, table, "UpdateWatermark", layer_prefixes, name_suffix="_DeltaLoad"
    )
    delta_delete_activities, delta_delete_final = _delete_handling_activities(
        draft, table, "CopyDeltaData", source_ref, target_ref, layer_prefixes
    )

    full_load_copy = _copy_activity(
        draft,
        "FullLoadCopy",
        _copy_source(draft, "full"),
        _copy_sink(draft, full_load=True),
        tuning,
        description=f"Truncate-and-load full snapshot of {draft.source_object}.",
    )

    delta_activities = [
        _lookup_activity(
            draft,
            "LookupLastWatermark",
            "Read the previous watermark for this object from the control table.",
            "SELECT WatermarkValue FROM @{pipeline().parameters.WatermarkTable} "
            "WHERE TaskId = @{pipeline().parameters.TaskId}",
        ),
        _lookup_activity(
            draft,
            "LookupNewWatermark",
            "Compute the current maximum watermark from the source object.",
            _new_watermark_lookup_query(draft),
            depends_on=[("LookupLastWatermark", "Succeeded")],
        ),
        _copy_activity(
            draft,
            "CopyDeltaData",
            _copy_source(draft, "delta"),
            _copy_sink(draft),
            tuning,
            description="Copy rows changed since the last watermark (watermark column > old AND <= new).",
            depends_on=[{"activity": "LookupNewWatermark", "dependencyConditions": ["Succeeded"]}],
        ),
        *delta_delete_activities,
        _lookup_activity(
            draft,
            "UpdateWatermark",
            "Write the new watermark back to the control table.",
            f"UPDATE @{{pipeline().parameters.WatermarkTable}} SET WatermarkValue = "
            f"'@{{{_new_watermark_value_expr(draft)}}}', LastRunTimestamp = GETDATE() "
            "WHERE TaskId = @{pipeline().parameters.TaskId}",
            depends_on=[(delta_delete_final, "Succeeded")],
        ),
        *delta_transform_activities,
    ]

    post_load_dq = _post_load_dq_table(draft, table, layer_prefixes)
    dq_activities, dq_gate_depends_on = _data_quality_activities(
        draft, schema, table, (post_load_dq, "RouteByLoadingBehavior") if post_load_dq else None
    )
    return {
        "name": resolve_child_pipeline_name(draft),
        "type": "Microsoft.DataFactory/factories/pipelines",
        "properties": {
            "description": (
                "Per-object child pipeline. Routes on LoadingBehavior: FullLoad performs a snapshot copy; "
                "DeltaLoad runs watermark lookup -> delta copy -> watermark write-back; ChunkedLoad fans out "
                "over row-range chunks with persisted restart status."
            ),
            "activities": [
                *([] if post_load_dq else dq_activities),
                {
                    "name": "RouteByLoadingBehavior",
                    "type": "Switch",
                    **({"dependsOn": dq_gate_depends_on} if dq_gate_depends_on and not post_load_dq else {}),
                    "typeProperties": {
                        "on": {"value": "@pipeline().parameters.LoadingBehavior", "type": "Expression"},
                        "cases": [
                            {
                                "value": "FullLoad",
                                "activities": _with_execution_log(
                                    draft, [full_load_copy, *full_load_transform_activities], full_load_final_name, "FullLoadCopy", log_table,
                                    log_suffix="_FullLoad",
                                ),
                            },
                            {
                                "value": "ChunkedLoad",
                                # Sample-guide parity gap 4 — see _chunked_load_activities. This ForEach
                                # fans out over row-range CHUNKS of one object, not over objects (that
                                # fan-out is the master pipeline's own ForEach) — not the orchestration
                                # patterns intentionally out of scope here.
                                "activities": (
                                    _chunked_load_activities(
                                        draft, _copy_sink(draft), chunk_plan_table, log_table, "_ChunkedLoad"
                                    )
                                    if is_chunked
                                    else []
                                ),
                            },
                        ],
                        "defaultActivities": _with_execution_log(
                            draft, delta_activities, delta_final_name, "CopyDeltaData", log_table, log_suffix="_DeltaLoad"
                        ),
                    },
                },
                # E2E-09: Silver/Gold checks run on the loaded layer target, after the Switch.
                *(dq_activities if post_load_dq else []),
            ],
            "parameters": _named_parameters([
                {"name": "TaskId", "type": "Int", "defaultValue": 1},
                {"name": "SourceType", "type": "String", "defaultValue": draft.source_type},
                {"name": "Source", "type": "String", "defaultValue": draft.source_object},
                {"name": "SourceQuery", "type": "String", "defaultValue": draft.source_query or ""},
                {"name": "SchemaName", "type": "String", "defaultValue": schema},
                {"name": "TableName", "type": "String", "defaultValue": table},
                {"name": "TargetType", "type": "String", "defaultValue": draft.target_type},
                {"name": "Target", "type": "String", "defaultValue": draft.target_path},
                {"name": "TargetFileName", "type": "String", "defaultValue": draft.target_file_name or ""},
                {"name": "LoadingBehavior", "type": "String", "defaultValue": _loading_behavior_of(draft)},
                {"name": "WatermarkColumn", "type": "String", "defaultValue": watermark_column},
                {"name": "WatermarkColumnList", "type": "String", "defaultValue": watermark_column_list},
                {"name": "WatermarkValue", "type": "String", "defaultValue": draft.watermark_initial_value or "1900-01-01"},
                {"name": "WatermarkTable", "type": "String", "defaultValue": draft.watermark_table or "control.adf_control_table"},
                {"name": "BatchCount", "type": "Int", "defaultValue": draft.batch_count},
                {"name": "ParallelCopies", "type": "Int", "defaultValue": draft.parallel_copies},
                *(
                    [{"name": "ColumnMapping", "type": "String", "defaultValue": _object_translator_json(draft)}]
                    if per_object_mappings
                    else []
                ),
            ]),
            "folder": {"name": f"metadata-driven/{draft.medallion_layer}"},
            "annotations": pipeline_annotations(draft),
        },
    }


def _independent_pipeline(draft: SpecDraft, mode: str, layer_prefixes: dict[str, str] | None = None) -> dict[str, Any]:
    """§24.3/§24.12 "Independent (single-object)" pattern: one source, one target, no control table —
    direct port of `independentPipeline` in pipeline-codegen.ts. Reuses the exact same Copy/log/DQ/
    transformation building blocks `_child_pipeline` uses for its FullLoad/default (delta) Switch cases,
    just as the pipeline's only top-level activities instead of one branch of a per-object Switch."""
    schema, table = _split_object_name(draft.source_object, draft.source_type)
    source_ref = _resolve_dataset_name(draft, "source")
    target_ref = _resolve_dataset_name(draft, "target")
    log_table = f"{_control_schema_of(draft)}.adf_execution_log"
    post_load_dq = _post_load_dq_table(draft, table, layer_prefixes)
    tuning = _copy_tuning(draft)

    if mode == "full":
        copy_activity_name = "FullLoadCopy"
        transform_activities, final_activity_name = _layer_transformation_activities(
            draft, schema, table, copy_activity_name, layer_prefixes, full_refresh=True
        )
        raw_activities: list[dict[str, Any]] = [
            _copy_activity(
                draft,
                copy_activity_name,
                _copy_source(draft, "full"),
                _copy_sink(draft, full_load=True),
                tuning,
                description=f"Truncate-and-load full snapshot of {draft.source_object}.",
            ),
            *transform_activities,
        ]
    else:
        copy_activity_name = "CopyDeltaData"
        transform_activities, final_activity_name = _layer_transformation_activities(
            draft, schema, table, "UpdateWatermark", layer_prefixes
        )
        delete_activities, delete_final = _delete_handling_activities(
            draft, table, copy_activity_name, source_ref, target_ref, layer_prefixes
        )
        raw_activities = [
            _lookup_activity(
                draft,
                "LookupLastWatermark",
                "Read the previous watermark for this object from the watermark table.",
                "SELECT WatermarkValue FROM @{pipeline().parameters.WatermarkTable} "
                "WHERE TaskId = @{pipeline().parameters.TaskId}",
            ),
            _lookup_activity(
                draft,
                "LookupNewWatermark",
                "Compute the current maximum watermark from the source object.",
                _new_watermark_lookup_query(draft),
                depends_on=[("LookupLastWatermark", "Succeeded")],
            ),
            _copy_activity(
                draft,
                copy_activity_name,
                _copy_source(draft, "delta"),
                _copy_sink(draft),
                tuning,
                description="Copy rows changed since the last watermark (watermark column > old AND <= new).",
                depends_on=[{"activity": "LookupNewWatermark", "dependencyConditions": ["Succeeded"]}],
            ),
            *delete_activities,
            _lookup_activity(
                draft,
                "UpdateWatermark",
                "Write the new watermark back to the watermark table.",
                f"UPDATE @{{pipeline().parameters.WatermarkTable}} SET WatermarkValue = "
                f"'@{{{_new_watermark_value_expr(draft)}}}', LastRunTimestamp = GETDATE() "
                "WHERE TaskId = @{pipeline().parameters.TaskId}",
                depends_on=[(delete_final, "Succeeded")],
            ),
            *transform_activities,
        ]

    load_type = "FullLoad" if mode == "full" else "DeltaLoad"
    wrapped = _with_execution_log(draft, raw_activities, final_activity_name, copy_activity_name, log_table, load_type)
    dq_activities, dq_gate_depends_on = _data_quality_activities(
        draft, schema, table, (post_load_dq, final_activity_name) if post_load_dq else None
    )
    # Equivalent to `_child_pipeline`'s DQ gate on its Switch: here there's no Switch to hang it off, so
    # it gates whichever activity actually runs first (LogRunStart when logging is on, else the copy
    # path's own first activity). E2E-09: Silver/Gold checks instead run after the layer load.
    if dq_gate_depends_on and wrapped and not post_load_dq:
        first = dict(wrapped[0])
        first["dependsOn"] = [*first.get("dependsOn", []), *dq_gate_depends_on]
        wrapped = [first, *wrapped[1:]]
    activities = [*wrapped, *dq_activities] if post_load_dq else [*dq_activities, *wrapped]

    watermark_column = draft.watermark_columns[0].name if draft.watermark_columns else "WatermarkValue"
    watermark_column_list = ", ".join(c.name for c in draft.watermark_columns)
    parameters: list[dict[str, Any]] = [
        {"name": "TaskId", "type": "Int", "defaultValue": 1},
        {"name": "SourceType", "type": "String", "defaultValue": draft.source_type},
        {"name": "Source", "type": "String", "defaultValue": draft.source_object},
        {"name": "SourceQuery", "type": "String", "defaultValue": draft.source_query or ""},
        {"name": "SchemaName", "type": "String", "defaultValue": schema},
        {"name": "TableName", "type": "String", "defaultValue": table},
        {"name": "TargetType", "type": "String", "defaultValue": draft.target_type},
        {"name": "Target", "type": "String", "defaultValue": draft.target_path},
        {"name": "TargetFileName", "type": "String", "defaultValue": draft.target_file_name or ""},
    ]
    if mode == "delta":
        parameters.extend(
            [
                {"name": "WatermarkColumn", "type": "String", "defaultValue": watermark_column},
                {"name": "WatermarkColumnList", "type": "String", "defaultValue": watermark_column_list},
                {"name": "WatermarkValue", "type": "String", "defaultValue": draft.watermark_initial_value or "1900-01-01"},
                {"name": "WatermarkTable", "type": "String", "defaultValue": draft.watermark_table or "control.watermarks"},
            ]
        )
    parameters.extend(
        [
            {"name": "BatchCount", "type": "Int", "defaultValue": draft.batch_count},
            {"name": "ParallelCopies", "type": "Int", "defaultValue": draft.parallel_copies},
        ]
    )

    description = (
        f"Independent single-object full-load pipeline for {draft.source_object}. One source, one target "
        "— no control table, no child pipeline."
        if mode == "full"
        else (
            f"Independent single-object watermark-delta pipeline for {draft.source_object}. One source, "
            "one target, its own watermark table — no control table, no child pipeline."
        )
    )
    return {
        "name": resolve_pipeline_name(draft),
        "type": "Microsoft.DataFactory/factories/pipelines",
        "properties": {
            # G6: top-level runs must not overlap (shared staging tables + watermark). Never on the child.
            "concurrency": 1,
            "description": description,
            "activities": activities,
            "parameters": _named_parameters(parameters),
            "folder": {"name": f"metadata-driven/{draft.medallion_layer}"},
            "annotations": pipeline_annotations(draft),
        },
    }


def _watermark_table_sql(draft: SpecDraft) -> str:
    """§24.3/§24.12 "Independent" watermark-delta pipeline's own watermark table — direct port of
    `watermarkTableSql` in pipeline-codegen.ts. A single tracked row (TaskId = 1), distinct from
    `_control_table_sql`'s multi-object `adf_control_table` (this pattern has no control table at all)."""
    table = draft.watermark_table.strip() or "control.watermarks"
    initial_value = (draft.watermark_initial_value or "1900-01-01").replace("'", "''")
    lines = [
        "-- Watermark table for this independent (single-object) watermark-delta pipeline.",
        "-- One row (TaskId = 1) tracks this pipeline's own last-processed watermark value.",
        "",
        *_create_table_if_missing(table),
        "  TaskId            INT           NOT NULL PRIMARY KEY,",
        "  WatermarkValue    NVARCHAR(256) NULL,",
        "  LastRunTimestamp  DATETIME2     NULL",
        ");",
        "",
        f"IF NOT EXISTS (SELECT 1 FROM {table} WHERE TaskId = 1)",
        f"  INSERT INTO {table} (TaskId, WatermarkValue) VALUES (1, N'{initial_value}');",
        "",
    ]
    return "\n".join(lines)


# §24.3/§24.12 "Single pipeline (metadata-driven)" pattern's per-row parameter set — the same set
# `_master_pipeline`'s ExecutePipeline call today overrides per control-table row. Direct port of
# FOREACH_ITEM_PARAM_NAMES in pipeline-codegen.ts.
_FOREACH_ITEM_PARAM_NAMES = (
    "TaskId", "SourceType", "SourceQuery", "Source", "TargetFileName", "TargetType", "Target",
    "LoadingBehavior", "WatermarkColumn", "WatermarkValue", "ColumnMapping",
)
_FOREACH_ITEM_PARAM_RE = re.compile(r"pipeline\(\)\.parameters\.(" + "|".join(_FOREACH_ITEM_PARAM_NAMES) + r")\b")


def _single_pipeline(draft: SpecDraft, layer_prefixes: dict[str, str] | None = None) -> dict[str, Any]:
    """§24.3/§24.12 "Single pipeline (metadata-driven)" pattern — direct port of `singlePipeline` in
    pipeline-codegen.ts. Same control-table-driven fan-out as `_master_pipeline`'s
    `ForEachExecutedObject`, but looping `_child_pipeline`'s own Switch inline instead of delegating to a
    nested `ExecutePipeline` — no child pipeline artifact at all. Built by taking the child's activities
    verbatim and rewriting every `@pipeline().parameters.X` reference for the set of parameters the
    master's `ExecutePipeline` call today overrides per row into `@item().X`, since those values now come
    from the `ForEach` loop's current control-table row. Every other parameter (SchemaName, TableName,
    WatermarkColumnList, WatermarkTable, ...) stays a fixed, build-time pipeline parameter, matching
    `_master_pipeline`/`_child_pipeline`'s existing (unchanged) semantics exactly."""
    child = _child_pipeline(draft, layer_prefixes)
    child_activities_json = json.dumps(child["properties"]["activities"])
    item_activities = json.loads(_FOREACH_ITEM_PARAM_RE.sub(r"item().\1", child_activities_json))
    control_table = draft.watermark_table or "control.adf_control_table"
    schema, table = _split_object_name(draft.source_object, draft.source_type)
    return {
        "name": resolve_pipeline_name(draft),
        "type": "Microsoft.DataFactory/factories/pipelines",
        "properties": {
            # G6: top-level runs must not overlap (shared staging tables + watermark). Never on the child.
            "concurrency": 1,
            "description": (
                f"Single metadata-driven pipeline for {draft.source_object}. Reads one row per object "
                "from the control table and loops it inline — no child pipeline."
            ),
            "activities": [
                {
                    "name": "ReadObjectsFromControlTable",
                    "description": "Read all enabled objects to copy from the metadata/control table.",
                    "type": "Lookup",
                    "policy": {
                        "timeout": "0.00:10:00.000",
                        "retry": draft.retry_count,
                        "retryIntervalInSeconds": draft.retry_interval_in_seconds,
                    },
                    "typeProperties": {
                        "source": {
                            "type": "SqlServerSource",
                            # See _master_pipeline's identical comment: ControlTable/TaskIdFilter are
                            # runtime expressions, not baked in at generation time.
                            "sqlReaderQuery": (
                                "SELECT TaskId, SourceType, Source, SourceQuery, TargetType, Target, "
                                "TargetFileName, LoadingBehavior, WatermarkColumn, WatermarkType, "
                                f"WatermarkValue, Enabled{', ColumnMapping' if per_object_column_mappings_active(draft) else ''} "
                                "FROM @{pipeline().parameters.ControlTable} "
                                "WHERE Enabled = 1 AND (@{pipeline().parameters.TaskIdFilter} = 0 OR "
                                "TaskId = @{pipeline().parameters.TaskIdFilter}) ORDER BY TaskId"
                            ),
                        },
                        # RUN-05: every control-table row, read as output.value by the ForEach.
                        "firstRowOnly": False,
                    },
                },
                {
                    "name": "ForEachExecutedObject",
                    "description": "Batch the control-table rows and process each object inline.",
                    "type": "ForEach",
                    "dependsOn": [{"activity": "ReadObjectsFromControlTable", "dependencyConditions": ["Succeeded"]}],
                    "typeProperties": {
                        "items": {"value": "@activity('ReadObjectsFromControlTable').output.value", "type": "Expression"},
                        "isSequential": draft.is_sequential,
                        "batchCount": draft.batch_count,
                        "activities": item_activities,
                    },
                },
            ],
            "parameters": _named_parameters([
                {"name": "ControlTable", "type": "String", "defaultValue": control_table},
                {"name": "TaskIdFilter", "type": "Int", "defaultValue": 0},
                {"name": "SourceLinkedService", "type": "String", "defaultValue": draft.source_linked_service.name},
                {"name": "TargetLinkedService", "type": "String", "defaultValue": draft.target_linked_service.name},
                {"name": "SchemaName", "type": "String", "defaultValue": schema},
                {"name": "TableName", "type": "String", "defaultValue": table},
                {"name": "WatermarkColumnList", "type": "String", "defaultValue": ", ".join(c.name for c in draft.watermark_columns)},
                {"name": "WatermarkTable", "type": "String", "defaultValue": draft.watermark_table or "control.adf_control_table"},
                {"name": "BatchCount", "type": "Int", "defaultValue": draft.batch_count},
                {"name": "ParallelCopies", "type": "Int", "defaultValue": draft.parallel_copies},
                {"name": "RetryCount", "type": "Int", "defaultValue": draft.retry_count},
            ]),
            "folder": {"name": f"metadata-driven/{draft.medallion_layer}"},
            "annotations": pipeline_annotations(draft),
        },
    }


# ---------------------------------------------------------------------------
# Datasets / linked services
# ---------------------------------------------------------------------------


def _dataset_artifact(draft: SpecDraft, kind: str) -> GeneratedArtifact:
    ls = draft.source_linked_service if kind == "source" else draft.target_linked_service
    ds = draft.source_dataset if kind == "source" else draft.target_dataset
    defaults = source_dataset_defaults(draft) if kind == "source" else target_dataset_defaults(draft)
    name = dataset_name(ds, defaults)
    value = {
        "name": name,
        "type": "Microsoft.DataFactory/factories/datasets",
        # §25 docs.description — a visibly-derived description, so generated output passes its own advisory.
        "properties": {
            "description": f"{kind.capitalize()} dataset for pipeline {draft.name} ({ls.connector_type}).",
            **build_dataset_properties(ds, ls, defaults),
        },
    }
    return _json_artifact(f"{name}.json", "dataset", "data", value)


def _linked_service_artifact(draft: SpecDraft, kind: str) -> GeneratedArtifact:
    ls = draft.source_linked_service if kind == "source" else draft.target_linked_service
    value = {
        "name": ls.name,
        "type": "Microsoft.DataFactory/factories/linkedServices",
        "properties": {
            "description": f"{kind.capitalize()} connection ({ls.connector_type}) for pipeline {draft.name}.",
            **build_linked_service_properties(ls),
        },
    }
    # `ls.name` already carries the `LS_` convention prefix (set by
    # `defaultLinkedServiceDraft` in frontend/lib/adf/connections.ts, which is where
    # every `SpecDraft` this function receives originates) — the file name must
    # match the declared `name` property exactly, or ADF's Git-integration import
    # sees a mismatch between the resource name and its file.
    return _json_artifact(f"{ls.name}.json", "linked_service", "connection", value)


def build_credential_artifact(name: str, resource_id: str) -> GeneratedArtifact:
    """§32.5/§32.7 — a `Microsoft.DataFactory/factories/credentials` resource for a
    user-assigned managed identity. Layer ``"connection"``, same as a linked service, so
    manifest ordering (§23.9) only needs credential artifacts appended before the linked
    service artifacts that reference them (stable sort preserves that order within a layer)."""
    value = {
        "name": name,
        "type": "Microsoft.DataFactory/factories/credentials",
        "properties": {
            "type": "ManagedIdentity",
            "typeProperties": {"resourceId": resource_id},
        },
    }
    return _json_artifact(f"{name}.json", "credential", "connection", value)


def _credential_artifacts_for(draft: SpecDraft) -> list[GeneratedArtifact]:
    """One credential artifact per distinct user-assigned-identity linked service on this
    draft (source/target may reference the same credential — emit it once)."""
    seen: dict[str, GeneratedArtifact] = {}
    for ls in (draft.source_linked_service, draft.target_linked_service):
        if ls.identity_kind == "user" and ls.credential_name.strip():
            seen.setdefault(ls.credential_name.strip(), build_credential_artifact(ls.credential_name.strip(), ls.credential_resource_id.strip()))
    return list(seen.values())


# ---------------------------------------------------------------------------
# Control table SQL / trigger / parameters
# ---------------------------------------------------------------------------


def _control_table_sql(draft: SpecDraft) -> str:
    objects = effective_source_objects(draft)
    default_watermark = draft.watermark_columns[0].name if draft.watermark_columns else "WatermarkValue"
    loading_behavior = _loading_behavior_of(draft)
    escaped_query = (draft.source_query or "").replace("'", "''").strip()
    # The pipeline's own Lookup ("ReadObjectsFromControlTable" in _master_pipeline)
    # and its ControlTable/WatermarkTable parameters read from exactly this
    # reference (draft.watermark_table) — the DDL below must create/seed the
    # *same* table, or a project/team that customizes it away from the default
    # gets a pipeline that queries a table this script never creates.
    control_table = draft.watermark_table.strip() or "control.adf_control_table"
    per_object_mappings = per_object_column_mappings_active(draft)
    lines: list[str] = [
        "-- Metadata / control table for the metadata-driven Copy Data pattern.",
        "-- One row per object to copy. Add, disable, or re-configure objects here",
        "-- without ever rebuilding the pipeline.",
        "",
        *_create_table_if_missing(control_table),
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
        *(
            ["  TaskGroup         NVARCHAR(128)  NULL,                        -- master Switch dispatch key (NULL = default child)"]
            if child_dispatch_cases(draft)
            else []
        ),
        *(
            ["  ColumnMapping     NVARCHAR(MAX)  NULL,                        -- per-object Copy translator JSON"]
            if per_object_mappings
            else []
        ),
        "  LastRunTimestamp  DATETIME2      NULL,",
        "  CreatedAt         DATETIME2      NOT NULL DEFAULT GETDATE()",
        ");",
        "",
        f"-- Seed rows for the pipeline created in this wizard ({draft.name}).",
    ]
    # RUN-15: a database target's Target is its schema.table, not the (file) target path.
    table_target = _target_table_name(draft)
    # Several objects into one lake folder would overwrite/mix each other's files: each gets its own subfolder.
    per_object_folder = table_target is None and len(objects) > 1
    for entry in objects:
        behavior = entry.loading_behavior or loading_behavior
        if table_target is not None:
            target_value = table_target
        elif per_object_folder:
            folder = re.sub(r"[^a-z0-9_]", "_", _split_object_name(entry.object, draft.source_type)[1].lower())
            target_value = f"{re.sub(r'/+$', '', draft.target_path)}/{folder}/"
        else:
            target_value = draft.target_path
        # A FullLoad row reads no watermark — never seed the form's default column name for it.
        # (Empty, not NULL: the master passes it into the child's String parameter either way.)
        watermark = (entry.watermark_column or "").strip() or ("" if behavior == "FullLoad" else default_watermark)
        lines.extend(
            [
                # RUN-15: re-running the script must not duplicate a row (same Source -> Target).
                f"IF NOT EXISTS (SELECT 1 FROM {control_table} WHERE Source = N'{entry.object.replace(chr(39), chr(39) * 2)}' "
                f"AND Target = N'{target_value}')",
                f"INSERT INTO {control_table} (",
                "  SourceType, Source, SourceQuery, TargetType, Target, TargetFileName,",
                "  LoadingBehavior, WatermarkColumn, WatermarkType, WatermarkValue, Enabled"
                + (", ColumnMapping" if per_object_mappings else ""),
                ") VALUES (",
                f"  N'{draft.source_type}', N'{entry.object.replace(chr(39), chr(39) * 2)}', N'{escaped_query}',",
                f"  N'{draft.target_type}', N'{target_value}', N'{draft.target_file_name or ''}',",
                f"  N'{behavior}', N'{watermark}', N'{draft.watermark_type}', "
                f"N'{draft.watermark_initial_value or '1900-01-01'}', 1"
                + (
                    f", N'{_object_translator_json(draft, entry).replace(chr(39), chr(39) * 2)}'"
                    if per_object_mappings
                    else ""
                ),
                ");",
                "",
            ]
        )
    control_schema = _control_schema_of(draft)
    if draft.enable_copy_log:
        # Standard execution logging (sample-guide parity gap 2) — the one shared
        # on-call/audit surface every generated pipeline's LogRunStart/Succeeded/
        # Failed activities (see _child_pipeline) write rows into.
        lines.extend(
            [
                "-- Shared execution-log control table. Every generated pipeline writes one row per run here",
                "-- (LogRunStart on start, then LogRunSucceeded/LogRunFailed on completion) so run history,",
                "-- row counts, and failures are queryable in one place instead of only in ADF's own run history.",
                *_create_table_if_missing(f"{control_schema}.adf_execution_log"),
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
            ]
        )
    if draft.chunking_enabled and (draft.chunk_column or "").strip():
        # Chunked historical/initial-load control table (sample-guide parity gap 4).
        # Persisted per-chunk status is what makes a chunked load restartable — a
        # re-run's LookupPendingChunks (see _child_pipeline) only reads PENDING/FAILED
        # rows, so a network blip mid-extract doesn't restart the whole object.
        lines.extend(
            [
                "-- Chunk-plan control table for the chunked historical/initial-load strategy.",
                "-- GenerateChunkPlan seeds one row per chunk (idempotent, per TaskId); ForEachChunk",
                "-- updates Status per chunk so a re-run only retries PENDING/FAILED chunks.",
                *_create_table_if_missing(f"{control_schema}.adf_chunk_plan"),
                "  TaskId         INT            NOT NULL,",
                "  ChunkId        INT            NOT NULL,",
                "  RangeStart     NVARCHAR(128)  NOT NULL,",
                f"  RangeEnd       NVARCHAR(128)  NOT NULL,   -- bounds on {draft.chunk_column.strip()}",
                "  Status         NVARCHAR(16)   NOT NULL DEFAULT 'PENDING', -- PENDING | RUNNING | SUCCEEDED | FAILED",
                "  RowsCopied     BIGINT         NULL,",
                "  StartedAt      DATETIME2      NULL,",
                "  CompletedAt    DATETIME2      NULL,",
                "  ErrorMessage   NVARCHAR(MAX)  NULL,",
                "  PRIMARY KEY (TaskId, ChunkId)",
                ");",
                "",
            ]
        )
    if draft.connection_control_enabled:
        lines.extend(_connection_control_sql(draft))
    return "\n".join(lines)


def _create_table_if_missing(table: str) -> list[str]:
    """T-SQL has no ``CREATE TABLE IF NOT EXISTS`` (Postgres/MySQL syntax) — guard with ``OBJECT_ID``.

    Parity twin of ``createTableIfMissing`` in ``frontend/lib/adf/pipeline-codegen.ts``.
    """
    escaped = table.replace("'", "''")
    return [f"IF OBJECT_ID(N'{escaped}', N'U') IS NULL", f"CREATE TABLE {table} ("]


def _sql_str_or_null(value: str | None) -> str:
    """SQL literal, or NULL when blank — avoids inserting an empty string for an optional column."""
    v = (value or "").strip()
    return f"N'{v.replace(chr(39), chr(39) * 2)}'" if v else "NULL"


def _connection_control_sql(draft: SpecDraft) -> list[str]:
    """Connection control table (AGENTS.md §26.5/§26.6, docs/DDL.md §2.1): one
    row per distinct linked service this pipeline references, decoupling its
    connector/Key Vault/property detail from the pipeline JSON so ops can
    repoint a connection per environment/team by editing this table instead of
    regenerating artifacts. Scoped to source + target — staging/rejects/
    trigger-storage slots are plain linked-service-name strings on SpecDraft
    with no connector-type/auth metadata attached, so they aren't represented.
    """
    conn_table = draft.connection_control_table.strip() or "control.adf_connection_control"
    rows: dict[str, LinkedServiceDraft] = {}
    for ls in (draft.source_linked_service, draft.target_linked_service):
        if ls.name.strip():
            rows[ls.name] = ls
    lines: list[str] = [
        "-- Connection control table: one row per linked service this pipeline uses,",
        "-- decoupling its connector/Key Vault/property detail from the pipeline JSON",
        "-- so ops can repoint a connection per environment without regenerating artifacts.",
        *_create_table_if_missing(conn_table),
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
    ]
    for name, ls in rows.items():
        field_defs = visible_fields(connector_schema(ls.connector_type), ls.auth_type)
        secret_key = next((f.key for f in field_defs if f.is_secret and (ls.fields.get(f.key) or "").strip()), None)
        secret_ref = ls.fields.get(secret_key) if secret_key else None
        non_secret = {k: v for k, v in sorted(ls.fields.items()) if k != secret_key and (v or "").strip()}
        lines.extend(
            [
                f"IF NOT EXISTS (SELECT 1 FROM {conn_table} WHERE ConnectionName = N'{name.replace(chr(39), chr(39) * 2)}')",
                f"INSERT INTO {conn_table} (",
                "  ConnectionName, ConnectorType, LinkedServiceName, KeyVaultName, SecretRef, Properties, Enabled",
                ") VALUES (",
                f"  N'{name.replace(chr(39), chr(39) * 2)}', N'{ls.connector_type}', "
                f"N'{ls.name.replace(chr(39), chr(39) * 2)}', "
                f"{_sql_str_or_null(ls.key_vault_linked_service)}, {_sql_str_or_null(secret_ref)}, "
                f"{_sql_str_or_null(json.dumps(non_secret))}, 1",
                ");",
                "",
            ]
        )
    return lines


def _registry_control_table_sql(draft: SpecDraft, kind: str, layer_prefixes: dict[str, str] | None = None) -> str:
    """§33.7 — direct port of pipeline-codegen.ts's registryControlTableSql.
    Generated **only** when this pipeline's medallion_layer matches `kind`
    **and** the matching silver_object_ref/mart_object_ref is set (built from
    a picked registry object, not a manually-authored Silver/Gold pipeline).
    Sibling to _control_table_sql/adf_control_table.sql, not an extension of
    it — see that function's own docstring for why. Full DDL: docs/DDL.md §2.3.
    """
    object_key = (draft.silver_object_ref if kind == "curated" else draft.mart_object_ref) or ""
    table = "control.adf_curated_control_table" if kind == "curated" else "control.adf_mart_control_table"
    target_table = layer_target_table_name(
        draft.medallion_layer, entity=draft.curated_entity, domain=draft.mart_domain, layer_prefixes=layer_prefixes
    )
    tag = draft.registry_tag or ""
    source_keys = "|".join(draft.registry_source_object_keys or [])
    transform_count = len(draft.transformations)
    dq_count = len(draft.data_quality_rules)
    loading_behavior = _loading_behavior_of(draft)
    esc = lambda v: v.replace(chr(39), chr(39) * 2)  # noqa: E731

    if kind == "curated":
        return "\n".join(
            [
                "-- Curated (Silver) control table — auto-captured on publish from the registry object this",
                "-- pipeline was built from (AGENTS.md §33.7). Sibling to adf_control_table.sql, not an extension of it.",
                *_create_table_if_missing(table),
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
                f"IF NOT EXISTS (SELECT 1 FROM {table} WHERE ObjectKey = N'{esc(object_key)}')",
                f"INSERT INTO {table} (",
                "  ObjectKey, Tag, CuratedEntity, TargetTable, SourceObjectKeys, TransformationCount,",
                "  DataQualityRuleCount, WriteBehavior, LoadingBehavior, Enabled",
                ") VALUES (",
                f"  N'{esc(object_key)}', N'{esc(tag)}', N'{esc(draft.curated_entity or '')}',",
                f"  N'{target_table}', {_sql_str_or_null(source_keys)}, {transform_count},",
                f"  {dq_count}, {_sql_str_or_null(draft.write_behavior)}, {_sql_str_or_null(loading_behavior)}, 1",
                ");",
                "",
            ]
        )
    return "\n".join(
        [
            "-- Mart (Gold) control table — auto-captured on publish from the registry object this pipeline",
            "-- was built from (AGENTS.md §33.7). Sibling to adf_control_table.sql, not an extension of it.",
            *_create_table_if_missing(table),
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
            f"IF NOT EXISTS (SELECT 1 FROM {table} WHERE ObjectKey = N'{esc(object_key)}')",
            f"INSERT INTO {table} (",
            "  ObjectKey, Tag, MartDomain, CuratedEntity, TargetTable, SourceCuratedEntities,",
            "  TransformationCount, DataQualityRuleCount, WriteBehavior, LoadingBehavior, Enabled",
            ") VALUES (",
            f"  N'{esc(object_key)}', N'{esc(tag)}', N'{esc(draft.mart_domain or '')}',",
            f"  N'{esc(draft.curated_entity or '')}', N'{target_table}', {_sql_str_or_null(source_keys)},",
            f"  {transform_count}, {dq_count}, {_sql_str_or_null(draft.write_behavior)}, {_sql_str_or_null(loading_behavior)}, 1",
            ");",
            "",
        ]
    )


def _curated_control_table_sql(draft: SpecDraft, layer_prefixes: dict[str, str] | None = None) -> str | None:
    """``None`` unless this is a Silver pipeline built from a picked ``app.silver_objects`` row."""
    if draft.medallion_layer != "silver" or not draft.silver_object_ref:
        return None
    return _registry_control_table_sql(draft, "curated", layer_prefixes)


def _mart_control_table_sql(draft: SpecDraft, layer_prefixes: dict[str, str] | None = None) -> str | None:
    """``None`` unless this is a Gold pipeline built from a picked ``app.mart_objects`` row."""
    if draft.medallion_layer != "gold" or not draft.mart_object_ref:
        return None
    return _registry_control_table_sql(draft, "mart", layer_prefixes)


def _schedule_recurrence(draft: SpecDraft) -> tuple[str, str, int, dict[str, Any]]:
    start_time = (draft.trigger_start_time or "").strip() or "2026-01-01T00:00:00Z"
    time_zone = (draft.trigger_time_zone or "").strip() or "UTC"
    interval = draft.trigger_interval if draft.trigger_interval > 0 else 1
    pipeline_name = resolve_trigger_pipeline_name(draft)
    pipeline_reference = {
        "pipelineReference": {"referenceName": pipeline_name, "type": "PipelineReference"},
        "parameters": {},
    }
    return start_time, time_zone, interval, pipeline_reference


def _fallback_schedule_trigger_artifact(draft: SpecDraft) -> dict[str, Any]:
    """File-arrival trigger safety net (sample-guide parity gap 5): both sample
    guides pair every storage-event trigger with a schedule trigger fallback in
    case an event is missed, rather than relying solely on eventing."""
    start_time, time_zone, interval, pipeline_reference = _schedule_recurrence(draft)
    return {
        "name": f"TR_{draft.name}_fallback",
        "type": "Microsoft.DataFactory/factories/triggers",
        "properties": {
            "annotations": ["fallback-for-missed-file-arrival-events"],
            "runtimeState": "Started" if draft.trigger_enabled else "Stopped",
            "type": "ScheduleTrigger",
            "typeProperties": {
                "recurrence": {"frequency": draft.trigger_frequency, "interval": interval, "startTime": start_time, "timeZone": time_zone},
            },
            "pipelines": [pipeline_reference],
        },
    }


def _trigger_artifact(draft: SpecDraft) -> dict[str, Any]:
    start_time, time_zone, interval, pipeline_reference = _schedule_recurrence(draft)
    if draft.trigger_type == "blobEvents":
        # File-arrival / storage-event trigger (sample-guide parity gap 5) — both
        # guides trigger every file-based source the moment a file lands, instead
        # of only ever polling on a fixed clock.
        container = (draft.trigger_container_name or "").strip() or "landing"
        begins_with = (draft.trigger_blob_path_begins_with or "").strip()
        ends_with = (draft.trigger_blob_path_ends_with or "").strip()
        return {
            "name": f"TR_{draft.name}",
            "type": "Microsoft.DataFactory/factories/triggers",
            "properties": {
                "annotations": [f"storageLinkedService:{(draft.trigger_storage_linked_service or '').strip()}"],
                "runtimeState": "Started" if draft.trigger_enabled else "Stopped",
                "type": "BlobEventsTrigger",
                "typeProperties": {
                    "blobPathBeginsWith": f"/{container}/blobs/{begins_with}",
                    **({"blobPathEndsWith": ends_with} if ends_with else {}),
                    "ignoreEmptyBlobs": True,
                    # Filled in per-factory at deploy time (§20 scope boundary, same
                    # convention as globalParameters.json's <FACTORY_NAME> placeholder).
                    "scope": "<STORAGE_ACCOUNT_RESOURCE_ID>",
                    "events": ["Microsoft.Storage.BlobCreated"],
                },
                "pipelines": [pipeline_reference],
            },
        }
    if draft.trigger_type == "tumbling":
        return {
            "name": f"TR_{draft.name}",
            "type": "Microsoft.DataFactory/factories/triggers",
            "properties": {
                "annotations": [],
                "runtimeState": "Started" if draft.trigger_enabled else "Stopped",
                "type": "TumblingWindowTrigger",
                "typeProperties": {
                    "frequency": "Minute" if draft.trigger_frequency == "Minute" else "Hour",
                    "interval": interval,
                    "startTime": start_time,
                    "delay": "00:00:00",
                    "maxConcurrency": draft.parallel_copies,
                    "retryPolicy": {"count": draft.retry_count, "intervalInSeconds": draft.retry_interval_in_seconds},
                },
                "pipeline": pipeline_reference,
            },
        }
    return {
        "name": f"TR_{draft.name}",
        "type": "Microsoft.DataFactory/factories/triggers",
        "properties": {
            "annotations": [],
            "runtimeState": "Started" if draft.trigger_enabled else "Stopped",
            "type": "ScheduleTrigger",
            "typeProperties": {
                "recurrence": {
                    "frequency": draft.trigger_frequency,
                    "interval": interval,
                    "startTime": start_time,
                    "timeZone": time_zone,
                },
            },
            "pipelines": [pipeline_reference],
        },
    }


def _arm_parameter_definition() -> dict[str, Any]:
    """ADF custom parameterization template (``arm-template-parameters-definition.json``).

    Port of ``armParameterDefinition`` in ``frontend/lib/adf/pipeline-codegen.ts`` — static,
    draft-independent, opt-in via ``generate_arm_parameter_definition``. A Git artifact only; the
    customer's own release pipeline consumes it (§20 scope boundary).
    """
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
    }


def _factory_artifact(draft: SpecDraft, environment: Any = None) -> dict[str, Any]:
    """ADF global-parameters artifact (§24.11 ``factory/<factoryName>.json``, gap A-2).

    This app never deploys and, absent a selected ADF instance, does not know
    the real target factory's name (that's owned by the customer's own
    ARM/Bicep pipeline, §20 scope boundary) — so ``name``/``Environment`` stay
    placeholder tokens, not literal resource names, exactly as before.

    When the caller (``worker/app/jobs/runner.py``) resolves
    ``draft.target_environment_name`` against the ``environments`` table
    (multi-instance support) and passes the matched row, this — and only this
    parameter-surface file — is filled in with real values: ``name`` becomes
    the real factory name and the ``Environment`` global parameter becomes
    the real instance name. Nothing else changes: pipeline/dataset/
    linkedService JSON and the trigger's ``<STORAGE_ACCOUNT_RESOURCE_ID>``
    placeholder stay parameterized as always — deliberately "fill parameter
    surfaces only", not a general per-instance resolution of artifacts.
    ``environment`` duck-types ``app.db.models.Environment`` (``.name``/
    ``.factory_name`` attributes) — any object with those two suffices.
    """
    key_vault_linked_service = (
        draft.source_linked_service.key_vault_linked_service
        or draft.target_linked_service.key_vault_linked_service
        or DEFAULT_KEY_VAULT_LINKED_SERVICE
    )
    factory_name = (getattr(environment, "factory_name", "") or "").strip() if environment else ""
    environment_name = (getattr(environment, "name", "") or "").strip() if environment else ""
    return {
        "name": factory_name or "<FACTORY_NAME>",
        "type": "Microsoft.DataFactory/factories",
        "properties": {
            "globalParameters": {
                "Environment": {"type": "String", "value": environment_name or "<ENVIRONMENT_NAME>"},
                "KeyVaultLinkedServiceName": {"type": "String", "value": key_vault_linked_service},
            }
        },
    }


def _parameters_file(draft: SpecDraft, layer_prefixes: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "BatchCount": draft.batch_count,
        "ParallelCopies": draft.parallel_copies,
        "DataIntegrationUnits": draft.data_integration_units if draft.data_integration_units is not None else 2,
        "EnableStaging": draft.enable_staging,
        "StagingLinkedService": draft.staging_linked_service,
        "StagingPath": draft.staging_path,
        "SourcePartitionOption": draft.source_partition_option,
        "SourcePartitionColumn": draft.source_partition_column or "",
        "QueryTimeout": draft.query_timeout,
        "SinkFileFormat": draft.sink_file_format,
        "CopyBehavior": draft.copy_behavior,
        "WriteBehavior": draft.write_behavior,
        "UpsertKeyColumns": draft.upsert_key_columns,
        "TriggerEnabled": draft.trigger_enabled,
        "TriggerType": draft.trigger_type,
        "TriggerFrequency": draft.trigger_frequency,
        "TriggerInterval": draft.trigger_interval,
        "RetryCount": draft.retry_count,
        "RetryIntervalInSeconds": draft.retry_interval_in_seconds,
        "RedirectIncompatibleRows": draft.redirect_incompatible_rows,
        "LogLevel": draft.log_level,
        "EnableAzureMonitor": draft.enable_azure_monitor,
        "EnableCopyLog": draft.enable_copy_log,
        "RowCountValidationEnabled": draft.row_count_validation_enabled,
        "NotifyOnSuccess": draft.notify_on_success,
        "NotifyOnFailure": draft.notify_on_failure,
        "NotificationEmails": list(draft.notification_emails),
        "WatermarkTable": draft.watermark_table or "control.adf_control_table",
        "WatermarkInitialValue": draft.watermark_initial_value or "1900-01-01",
        "RejectsLinkedService": draft.rejects_linked_service,
        "RejectsPath": draft.rejects_path,
        "ChunkingEnabled": draft.chunking_enabled,
        "ChunkColumn": draft.chunk_column or "",
        "ChunkSizeRows": draft.chunk_size_rows,
        "MaxParallelChunks": draft.max_parallel_chunks,
        "TriggerStorageLinkedService": draft.trigger_storage_linked_service or "",
        "TriggerContainerName": draft.trigger_container_name or "",
        "TriggerBlobPathBeginsWith": draft.trigger_blob_path_begins_with or "",
        "TriggerBlobPathEndsWith": draft.trigger_blob_path_ends_with or "",
        "TriggerFallbackSchedule": draft.trigger_fallback_schedule,
        "MedallionLayer": draft.medallion_layer,
        "LayerTargetTable": (
            ""
            if draft.medallion_layer == "bronze"
            else layer_target_table_name(
                draft.medallion_layer, entity=draft.curated_entity, domain=draft.mart_domain, layer_prefixes=layer_prefixes
            )
        ),
    }


def _lineage_document(draft: SpecDraft, layer_prefixes: dict[str, str] | None = None) -> dict[str, Any]:
    """Static source -> target lineage for this pipeline (opt-in via ``generate_lineage``).

    Port of ``lineageDocument`` in ``frontend/lib/adf/pipeline-codegen.ts``. Uses OpenLineage's
    job/dataset naming (``namespace`` + ``name``) so a catalog (Purview, Marquez, ...) can ingest it,
    but carries no run facets or ``eventTime`` — the generator stays a pure function of the draft.
    Every value comes from the draft; nothing is fabricated.
    """
    source_ns = f"{draft.source_type}://{draft.source_linked_service.name}"
    target_ns = f"{draft.target_type}://{draft.target_linked_service.name}"
    outputs: list[dict[str, str]] = []
    if draft.target_path.strip():
        outputs.append({"namespace": target_ns, "name": draft.target_path})
    if draft.medallion_layer in ("silver", "gold"):
        outputs.append(
            {
                "namespace": target_ns,
                "name": layer_target_table_name(
                    draft.medallion_layer,
                    entity=draft.curated_entity,
                    domain=draft.mart_domain,
                    layer_prefixes=layer_prefixes,
                ),
            }
        )
    return {
        "lineageVersion": "1.0",
        "producer": "adf-accelerator",
        "job": {"namespace": "adf", "name": resolve_trigger_pipeline_name(draft)},
        "medallionLayer": draft.medallion_layer,
        "inputs": [{"namespace": source_ns, "name": entry.object} for entry in effective_source_objects(draft)],
        "outputs": outputs,
    }


def _with_layer_landing(draft: SpecDraft, layer_prefixes: dict[str, str] | None) -> SpecDraft:
    """Silver/Gold on a database target land each object in the Bronze landed table the transformation
    chain reads (``raw.<source>_<object>``), so the Copy sink, the control-table ``Target`` and the
    full-load truncate all point there — the target dataset's own schema/table (default ``dbo.data``) is
    never read by the chain. Bronze and file targets are unchanged. Parity: ``withLayerLanding``."""
    if (draft.medallion_layer or "bronze") == "bronze" or connector_schema(draft.target_type).kind != "database":
        return draft
    _, table = _split_object_name(draft.source_object, draft.source_type)
    landed = layer_target_table_name(
        "bronze", source_type=bronze_source_token(draft), object_name=table, layer_prefixes=layer_prefixes
    )
    schema, _, name = landed.partition(".")
    return replace(draft, target_dataset=replace(draft.target_dataset, schema=schema, table=name))


def build_generated_artifacts(
    draft: SpecDraft, environment: Any = None, layer_prefixes: dict[str, str] | None = None
) -> list[GeneratedArtifact]:
    """Generate the complete ADF artifact set, ordered by §23.9 apply layer.

    ``environment`` is the resolved ``app.db.models.Environment`` row matching
    ``draft.target_environment_name`` (multi-instance support), or ``None``
    when no ADF instance was selected. It only affects ``globalParameters.json``
    (see ``_factory_artifact``); every other artifact is byte-identical either way.

    ``layer_prefixes`` is the project's Standards-configured custom zone
    prefix per medallion layer (``NamingConvention.layerPrefixes``, §25.3) —
    omitted/``None`` keeps today's raw/curated/mart default. It only affects
    Silver/Gold artifacts that reference a ``layer_target_table_name()``-computed
    table name; every Bronze artifact is byte-identical either way.
    """
    draft = _with_layer_landing(draft, layer_prefixes)
    name = draft.name
    pattern = draft.pattern or "master_child"

    # §24.12 "child_only"/"master_only": just the one pipeline + nothing else — it references an
    # already-existing master/child (and its LS/DS/control table) by name, so regenerating those would
    # either duplicate or silently diverge from the artifact the user is attaching to.
    if pattern == "child_only":
        return [
            _json_artifact(f"{resolve_child_pipeline_name(draft)}.json", "pipeline", "pipeline", _child_pipeline(draft, layer_prefixes))
        ]
    if pattern == "master_only":
        return [
            _json_artifact(f"{resolve_master_pipeline_name(draft)}.json", "pipeline", "pipeline", _master_pipeline(draft))
        ]

    # §24.6 "use existing, don't regenerate" — omit the LS/DS artifact for a slot whose connection
    # is marked reused; the pipeline's own LinkedServiceReference/DatasetReference (built from
    # draft.source*/target* unconditionally, elsewhere in this module) is unaffected either way.
    artifacts: list[GeneratedArtifact] = [*_credential_artifacts_for(draft)]
    if not draft.source_connection_reused:
        artifacts.append(_linked_service_artifact(draft, "source"))
        artifacts.append(_dataset_artifact(draft, "source"))
    if not draft.target_connection_reused:
        artifacts.append(_linked_service_artifact(draft, "target"))
        artifacts.append(_dataset_artifact(draft, "target"))
    if pattern == "single_pipeline":
        artifacts.append(
            _json_artifact(f"{resolve_pipeline_name(draft)}.json", "pipeline", "pipeline", _single_pipeline(draft, layer_prefixes))
        )
    elif pattern == "independent_full":
        artifacts.append(
            _json_artifact(f"{resolve_pipeline_name(draft)}.json", "pipeline", "pipeline", _independent_pipeline(draft, "full", layer_prefixes))
        )
    elif pattern == "independent_watermark":
        artifacts.append(
            _json_artifact(f"{resolve_pipeline_name(draft)}.json", "pipeline", "pipeline", _independent_pipeline(draft, "delta", layer_prefixes))
        )
    else:
        # master_child — current shape, byte-identical to before this pattern field existed.
        artifacts.append(_json_artifact(f"{resolve_master_pipeline_name(draft)}.json", "pipeline", "pipeline", _master_pipeline(draft)))
        artifacts.append(
            _json_artifact(f"{resolve_child_pipeline_name(draft)}.json", "pipeline", "pipeline", _child_pipeline(draft, layer_prefixes))
        )

    # Gap #28 — per-build opt-outs (each defaults True, i.e. unchanged output).
    if draft.generate_trigger:
        artifacts.append(_json_artifact(f"TR_{name}.json", "trigger", "runtime", _trigger_artifact(draft)))
    if draft.generate_global_parameters:
        artifacts.append(_json_artifact("globalParameters.json", "factory", "runtime", _factory_artifact(draft, environment)))
    if draft.generate_parameters_file:
        artifacts.append(_json_artifact("parameters.json", "parameter", "runtime", _parameters_file(draft, layer_prefixes)))
    if draft.generate_arm_parameter_definition:
        artifacts.append(
            _json_artifact("arm-template-parameters-definition.json", "parameter", "runtime", _arm_parameter_definition())
        )
    if draft.generate_lineage:
        artifacts.append(_json_artifact("lineage.json", "parameter", "metadata", _lineage_document(draft, layer_prefixes)))

    # "Independent" patterns have no multi-object control table by definition (§24.12); the watermark
    # variant gets its own single-row watermark table instead.
    if pattern == "independent_watermark":
        artifacts.append(_text_artifact("adf_watermark_table.sql", "parameter", "metadata", _watermark_table_sql(draft)))
    elif pattern != "independent_full":
        artifacts.append(_text_artifact("adf_control_table.sql", "parameter", "metadata", _control_table_sql(draft)))

    # File-arrival trigger safety net (sample-guide parity gap 5) — an extra trigger artifact,
    # only emitted when both a blob-events trigger and its fallback are actually configured.
    if draft.generate_trigger and draft.trigger_type == "blobEvents" and draft.trigger_fallback_schedule:
        artifacts.append(
            _json_artifact(f"TR_{name}_fallback.json", "trigger", "runtime", _fallback_schedule_trigger_artifact(draft))
        )
    # §33.7 — additive sibling control tables, only when built from a picked registry object.
    curated_sql = _curated_control_table_sql(draft, layer_prefixes)
    if curated_sql:
        artifacts.append(_text_artifact("adf_curated_control_table.sql", "parameter", "metadata", curated_sql))
    mart_sql = _mart_control_table_sql(draft, layer_prefixes)
    if mart_sql:
        artifacts.append(_text_artifact("adf_mart_control_table.sql", "parameter", "metadata", mart_sql))
    schemas_sql = _layer_schemas_sql(draft, layer_prefixes)
    if schemas_sql:
        artifacts.append(_text_artifact("adf_layer_schemas.sql", "parameter", "metadata", schemas_sql))
    return artifacts


def _layer_schemas_sql(draft: SpecDraft, layer_prefixes: dict[str, str] | None = None) -> str | None:
    """RUN-09 slice: the schemas a Silver/Gold chain reads and writes (Bronze landed table, staging,
    layer target), guarded so the script is rerunnable. The tables themselves are not created.
    Port of layerSchemasSql."""
    if draft.medallion_layer == "bronze" or not any(r.name.strip() for r in draft.transformations[:MAX_TRANSFORMATION_RULES]):
        return None
    _, table = _split_object_name(draft.source_object, draft.source_type)
    tables = [
        layer_target_table_name("bronze", source_type=bronze_source_token(draft), object_name=table, layer_prefixes=layer_prefixes),
        "stg.",
        layer_target_table_name(
            draft.medallion_layer, source_type=bronze_source_token(draft), object_name=table,
            entity=draft.curated_entity, domain=draft.mart_domain, layer_prefixes=layer_prefixes,
        ),
    ]
    schemas = list(dict.fromkeys(t.split(".")[0] for t in tables if "." in t))
    lines = ["-- Schemas the transformation chain reads and writes. Run once on the target store before the first run.", ""]
    lines += [f"IF SCHEMA_ID(N'{s}') IS NULL EXEC(N'CREATE SCHEMA [{s}]');" for s in schemas]
    return "\n".join(lines) + "\n"
