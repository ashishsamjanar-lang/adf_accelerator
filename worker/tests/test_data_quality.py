"""pytest for §30.4 data quality rules — direct port of
`frontend/lib/adf/pipeline-codegen.test.ts`'s `dataQualityActivities` suite and
`frontend/lib/adf/spec.test.ts`'s "data quality rules validation" suite.

`dataQualityRules` previously round-tripped through the spec (both generators'
`build_spec`/`buildSpec`) with no generated behavior behind it
(IMPLEMENTATION_PLAN.md's "Not done" note on gap §30.4) — this covers
`codegen.py`'s `_data_quality_activities` turning each rule into a real
pre-copy Lookup (+ IfCondition/Fail gate when `enforcement` is "block") and
`spec.py`'s `validate_data_quality_rules` gating Generate on the same shape.
"""
from __future__ import annotations

import json

from app.generator.codegen import build_generated_artifacts
from app.generator.spec import SpecDraft, validate_data_quality_rules, DataQualityRule

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


def _child_activities(draft: SpecDraft) -> list[dict]:
    artifacts = build_generated_artifacts(draft)
    child = next(a for a in artifacts if a.file_name.endswith("_child.json"))
    return json.loads(child.content)["properties"]["activities"]


def test_emits_nothing_when_no_rules_are_configured():
    activities = _child_activities(_draft())
    assert [a["name"] for a in activities] == ["RouteByLoadingBehavior"]
    assert "dependsOn" not in activities[0]


def test_block_rule_emits_lookup_and_gate_and_gates_the_switch():
    draft = _draft(
        dataQualityRules=[{"name": "no_nulls", "column": "customer_id", "rule": "IS_NOT_NULL", "enforcement": "block"}]
    )
    activities = _child_activities(draft)
    assert [a["name"] for a in activities] == ["DQ_no_nulls_Check", "DQ_no_nulls_Gate", "RouteByLoadingBehavior"]
    check_query = activities[0]["typeProperties"]["source"]["sqlReaderQuery"]
    assert check_query == (
        "SELECT COUNT(*) AS ViolationCount FROM @{pipeline().parameters.SchemaName}."
        "@{pipeline().parameters.TableName} WHERE customer_id IS NULL"
    )
    assert "outputVariableName" not in activities[0]
    assert activities[1]["typeProperties"]["ifTrueActivities"][0]["type"] == "Fail"
    assert activities[2]["dependsOn"] == [{"activity": "DQ_no_nulls_Gate", "dependencyConditions": ["Succeeded"]}]


def test_default_warn_enforcement_emits_only_a_lookup_and_does_not_gate():
    draft = _draft(dataQualityRules=[{"name": "dup_check", "column": "email", "rule": "UNIQUE"}])
    activities = _child_activities(draft)
    assert [a["name"] for a in activities] == ["DQ_dup_check_Check", "RouteByLoadingBehavior"]
    assert "dependsOn" not in activities[1]


def test_query_per_rule_type():
    schema_table = "@{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}"

    def query_for(rule: dict) -> str:
        activities = _child_activities(_draft(dataQualityRules=[rule]))
        return activities[0]["typeProperties"]["source"]["sqlReaderQuery"]

    assert query_for({"name": "len", "column": "sku", "rule": "LENGTH_LE", "value": "20"}) == (
        f"SELECT COUNT(*) AS ViolationCount FROM {schema_table} WHERE LEN(sku) > 20"
    )
    assert query_for({"name": "rng", "column": "age", "rule": "RANGE_BETWEEN", "value": "0,120"}) == (
        f"SELECT COUNT(*) AS ViolationCount FROM {schema_table} WHERE age < 0 OR age > 120"
    )
    assert query_for({"name": "fmt", "column": "code", "rule": "REGEX_MATCH", "value": "AB%"}) == (
        f"SELECT COUNT(*) AS ViolationCount FROM {schema_table} WHERE PATINDEX('AB%', code) = 0"
    )


