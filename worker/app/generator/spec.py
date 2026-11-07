"""``SpecDraft`` -> ``LogicalPipelineSpec`` transform + template registry.

Direct port of ``frontend/lib/adf/spec.ts``. ``SpecDraft`` is the wizard form
state; :func:`build_spec` is the pure transform to the cross-layer
``LogicalPipelineSpec`` (schemaVersion "2.1", AGENTS.md §23.3 — bump this
string, `frontend/types/pipeline.ts`, and `frontend/lib/adf/spec.ts` together
whenever a field is added to or removed from the spec, §23.13). The generator
(:mod:`app.generator.codegen`) consumes the draft/spec, never component state
(§16).

Field names are snake_case in Python; :meth:`SpecDraft.from_dict` accepts the
camelCase payload the frontend/API sends, and :func:`build_spec` emits the
camelCase ``LogicalPipelineSpec`` JSON shape from ``frontend/types/index.ts``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.connectors import (
    DatasetDefaults,
    DatasetDraft,
    LinkedServiceDraft,
    build_dataset_properties,
    build_linked_service_properties,
    dataset_name,
    default_dataset_draft,
    default_linked_service_draft,
    is_file_connector,
    linked_service_adf_type,
    validate_dataset_draft,
    validate_linked_service_draft,
)
from app.generator.qualifier import (
    QualifierEntryInput,
    build_safe_qualifier_token,
    check_qualifier_collision,
    check_sap_extraction_support,
    get_qualifier_profile,
    resolve_qualified_name,
)
from app.generator.medallion import (
    TransformationRule,
    validate_medallion_fields,
    validate_transformation_rules,
)

PipelineTemplateId = Literal[
    "full_load",
    "incremental_load",
    "metadata_driven_load",
    "initial_load",
    "composite_watermark_load",
]

# UI sink-format choice -> LogicalPipelineSpec formatSettings.type.
SINK_FORMAT_MAP: dict[str, str] = {
    "DelimitedText": "TextFormat",
    "Parquet": "ParquetFormat",
    "Json": "JsonFormat",
    "Avro": "AvroFormat",
    "Orc": "OrcFormat",
}

PIPELINE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


@dataclass(frozen=True)
class PipelineTemplate:
    id: str
    name: str
    version: str
    description: str
    supported_source_types: tuple[str, ...]
    supported_target_types: tuple[str, ...]
    fields: tuple[str, ...]
    defaults: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "supportedSourceTypes": list(self.supported_source_types),
            "supportedTargetTypes": list(self.supported_target_types),
            "fields": list(self.fields),
            "defaults": dict(self.defaults),
        }


_RELATIONAL_AND_FILE = (
    "azure_blob_storage",
    "azure_data_lake_storage_gen2",
    "azure_sql_database",
    "sql_server",
    "oracle",
    "postgresql",
    "mysql",
    "azure_synapse_analytics",
)
_TARGET_TYPES = (
    "azure_blob_storage",
    "azure_data_lake_storage_gen2",
    "azure_sql_database",
    "azure_synapse_analytics",
    "sql_server",
)

TEMPLATES: dict[str, PipelineTemplate] = {
    "full_load": PipelineTemplate(
        id="full_load",
        name="Full Load",
        version="2.0.0",
        description=(
            "Reliable replacement loads. Truncate-and-load or full snapshot of a source "
            "object. All settings are metadata-driven."
        ),
        supported_source_types=(*_RELATIONAL_AND_FILE, "snowflake"),
        supported_target_types=_TARGET_TYPES,
        fields=(
            "source.type", "source.object", "target.type", "target.path",
            "loadType.mode", "loadType.truncateTarget", "loadType.preCopyScript", "loadType.postCopyScript",
            "logging.enableCopyLog", "logging.logLevel", "logging.customLogColumns",
            "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
            "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
            "customMetadata",
        ),
        defaults={"sourceType": "sql_server", "targetType": "azure_data_lake_storage_gen2"},
    ),
    "incremental_load": PipelineTemplate(
        id="incremental_load",
        name="Incremental Load",
        version="2.0.0",
        description=(
            "Watermark-based delta ingestion. Only rows changed since the last run are "
            "moved. Supports single watermark column."
        ),
        supported_source_types=_RELATIONAL_AND_FILE,
        supported_target_types=_TARGET_TYPES,
        fields=(
            "source.type", "source.object", "source.filterCondition", "target.type", "target.path",
            "watermark.enabled", "watermark.type", "watermark.columns", "watermark.watermarkTable", "watermark.initialValue",
            "loadType.mode", "logging.enableCopyLog", "logging.customLogColumns",
            "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
            "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
            "customMetadata",
        ),
        defaults={"sourceType": "sql_server", "targetType": "azure_data_lake_storage_gen2"},
    ),
    "initial_load": PipelineTemplate(
        id="initial_load",
        name="Initial Load",
        version="2.0.0",
        description=(
            "First-time load with initial values. Seeds target with initial watermark "
            "values for subsequent incremental loads."
        ),
        supported_source_types=_RELATIONAL_AND_FILE,
        supported_target_types=_TARGET_TYPES,
        fields=(
            "source.type", "source.object", "source.filterCondition", "target.type", "target.path",
            "watermark.enabled", "watermark.type", "watermark.columns", "watermark.watermarkTable", "watermark.initialValue",
            "loadType.mode", "loadType.initialLoad", "logging.enableCopyLog", "logging.customLogColumns",
            "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
            "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
            "customMetadata",
        ),
        defaults={"sourceType": "sql_server", "targetType": "azure_data_lake_storage_gen2"},
    ),
    "composite_watermark_load": PipelineTemplate(
        id="composite_watermark_load",
        name="Composite Watermark Load",
        version="2.0.0",
        description=(
            "Multiple watermark columns (up to 5) for complex CDC scenarios. Supports "
            "multi-column watermark logic for accurate delta detection."
        ),
        supported_source_types=_RELATIONAL_AND_FILE,
        supported_target_types=_TARGET_TYPES,
        fields=(
            "source.type", "source.object", "source.filterCondition", "target.type", "target.path",
            "watermark.enabled", "watermark.type", "watermark.columns", "watermark.watermarkTable",
            "watermark.initialValue", "watermark.filterCondition",
            "loadType.mode", "logging.enableCopyLog", "logging.customLogColumns",
            "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
            "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
            "validation.rowCountValidation", "validation.dataQualityRules",
            "customMetadata",
        ),
        defaults={"sourceType": "sql_server", "targetType": "azure_data_lake_storage_gen2"},
    ),
    "metadata_driven_load": PipelineTemplate(
        id="metadata_driven_load",
        name="Metadata-Driven Load",
        version="2.0.0",
        description=(
            "Configuration-table-driven scale-out. Master/child pipeline architecture for "
            "multi-table automation with parallel execution."
        ),
        supported_source_types=_RELATIONAL_AND_FILE,
        supported_target_types=_TARGET_TYPES,
        fields=(
            "source.type", "source.object", "source.filterCondition", "target.type", "target.path",
            "watermark.enabled", "watermark.type", "watermark.columns", "watermark.watermarkTable", "watermark.initialValue",
            "loadType.mode", "pipeline.masterChildPattern", "pipeline.parallelSettings.isSequential",
            "pipeline.parallelSettings.batchCount",
            "logging.enableCopyLog", "logging.customLogColumns", "logging.enableAzureMonitor",
            "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits", "parallelProcessing.enableStaging",
            "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds", "errorHandling.redirectIncompatibleRows",
            "validation.rowCountValidation", "validation.schemaValidation", "validation.dataQualityRules",
            "notifications.onSuccess", "notifications.onFailure",
            "customMetadata",
        ),
        defaults={"sourceType": "sql_server", "targetType": "azure_data_lake_storage_gen2"},
    ),
}

TEMPLATE_LIST: list[PipelineTemplate] = list(TEMPLATES.values())


def template_by_id(template_id: str) -> PipelineTemplate:
    if template_id not in TEMPLATES:
        raise KeyError(f"Unknown pipeline template '{template_id}'.")
    return TEMPLATES[template_id]


# ---------------------------------------------------------------------------
# SpecDraft — wizard form state
# ---------------------------------------------------------------------------


@dataclass
class WatermarkColumn:
    name: str
    type: str = "datetime"  # datetime | int | bigint | string
    order: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "order": self.order}


@dataclass
class SourceObjectEntry:
    # Bare object/entity/file name, or the legacy full dotted string
    # (schema.table, catalog.schema.table) when catalog/schema below aren't
    # set. Parsed connector-aware via generator/qualifier.py — never split
    # blindly on "." — so 3-part and schema-less sources resolve correctly.
    object: str
    # Optional catalog/database segment — only meaningful for 3-part sources
    # (e.g. Snowflake, SAP HANA with a tenant DB).
    catalog: str | None = None
    # Optional schema segment, kept separate from `object` so schema-less
    # sources (SAP OData, Salesforce, ...) can omit it entirely.
    schema: str | None = None
    # Escape hatch: exact identifier to use when querying the source,
    # bypassing qualifier parsing/case-conformance entirely.
    qualifier_override: str | None = None
    # Escape hatch: skip automatic case-conformance (e.g. Oracle/SAP HANA
    # upper-casing) for this one object.
    case_override: str | None = None  # "as-typed" | None
    watermark_column: str | None = None
    loading_behavior: str | None = None  # FullLoad | DeltaLoad
    # Optional per-object Copy column mappings (control-table ColumnMapping column) — see the TS
    # SourceObjectEntry.columnMappings. Honored by master_child/single_pipeline only.
    column_mappings: list[ColumnMapping] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"object": self.object}
        if self.catalog:
            out["catalog"] = self.catalog
        if self.schema:
            out["schema"] = self.schema
        if self.qualifier_override:
            out["qualifierOverride"] = self.qualifier_override
        if self.case_override:
            out["caseOverride"] = self.case_override
        if self.watermark_column:
            out["watermarkColumn"] = self.watermark_column
        if self.loading_behavior:
            out["loadingBehavior"] = self.loading_behavior
        if self.column_mappings:
            out["columnMappings"] = [{"source": m.source, "sink": m.sink} for m in self.column_mappings]
        return out


@dataclass
class NamedValue:
    name: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "value": self.value}


@dataclass
class ColumnMapping:
    """One explicit Copy column mapping (schema-drift slice) — see the TS ColumnMapping."""

    source: str
    sink: str


@dataclass
class ChildDispatchCase:
    """One Switch case of the master's child dispatch (gap #30) — see the TS ChildDispatchCase."""

    match_value: str
    child_pipeline_name: str


