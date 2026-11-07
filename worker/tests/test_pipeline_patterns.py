"""pytest for the §24.3/§24.12 pipeline patterns (AGENTS.md §23.12 gap #5).

Direct port of `frontend/lib/adf/pipeline-codegen.test.ts`'s "pipeline patterns" describe block —
`worker/tests/test_generator_parity.py` already proves the frontend/worker generators agree
byte-for-byte on these fixtures; this file proves the semantics themselves (no control table for
independent patterns, no dangling `LoadingBehavior` parameter reference, item()-substitution for
single_pipeline, ...) against the worker port directly.
"""
from __future__ import annotations

import json

from app.generator.codegen import build_generated_artifacts
from app.generator.spec import SpecDraft, validate_draft

_BASE_DRAFT: dict = {
    "name": "customer_incremental",
    "projectId": "proj-a",
    "templateId": "incremental_load",
    "sourceType": "sql_server",
    "sourceObject": "dbo.customer",
    "targetType": "azure_data_lake_storage_gen2",
    "targetPath": "/customer/",
    "loadTypeMode": "incremental",
    "watermarkEnabled": True,
    "watermarkType": "single",
    "watermarkColumns": [{"name": "modified_date", "type": "datetime", "order": 1}],
    "watermarkTable": "control.watermarks",
    "watermarkInitialValue": "1900-01-01",
    "sourceLinkedService": {
        "name": "LS_sql_server_source",
        "connectorType": "sql_server",
        "authType": "sql_authentication",
        "fields": {
            "server": "sqlsrv01.database.windows.net",
            "database": "customerdb",
            "userName": "svc_reader",
            "password": "sqlserver-password",
        },
    },
    "targetLinkedService": {
        "name": "LS_adls_target",
        "connectorType": "azure_data_lake_storage_gen2",
        "authType": "account_key",
        "fields": {
            "accountName": "stgcustomer",
            "url": "https://stgcustomer.dfs.core.windows.net",
            "accountKey": "storage-account-key",
        },
    },
    "sourceDataset": {"name": "DS_customer_incremental_source", "linkedServiceName": "LS_sql_server_source"},
    "targetDataset": {"name": "DS_customer_incremental_target", "linkedServiceName": "LS_adls_target"},
}


def _draft(**overrides) -> SpecDraft:
    return SpecDraft.from_dict({**_BASE_DRAFT, **overrides})


def _by_name(artifacts, file_name: str):
    return next((a for a in artifacts if a.file_name == file_name), None)


def test_master_child_default_pattern_is_unaffected():
    without = build_generated_artifacts(_draft())
    with_explicit = build_generated_artifacts(_draft(pattern="master_child"))
    assert [(a.file_name, a.content) for a in without] == [(a.file_name, a.content) for a in with_explicit]
    assert any(a.file_name.endswith("_master.json") for a in without)
    assert any(a.file_name.endswith("_child.json") for a in without)


def test_child_only_emits_just_the_child_pipeline():
    artifacts = build_generated_artifacts(_draft(pattern="child_only", childPipelineName="PL_existing_child"))
    assert len(artifacts) == 1
    assert artifacts[0].file_name == "PL_existing_child.json"
    assert json.loads(artifacts[0].content)["name"] == "PL_existing_child"


def test_master_only_emits_just_the_master_pipeline_referencing_an_existing_child():
    artifacts = build_generated_artifacts(_draft(pattern="master_only", childPipelineName="PL_existing_child"))
    assert len(artifacts) == 1
    assert artifacts[0].file_name.endswith("_master.json")
    assert '"referenceName":"PL_existing_child"' in artifacts[0].content.replace(" ", "").replace("\n", "")


def test_single_pipeline_has_no_child_and_loops_inline():
    artifacts = build_generated_artifacts(_draft(pattern="single_pipeline"))
    assert not any(a.file_name.endswith("_master.json") for a in artifacts)
    assert not any(a.file_name.endswith("_child.json") for a in artifacts)
    pipeline = json.loads(_by_name(artifacts, "PL_customer_incremental.json").content)
    for_each = next(a for a in pipeline["properties"]["activities"] if a["name"] == "ForEachExecutedObject")
    assert for_each["type"] == "ForEach"
    dumped = json.dumps(for_each)
    assert "ExecutePipeline" not in dumped
    assert "pipeline().parameters.LoadingBehavior" not in dumped
    assert "item().LoadingBehavior" in dumped
    assert _by_name(artifacts, "adf_control_table.sql") is not None