def test_sanitizes_a_free_text_rule_name_into_a_safe_activity_name():
    draft = _draft(
        dataQualityRules=[{"name": "no nulls! (v2)", "column": "id", "rule": "IS_NOT_NULL", "enforcement": "block"}]
    )
    activities = _child_activities(draft)
    assert [a["name"] for a in activities] == [
        "DQ_no_nulls___v2__Check",
        "DQ_no_nulls___v2__Gate",
        "RouteByLoadingBehavior",
    ]


def test_skips_a_rule_missing_its_name_or_column():
    draft = _draft(
        dataQualityRules=[
            {"name": "", "column": "id", "rule": "IS_NOT_NULL"},
            {"name": "no_col", "column": "", "rule": "IS_NOT_NULL"},
        ]
    )
    assert [a["name"] for a in _child_activities(draft)] == ["RouteByLoadingBehavior"]


def test_caps_generated_checks_at_5():
    rules = [{"name": f"r{i}", "column": "id", "rule": "IS_NOT_NULL"} for i in range(7)]
    activities = _child_activities(_draft(dataQualityRules=rules))
    assert len([a for a in activities if a["name"].startswith("DQ_")]) == 5


# ---------------------------------------------------------------------------
# validate_data_quality_rules
# ---------------------------------------------------------------------------


def _rule(**overrides) -> DataQualityRule:
    base = {"name": "r", "column": "id", "rule": "IS_NOT_NULL", "value": "", "enforcement": "warn"}
    return DataQualityRule(**{**base, **overrides})


def test_validation_allows_a_fully_filled_in_rule_of_each_type():
    rules = [
        _rule(name="no_nulls", column="id", rule="IS_NOT_NULL", enforcement="block"),
        _rule(name="dup_check", column="email", rule="UNIQUE"),
        _rule(name="len", column="sku", rule="LENGTH_LE", value="20"),
        _rule(name="rng", column="age", rule="RANGE_BETWEEN", value="0,120"),
        _rule(name="fmt", column="code", rule="REGEX_MATCH", value="AB%"),
    ]
    assert validate_data_quality_rules(rules) == []


def test_validation_rejects_more_than_5_rules():
    rules = [_rule(name=f"r{i}") for i in range(6)]
    messages = [i.message for i in validate_data_quality_rules(rules)]
    assert "Maximum 5 data quality rules allowed." in messages


def test_validation_requires_a_name_and_a_column():
    messages = [i.message for i in validate_data_quality_rules([_rule(name="", column="")])]
    assert "Data quality rule 1 needs a name." in messages
    assert any("needs a column" in m for m in messages)


def test_validation_rejects_an_unknown_enforcement_value():
    messages = [i.message for i in validate_data_quality_rules([_rule(enforcement="fail")])]
    assert any('must be "warn" or "block"' in m for m in messages)


def test_validation_requires_a_whole_number_value_for_length_le():
    messages = [i.message for i in validate_data_quality_rules([_rule(rule="LENGTH_LE", value="abc")])]
    assert any("LENGTH_LE" in m for m in messages)


def test_validation_requires_a_valid_min_max_value_for_range_between():
    messages = [i.message for i in validate_data_quality_rules([_rule(rule="RANGE_BETWEEN", value="100,0")])]
    assert any("RANGE_BETWEEN" in m for m in messages)


def test_validation_requires_a_non_empty_pattern_for_regex_match():
    messages = [i.message for i in validate_data_quality_rules([_rule(rule="REGEX_MATCH", value="")])]
    assert any("REGEX_MATCH" in m for m in messages)


