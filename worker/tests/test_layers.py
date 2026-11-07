"""pytest for the Bronze/Silver/Gold medallion layer support — direct port of
`frontend/lib/adf/medallion.test.ts` and the "medallion layer codegen" suite
in `frontend/lib/adf/pipeline-codegen.test.ts`.

Covers `app.generator.medallion` (naming standard + transformation SQL, pure
functions) and its wiring into `app.generator.codegen._child_pipeline` /
`app.generator.spec.validate_draft`.
"""
from __future__ import annotations

import json

from app.generator.codegen import build_generated_artifacts
from app.generator.medallion import (
    TransformationRule,
    layer_target_table_name,
    transformation_rule_sql,
    validate_medallion_fields,
    validate_transformation_rules,
)
from app.generator.spec import SpecDraft, effective_source_objects, validate_draft

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
            "server": "sqlserver01.contoso.com",
            "database": "AdventureWorks",
            "userName": "svc_adf",
            "password": "sqlserver-password",
        },
    },
    "targetLinkedService": {
        "name": "LS_adls_target",
        "connectorType": "azure_data_lake_storage_gen2",
        "authType": "account_key",
        "fields": {
            "accountName": "mystorageaccount",
            "url": "https://mystorageaccount.dfs.core.windows.net",
            "accountKey": "storage-account-key",
        },
    },
    "sourceDataset": {
        "name": "DS_customer_incremental_source",
        "linkedServiceName": "LS_sql_server_source",
        "schema": "dbo",
        "table": "customer",
    },
    "targetDataset": {
        "name": "DS_customer_incremental_target",
        "linkedServiceName": "LS_adls_target",
        "folderPath": "/customer/",
    },
}


def _draft(**overrides) -> SpecDraft:
    return SpecDraft.from_dict({**_BASE_DRAFT, **overrides})


def _child_activities(draft: SpecDraft, layer_prefixes: dict | None = None) -> list[dict]:
    artifacts = build_generated_artifacts(draft, layer_prefixes=layer_prefixes)
    child = next(a for a in artifacts if a.file_name.endswith("_child.json"))
    return json.loads(child.content)["properties"]["activities"]


def _switch_cases(activities: list[dict]) -> dict:
    route = next(a for a in activities if a["name"] == "RouteByLoadingBehavior")
    return route["typeProperties"]


# ---------------------------------------------------------------------------
# layer_target_table_name / validate_medallion_fields / validate_transformation_rules
# ---------------------------------------------------------------------------


def test_layer_target_table_name_per_layer():
    assert layer_target_table_name("bronze", source_type="sql_server", object_name="GL Transaction") == "raw.sql_server_gl_transaction"
    assert layer_target_table_name("silver", entity="GL_Transaction") == "curated.gl_transaction"
    assert layer_target_table_name("gold", domain="Finance", entity="gl_fact") == "mart.finance_gl_fact"


def test_layer_target_table_name_honors_a_projects_custom_zone_prefix():
    """§25.3 Standards "Layer prefixes" — a project's custom zone prefix overrides the
    raw/curated/mart default, but only for the layer it's set on."""
    overrides = {"bronze": "lnd", "silver": "cur", "gold": "pub"}
    assert layer_target_table_name("bronze", source_type="sql_server", object_name="gl_transaction", layer_prefixes=overrides) == "lnd.sql_server_gl_transaction"
    assert layer_target_table_name("silver", entity="gl_transaction", layer_prefixes=overrides) == "cur.gl_transaction"
    assert layer_target_table_name("gold", domain="finance", entity="gl_fact", layer_prefixes=overrides) == "pub.finance_gl_fact"
    # Unset/blank layers keep their default.
    assert layer_target_table_name("silver", entity="gl_transaction", layer_prefixes={"bronze": "lnd"}) == "curated.gl_transaction"
    assert layer_target_table_name("silver", entity="gl_transaction", layer_prefixes={"silver": "   "}) == "curated.gl_transaction"


