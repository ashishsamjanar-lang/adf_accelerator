"""More real-code scenario coverage from docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md,
continuing test_scenario_smoke_dummy_artifacts.py's approach: dummy but
realistic drafts run through the real generator/validator, asserted against
actual output — never hand-typed literals.

These specifically fill in scenario IDs that `backend/tests/test_scenario_*.py`
stubs cannot reach for real: the generator, the qualifier/sanitizer, and the
structural `validate_draft` combination checks only exist in `worker/app`
(backend vendors only the connector registry and the naming-*convention*
evaluator — see backend/app/standards.py's own docstring). Each test names
the scenario ID it covers so it's traceable back to the catalog.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.generator.codegen import build_generated_artifacts
from app.generator.qualifier import (
    QualifierEntryInput,
    build_safe_qualifier_token,
    check_qualifier_collision,
    get_qualifier_profile,
    resolve_qualified_name,
)
from app.generator.spec import SpecDraft, validate_draft
from app.validator.rules import validate_artifacts

_BASE = {
    "name": "acme_widgets_full",
    "projectId": "proj-smoke-test",
    "templateId": "full_load",
    "sourceType": "sql_server",
    "sourceObject": "dbo.widgets",
    "targetType": "azure_data_lake_storage_gen2",
    "targetPath": "/widgets/",
    "sourceObjectMode": "form",
    "sourceObjects": [{"object": "dbo.widgets"}],
    "watermarkEnabled": False,
    "loadTypeMode": "full",
    "fullLoad": True,
    "batchCount": 1,
    "sinkFileFormat": "Parquet",
    "writeBehavior": "Insert",
    "sourceLinkedService": {
        "name": "LS_acme_sql",
        "connectorType": "sql_server",
        "authType": "sql_authentication",
        "fields": {"server": "acme-sql01.internal", "database": "Widgets", "userName": "svc_adf", "password": "acme-sql-password"},
    },
}


def _artifact(artifacts, *, suffix: str):
    (found,) = [a for a in artifacts if a.file_name.endswith(suffix)]
    return found


def _all_activities(child_doc: dict) -> list[dict]:
    """Every activity in the child pipeline, flattened across the
    `RouteByLoadingBehavior` Switch's cases (and default branch, if any) —
    the child pipeline's single top-level activity is that Switch; the
    Copy/Lookup/ForEach activities scenario tests care about all live inside
    its per-LoadingBehavior branches, not at the top level."""
    top = child_doc["properties"]["activities"]
    switches = [a for a in top if a["type"] == "Switch"]
    if not switches:
        return top
    (switch,) = switches
    flat = [a for a in top if a["type"] != "Switch"]
    for case in switch["typeProperties"].get("cases", []):
        flat.extend(case["activities"])
    flat.extend(switch["typeProperties"].get("defaultActivities", []))
    return flat


# --------------------------------------------------------------------------
# NAME-* — the always-on ADF hard naming rule (worker/app/validator/rules.py
# ARTIFACT_NAME_RE). backend/app/standards.py only vendors the opt-in naming
# *convention* evaluator (prefix/suffix/case/maxLength) — a materially
# different rule from this always-on one, so these can only be tested here.
# --------------------------------------------------------------------------


def test_name_01_hyphen_in_a_derived_linked_service_name_is_blocked():
    """NAME-01: a hyphen surviving into a declared LS/pipeline/dataset/trigger
    name must `block` — always on, no configuration involved."""
    draft = SpecDraft.from_dict({**_BASE, "name": "acme_widgets_full", "masterPipelineName": "PL_Order-Mgmt_master"})
    artifacts = build_generated_artifacts(draft)
    issues = validate_artifacts(artifacts)
    blocking = [i for i in issues if i.severity == "error" and "PL_Order-Mgmt_master" in (i.source or "")]
    assert blocking, "a hyphen in a declared artifact name must be blocked by the always-on ADF-name rule"


def test_name_02_leading_digit_pipeline_name_is_blocked():
    """NAME-02 / SAP-04: `0FI_GL_4`-derived pipeline name must `block`."""
    draft = SpecDraft.from_dict({**_BASE, "masterPipelineName": "0FI_GL_4_master"})
    issues = validate_artifacts(build_generated_artifacts(draft))
    assert any(i.severity == "error" and "0FI_GL_4_master" in (i.source or "") for i in issues)


def test_name_03_space_in_a_derived_name_is_blocked():
    """NAME-03: a space anywhere in a declared artifact name must `block`."""
    draft = SpecDraft.from_dict({**_BASE, "masterPipelineName": "PL_General Ledger_master"})
    issues = validate_artifacts(build_generated_artifacts(draft))
    assert any(i.severity == "error" and "PL_General Ledger_master" in (i.source or "") for i in issues)


def test_name_04_name_exceeding_128_chars_is_blocked():
    """NAME-04: ARTIFACT_NAME_RE allows only 128 chars total (1 + {1,127}) —
    any longer declared name blocks, independent of any configured maxLength
    naming-convention rule."""
    long_name = "master_" + ("x" * 130)
    draft = SpecDraft.from_dict({**_BASE, "masterPipelineName": long_name})
    issues = validate_artifacts(build_generated_artifacts(draft))
    assert len(long_name) > 128
    assert any(i.severity == "error" and long_name in (i.source or "") for i in issues)


def test_name_05_non_ascii_name_is_blocked_not_transliterated():
    """NAME-05: a non-ASCII token (`Société-Générale`) reaching a declared name
    must `block`, never silently transliterate — sanitize_for_adf_name only
    strips ADF-illegal punctuation/whitespace, not accented letters, so the
    accented character survives into ARTIFACT_NAME_RE and blocks there."""
    draft = SpecDraft.from_dict({**_BASE, "masterPipelineName": "PL_SociUte_GUnUrale_master".replace("U", "é")})
    issues = validate_artifacts(build_generated_artifacts(draft))
    assert any(i.severity == "error" for i in issues)


def test_name_07_factory_name_rule_set_allows_hyphens_pipeline_rule_forbids_them():
    """NAME-07: the factory-name rule (worker/app/validator/rules.py
    FACTORY_NAME_RE) is a deliberately different, hyphen-permitting rule set
    from the LS/pipeline/dataset/trigger rule (ARTIFACT_NAME_RE) — confirms
    they are not accidentally the same regex."""
    from app.validator.rules import ARTIFACT_NAME_RE, FACTORY_NAME_RE

    factory_name = "adf-acme-prod"
    assert FACTORY_NAME_RE.match(factory_name)
    assert not ARTIFACT_NAME_RE.match(factory_name)


def test_name_09_warn_level_naming_violation_never_blocks():
    """NAME-09: a `warn`-level (not `block`) project naming-convention
    violation must surface but never carry error severity."""
    from app.validator.naming import evaluate_name

    convention = {"enforcement": "warn", "rules": {"pipeline": {"prefix": "PL_"}}}
    violation = evaluate_name("NotPrefixed_master", "pipeline", convention)
    assert violation is not None
    assert violation.level == "warn"


# --------------------------------------------------------------------------
# SAP-* qualifier/generator scenarios not already covered by test_qualifier.py
# or test_scenario_smoke_dummy_artifacts.py.
# --------------------------------------------------------------------------


def test_sap_13_batch_count_is_a_genuine_per_draft_field_not_a_shared_default():
    """SAP-13: batchCount must be stored per pipeline draft, independent of
    any other draft — not silently shared/copied from a project default."""
    sap_draft = SpecDraft.from_dict({**_BASE, "sourceType": "sap_hana", "batchCount": 3})
    other_draft = SpecDraft.from_dict({**_BASE, "batchCount": 8})
    assert sap_draft.batch_count == 3
    assert other_draft.batch_count == 8
    assert sap_draft.batch_count != other_draft.batch_count


def test_sap_15_business_description_source_object_passes_presence_only_validation():
    """SAP-15: `sourceObject = "General Ledger Account Master"` (a
    description, not a technical name like SKA1) has no pattern check today —
    documents the real gap: validate_draft only checks presence."""
    draft = SpecDraft.from_dict(
        {**_BASE, "sourceType": "sap_hana", "sourceObject": "General Ledger Account Master",
         "sourceObjects": [{"object": "General Ledger Account Master"}]}
    )
    issues = validate_draft(draft)
    assert not any(i.field == "sourceObjects" and i.severity == "error" for i in issues)


def test_sap_16_chunk_column_is_a_single_free_text_field_no_composite_support():
    """SAP-16 / CHUNK-10: chunkColumn is a single string field — there is no
    structured way to express a composite fiscal-period key (GJAHR+POPER)."""
    draft = SpecDraft.from_dict({**_BASE, "chunkColumn": "GJAHR_POPER_COMPOSITE"})
    assert isinstance(draft.chunk_column, str)
    # A composite key can only be smuggled in as one opaque string — nothing
    # downstream parses it into two range-bound columns.


# --------------------------------------------------------------------------
# SFTP-02 / REGR-05 — dual-trigger generation.
# --------------------------------------------------------------------------


def test_sftp_02_regr_05_blob_events_with_fallback_generates_both_triggers():
    draft = SpecDraft.from_dict(
        {
            **_BASE,
            "name": "vendor_invoice_arrival",
            "sourceType": "sftp",
            "sourceObject": "/outbound/Acme_Invoice_*.csv",
            "triggerEnabled": True,
            "triggerType": "blobEvents",
            "triggerStorageLinkedService": "LS_acme_blob",
            "triggerContainerName": "landing",
            "triggerFallbackSchedule": True,
        }
    )
    artifacts = build_generated_artifacts(draft)
    trigger_files = [a.file_name for a in artifacts if a.artifact_type == "trigger"]
    assert "TR_vendor_invoice_arrival.json" in trigger_files
    assert "TR_vendor_invoice_arrival_fallback.json" in trigger_files
    assert len(trigger_files) == 2


def test_fallback_trigger_omitted_when_the_checkbox_is_off():
    draft = SpecDraft.from_dict(
        {
            **_BASE,
            "name": "vendor_invoice_arrival",
            "sourceType": "sftp",
            "triggerEnabled": True,
            "triggerType": "blobEvents",
            "triggerStorageLinkedService": "LS_acme_blob",
            "triggerContainerName": "landing",
            "triggerFallbackSchedule": False,
        }
    )
    trigger_files = [a.file_name for a in build_generated_artifacts(draft) if a.artifact_type == "trigger"]
    assert trigger_files == ["TR_vendor_invoice_arrival.json"]


# --------------------------------------------------------------------------
# CHUNK-* — chunk-plan generator.
# --------------------------------------------------------------------------


def _chunked_draft(**overrides):
    return SpecDraft.from_dict(
        {
            **_BASE,
            "chunkingEnabled": True,
            "chunkColumn": overrides.pop("chunkColumn", "claim_id"),
            "chunkSizeRows": 100_000,
            "maxParallelChunks": 4,
            **overrides,
        }
    )


def test_chunk_01_03_generate_chunk_plan_sql_is_idempotent_via_where_not_exists():
    """CHUNK-01/CHUNK-03: the chunk-plan seed INSERT is guarded by a
    `WHERE NOT EXISTS` against the same TaskId, so re-running Generate (or a
    re-run that only needs PENDING/FAILED chunks) can't duplicate ranges."""
    draft = _chunked_draft()
    artifacts = build_generated_artifacts(draft)
    child = json.loads(_artifact(artifacts, suffix="_child.json").content)
    activities = _all_activities(child)
    generate = next(a for a in activities if a["name"] == "GenerateChunkPlan")
    query = generate["typeProperties"]["source"]["sqlReaderQuery"]
    assert "WHERE NOT EXISTS" in query
    lookup_pending = next(a for a in activities if a["name"] == "LookupPendingChunks")
    lookup_query = lookup_pending["typeProperties"]["source"]["sqlReaderQuery"]
    assert "PENDING" in lookup_query and "FAILED" in lookup_query


