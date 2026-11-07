"""gaps.md RUN-03/04/05/07/15/16 — generated artifacts take the shape ADF actually accepts."""
from __future__ import annotations

import json

from app.generator.codegen import build_generated_artifacts
from app.generator.spec import SpecDraft
from tests.test_layers import _BASE_DRAFT


def _artifacts(**overrides):
    return build_generated_artifacts(SpecDraft.from_dict({**_BASE_DRAFT, **overrides}))


def _doc(artifacts, suffix: str) -> dict:
    return json.loads(next(a for a in artifacts if a.file_name.endswith(suffix)).content)


def _silver(**overrides):
    return _artifacts(
        medallionLayer="silver",
        curatedEntity="customer",
        transformations=[{"name": "dedupe_c", "type": "dedupe", "keyColumns": "id", "orderColumn": "modified_date"}],
        **overrides,
    )


def test_run_03_child_switch_uses_on_and_default_activities():
    route = next(a for a in _doc(_artifacts(), "_child.json")["properties"]["activities"] if a["name"] == "RouteByLoadingBehavior")
    tp = route["typeProperties"]
    assert tp["on"] == {"value": "@pipeline().parameters.LoadingBehavior", "type": "Expression"}
    assert "expression" not in tp and "default" not in tp
    assert any(a["name"] == "CopyDeltaData" for a in tp["defaultActivities"])


def test_run_04_transform_chain_names_are_unique_across_branches():
    route = next(a for a in _doc(_silver(), "_child.json")["properties"]["activities"] if a["name"] == "RouteByLoadingBehavior")
    tp = route["typeProperties"]
    names = [a["name"] for c in tp["cases"] for a in c["activities"]] + [a["name"] for a in tp["defaultActivities"]]
    assert len(names) == len(set(names))
    assert {"Transform_dedupe_c_FullLoad", "Transform_dedupe_c_DeltaLoad", "LoadToLayerTarget_DeltaLoad"} <= set(names)


def test_run_05_master_foreach_iterates_every_control_row():
    lookup, for_each = _doc(_artifacts(), "_master.json")["properties"]["activities"]
    assert lookup["typeProperties"]["firstRowOnly"] is False
    assert "outputVariableName" not in lookup
    assert for_each["dependsOn"] == [{"activity": "ReadObjectsFromControlTable", "dependencyConditions": ["Succeeded"]}]
    tp = for_each["typeProperties"]
    assert tp["items"]["value"] == "@activity('ReadObjectsFromControlTable').output.value"
    assert [a["type"] for a in tp["activities"]] == ["ExecutePipeline"]
    assert "activities" not in for_each
    assert not {"batchSize", "degreeOfParallelism", "retryCount"} & set(tp)


def test_run_07_table_dataset_keeps_schema_and_table_in_type_properties():
    ds = next(a for a in _artifacts() if a.artifact_type == "dataset" and a.file_name.endswith("_source.json"))
    props = json.loads(ds.content)["properties"]
    assert {"schema", "table"} <= set(props["typeProperties"])
    assert "sqlTableType" not in props and not isinstance(props.get("schema"), str)


def test_run_15_control_table_seed_inserts_are_guarded():
    artifacts = _silver(silverObjectRef="silver.customer", registryTag="crm")
    for suffix in ("adf_control_table.sql", "adf_curated_control_table.sql"):
        lines = next(a for a in artifacts if a.file_name.endswith(suffix)).content.split("\n")
        inserts = [i for i, line in enumerate(lines) if line.startswith("INSERT INTO")]
        assert inserts
        assert all(lines[i - 1].startswith("IF NOT EXISTS (SELECT 1 FROM") for i in inserts)


def _sql_target(**overrides):
    return _artifacts(
        targetType="azure_sql_database",
        targetDataset={"name": "", "linkedServiceName": "", "schema": "raw", "table": "customer",
                       "folderPath": "", "fileName": "", "parameterized": False},
        **overrides,
    )