def test_layer_target_table_name_sanitizes_a_custom_prefix():
    """A custom prefix is compiled straight into T-SQL — it can't be left as arbitrary text."""
    assert layer_target_table_name("silver", entity="x", layer_prefixes={"silver": "My Curated Zone!"}) == "my_curated_zone.x"
    # A prefix that sanitizes to nothing falls back to the default instead of producing "".x.
    assert layer_target_table_name("silver", entity="x", layer_prefixes={"silver": "!!!"}) == "curated.x"


def test_validate_medallion_fields_requires_entity_and_domain():
    assert validate_medallion_fields("bronze", curated_entity=None, mart_domain=None) == []
    silver_issues = validate_medallion_fields("silver", curated_entity=None, mart_domain=None)
    assert any(i.field == "curatedEntity" for i in silver_issues)
    gold_issues = validate_medallion_fields("gold", curated_entity="gl_fact", mart_domain=None)
    assert any(i.field == "martDomain" for i in gold_issues)
    assert validate_medallion_fields("gold", curated_entity="gl_fact", mart_domain="finance") == []


def test_validate_transformation_rules_requires_at_least_one_for_silver_gold():
    assert validate_transformation_rules("bronze", []) == []
    assert any(i.field == "transformations" for i in validate_transformation_rules("silver", []))


def test_validate_transformation_rules_rejects_wrong_layer_type():
    issues = validate_transformation_rules(
        "silver", [TransformationRule(name="bad", type="aggregate", group_by_columns="x", aggregate_expression="COUNT(*)")]
    )
    assert any("isn't valid for the silver layer" in i.message for i in issues)


def test_validate_transformation_rules_widening_allows_join_on_silver_and_scd2_on_gold():
    # §33.3: Silver additionally allows join (multi-source curation), Gold additionally allows scd2.
    silver_issues = validate_transformation_rules(
        "silver", [TransformationRule(name="join_orders", type="join", join_entity="raw.orders", join_condition="a.id = b.id", columns="a.*, b.total")]
    )
    assert silver_issues == []
    gold_issues = validate_transformation_rules(
        "gold", [TransformationRule(name="scd", type="scd2", key_columns="customer_id", order_column="modified_date", columns="name")]
    )
    assert gold_issues == []


def test_validate_transformation_rules_cap_raised_to_ten():
    ten_rules = [TransformationRule(name=f"step_{i}", type="conform_columns", columns="a") for i in range(10)]
    assert validate_transformation_rules("silver", ten_rules) == []
    eleven_rules = [TransformationRule(name=f"step_{i}", type="conform_columns", columns="a") for i in range(11)]
    issues = validate_transformation_rules("silver", eleven_rules)
    assert any(i.message == "Maximum 10 transformations allowed." for i in issues)


def test_validate_transformation_rules_accepts_full_rule_sets():
    silver_rules = [
        TransformationRule(name="conform", type="conform_columns", columns="cust_id:customer_id, modified_date"),
        TransformationRule(name="dedupe", type="dedupe", key_columns="customer_id", order_column="modified_date"),
    ]
    assert validate_transformation_rules("silver", silver_rules) == []
    gold_rules = [
        TransformationRule(name="join_customer", type="join", join_entity="curated.customer", join_condition="a.customer_id = b.customer_id", columns="a.*, b.segment"),
        TransformationRule(name="agg", type="aggregate", group_by_columns="customer_id", aggregate_expression="SUM(amount) AS total_amount"),
    ]
    assert validate_transformation_rules("gold", gold_rules) == []


def test_transformation_rule_sql_cast_schema_handles_commas_inside_parens():
    # Regression: a naive split(",") on "amount:DECIMAL(18,2), posted_date:DATETIME2"
    # would mis-split at the comma inside DECIMAL(18,2) — must split on top-level
    # commas only (matches the frontend medallion.ts fix of the same bug).
    sql = transformation_rule_sql(
        TransformationRule(name="r", type="cast_schema", cast_expression="amount:DECIMAL(18,2), posted_date:DATETIME2"),
        read_from="dbo.gl_transaction",
        write_to="stg.pl1_step1",
        target_table="curated.gl_transaction",
    )
    assert "ALTER TABLE stg.pl1_step1 ALTER COLUMN amount DECIMAL(18,2);" in sql
    assert "ALTER TABLE stg.pl1_step1 ALTER COLUMN posted_date DATETIME2;" in sql