def test_independent_full_has_one_copy_activity_no_control_table_no_watermark():
    artifacts = build_generated_artifacts(_draft(pattern="independent_full", loadTypeMode="full"))
    assert _by_name(artifacts, "adf_control_table.sql") is None
    assert _by_name(artifacts, "adf_watermark_table.sql") is None
    pipeline = json.loads(_by_name(artifacts, "PL_customer_incremental.json").content)
    names = [a["name"] for a in pipeline["properties"]["activities"]]
    assert "FullLoadCopy" in names
    assert "RouteByLoadingBehavior" not in names
    assert "WatermarkTable" not in pipeline["properties"]["parameters"]


def test_independent_full_logs_a_literal_full_load_never_a_dangling_parameter_reference():
    artifacts = build_generated_artifacts(_draft(pattern="independent_full", loadTypeMode="full", enableCopyLog=True))
    pipeline = _by_name(artifacts, "PL_customer_incremental.json").content
    assert "'FullLoad'" in pipeline
    assert "pipeline().parameters.LoadingBehavior" not in pipeline


def test_independent_watermark_has_delta_chain_and_its_own_watermark_table():
    artifacts = build_generated_artifacts(_draft(pattern="independent_watermark"))
    assert _by_name(artifacts, "adf_control_table.sql") is None
    watermark_sql = _by_name(artifacts, "adf_watermark_table.sql").content
    assert "CREATE TABLE control.watermarks (" in watermark_sql
    assert "TaskId = 1" in watermark_sql
    pipeline = json.loads(_by_name(artifacts, "PL_customer_incremental.json").content)
    names = {a["name"] for a in pipeline["properties"]["activities"]}
    assert {"LookupLastWatermark", "LookupNewWatermark", "CopyDeltaData", "UpdateWatermark"} <= names


def test_pipeline_name_override_is_honored_and_referenced_by_the_trigger():
    artifacts = build_generated_artifacts(
        _draft(pattern="independent_full", pipelineName="PL_CustomerFullCopy", triggerEnabled=True)
    )
    assert _by_name(artifacts, "PL_CustomerFullCopy.json") is not None
    trigger = next(a for a in artifacts if a.file_name.startswith("TR_"))
    assert '"referenceName":"PL_CustomerFullCopy"' in trigger.content.replace(" ", "").replace("\n", "")


# §24.6 "use existing, don't regenerate" (found missing 2026-09-20 — see
# `frontend/lib/adf/pipeline-codegen.test.ts`'s identical describe block for the full writeup):
# picking a saved Connection only ever prefilled the draft's LS/DS fields, so the pipeline always
# regenerated a fresh linkedService/dataset JSON regardless — "reuse" saved typing but never
# actually skipped an artifact. `source_connection_reused`/`target_connection_reused` fix that.
def test_generates_every_ls_ds_artifact_by_default():
    artifacts = build_generated_artifacts(_draft())
    assert any(a.artifact_type == "linked_service" for a in artifacts)
    assert any(a.artifact_type == "dataset" for a in artifacts)


def test_omits_only_source_ls_ds_when_source_connection_reused():
    artifacts = build_generated_artifacts(_draft(sourceConnectionReused=True))
    ls_names = [a.file_name for a in artifacts if a.artifact_type == "linked_service"]
    ds_names = [a.file_name for a in artifacts if a.artifact_type == "dataset"]
    assert "LS_sql_server_source.json" not in ls_names
    assert len(ls_names) == 1  # target LS still generated
    assert len(ds_names) == 1  # target DS still generated


def test_omits_only_target_ls_ds_when_target_connection_reused():
    artifacts = build_generated_artifacts(_draft(targetConnectionReused=True))
    assert len([a for a in artifacts if a.artifact_type == "linked_service"]) == 1  # source LS still generated
    assert len([a for a in artifacts if a.artifact_type == "dataset"]) == 1  # source DS still generated


def test_omits_both_when_both_reused_but_pipeline_still_references_them_by_name():
    artifacts = build_generated_artifacts(_draft(sourceConnectionReused=True, targetConnectionReused=True))
    assert not any(a.artifact_type == "linked_service" for a in artifacts)
    assert not any(a.artifact_type == "dataset" for a in artifacts)
    master = json.loads(_by_name(artifacts, "PL_customer_incremental_master.json").content)
    assert "LS_sql_server_source" in json.dumps(master)
    assert "LS_adls_target" in json.dumps(master)


