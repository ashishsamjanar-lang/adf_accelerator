"""pytest for app/validator/rules.py (AGENTS.md §17, §23.12 gap #20).

No fixtures needed — every rule here is a pure function over a spec dict /
artifact list, so these are plain unit tests.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.generator.codegen import GeneratedArtifact
from app.validator.rules import (
    ARTIFACT_NAME_RE,
    run_validation,
    scan_for_secrets,
    validate_artifacts,
    validate_git,
    validate_security_posture,
    validate_spec,
)


def _artifact(file_name: str, content: str, file_type: str = "json") -> GeneratedArtifact:
    return GeneratedArtifact(file_name, file_type, "pipeline", "pipeline", content)


# ---------------------------------------------------------------------------
# validate_spec — §17.1-4
# ---------------------------------------------------------------------------


def _base_spec(**overrides) -> dict:
    spec = {
        "pipeline": {"type": "incremental_load", "name": "pl_customer_incremental"},
        "source": {"object": "dbo.customer", "type": "sql_server"},
        "target": {"path": "/customer/", "type": "azure_data_lake_storage_gen2"},
        "watermark": {"enabled": False},
        "loadType": {"mode": "incremental"},
    }
    spec.update(overrides)
    return spec


def test_validate_spec_accepts_a_well_formed_spec():
    assert validate_spec(_base_spec()) == []


def test_validate_spec_rejects_unknown_template():
    issues = validate_spec(_base_spec(pipeline={"type": "not_a_real_template", "name": "pl_x"}))
    assert any(i.field == "templateId" and i.severity == "error" for i in issues)


def test_validate_spec_requires_name():
    issues = validate_spec(_base_spec(pipeline={"type": "incremental_load", "name": ""}))
    assert any(i.field == "name" for i in issues)


def test_validate_spec_rejects_bad_name_shape():
    issues = validate_spec(_base_spec(pipeline={"type": "incremental_load", "name": "PL Has Spaces"}))
    assert any(i.field == "name" for i in issues)


def test_validate_spec_requires_source_and_target():
    issues = validate_spec(_base_spec(source={"object": "", "type": "sql_server"}, target={"path": "", "type": "x"}))
    fields = {i.field for i in issues}
    assert "source.object" in fields
    assert "target.path" in fields


def test_validate_spec_requires_watermark_column_when_enabled():
    spec = _base_spec(watermark={"enabled": True, "columns": []})
    issues = validate_spec(spec)
    assert any(i.field == "watermark.columns" and "no watermark column" in i.message for i in issues)


def test_validate_spec_rejects_more_than_five_watermark_columns():
    spec = _base_spec(watermark={"enabled": True, "columns": list(range(6)), "watermarkTable": "control.wm"})
    issues = validate_spec(spec)
    assert any("at most 5 columns" in i.message for i in issues)


def test_validate_spec_requires_watermark_table_for_incremental():
    spec = _base_spec(watermark={"enabled": True, "columns": ["modified_date"], "watermarkTable": ""})
    issues = validate_spec(spec)
    assert any(i.field == "watermark.watermarkTable" for i in issues)


def test_validate_spec_skips_watermark_rules_for_full_load():
    spec = _base_spec(
        watermark={"enabled": True, "columns": [], "watermarkTable": ""},
        loadType={"mode": "full"},
    )
    issues = validate_spec(spec)
    assert not any(i.field and i.field.startswith("watermark") for i in issues)


# ---------------------------------------------------------------------------
# scan_for_secrets — §1.6 / §23.5
# ---------------------------------------------------------------------------


def test_scan_for_secrets_flags_a_literal_password_in_a_connection_string():
    issues = scan_for_secrets("Server=x;Database=y;Password=Sup3rSecret!;")
    assert len(issues) == 1
    assert issues[0].severity == "error"


def test_scan_for_secrets_flags_an_aws_access_key():
    issues = scan_for_secrets('{"accessKeyId": "AKIAABCDEFGHIJKLMNOP"}')
    assert len(issues) == 1


def test_scan_for_secrets_ignores_key_vault_references():
    content = (
        '{"password": {"type": "AzureKeyVaultSecret", "store": {"referenceName": "LS_KeyVault"}, '
        '"secretName": "sqlserver-password"}}'
    )
    assert scan_for_secrets(content) == []


def test_scan_for_secrets_ignores_pipeline_expressions():
    # Not a real secret shape, but must not false-positive against expression syntax.
    assert scan_for_secrets('{"value": "@pipeline().parameters.WatermarkColumn"}') == []


# ---------------------------------------------------------------------------
# validate_artifacts — §17.5-6, §1.6
# ---------------------------------------------------------------------------


def test_validate_artifacts_requires_at_least_one_selected():
    issues = validate_artifacts([_artifact("PL_x.json", "{}")], selected=[])
    assert any("Select at least one artifact" in i.message for i in issues)


def test_validate_artifacts_flags_invalid_json():
    issues = validate_artifacts([_artifact("PL_x.json", "{not json")])
    assert any("invalid" in i.message.lower() for i in issues)


def test_validate_artifacts_flags_bad_declared_name():
    content = '{"name": "not a valid name!"}'
    issues = validate_artifacts([_artifact("PL_x.json", content)])
    assert any("violates the configured naming rule" in i.message for i in issues)


def test_validate_artifacts_accepts_a_name_matching_the_default_rule():
    content = '{"name": "PL_customer_incremental_master"}'
    assert ARTIFACT_NAME_RE.match("PL_customer_incremental_master")
    issues = validate_artifacts([_artifact("PL_customer_incremental_master.json", content)])
    assert issues == []


def test_validate_artifacts_rejects_path_like_file_names():
    issues = validate_artifacts([_artifact("../etc/PL_x.json", "{}")])
    assert any("bare file name" in i.message for i in issues)


def test_validate_artifacts_scans_text_artifacts_for_secrets_too():
    issues = validate_artifacts([_artifact("adf_control_table.sql", "-- Password=hunter2hunter2;", file_type="sql")])
    assert any("hardcoded secret" in i.message for i in issues)


def test_validate_artifacts_skips_unselected_artifacts():
    good = _artifact("PL_ok.json", '{"name": "PL_ok"}')
    bad = _artifact("PL_bad.json", "{not json")
    issues = validate_artifacts([good, bad], selected=["PL_ok.json"])
    assert issues == []


# ---------------------------------------------------------------------------
# validate_artifacts — §25 opt-in naming standard (naming_convention)
# ---------------------------------------------------------------------------


def _naming_convention(enforcement: str, **rule: object) -> dict:
    return {"enforcement": enforcement, "rules": {"pipeline": rule}}


def test_validate_artifacts_ignores_naming_convention_when_enforcement_is_off():
    convention = _naming_convention("off", prefix="PL_", suffix="_v1")
    issues = validate_artifacts([_artifact("x.json", '{"name": "PL_customer"}')], naming_convention=convention)
    assert issues == []


def test_validate_artifacts_warns_on_a_warn_level_naming_violation():
    convention = _naming_convention("warn", suffix="_v1")
    issues = validate_artifacts([_artifact("x.json", '{"name": "PL_customer"}')], naming_convention=convention)
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert "Naming standard" in issues[0].message


def test_validate_artifacts_fails_on_a_block_level_naming_violation():
    convention = _naming_convention("block", suffix="_v1")
    issues = validate_artifacts([_artifact("x.json", '{"name": "PL_customer"}')], naming_convention=convention)
    assert len(issues) == 1
    assert issues[0].severity == "error"


def test_validate_artifacts_accepts_a_name_matching_the_naming_convention():
    convention = _naming_convention("block", prefix="PL_", suffix="_v1")
    issues = validate_artifacts([_artifact("x.json", '{"name": "PL_customer_v1"}')], naming_convention=convention)
    assert issues == []


# ---------------------------------------------------------------------------
# validate_git — §17.7-8 / §23.6
# ---------------------------------------------------------------------------


def test_validate_git_requires_repository_url():
    issues = validate_git(repository_url="", feature_branch="feat/x", base_branch="dev")
    assert any(i.field == "git.repositoryUrl" for i in issues)


def test_validate_git_requires_feature_branch():
    issues = validate_git(repository_url="https://github.com/x/y", feature_branch="", base_branch="dev")
    assert any(i.field == "git.featureBranch" for i in issues)


def test_validate_git_blocks_pushing_to_the_protected_base_branch():
    issues = validate_git(repository_url="https://github.com/x/y", feature_branch="dev", base_branch="dev")
    assert any("protected base branch" in i.message for i in issues)


def test_validate_git_accepts_a_valid_feature_branch():
    issues = validate_git(repository_url="https://github.com/x/y", feature_branch="feat/customer", base_branch="dev")
    assert issues == []


# ---------------------------------------------------------------------------
# run_validation — aggregate report + state
# ---------------------------------------------------------------------------


def test_run_validation_passes_for_a_clean_spec_and_artifact_set():
    report = run_validation(_base_spec(), [_artifact("PL_ok.json", '{"name": "PL_ok", "properties": {"activities": []}}')])
    assert report.state == "passed"
    assert report.ok


def test_run_validation_fails_when_any_rule_errors():
    report = run_validation(_base_spec(pipeline={"type": "incremental_load", "name": ""}), [])
    assert report.state == "failed"
    assert not report.ok


def test_run_validation_includes_git_rules_only_when_requested():
    spec = _base_spec()
    without_git = run_validation(spec, [], include_git=False)
    assert not any(i.field and i.field.startswith("git.") for i in without_git.issues)

    with_git = run_validation(spec, [], repository_url="", feature_branch="", base_branch="dev", include_git=True)
    assert any(i.field and i.field.startswith("git.") for i in with_git.issues)


# ---------------------------------------------------------------------------
# validate_security_posture — §29.1 layer 4
# ---------------------------------------------------------------------------


def _linked_service(name: str, type_properties: dict) -> GeneratedArtifact:
    import json

    body = {"name": name, "properties": {"type": "HttpServer", "typeProperties": type_properties}}
    return GeneratedArtifact(f"{name}.json", "json", "linked_service", "linkedService", json.dumps(body))


def test_security_posture_flags_weak_transport_and_anonymous_auth():
    artifact = _linked_service(
        "LS_http",
        {
            "url": "http://api.contoso.com",
            "authenticationType": "Anonymous",
            "enableServerCertificateValidation": False,
            "connectionString": "Server=x;Encrypt=False;TrustServerCertificate=True",
        },
    )
    issues = validate_security_posture([artifact])
    assert all(i.severity == "warning" and i.layer == "security" for i in issues)
    joined = " ".join(i.message for i in issues)
    for fragment in ("plain HTTP", "Anonymous", "enableServerCertificateValidation", "Encrypt=False", "TrustServerCertificate=True"):
        assert fragment in joined


def test_security_posture_ignores_secure_linked_services_and_unselected_files():
    secure = _linked_service("LS_https", {"url": "https://api.contoso.com", "authenticationType": "Basic"})
    weak = _linked_service("LS_http", {"url": "http://api.contoso.com"})
    assert validate_security_posture([secure]) == []
    assert validate_security_posture([weak], selected=["other.json"]) == []


def test_cross_references_clean_for_a_full_generated_set():
    from app.generator.codegen import build_generated_artifacts
    from app.generator.spec import SpecDraft
    from app.validator.rules import validate_cross_references

    assert validate_cross_references(build_generated_artifacts(SpecDraft.from_dict({"name": "xref_clean"}))) == []


def test_cross_references_warn_on_an_unresolved_reference():
    from app.validator.rules import validate_cross_references

    pipeline = _artifact(
        "PL_a.json",
        '{"name": "PL_a", "properties": {"activities": [{"type": "ExecutePipeline", "typeProperties": '
        '{"pipeline": {"referenceName": "PL_missing", "type": "PipelineReference"}}}, '
        '{"type": "ExecutePipeline", "typeProperties": {"pipeline": {"referenceName": "@item().x", "type": "PipelineReference"}}}]}}',
    )
    issues = validate_cross_references([pipeline])
    assert len(issues) == 1
    assert issues[0].severity == "warning" and "PL_missing" in issues[0].message and issues[0].layer == "semantic"


# §29 layer 2 — structural ADF resource-schema checks
def _schema_artifact(file_name, artifact_type, doc):
    import json as _json

    from app.generator.codegen import GeneratedArtifact

    return GeneratedArtifact(file_name, "json", artifact_type, "pipeline", _json.dumps(doc))


def test_resource_schema_flags_broken_pipeline():
    from app.validator.rules import validate_resource_schema

    doc = {
        "name": "PL_x",
        "properties": {
            "activities": [
                {"name": "A", "type": "Copy"},
                {"name": "A", "type": "Copy"},
                {"name": "B", "type": "Wait", "dependsOn": [{"activity": "Missing"}]},
                {"name": "Loop", "type": "ForEach", "typeProperties": {"activities": [{"name": "C"}]}},
            ]
        },
    }
    issues = validate_resource_schema([_schema_artifact("PL_x.json", "pipeline", doc)])
    messages = " ".join(i.message for i in issues)
    assert all(i.layer == "schema" and i.severity == "error" for i in issues)
    nested = {"name": "P", "properties": {"activities": [
        {"name": "S", "type": "Switch", "typeProperties": {"on": {"value": "@x", "type": "Expression"}, "cases": [{"value": "a", "activities": [{"name": "Log", "type": "Lookup"}]}],
                                                           "defaultActivities": [{"name": "Log", "type": "Lookup"}]}}]}}
    warned = validate_resource_schema([_schema_artifact("P.json", "pipeline", nested)])
    assert [i.severity for i in warned] == ["error"]
    assert "duplicate activity name 'A'" in messages
    assert "depends on 'Missing'" in messages
    assert "missing 'name' or 'type'" in messages


def test_resource_schema_flags_dataset_and_trigger_shape():
    from app.validator.rules import validate_resource_schema

    issues = validate_resource_schema(
        [
            _schema_artifact("DS_x.json", "dataset", {"name": "DS_x", "properties": {"type": "Parquet"}}),
            _schema_artifact("TR_x.json", "trigger", {"name": "TR_x", "properties": {"type": "ScheduleTrigger"}}),
            _schema_artifact("TR_y.json", "trigger", {"name": "TR_y", "properties": {"type": "TumblingWindowTrigger", "pipeline": {"pipelineReference": {"referenceName": "PL_x", "type": "PipelineReference"}}}}),
        ]
    )
    assert {i.source for i in issues} == {"DS_x.json", "TR_x.json"}


def test_resource_schema_guards_run_shape_regressions():
    """gaps.md RUN-12 slice: the shapes fixed by RUN-03/05/07/08/16 are errors if they come back."""
    from app.validator.rules import validate_resource_schema

    pipeline = {"name": "PL_x", "properties": {"activities": [
        {"name": "Route", "type": "Switch", "typeProperties": {"expression": "@x", "cases": [], "default": []}},
        {"name": "Loop", "type": "ForEach", "typeProperties": {"items": {"value": "@activity('L').output.firstRow", "type": "Expression"}},
         "activities": []},
        {"name": "Copy", "type": "Copy", "typeProperties": {"truncate": True}},
        {"name": "Old", "type": "Lookup", "outputVariableName": "v",
         "typeProperties": {"source": {"type": "SqlServerSource", "sqlWrapperType": "SqlWrapper", "query": "SELECT 1"}}},
        {"name": "Log", "type": "Lookup", "typeProperties": {"source": {"type": "SqlServerSource", "sqlReaderQuery": "UPDATE t SET x = 1"}}},
    ]}}
    dataset = {"name": "DS_x", "properties": {"type": "AzureSqlTable", "linkedServiceName": {"referenceName": "LS"},
                                              "schema": "dbo", "table": "t", "sqlTableType": "Table"}}
    trigger = {"name": "TR_x", "properties": {"type": "ScheduleTrigger", "pipelines": [{"pipelineReference": {"referenceName": "PL_x"}}]}}
    messages = " ".join(i.message for i in validate_resource_schema([
        _schema_artifact("PL_x.json", "pipeline", pipeline),
        _schema_artifact("DS_x.json", "dataset", dataset),
        _schema_artifact("TR_x.json", "trigger", trigger),
    ]))
    for fragment in ("(Switch) needs 'on'", "'typeProperties.activities'", "'.output.value'", "no 'truncate' property",
                     "under 'properties.typeProperties'", "\"type\": \"PipelineReference\"",
                     "'query'/'sqlWrapper*'/'outputVariableName'", "end the query with a SELECT"):
        assert fragment in messages, fragment


def test_resource_schema_flags_list_shaped_parameters():
    from app.validator.rules import validate_resource_schema

    doc = {"name": "PL_x", "properties": {
        "parameters": [{"name": "TaskId", "type": "Int"}],
        "activities": [{"name": "Run", "type": "ExecutePipeline", "typeProperties": {"parameters": [{"name": "TaskId"}]}}],
    }}
    messages = [i.message for i in validate_resource_schema([_schema_artifact("PL_x.json", "pipeline", doc)])]
    assert any("'properties.parameters' must be a name-keyed object" in m for m in messages)
    assert any("ExecutePipeline 'Run' parameters must be a name-keyed object" in m for m in messages)


def test_resource_schema_accepts_every_generated_artifact():
    from app.generator.codegen import build_generated_artifacts
    from app.generator.spec import SpecDraft, build_spec
    from app.validator.rules import validate_resource_schema

    draft = SpecDraft.from_dict({"name": "orders"})
    artifacts = build_generated_artifacts(draft)
    assert [i for i in validate_resource_schema(artifacts) if i.severity == "error"] == []


@pytest.mark.parametrize("fixture", sorted(Path(__file__).parent.glob("fixtures/spec_draft_*.json")), ids=lambda p: p.stem)
def test_every_generated_fixture_passes_resource_schema(fixture):
    """Guards the object-shaped `parameters` and pipeline-wide unique activity names (both errors now)."""
    from app.generator.codegen import build_generated_artifacts
    from app.generator.spec import SpecDraft
    from app.validator.rules import validate_resource_schema

    with open(fixture, encoding="utf-8") as fh:
        artifacts = build_generated_artifacts(SpecDraft.from_dict(json.load(fh)))
    assert validate_resource_schema(artifacts) == []