def test_chunk_06_chunking_combined_with_delta_load_is_not_rejected_by_validate_draft():
    """CHUNK-06: documents a real gap — validate_draft never cross-checks
    chunkingEnabled against loadTypeMode, so "Partition & chunking" can be
    turned on for an ongoing DeltaLoad pipeline without any validation error,
    even though chunking is designed for one-time Initial Load seeds."""
    draft = _chunked_draft(loadTypeMode="incremental", watermarkEnabled=True, watermarkColumns=[{"name": "updated_at"}], watermarkTable="control.watermarks")
    issues = validate_draft(draft)
    assert not any(i.field == "chunkColumn" for i in issues if i.severity == "error")


def test_chunk_08_guid_chunk_column_is_accepted_with_no_type_check():
    """CHUNK-08: documents a real gap — chunkColumn is a bare string field;
    validate_draft never rejects/warns about a non-orderable (e.g. GUID)
    chunk column."""
    draft = _chunked_draft(chunkColumn="row_guid")
    issues = validate_draft(draft)
    assert not any(i.field == "chunkColumn" for i in issues)


# --------------------------------------------------------------------------
# WM-* watermark scenarios.
# --------------------------------------------------------------------------


def test_wm_02_watermark_is_computed_from_a_real_max_query_never_getutcdate():
    draft = SpecDraft.from_dict(
        {**_BASE, "watermarkEnabled": True, "loadTypeMode": "incremental", "fullLoad": False,
         "watermarkColumns": [{"name": "updated_at", "type": "datetime", "order": 1}],
         "sourceObjects": [{"object": "dbo.widgets", "watermarkColumn": "updated_at"}],
         "watermarkTable": "control.watermarks"}
    )
    child = json.loads(_artifact(build_generated_artifacts(draft), suffix="_child.json").content)
    lookup_new = next(a for a in _all_activities(child) if a["name"] == "LookupNewWatermark")
    query = lookup_new["typeProperties"]["source"]["sqlReaderQuery"]
    assert "MAX(" in query
    assert "GETUTCDATE" not in query and "GETDATE" not in query