def test_run_15_database_target_seeds_schema_table_not_folder_path():
    sql = next(a for a in _sql_target() if a.file_name.endswith("adf_control_table.sql")).content
    assert "Target = N'raw.customer'" in sql and "/customer/" not in sql
    # A file target keeps its folder path.
    assert "Target = N'/customer/'" in next(a for a in _artifacts() if a.file_name.endswith("adf_control_table.sql")).content


def _full_load_sink(artifacts) -> dict:
    route = next(a for a in _doc(artifacts, "_child.json")["properties"]["activities"] if a["name"] == "RouteByLoadingBehavior")
    copy = next(a for c in route["typeProperties"]["cases"] for a in c["activities"] if a["name"] == "FullLoadCopy")
    assert "truncate" not in copy["typeProperties"]
    return copy["typeProperties"]["sink"]


def test_run_16_full_load_truncate_emits_pre_copy_script_on_a_database_target():
    assert _full_load_sink(_sql_target(truncateTarget=True))["preCopyScript"] == "TRUNCATE TABLE raw.customer"
    assert "preCopyScript" not in _full_load_sink(_sql_target())
    assert "preCopyScript" not in _full_load_sink(_artifacts(truncateTarget=True))  # file target


def _all_activities(node):
    if isinstance(node, list):
        for item in node:
            yield from _all_activities(item)
    elif isinstance(node, dict):
        if "type" in node and "name" in node and "typeProperties" in node:
            yield node
        for value in node.values():
            yield from _all_activities(value)


def test_run_02_lookups_use_sql_reader_query_and_always_return_a_result_set():
    artifacts = _artifacts(enableCopyLog=True)
    lookups = [
        a
        for f in ("_master.json", "_child.json")
        for a in _all_activities(_doc(artifacts, f)["properties"]["activities"])
        if a["type"] == "Lookup"
    ]
    assert {"LogRunStart_FullLoad", "UpdateWatermark"} <= {a["name"] for a in lookups}
    for lookup in lookups:
        source = lookup["typeProperties"]["source"]
        assert "outputVariableName" not in lookup
        assert not {"query", "sqlWrapperType", "sqlWrapperSourceType"} & set(source)
        query = source["sqlReaderQuery"]
        if any(w in query for w in ("INSERT ", "UPDATE ")):
            assert query.endswith("; SELECT 1 AS Done"), lookup["name"]
        else:
            assert "AS Done" not in query


def _layer_load_sql(artifacts) -> str:
    acts = _all_activities(_doc(artifacts, "_child.json")["properties"]["activities"])
    return next(a for a in acts if a["name"] == "LoadToLayerTarget_DeltaLoad")["typeProperties"]["scripts"][0]["text"]


def test_run_14_layer_keys_drive_the_layer_load_not_the_bronze_upsert():
    artifacts = _sql_target(
        medallionLayer="silver",
        curatedEntity="customer",
        transformations=[{"name": "rename", "type": "conform_columns", "columnMappings": "cust_id:customer_id"}],
        writeBehavior="Upsert",
        upsertKeyColumns="cust_id",
        layerKeyColumns="customer_id",
    )
    assert "tgt.customer_id = src.customer_id" in _layer_load_sql(artifacts)
    acts = _all_activities(_doc(artifacts, "_child.json")["properties"]["activities"])
    copy = next(a for a in acts if a["name"] == "CopyDeltaData")
    assert "cust_id" in json.dumps(copy["typeProperties"]["sink"]) and "customer_id" not in json.dumps(copy["typeProperties"]["sink"])


def test_run_09_silver_chain_ships_a_rerunnable_schema_script():
    sql = next(a for a in _silver() if a.file_name == "adf_layer_schemas.sql").content
    for schema in ("raw", "stg", "curated"):
        assert f"IF SCHEMA_ID(N'{schema}') IS NULL EXEC(N'CREATE SCHEMA [{schema}]');" in sql
    assert not any(a.file_name == "adf_layer_schemas.sql" for a in _artifacts())