def test_transformation_rule_sql_scd2_targets_the_real_table():
    sql = transformation_rule_sql(
        TransformationRule(name="r", type="scd2", key_columns="customer_id", order_column="modified_date"),
        read_from="dbo.customer",
        write_to="curated.customer",
        target_table="curated.customer",
    )
    assert "MERGE curated.customer AS tgt" in sql
    assert "INSERT INTO curated.customer" in sql
    assert "_is_current" in sql


def test_transformation_rule_sql_scd2_uses_named_history_columns():
    """DMT-09: the template's own history column names replace the generated defaults."""
    sql = transformation_rule_sql(
        TransformationRule(name="r", type="scd2", key_columns="customer_id", order_column="modified_date",
                           current_flag_column="is_current", valid_from_column="eff_from", valid_to_column="eff_to"),
        read_from="stg.x", write_to="curated.customer", target_table="curated.customer",
    )
    assert "modified_date, is_current, eff_from, eff_to)" in sql
    assert "UPDATE SET tgt.is_current = 0, tgt.eff_to = GETUTCDATE();" in sql
    assert "_is_current" not in sql


def test_scd2_change_hash_is_null_safe():
    """RUN-18: CONCAT_WS skips NULLs, so each hashed column is NULL-coalesced first."""
    sql = transformation_rule_sql(
        TransformationRule(name="r", type="scd2", key_columns="customer_id", order_column="modified_date", columns="email, phone"),
        read_from="stg.x", write_to="curated.customer", target_table="curated.customer",
    )
    assert "ISNULL(CAST(tgt.email AS NVARCHAR(MAX)), CHAR(0)), ISNULL(CAST(tgt.phone AS NVARCHAR(MAX)), CHAR(0))" in sql
    assert "ISNULL(CAST(src.phone AS NVARCHAR(MAX)), CHAR(0))" in sql


def test_filter_step_stages_the_matching_rows():
    """DMT-03."""
    rule = TransformationRule(name="f", type="filter", filter_condition="status <> 'X'")
    sql = transformation_rule_sql(rule, read_from="raw.t", write_to="stg.p_step1", target_table="curated.t")
    assert sql == "DROP TABLE IF EXISTS stg.p_step1;\nSELECT * INTO stg.p_step1 FROM raw.t WHERE status <> 'X';"
    assert validate_transformation_rules("gold", [rule]) == []
    assert "filter condition" in validate_transformation_rules("silver", [TransformationRule(name="f", type="filter")])[0].message


# ---------------------------------------------------------------------------
# validate_draft wiring
# ---------------------------------------------------------------------------


def test_validate_draft_bronze_default_has_no_medallion_errors():
    issues = validate_draft(_draft())
    assert not any(i.field in ("curatedEntity", "martDomain", "transformations") for i in issues)


def test_validate_draft_silver_requires_entity_and_transformation():
    issues = validate_draft(_draft(medallionLayer="silver"))
    assert any(i.field == "curatedEntity" for i in issues)
    assert any(i.field == "transformations" for i in issues)


def test_validate_draft_silver_passes_when_fully_configured():
    issues = validate_draft(
        _draft(
            medallionLayer="silver",
            curatedEntity="gl_transaction",
            transformations=[{"name": "dedupe_gl", "type": "dedupe", "keyColumns": "transaction_id", "orderColumn": "modified_date"}],
        )
    )
    assert not any(i.field in ("curatedEntity", "martDomain", "transformations") for i in issues)


# ---------------------------------------------------------------------------
# codegen wiring — mirrors "medallion layer codegen" in pipeline-codegen.test.ts
# ---------------------------------------------------------------------------


def test_bronze_default_generates_no_transformation_activities():
    draft = _draft()
    assert draft.medallion_layer == "bronze"
    cases = _switch_cases(_child_activities(draft))
    full_load_names = [a["name"] for a in next(c for c in cases["cases"] if c["value"] == "FullLoad")["activities"]]
    assert full_load_names == ["LogRunStart_FullLoad", "FullLoadCopy", "LogRunSucceeded_FullLoad", "LogRunFailed_FullLoad"]
    default_names = [a["name"] for a in cases["defaultActivities"]]
    assert "LoadToLayerTarget_DeltaLoad" not in default_names