def test_annotations_are_stamped_on_every_pipeline_only_when_set():
    def pipeline_annotations(draft):
        return [
            json.loads(a.content)["properties"]["annotations"]
            for a in build_generated_artifacts(draft)
            if a.artifact_type == "pipeline"
        ]

    assert all(a == [] for a in pipeline_annotations(_draft()))
    tagged = pipeline_annotations(_draft(annotations=[" domain:finance", "", "domain:finance", "owner:de"]))
    assert len(tagged) > 1
    assert all(a == ["domain:finance", "owner:de"] for a in tagged)


# --- Gap #30: Switch-based dispatch across multiple children -------------------------------------

_DISPATCH = [
    {"matchValue": "finance", "childPipelineName": "PL_finance_child"},
    {"matchValue": "hr", "childPipelineName": "PL_hr_child"},
]


def test_child_dispatch_default_is_single_execute_pipeline():
    master = json.loads(_by_name(build_generated_artifacts(_draft()), "PL_customer_incremental_master.json").content)
    inner = master["properties"]["activities"][1]["typeProperties"]["activities"]
    assert [a["type"] for a in inner] == ["ExecutePipeline"]
    assert "TaskGroup" not in master["properties"]["activities"][0]["typeProperties"]["source"]["sqlReaderQuery"]


def test_child_dispatch_emits_switch_with_default_own_child():
    artifacts = build_generated_artifacts(_draft(childDispatch=_DISPATCH))
    master = json.loads(_by_name(artifacts, "PL_customer_incremental_master.json").content)
    assert ", TaskGroup FROM" in master["properties"]["activities"][0]["typeProperties"]["source"]["sqlReaderQuery"]
    (switch,) = master["properties"]["activities"][1]["typeProperties"]["activities"]
    assert switch["type"] == "Switch"
    cases = switch["typeProperties"]["cases"]
    assert [c["value"] for c in cases] == ["finance", "hr"]
    assert [c["activities"][0]["typeProperties"]["pipeline"]["referenceName"] for c in cases] == [
        "PL_finance_child",
        "PL_hr_child",
    ]
    default = switch["typeProperties"]["defaultActivities"][0]
    assert default["typeProperties"]["pipeline"]["referenceName"] == "PL_customer_incremental_child"
    sql = _by_name(artifacts, "adf_control_table.sql").content
    assert "TaskGroup" in sql


def test_child_dispatch_ignored_for_patterns_without_a_master():
    artifacts = build_generated_artifacts(_draft(pattern="single_pipeline", childDispatch=_DISPATCH))
    assert all("DispatchToChildPipeline" not in a.content for a in artifacts)


def test_child_dispatch_validation():
    from app.generator.spec import validate_draft

    bad = [
        {"matchValue": "a", "childPipelineName": "PL_x"},
        {"matchValue": "a", "childPipelineName": "bad-name"},
        {"matchValue": "", "childPipelineName": "PL_y"},
        {"matchValue": "b", "childPipelineName": "PL_customer_incremental_master"},
    ]
    messages = [i.message for i in validate_draft(_draft(childDispatch=bad)) if i.field == "childDispatch"]
    assert len(messages) == 4
    assert not [i for i in validate_draft(_draft(childDispatch=_DISPATCH)) if i.field == "childDispatch"]


def test_source_system_switches_default_names_to_pl_master_and_source_child():
    names = {a.file_name for a in build_generated_artifacts(_draft(sourceSystem="SAP ECC"))}
    assert "pl_master.json" in names
    assert "pl_sap_ecc_child.json" in names
    # an explicit override still wins
    names = {a.file_name for a in build_generated_artifacts(_draft(sourceSystem="sap", masterPipelineName="PL_x"))}
    assert "PL_x.json" in names and "pl_sap_child.json" in names


def test_copy_timeout_defaults_and_override():
    def copy_timeouts(draft):
        out = []
        for a in build_generated_artifacts(draft):
            if a.file_name.endswith(".json") and '"Copy"' in a.content:
                def walk(node):
                    if isinstance(node, dict):
                        if node.get("type") == "Copy":
                            out.append(node["policy"]["timeout"])
                        for v in node.values():
                            walk(v)
                    elif isinstance(node, list):
                        for v in node:
                            walk(v)
                walk(json.loads(a.content))
        return out

    assert copy_timeouts(_draft()) and set(copy_timeouts(_draft())) == {"0.02:00:00.000"}
    assert set(copy_timeouts(_draft(copyTimeout="0.04:00:00"))) == {"0.04:00:00"}