def test_wm_03_schedule_trigger_time_zone_defaults_to_utc():
    """WM-03: a `schedule`-type trigger's `recurrence.timeZone` defaults to
    UTC (so DST never applies to hourly window boundaries). Note:
    `tumbling`-type triggers generate a `TumblingWindowTrigger`, which has no
    `timeZone` property at all in ADF's own schema — that's correct, not a
    gap (tumbling windows are always UTC-anchored internally)."""
    draft = SpecDraft.from_dict(
        {**_BASE, "triggerEnabled": True, "triggerType": "schedule", "triggerFrequency": "Hour", "triggerInterval": 1}
    )
    trigger = json.loads(_artifact(build_generated_artifacts(draft), suffix=f"TR_{draft.name}.json").content)
    assert trigger["properties"]["typeProperties"]["recurrence"]["timeZone"] == "UTC"

    tumbling_draft = SpecDraft.from_dict(
        {**_BASE, "triggerEnabled": True, "triggerType": "tumbling", "triggerFrequency": "Hour", "triggerInterval": 1}
    )
    tumbling_trigger = json.loads(_artifact(build_generated_artifacts(tumbling_draft), suffix=f"TR_{draft.name}.json").content)
    assert "timeZone" not in tumbling_trigger["properties"]["typeProperties"]