def test_silver_appends_transform_chain_ending_in_curated_table():
    draft = _draft(
        medallionLayer="silver",
        curatedEntity="gl_transaction",
        loadTypeMode="full",
        transformations=[{"name": "dedupe_gl", "type": "dedupe", "keyColumns": "transaction_id", "orderColumn": "modified_date"}],
    )
    full_load = next(c for c in _switch_cases(_child_activities(draft))["cases"] if c["value"] == "FullLoad")["activities"]
    names = [a["name"] for a in full_load]
    assert "Transform_dedupe_gl_FullLoad" in names
    assert "LoadToLayerTarget_FullLoad" in names
    load = next(a for a in full_load if a["name"] == "LoadToLayerTarget_FullLoad")
    assert "INSERT INTO curated.gl_transaction" in load["typeProperties"]["scripts"][0]["text"]
    log_succeeded = next(a for a in full_load if a["name"] == "LogRunSucceeded_FullLoad")
    assert log_succeeded["dependsOn"][0]["activity"] == "DropStagingTables_FullLoad"
    # RUN-06: per-object, per-run staging tables, dropped after the load.
    transform = next(a for a in full_load if a["name"] == "Transform_dedupe_gl_FullLoad")
    stage = f"stg.{draft.name}_step1_@{{pipeline().parameters.TaskId}}_@{{replace(pipeline().RunId, '-', '')}}"
    assert f"INTO {stage}" in transform["typeProperties"]["scripts"][0]["text"]
    drop = next(a for a in full_load if a["name"] == "DropStagingTables_FullLoad")
    assert drop["dependsOn"][0]["activity"] == "LoadToLayerTarget_FullLoad"
    assert drop["typeProperties"]["scripts"][0]["text"] == f"DROP TABLE IF EXISTS {stage};"


def test_silver_custom_zone_prefix_threads_through_to_the_generated_load_activity():
    """§25.3 Standards "Layer prefixes" wired end to end through `build_generated_artifacts` ->
    `_child_pipeline` -> `_layer_transformation_activities` -> `layer_target_table_name`."""
    draft = _draft(
        medallionLayer="silver",
        curatedEntity="gl_transaction",
        loadTypeMode="full",
        transformations=[{"name": "dedupe_gl", "type": "dedupe", "keyColumns": "transaction_id", "orderColumn": "modified_date"}],
    )
    full_load = next(c for c in _switch_cases(_child_activities(draft, layer_prefixes={"silver": "cur"}))["cases"] if c["value"] == "FullLoad")["activities"]
    load = next(a for a in full_load if a["name"] == "LoadToLayerTarget_FullLoad")
    assert "INSERT INTO cur.gl_transaction" in load["typeProperties"]["scripts"][0]["text"]


def test_silver_scd2_writes_directly_to_target_no_synthesized_load():
    draft = _draft(
        medallionLayer="silver",
        curatedEntity="gl_transaction",
        loadTypeMode="full",
        transformations=[{"name": "scd2_gl", "type": "scd2", "keyColumns": "transaction_id", "orderColumn": "modified_date"}],
    )
    full_load = next(c for c in _switch_cases(_child_activities(draft))["cases"] if c["value"] == "FullLoad")["activities"]
    names = [a["name"] for a in full_load]
    assert "Transform_scd2_gl_FullLoad" in names
    assert "LoadToLayerTarget_FullLoad" not in names


def test_gold_appends_to_delta_branch_and_writes_mart_table():
    draft = _draft(
        medallionLayer="gold",
        curatedEntity="gl_fact",
        martDomain="finance",
        transformations=[{"name": "agg_gl", "type": "aggregate", "groupByColumns": "customer_id", "aggregateExpression": "SUM(amount) AS total_amount"}],
    )
    default_activities = _switch_cases(_child_activities(draft))["defaultActivities"]
    names = [a["name"] for a in default_activities]
    assert "Transform_agg_gl_DeltaLoad" in names
    assert "LoadToLayerTarget_DeltaLoad" in names
    load = next(a for a in default_activities if a["name"] == "LoadToLayerTarget_DeltaLoad")
    assert "INSERT INTO mart.finance_gl_fact" in load["typeProperties"]["scripts"][0]["text"]