def test_run_09_layer_load_inserts_by_column_name_not_position():
    sql = _layer_load_sql(_silver())
    assert "SELECT * FROM" not in sql
    assert "COL_LENGTH(N'curated.customer', s.name) IS NOT NULL" in sql
    assert "EXEC(N'INSERT INTO curated.customer (' + @cols + N') SELECT ' + @cols" in sql
    assert sql.index("THROW 50001") < sql.index("BEGIN TRANSACTION")


def test_run_12_sql_dialect_check_flags_spark_in_a_hand_written_rule():
    from app.validator.rules import validate_sql_dialect

    assert validate_sql_dialect(_silver()) == []
    bad = _artifacts(
        medallionLayer="silver",
        curatedEntity="customer",
        transformations=[{"name": "conform", "type": "conform_columns", "columns": "nvl(email, 'n/a'):email, date_format(d, 'EEE'):dow"}],
    )
    messages = {i.message for i in validate_sql_dialect(bad)}
    assert any("Transform_conform_FullLoad" in m and "nvl, date_format" in m for m in messages)
    assert all(i.severity == "warning" and i.rule_id == "sql.dialect" for i in validate_sql_dialect(bad))


def _copy(artifacts, name: str = "CopyDeltaData") -> dict:
    return next(a for a in _all_activities(_doc(artifacts, "_child.json")["properties"]["activities"]) if a["name"] == name)


def test_run_01_copy_names_datasets_in_inputs_outputs_with_store_typed_source_and_sink():
    copy = _copy(_sql_target())
    assert copy["inputs"][0]["type"] == "DatasetReference"
    assert set(copy["inputs"][0]["parameters"]) == {"SchemaName", "TableName"}
    assert copy["outputs"][0]["type"] == "DatasetReference" and "parameters" not in copy["outputs"][0]
    tp = copy["typeProperties"]
    assert tp["source"]["type"] == "SqlServerSource" and "sqlReaderQuery" in tp["source"]
    assert tp["sink"]["type"] == "AzureSqlSink"
    assert not {"formatSettings", "copyBehavior", "typeProperties"} & set(tp["sink"])
    assert "translation" not in tp and "mapping" not in tp


def test_run_01_file_target_gets_format_and_store_write_settings():
    copy = _copy(_artifacts())
    sink = copy["typeProperties"]["sink"]
    assert sink["type"] == "ParquetSink" and sink["formatSettings"]["type"] == "ParquetWriteSettings"
    assert sink["storeSettings"]["type"].endswith("WriteSettings") and "writeBehavior" not in sink
    assert set(copy["outputs"][0]["parameters"]) == {"FolderPath", "FileName"}


def test_run_12_validator_rejects_the_old_copy_shape():
    from app.validator.rules import validate_resource_schema

    assert not [i for i in validate_resource_schema(_sql_target()) if i.severity == "error"]
    artifacts = _sql_target()
    child = next(a for a in artifacts if a.file_name.endswith("_child.json"))
    doc = json.loads(child.content)
    copy = next(a for a in _all_activities(doc["properties"]["activities"]) if a["name"] == "CopyDeltaData")
    copy.pop("inputs")
    copy["typeProperties"]["source"] = {"type": "DatasetReference", "dataset": {"referenceName": "x"}}
    copy["typeProperties"]["sink"]["formatSettings"] = {"type": "ParquetWriteSettings"}
    copy["typeProperties"]["translation"] = {"type": "Tabular"}
    child.content = json.dumps(doc)
    messages = " ".join(i.message for i in validate_resource_schema(artifacts) if i.severity == "error")
    for needle in ("'inputs'/'outputs'", "must be a store type", "'translator'", "is not a file sink"):
        assert needle in messages