def test_wm_04_watermark_initial_value_is_generated_but_never_referenced_by_any_activity():
    """WM-04: documents a real, confirmed gap — `WatermarkInitialValue` is
    emitted into `parameters.json`, but it is never declared as a child-
    pipeline parameter and never referenced by `LookupLastWatermark` (or
    anything else) as a fallback when the control table has no prior row for
    this TaskId yet. The "first run uses the seed value" behavior this
    scenario expects would have to come entirely from how the control table
    itself gets seeded at Onboarding time, not from anything the generator
    emits into the pipeline."""
    draft = SpecDraft.from_dict(
        {**_BASE, "watermarkEnabled": True, "loadTypeMode": "incremental", "fullLoad": False,
         "watermarkColumns": [{"name": "updated_at", "type": "datetime", "order": 1}],
         "sourceObjects": [{"object": "dbo.widgets", "watermarkColumn": "updated_at"}],
         "watermarkTable": "control.watermarks", "watermarkInitialValue": "1900-01-01"}
    )
    artifacts = build_generated_artifacts(draft)
    params_doc = json.loads(_artifact(artifacts, suffix="parameters.json").content)
    assert params_doc["WatermarkInitialValue"] == "1900-01-01"

    child = json.loads(_artifact(artifacts, suffix="_child.json").content)
    assert "WatermarkInitialValue" not in child["properties"]["parameters"]
    assert "WatermarkInitialValue" not in json.dumps(_all_activities(child))


def test_wm_09_inclusive_upper_bound_flag_does_not_exist_in_the_draft():
    """WM-09: documents a real gap — there is no `watermarkIsInclusiveUpper`
    (or equivalent) field on SpecDraft, so the emitted comparison operator
    can't be flipped from `>` to `>=`; it's always exclusive today."""
    assert not hasattr(SpecDraft(), "watermark_is_inclusive_upper")