def test_gold_ending_in_select_columns_still_gets_an_idempotent_load():
    """E2E-08: a terminal-looking final projection used to plain-INSERT the target, so every rerun duplicated."""
    draft = _draft(
        medallionLayer="gold",
        curatedEntity="gl_fact",
        martDomain="finance",
        loadTypeMode="full",
        transformations=[
            {"name": "agg_gl", "type": "aggregate", "groupByColumns": "customer_id", "aggregateExpression": "SUM(amount) AS total_amount"},
            {"name": "final", "type": "select_columns", "columns": "customer_id, total_amount"},
        ],
    )
    full_load = next(c for c in _switch_cases(_child_activities(draft))["cases"] if c["value"] == "FullLoad")["activities"]
    final = next(a for a in full_load if a["name"] == "Transform_final_FullLoad")
    assert "INSERT INTO mart.finance_gl_fact" not in final["typeProperties"]["scripts"][0]["text"]
    load = next(a for a in full_load if a["name"] == "LoadToLayerTarget_FullLoad")
    assert "TRUNCATE TABLE mart.finance_gl_fact" in load["typeProperties"]["scripts"][0]["text"]


def test_pipelines_are_grouped_by_layer_in_the_folder_tree():
    draft = _draft(
        medallionLayer="silver",
        curatedEntity="gl_transaction",
        transformations=[{"name": "d", "type": "dedupe", "keyColumns": "id", "orderColumn": "modified_date"}],
    )
    artifacts = build_generated_artifacts(draft)
    master = json.loads(next(a for a in artifacts if a.file_name.endswith("_master.json")).content)
    assert master["properties"]["folder"]["name"] == "metadata-driven/silver"


def test_codegen_emits_every_rule_up_to_the_shared_cap_not_just_five():
    # G1: validation allows MAX_TRANSFORMATION_RULES; codegen used to hard-slice to 5.
    rules = [{"name": f"dedupe_{i}", "type": "dedupe", "keyColumns": "id", "orderColumn": "modified_date"} for i in range(7)]
    draft = _draft(medallionLayer="silver", curatedEntity="customer", loadTypeMode="full", transformations=rules)
    full_load = next(c for c in _switch_cases(_child_activities(draft))["cases"] if c["value"] == "FullLoad")["activities"]
    assert sum(1 for a in full_load if a["name"].startswith("Transform_")) == 7


def test_top_level_pipelines_are_single_concurrency_but_child_is_not():
    # G6: overlapping runs would race on fixed-name staging tables + the watermark.
    artifacts = {a.file_name: json.loads(a.content) for a in build_generated_artifacts(_draft()) if a.file_name.endswith(".json")}
    master = next(v for k, v in artifacts.items() if k.endswith("_master.json"))
    child = next(v for k, v in artifacts.items() if k.endswith("_child.json"))
    assert master["properties"]["concurrency"] == 1
    assert "concurrency" not in child["properties"]


def test_transformation_script_activities_reference_the_target_linked_service():
    # G2: ADF rejects a Script activity with no linkedServiceName.
    draft = _draft(
        medallionLayer="silver",
        curatedEntity="customer",
        loadTypeMode="full",
        transformations=[{"name": "d", "type": "dedupe", "keyColumns": "id", "orderColumn": "modified_date"}],
    )
    full_load = next(c for c in _switch_cases(_child_activities(draft))["cases"] if c["value"] == "FullLoad")["activities"]
    scripts = [a for a in full_load if a["type"] == "Script"]
    assert len(scripts) == 3  # transform, layer load, staging drop
    for a in scripts:
        assert a["linkedServiceName"] == {"referenceName": draft.target_linked_service.name, "type": "LinkedServiceReference"}