def test_row_count_and_freshness_rules_generate_whole_object_checks():
    draft = _draft(
        dataQualityRules=[
            {"name": "has_rows", "column": "", "rule": "ROW_COUNT_MIN", "value": "10", "enforcement": "block"},
            {"name": "fresh", "column": "loaded_at", "rule": "FRESHNESS_HOURS", "value": "24"},
        ]
    )
    activities = _child_activities(draft)
    assert [a["name"] for a in activities] == ["DQ_has_rows_Check", "DQ_has_rows_Gate", "DQ_fresh_Check", "RouteByLoadingBehavior"]
    t = "@{pipeline().parameters.SchemaName}.@{pipeline().parameters.TableName}"
    assert activities[0]["typeProperties"]["source"]["sqlReaderQuery"] == f"SELECT CASE WHEN COUNT(*) < 10 THEN 1 ELSE 0 END AS ViolationCount FROM {t}"
    assert "(ROW_COUNT_MIN) on " in activities[0]["description"] and not activities[0]["description"].endswith("..")
    fail_msg = activities[1]["typeProperties"]["ifTrueActivities"][0]["typeProperties"]["message"]["value"]
    assert "on column" not in fail_msg
    assert activities[2]["typeProperties"]["source"]["sqlReaderQuery"] == (
        f"SELECT CASE WHEN MAX(loaded_at) IS NULL OR MAX(loaded_at) < DATEADD(HOUR, -24, GETUTCDATE()) THEN 1 ELSE 0 END AS ViolationCount FROM {t}"
    )


def test_threshold_rules_validate_and_row_count_needs_no_column():
    ok = validate_data_quality_rules([DataQualityRule(name="has_rows", column="", rule="ROW_COUNT_MIN", value="1")])
    assert ok == []
    bad = validate_data_quality_rules([DataQualityRule(name="fresh", column="loaded_at", rule="FRESHNESS_HOURS", value="soon")])
    assert [i.message for i in bad] == [
        'Data quality rule "fresh" (FRESHNESS_HOURS) needs a whole-number maximum age in hours in Value.'
    ]


def test_rule_names_that_collide_after_sanitizing_are_rejected():
    issues = validate_data_quality_rules(
        [
            DataQualityRule(name="id check", column="id", rule="IS_NOT_NULL"),
            DataQualityRule(name="ID_check", column="id", rule="UNIQUE"),
        ]
    )
    assert [i.message for i in issues] == [
        'Data quality rules "id check" and "ID_check" produce the same activity name — rename one.'
    ]


def test_run_17_regex_syntax_in_regex_match_warns_but_like_patterns_do_not():
    issues = validate_data_quality_rules([_rule(name="email", column="email", rule="REGEX_MATCH", value="^[^@]+@.+$")])
    assert [i.severity for i in issues] == ["warning"] and "PATINDEX" in issues[0].message
    for like in ("%_@_%._%", "[A-Z][A-Z]%", "AB%"):
        assert validate_data_quality_rules([_rule(rule="REGEX_MATCH", value=like)]) == []


def test_dmt_05_max_violation_pct_gates_on_the_pass_rate():
    rule = {"name": "mostly_set", "column": "id", "rule": "IS_NOT_NULL", "enforcement": "block", "maxViolationPct": "5"}
    check, gate = _child_activities(_draft(dataQualityRules=[rule]))[:2]
    query = check["typeProperties"]["source"]["sqlReaderQuery"]
    assert "ViolationCount * 100.0 > 5 * t.TotalRows THEN 1 ELSE 0 END AS ThresholdBreached" in query
    assert "WHERE id IS NULL) v CROSS JOIN (SELECT COUNT(*) AS TotalRows FROM" in query
    assert gate["typeProperties"]["expression"]["value"] == "@equals(int(activity('DQ_mostly_set_Check').output.firstRow.ThresholdBreached), 1)"
    assert "(more than 5% of rows)" in gate["typeProperties"]["ifTrueActivities"][0]["typeProperties"]["message"]["value"]


def test_dmt_05_max_violation_pct_is_validated():
    def messages(**kw):
        return [i.message for i in validate_data_quality_rules([DataQualityRule("r", "id", kw.pop("rule", "IS_NOT_NULL"), kw.pop("value", "1"), "warn", kw["pct"])])]

    assert messages(pct="2.5") == []
    assert any("0 to 100" in m for m in messages(pct="150"))
    assert any("whole-table check" in m for m in messages(pct="5", rule="ROW_COUNT_MIN"))