# --------------------------------------------------------------------------
# REGR-* regression watchlist — re-verified against the real generator.
# --------------------------------------------------------------------------


def test_regr_01_custom_metadata_columns_appear_as_copy_source_additional_columns():
    """REGR-01: the project-level metadata contract (`customMetadata`) must
    appear as `additionalColumns` on the Copy activity's *source*
    (`source.additionalColumns` — ADF's real location for
    this), not just sit unused in `parameters.json`."""
    draft = SpecDraft.from_dict(
        {**_BASE, "customMetadata": [{"name": "_source_system", "value": "SQL_SERVER"}, {"name": "_ingestion_ts_utc", "value": "@utcnow()"}]}
    )
    child = json.loads(_artifact(build_generated_artifacts(draft), suffix="_child.json").content)
    copy = next(a for a in _all_activities(child) if a["type"] == "Copy")
    additional_columns = copy["typeProperties"]["source"]["additionalColumns"]
    names = {c["name"] for c in additional_columns}
    assert {"_source_system", "_ingestion_ts_utc"} <= names
    ingestion_ts = next(c for c in additional_columns if c["name"] == "_ingestion_ts_utc")
    assert ingestion_ts["value"] == {"type": "Expression", "value": "@utcnow()"}
    source_system = next(c for c in additional_columns if c["name"] == "_source_system")
    assert source_system["value"] == "SQL_SERVER"  # literal, not wrapped — no leading "@"


def test_regr_02_execution_logging_lookups_have_the_documented_dependson_wiring():
    draft = SpecDraft.from_dict({**_BASE, "enableCopyLog": True})
    child = json.loads(_artifact(build_generated_artifacts(draft), suffix="_child.json").content)
    names = [a["name"] for a in _all_activities(child)]
    assert len(names) == len(set(names)), "ADF requires pipeline-wide unique activity names"
    activities = {a["name"]: a for a in _all_activities(child)}
    # One LogRun* set per Switch branch, suffixed by branch (FullLoad / DeltaLoad default).
    assert {"LogRunStart_DeltaLoad", "LogRunSucceeded_DeltaLoad", "LogRunFailed_DeltaLoad", "LogRunStart_FullLoad"} <= activities.keys()
    succeeded_conditions = {d["dependencyConditions"][0] for d in activities["LogRunSucceeded_DeltaLoad"]["dependsOn"]}
    failed_conditions = {d["dependencyConditions"][0] for d in activities["LogRunFailed_DeltaLoad"]["dependsOn"]}
    assert succeeded_conditions == {"Succeeded"}
    assert failed_conditions == {"Failed"}


def test_regr_04_chunked_historical_seed_generates_the_full_documented_activity_set():
    draft = _chunked_draft(loadTypeMode="full", fullLoad=True)
    child = json.loads(_artifact(build_generated_artifacts(draft), suffix="_child.json").content)
    names = {a["name"] for a in _all_activities(child)}
    assert {"GenerateChunkPlan", "LookupPendingChunks", "ForEachChunk", "VerifyAllChunksSucceeded", "FailIfChunksIncomplete"} <= names
    control_sql = _artifact(build_generated_artifacts(draft), suffix="adf_control_table.sql").content
    assert "adf_chunk_plan" in control_sql


def test_regr_06_redirect_incompatible_rows_requires_rejects_linked_service_to_validate():
    draft_missing = SpecDraft.from_dict({**_BASE, "redirectIncompatibleRows": True, "rejectsLinkedService": ""})
    issues = validate_draft(draft_missing)
    assert any(i.field == "rejectsLinkedService" and i.severity == "error" for i in issues)

    draft_ok = SpecDraft.from_dict({**_BASE, "redirectIncompatibleRows": True, "rejectsLinkedService": "LS_acme_rejects"})
    child = json.loads(_artifact(build_generated_artifacts(draft_ok), suffix="_child.json").content)
    copy = next(a for a in _all_activities(child) if a["type"] == "Copy")
    assert "redirectIncompatibleRowSettings" in copy["typeProperties"]