def test_join_uses_explicit_projection_and_warns_without_one():
    # G4: `a.*, b.*` duplicates the join keys, which SELECT ... INTO rejects.
    rule = TransformationRule(name="r", type="join", join_entity="curated.customer", join_condition="a.id = b.id", columns="a.*, b.segment")
    sql = transformation_rule_sql(rule, read_from="stg.x", write_to="stg.y", target_table="curated.z")
    assert "SELECT a.*, b.segment\nINTO" in sql
    rule.columns = None
    issues = validate_transformation_rules("silver", [rule])
    assert [i.severity for i in issues] == ["warning"]


def test_runtime_artifacts_can_be_opted_out():
    # Gap #28 — defaults unchanged; each flag drops exactly its own artifact(s).
    all_names = [a.file_name for a in build_generated_artifacts(_draft())]
    assert "globalParameters.json" in all_names and "parameters.json" in all_names
    assert any(n.startswith("TR_") for n in all_names)
    none = [
        a.file_name
        for a in build_generated_artifacts(_draft(generateTrigger=False, generateGlobalParameters=False, generateParametersFile=False))
    ]
    assert "globalParameters.json" not in none and "parameters.json" not in none
    assert not any(n.startswith("TR_") for n in none)
    assert len(none) == len(all_names) - 3


def test_arm_parameter_definition_is_opt_in():
    default_names = [a.file_name for a in build_generated_artifacts(_draft())]
    assert "arm-template-parameters-definition.json" not in default_names
    artifact = next(
        a
        for a in build_generated_artifacts(_draft(generateArmParameterDefinition=True))
        if a.file_name == "arm-template-parameters-definition.json"
    )
    parsed = json.loads(artifact.content)
    assert parsed["Microsoft.DataFactory/factories/linkedServices"]["*"]["properties"]["typeProperties"]["connectionString"] == (
        "|:-connectionString:secureString"
    )


def test_lineage_is_opt_in_and_uses_only_draft_values():
    assert "lineage.json" not in [a.file_name for a in build_generated_artifacts(_draft())]
    draft = _draft(generateLineage=True)
    artifact = next(a for a in build_generated_artifacts(draft) if a.file_name == "lineage.json")
    parsed = json.loads(artifact.content)
    assert parsed["job"]["name"].endswith("_master")
    assert [i["name"] for i in parsed["inputs"]] == [o.object for o in effective_source_objects(draft)]
    assert all(i["namespace"] == f"{draft.source_type}://{draft.source_linked_service.name}" for i in parsed["inputs"])
    assert parsed["outputs"][0]["name"] == draft.target_path


def test_silver_on_a_file_target_warns_that_sql_transformations_need_a_sql_store():
    draft = _draft(
        medallionLayer="silver",
        curatedEntity="customer",
        transformations=[{"name": "d", "type": "dedupe", "keyColumns": "id", "orderColumn": "modified_date"}],
    )
    assert any(i.field == "targetType" and i.severity == "warning" for i in validate_draft(draft))


def test_scd2_carries_tracked_columns_and_detects_change_by_row_hash():
    """G3: tracked attributes land in history and drive change detection."""
    rule = TransformationRule(name="h", type="scd2", key_columns="customer_id", order_column="modified_date", columns="name, segment")
    sql = transformation_rule_sql(rule, read_from="stg.x", write_to="curated.c", target_table="curated.c")
    assert "INSERT INTO curated.c (customer_id, name, segment, modified_date, _is_current" in sql
    assert "SELECT src.customer_id, src.name, src.segment, src.modified_date" in sql
    assert "HASHBYTES('SHA2_256', CONCAT_WS('|', ISNULL(CAST(tgt.name AS NVARCHAR(MAX)), CHAR(0)), ISNULL(CAST(tgt.segment AS NVARCHAR(MAX)), CHAR(0)), ISNULL(CAST(tgt.modified_date AS NVARCHAR(MAX)), CHAR(0))))" in sql
    no_cols = validate_transformation_rules("silver", [TransformationRule(name="h", type="scd2", key_columns="k", order_column="d")])
    assert [i.severity for i in no_cols] == ["warning"]