def test_copy_timeout_rejects_bad_format():
    from app.generator.spec import validate_draft

    assert any(i.field == "copyTimeout" for i in validate_draft(_draft(copyTimeout="2 hours")))


def test_copy_type_conversion_translator_is_opt_in():
    def translators(draft):
        out = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "Copy":
                    out.append(node["typeProperties"].get("translator"))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        for a in build_generated_artifacts(draft):
            if a.file_name.endswith(".json"):
                walk(json.loads(a.content))
        return out

    assert translators(_draft()) and all(t is None for t in translators(_draft()))
    on = translators(_draft(copyTypeConversion=True))
    assert on and all(t and t["type"] == "TabularTranslator" and t["typeConversion"] is True for t in on)
    assert all(t["typeConversionSettings"]["allowDataTruncation"] is False for t in on)
    # Explicit column mappings alone produce a plain translator with mappings (blank rows dropped).
    mapped = translators(_draft(columnMappings=[{"source": "CUST_NM", "sink": "customer_name"}, {"source": "", "sink": ""}]))
    assert mapped and all(
        t == {"type": "TabularTranslator", "mappings": [{"source": {"name": "CUST_NM"}, "sink": {"name": "customer_name"}}]}
        for t in mapped
    )


def test_column_mappings_validation():
    issues = validate_draft(_draft(columnMappings=[{"source": "a", "sink": "x"}, {"source": "b", "sink": "X"}, {"source": "c", "sink": ""}]))
    errors = [i.message for i in issues if i.field == "columnMappings" and i.severity == "error"]
    assert any("needs both" in m for m in errors)
    assert any("more than once" in m for m in errors)


_PER_OBJECT_ROWS = [
    {"object": "dbo.customer", "columnMappings": [{"source": "CUST_NM", "sink": "customer_name"}]},
    {"object": "dbo.orders"},
]


def test_per_object_column_mappings_master_child():
    artifacts = build_generated_artifacts(
        _draft(sourceObjects=_PER_OBJECT_ROWS, columnMappings=[{"source": "ORD_NO", "sink": "order_number"}])
    )
    sql = _by_name(artifacts, "adf_control_table.sql").content
    assert "ColumnMapping     NVARCHAR(MAX)" in sql
    # Each row carries its own translator, falling back to the pipeline-level mappings.
    assert '"mappings":[{"source":{"name":"CUST_NM"},"sink":{"name":"customer_name"}}]' in sql
    assert '"mappings":[{"source":{"name":"ORD_NO"},"sink":{"name":"order_number"}}]' in sql
    master = json.loads(next(a for a in artifacts if a.file_name.endswith("_master.json")).content)
    assert ", ColumnMapping FROM" in json.dumps(master)
    child = json.loads(next(a for a in artifacts if a.file_name.endswith("_child.json")).content)
    params = child["properties"]["parameters"]
    assert json.loads(params["ColumnMapping"]["defaultValue"])["mappings"][0]["source"]["name"] == "ORD_NO"
    assert json.dumps(child).count("@json(pipeline().parameters.ColumnMapping)") == 2


def test_per_object_column_mappings_single_pipeline_reads_item():
    artifacts = build_generated_artifacts(_draft(pattern="single_pipeline", sourceObjects=_PER_OBJECT_ROWS))
    pipeline = next(a for a in artifacts if a.artifact_type == "pipeline").content
    assert "@json(item().ColumnMapping)" in pipeline
    assert "pipeline().parameters.ColumnMapping" not in pipeline


def test_per_object_column_mappings_absent_leaves_output_unchanged():
    plain = build_generated_artifacts(_draft(sourceObjects=[{"object": "dbo.customer"}, {"object": "dbo.orders"}]))
    assert all("ColumnMapping" not in a.content for a in plain)


def test_per_object_column_mappings_validation():
    bad = [{"object": "dbo.customer", "columnMappings": [{"source": "a", "sink": ""}, {"source": "b", "sink": "x"}, {"source": "c", "sink": "X"}]}]
    errors = [i.message for i in validate_draft(_draft(sourceObjects=bad)) if i.field == "sourceObjects" and i.severity == "error"]
    assert any("needs both" in m for m in errors)
    assert any("more than once" in m for m in errors)
    ignored = validate_draft(_draft(pattern="independent_full", sourceObjects=_PER_OBJECT_ROWS[:1]))
    assert any(i.severity == "warning" and "only applied" in i.message for i in ignored)