def test_regr_07_workday_and_event_hub_are_selectable_with_matching_worker_backend_fingerprint():
    """REGR-07: registry-fingerprint parity between worker and backend's
    vendored connector-registry copies (both must include workday/
    azure_event_hub and hash identically)."""
    import importlib.util
    import sys
    from pathlib import Path

    from app.connectors.schemas import ALL_CONNECTORS, registry_fingerprint

    assert "workday" in ALL_CONNECTORS
    assert "azure_event_hub" in ALL_CONNECTORS

    backend_schemas_path = Path(__file__).resolve().parents[2] / "backend" / "app" / "connectors" / "schemas.py"
    assert backend_schemas_path.is_file(), "backend's vendored connector-registry copy is missing"
    module_name = "backend_connector_schemas_under_test"
    spec = importlib.util.spec_from_file_location(module_name, backend_schemas_path)
    backend_schemas = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = backend_schemas  # dataclasses w/ `from __future__ import annotations` needs this registered
    try:
        spec.loader.exec_module(backend_schemas)  # type: ignore[union-attr]
        assert "workday" in backend_schemas.ALL_CONNECTORS
        assert "azure_event_hub" in backend_schemas.ALL_CONNECTORS
        assert backend_schemas.registry_fingerprint() == registry_fingerprint()
    finally:
        del sys.modules[module_name]


# --------------------------------------------------------------------------
# META-* metadata/logging contract.
# --------------------------------------------------------------------------


def test_meta_05_log_run_failed_captures_the_real_activity_error_not_a_generic_string():
    draft = SpecDraft.from_dict({**_BASE, "enableCopyLog": True})
    child = json.loads(_artifact(build_generated_artifacts(draft), suffix="_child.json").content)
    log_failed = next(a for a in _all_activities(child) if a["name"] == "LogRunFailed_DeltaLoad")
    query = log_failed["typeProperties"]["source"]["sqlReaderQuery"]
    assert "activity(" in query and ".Error.message" in query
    assert "pipeline failed" not in query.lower()


# --------------------------------------------------------------------------
# WD-04 / API-02 — retry/backoff is a genuine per-draft field.
# --------------------------------------------------------------------------


def test_wd_04_api_02_retry_and_backoff_are_per_draft_not_hardcoded():
    workday_draft = SpecDraft.from_dict({**_BASE, "sourceType": "workday", "retryCount": 3, "retryIntervalInSeconds": 30})
    other_draft = SpecDraft.from_dict({**_BASE, "retryCount": 1, "retryIntervalInSeconds": 5})
    assert workday_draft.retry_count == 3 and workday_draft.retry_interval_in_seconds == 30
    assert other_draft.retry_count == 1 and other_draft.retry_interval_in_seconds == 5
    child = json.loads(_artifact(build_generated_artifacts(workday_draft), suffix="_child.json").content)
    copy = next(a for a in _all_activities(child) if a["type"] == "Copy")
    assert copy["policy"]["retry"] == 3
    assert copy["policy"]["retryIntervalInSeconds"] == 30


# --------------------------------------------------------------------------
# EVT-06 / EVT-07 — Event Hub pipeline shape.
# --------------------------------------------------------------------------


def test_evt_06_watermark_table_parameter_is_always_present_even_when_unused():
    """EVT-06: documents a real gap — `WatermarkTable` is unconditionally
    emitted in parameters.json/child-pipeline parameters regardless of
    whether the source actually uses watermarking (e.g. an Event Hub
    ingestion pattern), contrary to the scenario's expectation that it would
    be omitted when irrelevant."""
    draft = SpecDraft.from_dict({**_BASE, "sourceType": "azure_event_hub", "watermarkEnabled": False})
    child = json.loads(_artifact(build_generated_artifacts(draft), suffix="_child.json").content)
    assert "WatermarkTable" in child["properties"]["parameters"]


def test_evt_07_write_behavior_defaults_to_insert_never_upsert():
    draft = SpecDraft()
    assert draft.write_behavior == "Insert"


def test_git_01_regenerating_an_unchanged_draft_is_byte_identical():
    """GIT-01: the generator is a pure function of SpecDraft — two runs over every parity fixture
    give the same files with the same bytes, so an unchanged pipeline never produces a Git diff."""
    for fixture in sorted((Path(__file__).resolve().parent / "fixtures").glob("spec_draft_*.json")):
        data = json.loads(fixture.read_text())
        runs = [
            [(a.file_name, a.content) for a in build_generated_artifacts(SpecDraft.from_dict(data))]
            for _ in range(2)
        ]
        assert runs[0] == runs[1], fixture.name