def test_final_load_is_idempotent_truncate_on_full_and_key_delete_on_delta():
    """G5: a rerun never duplicates Silver/Gold rows."""
    full = _draft(
        medallionLayer="silver",
        curatedEntity="gl_transaction",
        loadTypeMode="full",
        transformations=[{"name": "dedupe_gl", "type": "dedupe", "keyColumns": "transaction_id", "orderColumn": "modified_date"}],
    )
    cases = _switch_cases(_child_activities(full))
    full_load = next(c for c in cases["cases"] if c["value"] == "FullLoad")["activities"]
    full_sql = next(a for a in full_load if a["name"] == "LoadToLayerTarget_FullLoad")["typeProperties"]["scripts"][0]["text"]
    assert "TRUNCATE TABLE curated.gl_transaction;" in full_sql
    delta_sql = next(a for a in cases["defaultActivities"] if a["name"] == "LoadToLayerTarget_DeltaLoad")["typeProperties"]["scripts"][0]["text"]
    assert "DELETE tgt FROM curated.gl_transaction tgt WHERE EXISTS" in delta_sql
    assert "tgt.transaction_id = src.transaction_id" in delta_sql
    assert "TRUNCATE" not in delta_sql


def test_silver_chain_reads_the_bronze_landed_table_not_the_source_object():
    """G7: the first transformation reads raw.<source>_<object> on the target store."""
    draft = _draft(
        medallionLayer="silver",
        curatedEntity="customer",
        loadTypeMode="full",
        transformations=[{"name": "dedupe_c", "type": "dedupe", "keyColumns": "id", "orderColumn": "modified_date"}],
    )
    full_load = next(c for c in _switch_cases(_child_activities(draft))["cases"] if c["value"] == "FullLoad")["activities"]
    sql = next(a for a in full_load if a["name"] == "Transform_dedupe_c_FullLoad")["typeProperties"]["scripts"][0]["text"]
    assert "FROM raw.sql_server_customer" in sql
    assert "FROM dbo.customer" not in sql


def test_mapping_pair_splits_on_last_top_level_colon():
    """DMT-06: a colon inside a quoted literal, parentheses or a `::` cast no longer corrupts the pair."""
    from app.generator.medallion import split_mapping_pair

    assert split_mapping_pair("cust_id:customer_id") == ("cust_id", "customer_id")
    assert split_mapping_pair("CASE WHEN t = '12:00' THEN 1 END:is_noon") == ("CASE WHEN t = '12:00' THEN 1 END", "is_noon")
    assert split_mapping_pair("amount::int") == ("amount::int", "")
    assert split_mapping_pair("'12:00'") == ("'12:00'", "")
    assert split_mapping_pair("price:DECIMAL(10,2)") == ("price", "DECIMAL(10,2)")


def test_silver_dq_checks_run_on_the_layer_target_after_the_load():
    """E2E-09: a Silver/Gold rule names a target-layer column, so its check reads the curated table
    after RouteByLoadingBehavior instead of gating the copy on the source object."""
    rule = {"name": "cust_id_nn", "column": "customer_id", "rule": "IS_NOT_NULL", "enforcement": "block"}
    silver = _draft(
        medallionLayer="silver",
        curatedEntity="customer",
        transformations=[{"name": "conform", "type": "conform_columns", "columns": "cust_id:customer_id"}],
        dataQualityRules=[rule],
    )
    activities = _child_activities(silver)
    names = [a["name"] for a in activities]
    assert names.index("RouteByLoadingBehavior") < names.index("DQ_cust_id_nn_Check")
    check = next(a for a in activities if a["name"] == "DQ_cust_id_nn_Check")
    assert check["dependsOn"] == [{"activity": "RouteByLoadingBehavior", "dependencyConditions": ["Succeeded"]}]
    assert "FROM curated.customer WHERE customer_id IS NULL" in check["typeProperties"]["source"]["sqlReaderQuery"]
    assert "dependsOn" not in next(a for a in activities if a["name"] == "RouteByLoadingBehavior")

    bronze = _child_activities(_draft(dataQualityRules=[rule]))
    assert bronze[0]["name"] == "DQ_cust_id_nn_Check"
    assert "@{pipeline().parameters.SchemaName}" in bronze[0]["typeProperties"]["source"]["sqlReaderQuery"]


