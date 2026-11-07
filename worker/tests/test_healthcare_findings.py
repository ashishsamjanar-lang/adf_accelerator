"""IMPLEMENTATION_PLAN.md "Cross-cutting test coverage" #9 — healthcare medallion findings."""
from __future__ import annotations

import json

from app.generator.codegen import build_generated_artifacts
from app.generator.medallion import GOLD_TRANSFORM_TYPES, TransformationRule, transformation_rule_sql
from app.generator.spec import TEMPLATES, SpecDraft
from tests.test_layers import _BASE_DRAFT

_SILVER_DB = {
    "medallionLayer": "silver",
    "curatedEntity": "patient",
    "targetType": "azure_sql_database",
    "sourceObject": "dbo.PATIENT",
    "sourceObjects": [{"object": "dbo.PATIENT"}],
    "sourceSystem": "epic",
    "transformations": [{"name": "dedupe_p", "type": "dedupe", "keyColumns": "id", "orderColumn": "modified_date"}],
}


def _artifacts(**overrides):
    base = dict(_BASE_DRAFT)
    if "targetType" in overrides:  # let from_dict derive the target LS/DS for the overridden connector
        base.pop("targetLinkedService", None)
        base.pop("targetDataset", None)
    return build_generated_artifacts(SpecDraft.from_dict({**base, **overrides}))


def _text(artifacts, suffix: str) -> str:
    return next(a for a in artifacts if a.file_name.endswith(suffix)).content


def test_silver_on_a_database_target_lands_in_the_table_the_chain_reads():
    artifacts = _artifacts(**_SILVER_DB)
    target_ds = json.loads(next(a for a in artifacts if a.artifact_type == "dataset" and "target" in a.file_name.lower()).content)
    params = target_ds["properties"]["parameters"]
    assert (params["SchemaName"]["defaultValue"], params["TableName"]["defaultValue"]) == ("raw", "epic_patient")
    sql = _text(artifacts, "adf_control_table.sql")
    assert "N'raw.epic_patient'" in sql and "dbo.data" not in sql
    assert "FROM raw.epic_patient" in _text(artifacts, "_child.json")


def test_bronze_database_target_keeps_the_dataset_table():
    artifacts = _artifacts(**{**_SILVER_DB, "medallionLayer": "bronze", "transformations": []})
    assert "raw.epic_patient" not in _text(artifacts, "adf_control_table.sql")


def test_scd2_reads_one_row_per_key():
    rule = TransformationRule(name="h", type="scd2", key_columns="provider_id", order_column="updated_at", columns="name")
    sql = transformation_rule_sql(rule, read_from="raw.curated_provider", write_to="mart.dim_provider", target_table="mart.dim_provider")
    latest = "(SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY provider_id ORDER BY updated_at DESC) AS _scd2_rn FROM raw.curated_provider) v WHERE _scd2_rn = 1)"
    assert f"USING {latest} AS src" in sql and f"FROM {latest} src" in sql


def test_full_load_row_seeds_no_default_watermark_column():
    sql = _text(_artifacts(sourceObjects=[{"object": "dbo.a", "loadingBehavior": "FullLoad"}, {"object": "dbo.b"}]), "adf_control_table.sql")
    assert "N'FullLoad', N''," in sql
    assert "N'DeltaLoad', N'modified_date'," in sql


def test_several_objects_to_a_lake_get_their_own_folder():
    sql = _text(_artifacts(targetPath="/bronze/epic/", sourceObjects=[{"object": "dbo.PATIENT"}, {"object": "dbo.ENCOUNTER"}]), "adf_control_table.sql")
    assert "N'/bronze/epic/patient/'" in sql and "N'/bronze/epic/encounter/'" in sql


def test_synapse_is_a_build_source_and_gold_allows_dedupe():
    assert all("azure_synapse_analytics" in t.supported_source_types for t in TEMPLATES.values())
    assert "dedupe" in GOLD_TRANSFORM_TYPES