@dataclass
class DataQualityRule:
    name: str
    column: str
    rule: str
    # Rule-specific threshold/pattern: max length (LENGTH_LE), "min,max" (RANGE_BETWEEN), or a
    # pattern (REGEX_MATCH). Unused by IS_NOT_NULL/UNIQUE.
    value: str = ""
    # "warn" (default) logs the violation count in ADF run history; "block" fails the pipeline
    # run via a generated IfCondition/Fail gate (AGENTS.md §30.4).
    enforcement: str = "warn"
    # DMT-05: optional pass-rate tolerance for a row-level rule — the check breaches only when more
    # than this percentage (0-100) of the checked rows violate it. Blank = any violating row breaches.
    max_violation_pct: str = ""

    def to_dict(self) -> dict[str, str]:
        out = {"name": self.name, "column": self.column, "rule": self.rule, "value": self.value, "enforcement": self.enforcement}
        if self.max_violation_pct.strip():
            out["maxViolationPct"] = self.max_violation_pct.strip()
        return out


def _str(data: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        if data.get(key) is not None:
            return str(data[key])
    return default


def _opt_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if data.get(key) not in (None, ""):
            return str(data[key])
    return None


def _bool(data: dict[str, Any], key: str, default: bool = False) -> bool:
    return bool(data.get(key, default))


def _int(data: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(data[key])
    except (KeyError, TypeError, ValueError):
        return default


def _opt_int(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if data.get(key) not in (None, ""):
            try:
                return int(data[key])
            except (TypeError, ValueError):
                return None
    return None


# §24.3 pipeline pattern — independent of load behavior/template. "master_child" (default) is today's
# only-ever-emitted shape; direct port of spec.ts's PipelinePattern (§23.12 gap #5, §24.12):
#   - single_pipeline        one pipeline loops the control table inline (no child, no ExecutePipeline)
#   - independent_full       one source, one target, single Copy — no control table
#   - independent_watermark  one source, one target, watermark delta — its own watermark table
#   - child_only             just the child pipeline (add an object to an existing master)
#   - master_only            just the master pipeline, referencing an existing child by name
PipelinePattern = Literal[
    "master_child", "single_pipeline", "independent_full", "independent_watermark", "child_only", "master_only",
]


@dataclass
class SpecDraft:
    name: str = ""
    # §24.3 pipeline pattern; defaults to "master_child" — today's only behavior, unchanged.
    pattern: str = "master_child"
    # Independent overrides for the generated master/child pipeline artifact names (§24.5) — direct
    # port of spec.ts's masterPipelineName/childPipelineName. Falls back to
    # PL_{name}_master/PL_{name}_child when unset (see resolve_master_pipeline_name/
    # resolve_child_pipeline_name in codegen.py). `name` stays the pipeline record's own identity.
    # Used by master_child/child_only/master_only.
    master_pipeline_name: str | None = None
    child_pipeline_name: str | None = None
    # Optional source-system label (gap #29) — port of spec.ts's sourceSystem. When set, defaults
    # become pl_master / pl_<source_system>_child; unset = unchanged.
    source_system: str | None = None
    # Copy activity policy.timeout — port of spec.ts's copyTimeout; unset = COPY_TIMEOUT_DEFAULT.
    copy_timeout: str | None = None
    # gaps.md SQL-05: the user's approximate source row count (optional, never guessed) — only
    # drives the Full Load size warning in validate_draft.
    estimated_source_rows: int | None = None
    # Override for the single generated pipeline's artifact name under the single_pipeline/
    # independent_full/independent_watermark patterns — falls back to PL_{name} when unset (see
    # resolve_pipeline_name). Unused by master_child/child_only/master_only.
    pipeline_name: str | None = None
    project_id: str = ""
    template_id: str = "incremental_load"
    source_type: str = "sql_server"
    source_object: str = "dbo.customer"
    source_query: str | None = None
    source_filter_condition: str | None = None
    target_type: str = "azure_data_lake_storage_gen2"
    target_path: str = "/customer/"
    target_file_name: str | None = None
    source_partition_option: str = "None"  # None | DynamicRange | PhysicalPartitionsOfTable
    source_partition_column: str | None = None
    source_isolation_level: str = ""
    query_timeout: str = "02:00:00"
    sink_file_format: str = "Parquet"  # DelimitedText | Parquet | Json | Avro | Orc
    copy_behavior: str = "PreserveHierarchy"  # PreserveHierarchy | FlattenHierarchy | MergeFiles
    max_rows_per_file: int | None = None
    write_behavior: str = "Insert"  # Insert | Upsert
    upsert_key_columns: str = ""
    layer_key_columns: str = ""  # RUN-14, see layerKeyColumns
    write_batch_size: int | None = None
    source_object_mode: str = "form"  # form | file
    source_objects: list[SourceObjectEntry] = field(default_factory=list)
    source_linked_service: LinkedServiceDraft = field(
        default_factory=lambda: default_linked_service_draft("source", "sql_server")
    )
    target_linked_service: LinkedServiceDraft = field(
        default_factory=lambda: default_linked_service_draft("target", "azure_data_lake_storage_gen2")
    )
    source_dataset: DatasetDraft = field(default_factory=lambda: default_dataset_draft("source", "sql_server"))
    target_dataset: DatasetDraft = field(
        default_factory=lambda: default_dataset_draft("target", "azure_data_lake_storage_gen2")
    )
    # §24.6 "use existing, don't regenerate" — see the TS SpecDraft's identical fields for the
    # full contract. Defaults to False (unchanged behavior: always generate).
    source_connection_reused: bool = False
    target_connection_reused: bool = False
    # Gap #28 — per-build opt-outs for the runtime artifacts (default True = unchanged output).
    generate_trigger: bool = True
    generate_global_parameters: bool = True
    generate_parameters_file: bool = True
    # Opt-in (default False) ADF custom parameterization template — see the TS SpecDraft field.
    generate_arm_parameter_definition: bool = False
    generate_lineage: bool = False
    # Schema-drift slice — opt-in Copy TabularTranslator with type conversion (see the TS field).
    copy_type_conversion: bool = False
    # Schema-drift slice — explicit per-column Copy mappings (see the TS SpecDraft field).
    column_mappings: list[ColumnMapping] = field(default_factory=list)
    # Gap #33 — ADF annotations on every generated pipeline (see the TS SpecDraft field).
    annotations: list[str] = field(default_factory=list)
    # Gap #30 — Switch-based dispatch across several children (see the TS SpecDraft field).
    child_dispatch: list[ChildDispatchCase] = field(default_factory=list)
    # Hard-delete handling on the DeltaLoad branch (see the TS SpecDraft field): none | soft_delete | cdc.
    delete_handling: str = "none"
    soft_delete_column: str = ""
    cdc_capture_instance: str = ""
    watermark_enabled: bool = True
    watermark_type: str = "single"  # single | composite
    watermark_columns: list[WatermarkColumn] = field(
        default_factory=lambda: [WatermarkColumn("modified_date", "datetime", 1)]
    )
    watermark_table: str = "control.watermarks"
    watermark_initial_value: str = "1900-01-01"
    watermark_filter_condition: str | None = None
    # Connection control table (AGENTS.md §26.5/§26.6, docs/DDL.md §2.1): opt-in
    # extra artifact that decouples each linked service this pipeline uses from
    # a hardcoded reference in the pipeline JSON, so ops can repoint a
    # connection per environment/team by editing this table instead of
    # regenerating artifacts. Off by default — purely additive.
    connection_control_enabled: bool = False
    connection_control_table: str = "control.adf_connection_control"
    load_type_mode: str = "incremental"  # full | incremental | initial | composite_watermark
    initial_load: bool = False
    full_load: bool = False
    truncate_target: bool = False
    pre_copy_script: str | None = None
    post_copy_script: str | None = None
    master_child_pattern: bool = False
    is_sequential: bool = False
    batch_count: int = 20
    enable_copy_log: bool = True
    log_level: str = "Info"  # Info | Warning | Error | Verbose | None
    enable_azure_monitor: bool = True
    custom_log_columns: list[NamedValue] = field(
        default_factory=lambda: [
            NamedValue("PipelineRunId", "@pipeline().RunId"),
            NamedValue("ExecutionDate", "@utcnow()"),
        ]
    )
    parallel_copies: int = 10
    data_integration_units: int | None = None
    enable_staging: bool = False
    staging_linked_service: str = ""
    staging_path: str = "staging/"
    staging_enable_compression: bool = False
    retry_count: int = 3
    retry_interval_in_seconds: int = 30
    redirect_incompatible_rows: bool = False
    # Rejects destination for redirected incompatible rows (ADF requires this
    # or skipped rows are just discarded, not landed anywhere inspectable).
    rejects_linked_service: str = ""
    rejects_path: str = "rejects/"
    # Chunked historical/initial load: bounded-range fan-out with persisted
    # per-chunk restart status, distinct from parallelCopies/partitionOption.
    chunking_enabled: bool = False
    chunk_column: str | None = None
    chunk_size_rows: int = 1000000
    max_parallel_chunks: int = 4
    row_count_validation_enabled: bool = False
    data_quality_rules: list[DataQualityRule] = field(default_factory=list)
    notify_on_success: bool = False
    notify_on_failure: bool = False
    notification_emails: list[str] = field(default_factory=list)
    trigger_enabled: bool = False
    trigger_type: str = "schedule"  # schedule | tumbling | blobEvents
    trigger_frequency: str = "Day"  # Minute | Hour | Day | Week | Month
    trigger_interval: int = 1
    trigger_start_time: str = ""
    trigger_time_zone: str = "UTC"
    # File-arrival trigger settings (BlobEventsTrigger).
    trigger_storage_linked_service: str | None = None
    trigger_container_name: str | None = None
    trigger_blob_path_begins_with: str | None = None
    trigger_blob_path_ends_with: str | None = None
    trigger_fallback_schedule: bool = True
    custom_metadata: list[NamedValue] = field(default_factory=list)
    # Which registered ADF instance (``Environment.name``) this draft targets —
    # optional, mirrors frontend/lib/adf/spec.ts's ``targetEnvironmentName``.
    # Resolved against the ``environments`` table by the caller (jobs/runner.py);
    # codegen never looks it up itself, only consumes the resolved connection.
    target_environment_name: str | None = None
    # Medallion layer (Bronze/Silver/Gold) — direct port of spec.ts's
    # medallionLayer/curatedEntity/martDomain/transformations. "bronze" is the
    # default and matches today's behavior exactly (plain copy, no
    # transformations) — see app/generator/medallion.py.
    medallion_layer: str = "bronze"
    curated_entity: str | None = None
    mart_domain: str | None = None
    transformations: list[TransformationRule] = field(default_factory=list)
    # §33.7/§33.8: which app.silver_objects/app.mart_objects row (object_key)
    # this draft's curatedEntity/martDomain/transformations/dataQualityRules
    # were prefilled from, when picked via Build's registry dropdown — only
    # one of the two is ever set. Direct port of spec.ts's
    # silverObjectRef/martObjectRef. Gates the two additive
    # curatedControlTableSql/martControlTableSql artifacts (see codegen.py).
    silver_object_ref: str | None = None
    mart_object_ref: str | None = None
    # The picked registry object's tag/source_object_keys at selection time —
    # needed by _curated_control_table_sql/_mart_control_table_sql to
    # populate the auto-captured control table's Tag/SourceObjectKeys columns
    # while keeping the generator a pure function of SpecDraft. Direct port
    # of spec.ts's registryTag/registrySourceObjectKeys.
    registry_tag: str | None = None
    registry_source_object_keys: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpecDraft":
        """Build a draft from the camelCase payload the frontend/API sends."""
        d = data or {}
        source_type = _str(d, "sourceType", "source_type", default="sql_server")
        target_type = _str(d, "targetType", "target_type", default="azure_data_lake_storage_gen2")

        def _named_values(key: str) -> list[NamedValue]:
            return [NamedValue(str(r.get("name", "")), str(r.get("value", ""))) for r in (d.get(key) or [])]

        return cls(
            name=_str(d, "name"),
            pattern=_str(d, "pattern", default="master_child"),
            master_pipeline_name=_opt_str(d, "masterPipelineName", "master_pipeline_name"),
            child_pipeline_name=_opt_str(d, "childPipelineName", "child_pipeline_name"),
            source_system=_opt_str(d, "sourceSystem", "source_system"),
            copy_timeout=_opt_str(d, "copyTimeout", "copy_timeout"),
            estimated_source_rows=_opt_int(d, "estimatedSourceRows", "estimated_source_rows"),
            pipeline_name=_opt_str(d, "pipelineName", "pipeline_name"),
            project_id=_str(d, "projectId", "project_id"),
            template_id=_str(d, "templateId", "template_id", default="incremental_load"),
            source_type=source_type,
            source_object=_str(d, "sourceObject", "source_object", default="dbo.customer"),
            source_query=_opt_str(d, "sourceQuery", "source_query"),
            source_filter_condition=_opt_str(d, "sourceFilterCondition", "source_filter_condition"),
            target_type=target_type,
            target_path=_str(d, "targetPath", "target_path", default="/customer/"),
            target_file_name=_opt_str(d, "targetFileName", "target_file_name"),
            source_partition_option=_str(d, "sourcePartitionOption", "source_partition_option", default="None"),
            source_partition_column=_opt_str(d, "sourcePartitionColumn", "source_partition_column"),
            source_isolation_level=_str(d, "sourceIsolationLevel", "source_isolation_level"),
            query_timeout=_str(d, "queryTimeout", "query_timeout", default="02:00:00"),
            sink_file_format=_str(d, "sinkFileFormat", "sink_file_format", default="Parquet"),
            copy_behavior=_str(d, "copyBehavior", "copy_behavior", default="PreserveHierarchy"),
            max_rows_per_file=_opt_int(d, "maxRowsPerFile", "max_rows_per_file"),
            write_behavior=_str(d, "writeBehavior", "write_behavior", default="Insert"),
            upsert_key_columns=_str(d, "upsertKeyColumns", "upsert_key_columns"),
            layer_key_columns=_str(d, "layerKeyColumns", "layer_key_columns"),
            write_batch_size=_opt_int(d, "writeBatchSize", "write_batch_size"),
            source_object_mode=_str(d, "sourceObjectMode", "source_object_mode", default="form"),
            source_objects=[
                SourceObjectEntry(
                    object=str(r.get("object", "")),
                    catalog=(r.get("catalog")) or None,
                    schema=(r.get("schema")) or None,
                    qualifier_override=(r.get("qualifierOverride") or r.get("qualifier_override")) or None,
                    case_override=(r.get("caseOverride") or r.get("case_override")) or None,
                    watermark_column=(r.get("watermarkColumn") or r.get("watermark_column")) or None,
                    loading_behavior=(r.get("loadingBehavior") or r.get("loading_behavior")) or None,
                    column_mappings=[
                        ColumnMapping(str(m.get("source", "") or ""), str(m.get("sink", "") or ""))
                        for m in (r.get("columnMappings") or r.get("column_mappings") or [])
                    ],
                )
                for r in (d.get("sourceObjects") or d.get("source_objects") or [])
            ],
            source_linked_service=LinkedServiceDraft.from_dict(
                d.get("sourceLinkedService") or d.get("source_linked_service") or {}
            )
            if (d.get("sourceLinkedService") or d.get("source_linked_service"))
            else default_linked_service_draft("source", source_type),
            target_linked_service=LinkedServiceDraft.from_dict(
                d.get("targetLinkedService") or d.get("target_linked_service") or {}
            )
            if (d.get("targetLinkedService") or d.get("target_linked_service"))
            else default_linked_service_draft("target", target_type),
            source_dataset=DatasetDraft.from_dict(d.get("sourceDataset") or d.get("source_dataset") or {}),
            target_dataset=DatasetDraft.from_dict(d.get("targetDataset") or d.get("target_dataset") or {}),
            source_connection_reused=_bool(d, "sourceConnectionReused") or _bool(d, "source_connection_reused"),
            target_connection_reused=_bool(d, "targetConnectionReused") or _bool(d, "target_connection_reused"),
            generate_trigger=_bool(d, "generateTrigger", True) if "generateTrigger" in d else _bool(d, "generate_trigger", True),
            generate_global_parameters=_bool(d, "generateGlobalParameters", True)
            if "generateGlobalParameters" in d
            else _bool(d, "generate_global_parameters", True),
            generate_parameters_file=_bool(d, "generateParametersFile", True)
            if "generateParametersFile" in d
            else _bool(d, "generate_parameters_file", True),
            generate_arm_parameter_definition=_bool(d, "generateArmParameterDefinition")
            or _bool(d, "generate_arm_parameter_definition"),
            generate_lineage=_bool(d, "generateLineage") or _bool(d, "generate_lineage"),
            copy_type_conversion=_bool(d, "copyTypeConversion") or _bool(d, "copy_type_conversion"),
            annotations=[str(a) for a in (d.get("annotations") or [])],
            column_mappings=[
                ColumnMapping(str(m.get("source", "") or ""), str(m.get("sink", "") or ""))
                for m in (d.get("columnMappings") or d.get("column_mappings") or [])
            ],
            child_dispatch=[
                ChildDispatchCase(
                    str(c.get("matchValue", c.get("match_value", "")) or ""),
                    str(c.get("childPipelineName", c.get("child_pipeline_name", "")) or ""),
                )
                for c in (d.get("childDispatch") or d.get("child_dispatch") or [])
            ],
            delete_handling=_str(d, "deleteHandling", "delete_handling", default="none") or "none",
            soft_delete_column=_str(d, "softDeleteColumn", "soft_delete_column"),
            cdc_capture_instance=_str(d, "cdcCaptureInstance", "cdc_capture_instance"),
            watermark_enabled=_bool(d, "watermarkEnabled", True) if "watermarkEnabled" in d else _bool(d, "watermark_enabled", True),
            watermark_type=_str(d, "watermarkType", "watermark_type", default="single"),
            watermark_columns=[
                WatermarkColumn(
                    name=str(c.get("name", "")),
                    type=str(c.get("type", "datetime")),
                    order=int(c.get("order", i + 1)),
                )
                for i, c in enumerate(d.get("watermarkColumns") or d.get("watermark_columns") or [])
            ]
            or [WatermarkColumn("modified_date", "datetime", 1)],
            watermark_table=_str(d, "watermarkTable", "watermark_table", default="control.watermarks"),
            watermark_initial_value=_str(d, "watermarkInitialValue", "watermark_initial_value", default="1900-01-01"),
            watermark_filter_condition=_opt_str(d, "watermarkFilterCondition", "watermark_filter_condition"),
            connection_control_enabled=_bool(d, "connectionControlEnabled") or _bool(d, "connection_control_enabled"),
            connection_control_table=_str(
                d, "connectionControlTable", "connection_control_table", default="control.adf_connection_control"
            ),
            load_type_mode=_str(d, "loadTypeMode", "load_type_mode", default="incremental"),
            initial_load=_bool(d, "initialLoad") or _bool(d, "initial_load"),
            full_load=_bool(d, "fullLoad") or _bool(d, "full_load"),
            truncate_target=_bool(d, "truncateTarget") or _bool(d, "truncate_target"),
            pre_copy_script=_opt_str(d, "preCopyScript", "pre_copy_script"),
            post_copy_script=_opt_str(d, "postCopyScript", "post_copy_script"),
            master_child_pattern=_bool(d, "masterChildPattern") or _bool(d, "master_child_pattern"),
            is_sequential=_bool(d, "isSequential") or _bool(d, "is_sequential"),
            batch_count=_int(d, "batchCount", _int(d, "batch_count", 20)),
            enable_copy_log=_bool(d, "enableCopyLog", True) if "enableCopyLog" in d else _bool(d, "enable_copy_log", True),
            log_level=_str(d, "logLevel", "log_level", default="Info"),
            enable_azure_monitor=_bool(d, "enableAzureMonitor", True)
            if "enableAzureMonitor" in d
            else _bool(d, "enable_azure_monitor", True),
            custom_log_columns=_named_values("customLogColumns")
            or _named_values("custom_log_columns")
            or [NamedValue("PipelineRunId", "@pipeline().RunId"), NamedValue("ExecutionDate", "@utcnow()")],
            parallel_copies=_int(d, "parallelCopies", _int(d, "parallel_copies", 10)),
            data_integration_units=_opt_int(d, "dataIntegrationUnits", "data_integration_units"),
            enable_staging=_bool(d, "enableStaging") or _bool(d, "enable_staging"),
            staging_linked_service=_str(d, "stagingLinkedService", "staging_linked_service"),
            staging_path=_str(d, "stagingPath", "staging_path", default="staging/"),
            staging_enable_compression=_bool(d, "stagingEnableCompression") or _bool(d, "staging_enable_compression"),
            retry_count=_int(d, "retryCount", _int(d, "retry_count", 3)),
            retry_interval_in_seconds=_int(d, "retryIntervalInSeconds", _int(d, "retry_interval_in_seconds", 30)),
            redirect_incompatible_rows=_bool(d, "redirectIncompatibleRows") or _bool(d, "redirect_incompatible_rows"),
            rejects_linked_service=_str(d, "rejectsLinkedService", "rejects_linked_service"),
            rejects_path=_str(d, "rejectsPath", "rejects_path", default="rejects/"),
            chunking_enabled=_bool(d, "chunkingEnabled") or _bool(d, "chunking_enabled"),
            chunk_column=_opt_str(d, "chunkColumn", "chunk_column"),
            chunk_size_rows=_int(d, "chunkSizeRows", _int(d, "chunk_size_rows", 1000000)),
            max_parallel_chunks=_int(d, "maxParallelChunks", _int(d, "max_parallel_chunks", 4)),
            row_count_validation_enabled=_bool(d, "rowCountValidationEnabled")
            or _bool(d, "row_count_validation_enabled"),
            data_quality_rules=[
                DataQualityRule(
                    str(r.get("name", "")),
                    str(r.get("column", "")),
                    str(r.get("rule", "")),
                    str(r.get("value", "")),
                    str(r.get("enforcement") or "warn"),
                    str(r.get("maxViolationPct") or r.get("max_violation_pct") or ""),
                )
                for r in (d.get("dataQualityRules") or d.get("data_quality_rules") or [])
            ],
            notify_on_success=_bool(d, "notifyOnSuccess") or _bool(d, "notify_on_success"),
            notify_on_failure=_bool(d, "notifyOnFailure") or _bool(d, "notify_on_failure"),
            notification_emails=[str(x) for x in (d.get("notificationEmails") or d.get("notification_emails") or [])],
            trigger_enabled=_bool(d, "triggerEnabled") or _bool(d, "trigger_enabled"),
            trigger_type=_str(d, "triggerType", "trigger_type", default="schedule"),
            trigger_frequency=_str(d, "triggerFrequency", "trigger_frequency", default="Day"),
            trigger_interval=_int(d, "triggerInterval", _int(d, "trigger_interval", 1)),
            trigger_start_time=_str(d, "triggerStartTime", "trigger_start_time"),
            trigger_time_zone=_str(d, "triggerTimeZone", "trigger_time_zone", default="UTC"),
            trigger_storage_linked_service=_opt_str(d, "triggerStorageLinkedService", "trigger_storage_linked_service"),
            trigger_container_name=_opt_str(d, "triggerContainerName", "trigger_container_name"),
            trigger_blob_path_begins_with=_opt_str(d, "triggerBlobPathBeginsWith", "trigger_blob_path_begins_with"),
            trigger_blob_path_ends_with=_opt_str(d, "triggerBlobPathEndsWith", "trigger_blob_path_ends_with"),
            trigger_fallback_schedule=(
                _bool(d, "triggerFallbackSchedule", True)
                if "triggerFallbackSchedule" in d
                else _bool(d, "trigger_fallback_schedule", True)
            ),
            custom_metadata=_named_values("customMetadata") or _named_values("custom_metadata"),
            target_environment_name=_opt_str(d, "targetEnvironmentName", "target_environment_name"),
            medallion_layer=_str(d, "medallionLayer", "medallion_layer", default="bronze"),
            curated_entity=_opt_str(d, "curatedEntity", "curated_entity"),
            mart_domain=_opt_str(d, "martDomain", "mart_domain"),
            transformations=[
                TransformationRule.from_dict(r) for r in (d.get("transformations") or [])
            ],
            silver_object_ref=_opt_str(d, "silverObjectRef", "silver_object_ref"),
            mart_object_ref=_opt_str(d, "martObjectRef", "mart_object_ref"),
            registry_tag=_opt_str(d, "registryTag", "registry_tag"),
            registry_source_object_keys=[
                str(k) for k in (d.get("registrySourceObjectKeys") or d.get("registry_source_object_keys") or [])
            ],
        )


def empty_spec_draft() -> SpecDraft:
    """A fresh draft with the same defaults as ``emptySpecDraft()`` in spec.ts."""
    return SpecDraft(
        source_objects=[SourceObjectEntry("dbo.customer")],
        source_linked_service=default_linked_service_draft("source", "sql_server"),
        target_linked_service=default_linked_service_draft("target", "azure_data_lake_storage_gen2"),
        source_dataset=default_dataset_draft("source", "sql_server"),
        target_dataset=default_dataset_draft("target", "azure_data_lake_storage_gen2"),
    )


_PL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,259}$")


COPY_TIMEOUT_DEFAULT = "0.02:00:00.000"
ADF_TIMESPAN_RE = re.compile(r"^\d{1,2}\.\d{2}:\d{2}:\d{2}(\.\d{1,3})?$")


def resolve_copy_timeout(draft: SpecDraft) -> str:
    """Direct port of spec.ts's ``resolveCopyTimeout``."""
    return (draft.copy_timeout or "").strip() or COPY_TIMEOUT_DEFAULT


def source_system_token(draft: SpecDraft) -> str:
    """Direct port of spec.ts's ``sourceSystemToken``."""
    return re.sub(r"[^a-z0-9_]", "_", (draft.source_system or "").strip().lower())


def bronze_source_token(draft: SpecDraft) -> str:
    """Direct port of spec.ts's ``bronzeSourceToken`` — source-system label, else connector type."""
    return source_system_token(draft) or draft.source_type


def resolve_master_pipeline_name(draft: SpecDraft) -> str:
    """Direct port of spec.ts's ``resolveMasterPipelineName`` — single source of truth for codegen.py."""
    override = (draft.master_pipeline_name or "").strip()
    return override or ("pl_master" if source_system_token(draft) else f"PL_{draft.name}_master")


def resolve_child_pipeline_name(draft: SpecDraft) -> str:
    """Direct port of spec.ts's ``resolveChildPipelineName``."""
    override = (draft.child_pipeline_name or "").strip()
    token = source_system_token(draft)
    return override or (f"pl_{token}_child" if token else f"PL_{draft.name}_child")


def resolve_pipeline_name(draft: SpecDraft) -> str:
    """Direct port of spec.ts's ``resolvePipelineName`` (single_pipeline/independent_* patterns)."""
    override = (draft.pipeline_name or "").strip()
    return override or f"PL_{draft.name}"


def resolve_trigger_pipeline_name(draft: SpecDraft) -> str:
    """Direct port of spec.ts's ``resolveTriggerPipelineName``."""
    pattern = draft.pattern or "master_child"
    if pattern in ("single_pipeline", "independent_full", "independent_watermark"):
        return resolve_pipeline_name(draft)
    return resolve_master_pipeline_name(draft)


# ---------------------------------------------------------------------------
# Dataset defaults + effective object list
# ---------------------------------------------------------------------------


def complete_column_mappings(mappings: list[ColumnMapping] | None) -> list[ColumnMapping]:
    """Port of spec.ts's completeColumnMappings — trimmed mappings with both sides non-blank."""
    out = [ColumnMapping(m.source.strip(), m.sink.strip()) for m in (mappings or [])]
    return [m for m in out if m.source and m.sink]


def per_object_column_mappings_active(draft: SpecDraft) -> bool:
    """Port of spec.ts's perObjectColumnMappingsActive."""
    if (draft.pattern or "master_child") not in ("master_child", "single_pipeline"):
        return False
    return any(complete_column_mappings(o.column_mappings) for o in effective_source_objects(draft))


def effective_source_objects(draft: SpecDraft) -> list[SourceObjectEntry]:
    rows = [row for row in draft.source_objects if row.object.strip()]
    if rows:
        return rows
    single = draft.source_object.strip()
    return [SourceObjectEntry(single)] if single else []


def source_dataset_defaults(draft: SpecDraft) -> DatasetDefaults:
    raw = draft.source_object.strip() or "source_table"
    profile = get_qualifier_profile(draft.source_type)
    resolved = resolve_qualified_name(QualifierEntryInput(object=raw), profile)
    schema = resolved.schema or "dbo"
    table = resolved.object or "source_table"
    # File-kind connectors (sftp/ftp/blob/s3/sharepoint/...) put the actual file
    # name or wildcard pattern straight into `sourceObject` (the qualifier is a
    # no-op for them, so `table` above is that same raw string) — appending
    # ".csv" unconditionally, as the table-based default below does, doubles
    # up any extension the author already typed (e.g. "invoice_*.csv.csv").
    # Only synthesize a ".csv" default for schema/table-shaped sources, where
    # `table` is a bare object name with no extension of its own.
    file_name = raw.rsplit("/", 1)[-1] if is_file_connector(draft.source_type) else f"{table}.csv"
    return DatasetDefaults(
        schema=schema,
        table=table,
        folder_path=raw,
        file_name=file_name,
        fallback_name=f"DS_{draft.name.strip() or 'pipeline'}_source",
    )


def target_dataset_defaults(draft: SpecDraft) -> DatasetDefaults:
    folder = draft.target_path.strip() or "target"
    file = (draft.target_file_name or "").strip() or "data.csv"
    return DatasetDefaults(
        schema="dbo",
        table=re.sub(r"\.[^.]+$", "", file) or folder,
        folder_path=folder,
        file_name=file,
        fallback_name=f"DS_{draft.name.strip() or 'pipeline'}_target",
    )


# ---------------------------------------------------------------------------
# build_spec — the pure transform
# ---------------------------------------------------------------------------


def _linked_service_config(ls: LinkedServiceDraft) -> dict[str, Any]:
    props = build_linked_service_properties(ls)
    return {
        "name": ls.name,
        "type": linked_service_adf_type(ls.connector_type),
        "connectorType": ls.connector_type,
        "authenticationType": ls.auth_type,
        "connectVia": ls.integration_runtime,
        "keyVaultLinkedService": ls.key_vault_linked_service,
        "parameters": props["parameters"],
        "typeProperties": props["typeProperties"],
    }


def build_spec(draft: SpecDraft) -> dict[str, Any]:
    """Build the stable ``LogicalPipelineSpec`` (schemaVersion "2.1") from form state."""
    objects = effective_source_objects(draft)
    source_defaults = source_dataset_defaults(draft)
    target_defaults = target_dataset_defaults(draft)
    source_ds_props = build_dataset_properties(draft.source_dataset, draft.source_linked_service, source_defaults)
    target_ds_props = build_dataset_properties(draft.target_dataset, draft.target_linked_service, target_defaults)

    spec: dict[str, Any] = {
        "schemaVersion": "2.1",
        "pipeline": {
            "name": draft.name,
            "type": draft.template_id,
            "description": f"{draft.template_id} pipeline for {draft.source_object}",
            "masterChildPattern": draft.master_child_pattern,
            "parallelSettings": {
                "isSequential": draft.is_sequential,
                "batchCount": draft.batch_count,
            },
        },
        "source": {
            "type": draft.source_type,
            "object": objects[0].object if objects else draft.source_object,
            "query": draft.source_query,
            "filterCondition": draft.source_filter_condition,
            "partitionOption": draft.source_partition_option,
            "partitionSettings": (
                {"partitionColumnName": draft.source_partition_column.strip()}
                if draft.source_partition_option == "DynamicRange" and (draft.source_partition_column or "").strip()
                else None
            ),
            "isolationLevel": draft.source_isolation_level or None,
            "queryTimeout": draft.query_timeout or None,
            # Standard metadata/lineage columns (sample-guide parity gap 1). A value
            # starting with "@" is emitted as ADF dynamic content downstream.
            "additionalColumns": [c.to_dict() for c in draft.custom_metadata[:10]] or None,
        },
        "target": {
            "type": draft.target_type,
            "path": draft.target_path,
            "fileName": draft.target_file_name,
            "copyBehavior": draft.copy_behavior,
            "formatSettings": {
                "type": SINK_FORMAT_MAP.get(draft.sink_file_format, "ParquetFormat"),
                "maxRowsPerFile": draft.max_rows_per_file,
            },
        },
        "watermark": {
            "enabled": draft.watermark_enabled,
            "type": draft.watermark_type,
            "columns": [c.to_dict() for c in draft.watermark_columns[:5]],
            "watermarkTable": draft.watermark_table or "control.watermarks",
            "watermarkColumn": "watermark_value",
            "initialValue": draft.watermark_initial_value or "1900-01-01",
            "filterCondition": draft.watermark_filter_condition,
        },
        "loadType": {
            "mode": draft.load_type_mode,
            "initialLoad": draft.initial_load,
            "fullLoad": draft.full_load,
            "truncateTarget": draft.truncate_target,
            "preCopyScript": draft.pre_copy_script,
            "postCopyScript": draft.post_copy_script,
        },
        "linkedServices": {
            "source": _linked_service_config(draft.source_linked_service),
            "target": _linked_service_config(draft.target_linked_service),
        },
        "datasets": {
            "source": {
                "name": dataset_name(draft.source_dataset, source_defaults),
                "linkedServiceName": draft.source_linked_service.name,
                "type": source_ds_props.get("type"),
                "parameters": source_ds_props.get("parameters", {}),
                "properties": source_ds_props,
            },
            "target": {
                "name": dataset_name(draft.target_dataset, target_defaults),
                "linkedServiceName": draft.target_linked_service.name,
                "type": target_ds_props.get("type"),
                "parameters": target_ds_props.get("parameters", {}),
                "properties": target_ds_props,
            },
        },
        "sourceObjects": [o.to_dict() for o in objects],
        "logging": {
            "enableCopyLog": draft.enable_copy_log,
            "logLevel": draft.log_level,
            "logStorageSettings": {
                "linkedServiceName": draft.target_linked_service.name,
                "path": "logs/copy/",
                "enableTimestampsInPath": True,
            },
            "enableMetricCapture": True,
            "enableAzureMonitor": draft.enable_azure_monitor,
            "customLogColumns": [c.to_dict() for c in draft.custom_log_columns[:10]],
        },
        "parallelProcessing": {
            "dataIntegrationUnits": draft.data_integration_units,
            "parallelCopies": draft.parallel_copies,
            "retryCount": draft.retry_count,
            "retryIntervalInSeconds": draft.retry_interval_in_seconds,
            "timeout": "7.00:00:00",
            "enableStaging": draft.enable_staging,
            "stagingSettings": (
                {
                    "linkedServiceName": draft.staging_linked_service,
                    "path": draft.staging_path,
                    "enableCompression": draft.staging_enable_compression,
                }
                if draft.enable_staging
                else None
            ),
        },
        "errorHandling": {
            "onFailure": "Stop",
            "skipErrorFile": {
                "fileMissing": True,
                "fileConflict": False,
                "dataInconsistency": True,
            },
            "enableFaultInjection": False,
            "redirectIncompatibleRows": draft.redirect_incompatible_rows,
            # Sample-guide parity gap 6 — enableSkipIncompatibleRow alone just discards
            # rejected rows. ADF also needs a destination to actually land them.
            "redirectRowSettings": (
                {"linkedServiceName": draft.rejects_linked_service.strip(), "path": draft.rejects_path or "rejects/"}
                if draft.redirect_incompatible_rows and draft.rejects_linked_service.strip()
                else None
            ),
        },
        "customMetadata": [c.to_dict() for c in draft.custom_metadata[:10]],
        "validation": {
            "rowCountValidation": (
                {
                    "enabled": True,
                    "sourceQuery": f"SELECT COUNT(*) FROM {draft.source_object}",
                    "targetQuery": f"SELECT COUNT(*) FROM {draft.target_path}",
                    "tolerancePercent": 5,
                }
                if draft.row_count_validation_enabled
                else None
            ),
            "dataQualityRules": [r.to_dict() for r in draft.data_quality_rules],
        },
        "notifications": {
            "onSuccess": (
                {"email": list(draft.notification_emails)}
                if draft.notify_on_success and draft.notification_emails
                else None
            ),
            "onFailure": (
                {"email": list(draft.notification_emails)}
                if draft.notify_on_failure and draft.notification_emails
                else None
            ),
        },
    }
    return spec


# ---------------------------------------------------------------------------
# validate_draft — §17 client-parity pre-validation
# ---------------------------------------------------------------------------


@dataclass
class ValidationIssue:
    severity: str  # error | warning | info
    message: str
    field: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"severity": self.severity, "message": self.message}
        if self.field:
            out["field"] = self.field
        if self.source:
            out["source"] = self.source
        return out


def looks_like_regex(pattern: str) -> bool:
    """RUN-17: regex-only syntax LIKE/PATINDEX doesn't have — port of looksLikeRegex in spec.ts."""
    return re.search(r"^\^|\$$|[+\\(){}|*?]", pattern.strip()) is not None


_DQ_RULE_TYPES = {"IS_NOT_NULL", "UNIQUE", "LENGTH_LE", "RANGE_BETWEEN", "REGEX_MATCH", "ROW_COUNT_MIN", "FRESHNESS_HOURS"}


def dq_rule_needs_column(rule: str) -> bool:
    """Port of dqRuleNeedsColumn — ROW_COUNT_MIN checks the whole object."""
    return rule != "ROW_COUNT_MIN"


SOFT_DELETE_COLUMN_DEFAULT = "_is_deleted"
# Sources with SQL Server change-data-capture (cdc.fn_cdc_get_all_changes_<instance>).
CDC_SOURCE_TYPES = ("sql_server", "azure_sql_database", "azure_sql_managed_instance", "amazon_rds_sql_server")
_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def resolve_soft_delete_column(draft: SpecDraft) -> str:
    return (draft.soft_delete_column or "").strip() or SOFT_DELETE_COLUMN_DEFAULT


def validate_delete_handling(draft: SpecDraft) -> list[ValidationIssue]:
    """Port of validateDeleteHandling (spec.ts); nothing when delete_handling is none."""
    mode = draft.delete_handling or "none"
    if mode == "none":
        return []
    issues: list[ValidationIssue] = []
    field_name = "deleteHandling"
    if not any(k.strip() for k in draft.upsert_key_columns.split(",")):
        issues.append(ValidationIssue("error", "Delete handling needs key column(s) — set the upsert key columns.", "upsertKeyColumns"))
    if not _SQL_IDENTIFIER_RE.match(resolve_soft_delete_column(draft)):
        issues.append(ValidationIssue("error", "Soft-delete column must be a plain SQL identifier.", "softDeleteColumn"))
    if draft.load_type_mode == "full" or draft.full_load:
        issues.append(
            ValidationIssue(
                "warning",
                "Delete handling only runs on the DeltaLoad branch — a full load already replaces the target.",
                field_name,
            )
        )
    if len(effective_source_objects(draft)) > 1:
        issues.append(
            ValidationIssue(
                "warning",
                "Delete handling marks the pipeline's own landed table using pipeline-level keys; with several "
                "source objects every row applies it to that one table.",
                field_name,
            )
        )
    if mode == "cdc":
        if draft.source_type not in CDC_SOURCE_TYPES:
            issues.append(
                ValidationIssue(
                    "error",
                    "CDC delete detection needs a SQL Server-family source (SQL Server, Azure SQL Database/Managed "
                    "Instance, RDS SQL Server).",
                    field_name,
                )
            )
        cols = draft.watermark_columns
        if draft.watermark_type == "composite" or len(cols) != 1 or cols[0].type != "datetime":
            issues.append(
                ValidationIssue(
                    "error",
                    "CDC delete detection maps the watermark window to LSNs — use one datetime watermark column.",
                    "watermarkColumns",
                )
            )
        ci = (draft.cdc_capture_instance or "").strip()
        if ci and not _SQL_IDENTIFIER_RE.match(ci):
            issues.append(ValidationIssue("error", "CDC capture instance must be a plain SQL identifier.", "cdcCaptureInstance"))
    return issues


# DMT-05: rules whose ViolationCount counts rows, so a percentage of the checked rows is meaningful.
DQ_PCT_RULE_TYPES = frozenset({"IS_NOT_NULL", "UNIQUE", "LENGTH_LE", "RANGE_BETWEEN", "REGEX_MATCH"})


def validate_data_quality_rules(rules: list["DataQualityRule"]) -> list[ValidationIssue]:
    """Validates the §30.4 data quality rules — kept in parity with frontend/lib/adf/spec.ts's
    validateDataQualityRules, since both sides gate Generate on the same rule shape."""
    issues: list[ValidationIssue] = []
    if len(rules) > 5:
        issues.append(ValidationIssue("error", "Maximum 5 data quality rules allowed.", "dataQualityRules"))
    for i, rule in enumerate(rules):
        label = rule.name.strip() or f"Rule {i + 1}"
        if not rule.name.strip():
            issues.append(ValidationIssue("error", f"Data quality rule {i + 1} needs a name.", "dataQualityRules"))
        if not rule.column.strip() and dq_rule_needs_column(rule.rule):
            issues.append(ValidationIssue("error", f'Data quality rule "{label}" needs a column.', "dataQualityRules"))
        if rule.rule not in _DQ_RULE_TYPES:
            issues.append(ValidationIssue("error", f'Data quality rule "{label}" has an unknown rule type.', "dataQualityRules"))
        if rule.enforcement and rule.enforcement not in ("warn", "block"):
            issues.append(
                ValidationIssue("error", f'Data quality rule "{label}" enforcement must be "warn" or "block".', "dataQualityRules")
            )
        if rule.rule == "LENGTH_LE" and not re.match(r"^\d+$", rule.value.strip()):
            issues.append(
                ValidationIssue(
                    "error", f'Data quality rule "{label}" (LENGTH_LE) needs a whole-number max length in Value.', "dataQualityRules"
                )
            )
        if rule.rule in ("ROW_COUNT_MIN", "FRESHNESS_HOURS") and not re.match(r"^\d+$", rule.value.strip()):
            what = "minimum row count" if rule.rule == "ROW_COUNT_MIN" else "maximum age in hours"
            issues.append(
                ValidationIssue("error", f'Data quality rule "{label}" ({rule.rule}) needs a whole-number {what} in Value.', "dataQualityRules")
            )
        if rule.rule == "RANGE_BETWEEN":
            parts = [p.strip() for p in rule.value.split(",")]
            min_ok = len(parts) == 2 and all(parts)
            if min_ok:
                try:
                    min_ok = float(parts[0]) <= float(parts[1])
                except ValueError:
                    min_ok = False
            if not min_ok:
                issues.append(
                    ValidationIssue(
                        "error",
                        f'Data quality rule "{label}" (RANGE_BETWEEN) needs "min,max" in Value (e.g. "0,100").',
                        "dataQualityRules",
                    )
                )
        pct = rule.max_violation_pct.strip()
        if pct and rule.rule not in DQ_PCT_RULE_TYPES:
            issues.append(
                ValidationIssue("error", f'Data quality rule "{label}" ({rule.rule}) is a whole-table check; a max violation % applies only to row-level rules.', "dataQualityRules")
            )
        elif pct and not (re.match(r"^\d+(?:\.\d+)?$", pct) and float(pct) <= 100):
            issues.append(
                ValidationIssue("error", f'Data quality rule "{label}" max violation % must be a number from 0 to 100.', "dataQualityRules")
            )
        if rule.rule == "REGEX_MATCH" and not rule.value.strip():
            issues.append(
                ValidationIssue("error", f'Data quality rule "{label}" (REGEX_MATCH) needs a pattern in Value.', "dataQualityRules")
            )
        elif rule.rule == "REGEX_MATCH" and looks_like_regex(rule.value):
            # RUN-17: the check is PATINDEX, so a real regex never matches and every row is a violation.
            issues.append(
                ValidationIssue(
                    "warning",
                    f'Data quality rule "{label}" (REGEX_MATCH) is generated as T-SQL PATINDEX (LIKE wildcards %, _, [ ]), not a regex; "{rule.value.strip()}" looks like a regex and would flag every row. Rewrite it as a LIKE pattern (e.g. %_@_%._%).',
                    "dataQualityRules",
                )
            )
    # Two names that sanitize to the same activity token would emit duplicate DQ_<name>_Check activities (DQ-06).
    seen: dict[str, str] = {}
    for rule in rules:
        name = rule.name.strip()
        if not name:
            continue
        token = re.sub(r"[^A-Za-z0-9_]", "_", name).lower()
        if token in seen:
            issues.append(
                ValidationIssue(
                    "error", f'Data quality rules "{seen[token]}" and "{name}" produce the same activity name — rename one.', "dataQualityRules"
                )
            )
        else:
            seen[token] = name
    return issues


FULL_LOAD_ROW_WARNING = 1_000_000  # gaps.md SQL-05 — the sample guides' Full Load rule of thumb


def validate_draft(draft: SpecDraft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    name = draft.name.strip()
    if not name:
        issues.append(ValidationIssue("error", "A pipeline name is required.", "name"))
    elif not PIPELINE_NAME_RE.match(name):
        issues.append(
            ValidationIssue(
                "error",
                "Pipeline name must start with a lowercase letter and contain only lowercase letters, "
                "digits, and underscores (3–64 characters).",
                "name",
            )
        )
    if not draft.project_id:
        issues.append(ValidationIssue("error", "Select a project.", "projectId"))

    # SQL-05: the sample guides' rule of thumb is Full Load only below ~1M rows; a larger table set
    # to an unchunked Full Load is warned about, never blocked.
    rows = draft.estimated_source_rows
    is_full = draft.load_type_mode == "full" or draft.full_load
    if rows is not None and rows > FULL_LOAD_ROW_WARNING and is_full and not (draft.chunking_enabled and (draft.chunk_column or "").strip()):
        issues.append(
            ValidationIssue(
                "warning",
                f"About {rows:,} source rows on a Full Load: every run re-copies the whole table. Consider a delta "
                f"(watermark) load or chunking for tables over {FULL_LOAD_ROW_WARNING:,} rows.",
                "estimatedSourceRows",
            )
        )

    copy_timeout = (draft.copy_timeout or "").strip()
    if copy_timeout and not ADF_TIMESPAN_RE.match(copy_timeout):
        issues.append(
            ValidationIssue(
                "error", "Copy activity timeout must use ADF's d.hh:mm:ss format (e.g. 0.02:00:00).", "copyTimeout"
            )
        )

    # Master/child artifact-name overrides (§24.5) — optional, but must be ADF-legal when set, and
    # distinct from each other. Direct port of spec.ts's validateDraft.
    master_override = (draft.master_pipeline_name or "").strip()
    child_override = (draft.child_pipeline_name or "").strip()
    if master_override and not _PL_NAME_RE.match(master_override):
        issues.append(
            ValidationIssue(
                "error",
                "Master pipeline name must start with a letter or underscore and contain only letters, "
                "digits, and underscores (max 260 characters).",
                "masterPipelineName",
            )
        )
    if child_override and not _PL_NAME_RE.match(child_override):
        issues.append(
            ValidationIssue(
                "error",
                "Child pipeline name must start with a letter or underscore and contain only letters, "
                "digits, and underscores (max 260 characters).",
                "childPipelineName",
            )
        )
    if master_override and child_override and master_override == child_override:
        issues.append(ValidationIssue("error", "Master and child pipeline names must be different.", "childPipelineName"))
    single_override = (draft.pipeline_name or "").strip()
    if single_override and not _PL_NAME_RE.match(single_override):
        issues.append(
            ValidationIssue(
                "error",
                "Pipeline name must start with a letter or underscore and contain only letters, digits, "
                "and underscores (max 260 characters).",
                "pipelineName",
            )
        )
    # §24.3 pattern-specific requirements — direct port of spec.ts's validateDraft.
    if draft.pattern == "child_only" and not child_override:
        issues.append(
            ValidationIssue(
                "error",
                'Child pipeline name is required for the "child only" pattern (it must match the existing '
                "master's ExecutePipeline reference).",
                "childPipelineName",
            )
        )
    if draft.pattern == "master_only" and not child_override:
        issues.append(
            ValidationIssue(
                "error",
                'Child pipeline name is required for the "master only" pattern (the existing child this '
                "master delegates to).",
                "childPipelineName",
            )
        )

    # Schema-drift slice — explicit column mappings (direct port of spec.ts's validateDraft).
    sinks: set[str] = set()
    for i, m in enumerate(draft.column_mappings):
        source, sink = m.source.strip(), m.sink.strip()
        if not source or not sink:
            issues.append(
                ValidationIssue("error", f"Column mapping {i + 1} needs both a source and a sink column.", "columnMappings")
            )
            continue
        if sink.lower() in sinks:
            issues.append(
                ValidationIssue(
                    "error", f'Column mapping {i + 1}: sink column "{sink}" is mapped more than once.', "columnMappings"
                )
            )
        sinks.add(sink.lower())
    mapped_objects = effective_source_objects(draft)
    for obj in mapped_objects:
        obj_sinks: set[str] = set()
        for i, m in enumerate(obj.column_mappings):
            source, sink = m.source.strip(), m.sink.strip()
            label = f'Object "{obj.object}" column mapping {i + 1}'
            if not source or not sink:
                issues.append(ValidationIssue("error", f"{label} needs both a source and a sink column.", "sourceObjects"))
                continue
            if sink.lower() in obj_sinks:
                issues.append(
                    ValidationIssue("error", f'{label}: sink column "{sink}" is mapped more than once.', "sourceObjects")
                )
            obj_sinks.add(sink.lower())
    objects_with_own = [o for o in mapped_objects if complete_column_mappings(o.column_mappings)]
    if (draft.pattern or "master_child") not in ("master_child", "single_pipeline") and objects_with_own:
        issues.append(
            ValidationIssue(
                "warning",
                "Per-object column mappings are only applied by the master/child and single-pipeline patterns — "
                "they are ignored here.",
                "sourceObjects",
            )
        )
    if (
        draft.column_mappings
        and len(mapped_objects) > 1
        and not (per_object_column_mappings_active(draft) and len(objects_with_own) == len(mapped_objects))
    ):
        issues.append(
            ValidationIssue(
                "warning",
                "Column mappings apply to every source object's Copy — objects without these columns will fail to copy.",
                "columnMappings",
            )
        )

    # Gap #30 — child dispatch cases (direct port of spec.ts's validateDraft).
    if draft.pattern in ("master_child", "master_only"):
        seen: set[str] = set()
        master = resolve_master_pipeline_name(draft)
        for i, c in enumerate(draft.child_dispatch):
            match = c.match_value.strip()
            child = c.child_pipeline_name.strip()
            label = f"Child dispatch case {i + 1}"
            if not match or not child:
                issues.append(
                    ValidationIssue("error", f"{label} needs both a TaskGroup value and a child pipeline name.", "childDispatch")
                )
                continue
            if match in seen:
                issues.append(
                    ValidationIssue(
                        "error", f'{label}: TaskGroup value "{match}" is used by more than one case.', "childDispatch"
                    )
                )
            seen.add(match)
            if not _PL_NAME_RE.match(child):
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{label}: child pipeline name must start with a letter or underscore and contain only "
                        "letters, digits, and underscores (max 260 characters).",
                        "childDispatch",
                    )
                )
            elif child == master:
                issues.append(ValidationIssue("error", f"{label}: a master can't dispatch to itself.", "childDispatch"))

    if draft.template_id not in TEMPLATES:
        issues.append(ValidationIssue("error", f"Unknown template '{draft.template_id}'.", "templateId"))

    objects = effective_source_objects(draft)
    if not objects:
        issues.append(
            ValidationIssue(
                "error",
                "Add at least one source object (table or input file). Upload a list file or fill the form.",
                "sourceObjects",
            )
        )
    if len(objects) > 200:
        issues.append(ValidationIssue("error", "Maximum 200 source objects per pipeline.", "sourceObjects"))

    # Qualifier resolution (naming-convention edge cases): connector-aware,
    # never a blind ".".split on the raw object string (generator/qualifier.py).
    profile = get_qualifier_profile(draft.source_type)
    resolved_objects = [
        (row, resolve_qualified_name(QualifierEntryInput(row.object, row.catalog, row.schema, row.qualifier_override, row.case_override), profile))
        for row in objects
    ]
    # Always-on safety rule (§25.5): two objects that would collide on the
    # same generated target-name token are a correctness bug, not a naming
    # style preference — never silently generate one artifact from two.
    collisions = check_qualifier_collision(
        [(row.object, build_safe_qualifier_token(resolved)) for row, resolved in resolved_objects]
    )
    for collision in collisions:
        issues.append(
            ValidationIssue(
                "error",
                f'These source objects all generate the same artifact name "{collision.token}" once sanitized: '
                f"{', '.join(collision.keys)}. Give them distinct names or an explicit override.",
                "sourceObjects",
            )
        )
    # SAP flag-only check: some objects need a different extraction
    # mechanism entirely (RFC/Open Hub), which no naming fix can solve.
    for row, resolved in resolved_objects:
        sap_issue = check_sap_extraction_support(draft.source_type, resolved.object)
        if sap_issue:
            issues.append(ValidationIssue(sap_issue.level, sap_issue.message, "sourceObjects"))

    for issue in validate_linked_service_draft(draft.source_linked_service, "sourceLinkedService"):
        issues.append(ValidationIssue(issue.severity, issue.message, issue.field))
    for issue in validate_linked_service_draft(draft.target_linked_service, "targetLinkedService"):
        issues.append(ValidationIssue(issue.severity, issue.message, issue.field))
    for issue in validate_dataset_draft(
        draft.source_dataset, draft.source_linked_service, source_dataset_defaults(draft), "sourceDataset"
    ):
        issues.append(ValidationIssue(issue.severity, issue.message, issue.field))
    for issue in validate_dataset_draft(
        draft.target_dataset, draft.target_linked_service, target_dataset_defaults(draft), "targetDataset"
    ):
        issues.append(ValidationIssue(issue.severity, issue.message, issue.field))

    if not draft.target_path.strip():
        issues.append(ValidationIssue("error", "Target path is required.", "targetPath"))

    if draft.watermark_enabled and draft.load_type_mode != "full":
        if not draft.watermark_columns:
            issues.append(
                ValidationIssue("error", "At least one watermark column is required for incremental loads.", "watermarkColumns")
            )
        if len(draft.watermark_columns) > 5:
            issues.append(
                ValidationIssue("error", "Maximum 5 watermark columns allowed for composite watermark.", "watermarkColumns")
            )
        if not draft.watermark_table.strip():
            issues.append(ValidationIssue("error", "Watermark table is required for incremental loads.", "watermarkTable"))

    if len(draft.custom_log_columns) > 10:
        issues.append(ValidationIssue("error", "Maximum 10 custom log columns allowed.", "customLogColumns"))
    if len(draft.custom_metadata) > 10:
        issues.append(ValidationIssue("error", "Maximum 10 custom metadata columns allowed.", "customMetadata"))
    issues.extend(validate_data_quality_rules(draft.data_quality_rules))
    if draft.batch_count < 1 or draft.batch_count > 50:
        issues.append(ValidationIssue("error", "Batch count must be between 1 and 50.", "batchCount"))
    if draft.parallel_copies < 1 or draft.parallel_copies > 50:
        issues.append(ValidationIssue("error", "Parallel copies must be between 1 and 50.", "parallelCopies"))
    if draft.enable_staging and not draft.staging_linked_service.strip():
        issues.append(
            ValidationIssue("error", "A staging linked service is required when staged copy is enabled.", "stagingLinkedService")
        )
    if draft.source_partition_option == "DynamicRange" and not (draft.source_partition_column or "").strip():
        issues.append(
            ValidationIssue("error", "A partition column is required for dynamic-range partitioned reads.", "sourcePartitionColumn")
        )
    if draft.write_behavior == "Upsert" and not draft.upsert_key_columns.strip():
        issues.append(
            ValidationIssue("error", "Upsert key column(s) are required when the write behavior is Upsert.", "upsertKeyColumns")
        )
    issues.extend(validate_delete_handling(draft))
    if draft.trigger_enabled and draft.trigger_type != "blobEvents" and draft.trigger_interval < 1:
        issues.append(ValidationIssue("error", "Trigger interval must be a positive whole number.", "triggerInterval"))
    if draft.trigger_enabled and draft.trigger_type == "blobEvents":
        if not (draft.trigger_storage_linked_service or "").strip():
            issues.append(
                ValidationIssue("error", "A storage linked service is required for a file-arrival trigger.", "triggerStorageLinkedService")
            )
        if not (draft.trigger_container_name or "").strip():
            issues.append(
                ValidationIssue("error", "A container name is required for a file-arrival trigger.", "triggerContainerName")
            )
    if draft.redirect_incompatible_rows and not draft.rejects_linked_service.strip():
        issues.append(
            ValidationIssue(
                "error", "A rejects destination linked service is required when redirecting incompatible rows.", "rejectsLinkedService"
            )
        )
    if draft.chunking_enabled:
        if not (draft.chunk_column or "").strip():
            issues.append(
                ValidationIssue(
                    "error", "A chunk column (numeric or date range) is required when chunking is enabled.", "chunkColumn"
                )
            )
        if draft.chunk_size_rows < 1000:
            issues.append(ValidationIssue("error", "Chunk size must be at least 1,000 rows.", "chunkSizeRows"))
        if draft.max_parallel_chunks < 1 or draft.max_parallel_chunks > 50:
            issues.append(ValidationIssue("error", "Max parallel chunks must be between 1 and 50.", "maxParallelChunks"))
    # Medallion layer (Bronze/Silver/Gold) — Silver/Gold need a resolvable
    # target entity/domain (layer-wise table naming standard) and at least
    # one valid transformation; Bronze is a no-op (unchanged existing
    # behavior). Kept in parity with spec.ts's validateDraft.
    for medallion_issue in validate_medallion_fields(draft.medallion_layer, curated_entity=draft.curated_entity, mart_domain=draft.mart_domain):
        issues.append(ValidationIssue(medallion_issue.severity, medallion_issue.message, medallion_issue.field, medallion_issue.source))
    for transform_issue in validate_transformation_rules(draft.medallion_layer, draft.transformations):
        issues.append(ValidationIssue(transform_issue.severity, transform_issue.message, transform_issue.field, transform_issue.source))
    # G2: Silver/Gold transformations run as Script (SQL) activities on the target store.
    if draft.medallion_layer != "bronze" and is_file_connector(draft.target_type):
        issues.append(
            ValidationIssue(
                "warning",
                f"The {draft.medallion_layer} layer runs SQL transformations on the target, so the target must be a SQL-capable store, not a file store.",
                "targetType",
            )
        )
    if draft.connection_control_enabled and not draft.connection_control_table.strip():
        issues.append(
            ValidationIssue("error", "A connection control table name is required when it's enabled.", "connectionControlTable")
        )
    return issues


def field_errors(issues: list[ValidationIssue], field_name: str) -> list[str]:
    return [i.message for i in issues if i.field == field_name]


def child_dispatch_cases(draft: SpecDraft) -> list[ChildDispatchCase]:
    """Gap #30 — trimmed, complete dispatch cases; ``[]`` unless the pattern has a master."""
    if draft.pattern not in ("master_child", "master_only"):
        return []
    cases = [ChildDispatchCase(c.match_value.strip(), c.child_pipeline_name.strip()) for c in draft.child_dispatch]
    return [c for c in cases if c.match_value and c.child_pipeline_name]


def pipeline_annotations(draft: SpecDraft) -> list[str]:
    """Trimmed, de-duplicated pipeline annotations (gap #33) — ``[]`` when none."""
    return list(dict.fromkeys(a.strip() for a in draft.annotations if a.strip()))