def test_dq_checks_on_an_scd2_target_read_only_current_rows():
    """RUN-13: a post-load check on an SCD2 target must not count closed history rows."""
    silver = _draft(
        medallionLayer="silver",
        curatedEntity="customer",
        transformations=[{"name": "hist", "type": "scd2", "keyColumns": "customer_id", "orderColumn": "modified_date",
                          "currentFlagColumn": "is_current"}],
        dataQualityRules=[{"name": "cust_uq", "column": "customer_id", "rule": "UNIQUE", "enforcement": "block"}],
    )
    check = next(a for a in _child_activities(silver) if a["name"] == "DQ_cust_uq_Check")
    assert "FROM (SELECT * FROM curated.customer WHERE is_current = 1) cur GROUP BY customer_id" in check["typeProperties"]["source"]["sqlReaderQuery"]
    assert check["description"].endswith("on curated.customer.customer_id.")


def test_trigger_pipeline_reference_has_its_type():
    """RUN-08: ADF rejects a trigger pipelineReference without type PipelineReference."""
    artifacts = build_generated_artifacts(_draft())
    trigger = next(json.loads(a.content) for a in artifacts if a.artifact_type == "trigger")
    for ref in trigger["properties"]["pipelines"]:
        assert ref["pipelineReference"]["type"] == "PipelineReference"


def _all_text(draft: SpecDraft) -> str:
    return "\n".join(a.content for a in build_generated_artifacts(draft))


def test_bronze_landed_table_uses_source_system_when_set():
    """Two systems on the same connector must not share one raw.<connector>_<object> landing table."""
    base = dict(
        medallionLayer="silver",
        curatedEntity="customer",
        loadTypeMode="full",
        transformations=[{"name": "dedupe_c", "type": "dedupe", "keyColumns": "customer_id", "orderColumn": "modified_date"}],
    )
    assert "raw.sql_server_customer" in _all_text(_draft(**base))
    labelled = _all_text(_draft(**base, sourceSystem="Epic Clarity"))
    assert "raw.epic_clarity_customer" in labelled
    assert "raw.sql_server_customer" not in labelled


def test_chain_check_warns_when_a_later_step_uses_a_column_an_earlier_projection_dropped():
    rules = [
        TransformationRule(name="conform", type="conform_columns", columns="PAT_ID:patient_id, PAT_NAME:patient_name"),
        TransformationRule(name="dedupe", type="dedupe", key_columns="patient_id", order_column="UPDATE_DATE"),
    ]
    warnings = [i for i in validate_transformation_rules("silver", rules) if i.severity == "warning"]
    assert len(warnings) == 1
    assert '"UPDATE_DATE"' in warnings[0].message and '"conform"' in warnings[0].message


def test_chain_check_is_silent_for_pass_through_and_mapped_columns():
    rules = [
        TransformationRule(name="dedupe_first", type="dedupe", key_columns="anything", order_column="whatever"),
        TransformationRule(name="conform", type="conform_columns", columns="PAT_ID:patient_id, UPDATE_DATE:updated_at"),
        TransformationRule(name="cast", type="cast_schema", cast_expression="updated_at:DATETIME2"),
        TransformationRule(name="dedupe", type="dedupe", key_columns="PATIENT_ID", order_column="updated_at"),
    ]
    assert [i for i in validate_transformation_rules("silver", rules) if i.severity == "warning"] == []


def test_chain_check_tracks_aggregate_output_and_stops_after_a_star_projection():
    gold = [
        TransformationRule(name="agg", type="aggregate", group_by_columns="region", aggregate_expression="SUM(amount) AS total"),
        TransformationRule(name="final", type="select_columns", columns="region, total, amount"),
    ]
    warnings = [i for i in validate_transformation_rules("gold", gold) if i.severity == "warning"]
    assert len(warnings) == 1 and '"amount"' in warnings[0].message
    star = [
        TransformationRule(name="join", type="join", join_entity="curated.x", join_condition="a.id = b.id", columns="a.*, b.extra"),
        TransformationRule(name="final", type="select_columns", columns="anything"),
    ]
    assert [i for i in validate_transformation_rules("gold", star) if i.severity == "warning"] == []
