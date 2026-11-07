"""Pipeline template registry (AGENTS.md §6, §23.4b).

Mirrors ``TEMPLATES`` in ``frontend/lib/adf/spec.ts`` and
``worker/app/generator/spec.py`` — same ids, versions, and ``fields`` allow-lists
(the renderer shows only a template's declared fields). Seeded into
``pipeline_templates``; the worker generator is the canonical field owner.
"""
from __future__ import annotations

from typing import Any

_RELATIONAL_AND_FILE = [
    "azure_blob_storage",
    "azure_data_lake_storage_gen2",
    "azure_sql_database",
    "sql_server",
    "oracle",
    "postgresql",
    "mysql",
    "azure_synapse_analytics",
]
_TARGET_TYPES = [
    "azure_blob_storage",
    "azure_data_lake_storage_gen2",
    "azure_sql_database",
    "azure_synapse_analytics",
    "sql_server",
]
_DEFAULTS = {"sourceType": "sql_server", "targetType": "azure_data_lake_storage_gen2"}

TEMPLATE_DOCUMENTS: list[dict[str, Any]] = [
    {
        "id": "full_load",
        "name": "Full Load",
        "version": "2.0.0",
        "description": "Reliable replacement loads. Truncate-and-load or full snapshot of a source object. "
        "All settings are metadata-driven.",
        "supportedSourceTypes": [*_RELATIONAL_AND_FILE, "snowflake"],
        "supportedTargetTypes": _TARGET_TYPES,
        "fields": [
            "source.type", "source.object", "target.type", "target.path",
            "loadType.mode", "loadType.truncateTarget", "loadType.preCopyScript", "loadType.postCopyScript",
            "logging.enableCopyLog", "logging.logLevel", "logging.customLogColumns",
            "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
            "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
            "customMetadata",
        ],
        "defaults": _DEFAULTS,
    },
    {
        "id": "incremental_load",
        "name": "Incremental Load",
        "version": "2.0.0",
        "description": "Watermark-based delta ingestion. Only rows changed since the last run are moved. "
        "Supports single watermark column.",
        "supportedSourceTypes": _RELATIONAL_AND_FILE,
        "supportedTargetTypes": _TARGET_TYPES,
        "fields": [
            "source.type", "source.object", "source.filterCondition", "target.type", "target.path",
            "watermark.enabled", "watermark.type", "watermark.columns", "watermark.watermarkTable", "watermark.initialValue",
            "loadType.mode", "logging.enableCopyLog", "logging.customLogColumns",
            "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
            "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
            "customMetadata",
        ],
        "defaults": _DEFAULTS,
    },
    {
        "id": "initial_load",
        "name": "Initial Load",
        "version": "2.0.0",
        "description": "First-time load with initial values. Seeds target with initial watermark values for "
        "subsequent incremental loads.",
        "supportedSourceTypes": _RELATIONAL_AND_FILE,
        "supportedTargetTypes": _TARGET_TYPES,
        "fields": [
            "source.type", "source.object", "source.filterCondition", "target.type", "target.path",
            "watermark.enabled", "watermark.type", "watermark.columns", "watermark.watermarkTable", "watermark.initialValue",
            "loadType.mode", "loadType.initialLoad", "logging.enableCopyLog", "logging.customLogColumns",
            "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
            "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
            "customMetadata",
        ],
        "defaults": _DEFAULTS,
    },
    {
        "id": "composite_watermark_load",
        "name": "Composite Watermark Load",
        "version": "2.0.0",
        "description": "Multiple watermark columns (up to 5) for complex CDC scenarios. Supports multi-column "
        "watermark logic for accurate delta detection.",
        "supportedSourceTypes": _RELATIONAL_AND_FILE,
        "supportedTargetTypes": _TARGET_TYPES,
        "fields": [
            "source.type", "source.object", "source.filterCondition", "target.type", "target.path",
            "watermark.enabled", "watermark.type", "watermark.columns", "watermark.watermarkTable",
            "watermark.initialValue", "watermark.filterCondition",
            "loadType.mode", "logging.enableCopyLog", "logging.customLogColumns",
            "parallelProcessing.parallelCopies", "parallelProcessing.dataIntegrationUnits",
            "errorHandling.retryCount", "errorHandling.retryIntervalInSeconds",
            "validation.rowCountValidation", "validation.dataQualityRules",
            "customMetadata",
        ],
        "defaults": _DEFAULTS,
    },
    {
        "id": "metadata_driven_load",
        "name": "Metadata-Driven Load",
        "version": "2.0.0",
        "description": "Configuration-table-driven scale-out. Master/child pipeline architecture for "
        "multi-table automation with parallel execution.",
        "supportedSourceTypes": _RELATIONAL_AND_FILE,
        "supportedTargetTypes": _TARGET_TYPES,
        "fields": [
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
        ],
        "defaults": _DEFAULTS,
    },
]
