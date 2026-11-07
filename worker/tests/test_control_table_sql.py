"""pytest for `_control_table_sql`'s per-project control-table naming and the
opt-in connection control table (AGENTS.md §26.5/§26.6, docs/DDL.md §2.1).

Regression coverage for a real bug found 2026-09-13: the generated
`adf_control_table.sql`'s CREATE TABLE / INSERT statements hardcoded the
literal `control.adf_control_table`, while the master pipeline's own Lookup
query and `ControlTable`/`WatermarkTable` parameters already read from
`draft.watermark_table` — so any project/team that customized that field away
from the default got a pipeline that queried a table this script never
created. Both `worker/app/generator/codegen.py` and its
`frontend/lib/adf/pipeline-codegen.ts` twin were fixed to emit the DDL against
the *same* reference the pipeline reads from, which is what makes per-team/
per-project control-table schema/table names (Project Settings §5C
`ControlTableSettings`) actually take effect end to end.
"""
from __future__ import annotations

import json
import re

from app.generator.codegen import _connection_control_sql, build_generated_artifacts
from app.generator.spec import SpecDraft

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


def _sql(draft: SpecDraft) -> str:
    artifacts = build_generated_artifacts(draft)
    return next(a for a in artifacts if a.file_name == "adf_control_table.sql").content


def test_generated_sql_uses_t_sql_create_guards_not_if_not_exists():
    """Regression (2026-09-27): `CREATE TABLE IF NOT EXISTS` is Postgres/MySQL syntax and fails on
    Azure SQL / SQL Server, so every generated SQL artifact guards with `OBJECT_ID` instead."""
    draft = _draft(enableCopyLog=True, connectionControlEnabled=True)
    for artifact in build_generated_artifacts(draft):
        if artifact.file_name.endswith(".sql"):
            assert "CREATE TABLE IF NOT EXISTS" not in artifact.content, artifact.file_name
    sql = _sql(draft)
    assert "IF OBJECT_ID(N'control.watermarks', N'U') IS NULL\nCREATE TABLE control.watermarks (" in sql
    assert "IF OBJECT_ID(N'control.adf_execution_log', N'U') IS NULL" in sql


def test_control_table_ddl_uses_the_default_reference():
    sql = _sql(_draft())
    assert "CREATE TABLE control.watermarks (" in sql
    assert "INSERT INTO control.watermarks (" in sql
    assert "control.adf_control_table" not in sql


def test_control_table_ddl_follows_a_custom_per_project_schema_and_table():
    """A team that points a pipeline's control table at its own schema/table
    (whether typed directly or prefilled from Project Settings) must get DDL
    that creates and seeds that exact table — not the hardcoded default."""
    sql = _sql(_draft(watermarkTable="acme_team.control_table"))
    assert "CREATE TABLE acme_team.control_table (" in sql
    assert "INSERT INTO acme_team.control_table (" in sql
    assert "control.watermarks" not in sql
    assert "control.adf_control_table" not in sql


def test_generated_ddl_matches_what_the_master_pipeline_actually_queries():
    draft = _draft(watermarkTable="acme_team.control_table")
    artifacts = build_generated_artifacts(draft)
    master = next(a for a in artifacts if a.file_name.endswith("_master.json"))
    master_json = json.loads(master.content)
    control_table_param = master_json["properties"]["parameters"]["ControlTable"]["defaultValue"]
    sql = _sql(draft)
    assert control_table_param == "acme_team.control_table"
    assert "CREATE TABLE acme_team.control_table (" in sql


def test_master_pipeline_reads_control_table_and_task_id_filter_as_runtime_parameters():
    """Real bug found 2026-09-20: the Lookup query interpolated the control table name at
    generation time, so the declared `ControlTable` pipeline parameter had no effect if an
    operator overrode it when triggering an already-deployed pipeline — unlike its sibling
    `WatermarkTable` parameter, which the child pipeline already reads via
    `@{pipeline().parameters.WatermarkTable}`. Fixed by making the query read
    `@{pipeline().parameters.ControlTable}` at runtime; a new `TaskIdFilter` parameter
    (0 = every enabled row, the default) restarts a single control-table row without touching
    every other row's `Enabled` flag or redeploying."""
    draft = _draft(watermarkTable="acme_team.control_table")
    artifacts = build_generated_artifacts(draft)
    master = next(a for a in artifacts if a.file_name.endswith("_master.json"))
    master_json = json.loads(master.content)
    query = master_json["properties"]["activities"][0]["typeProperties"]["source"]["sqlReaderQuery"]
    assert "FROM @{pipeline().parameters.ControlTable}" in query
    assert "@{pipeline().parameters.TaskIdFilter} = 0 OR TaskId = @{pipeline().parameters.TaskIdFilter}" in query
    assert "FROM acme_team.control_table" not in query
    task_id_filter_param = master_json["properties"]["parameters"]["TaskIdFilter"]
    assert task_id_filter_param == {"type": "Int", "defaultValue": 0}


def test_connection_control_table_is_not_emitted_by_default():
    sql = _sql(_draft())
    assert "adf_connection_control" not in sql


def test_connection_control_table_emits_one_row_per_distinct_linked_service():
    sql = _sql(
        _draft(
            connectionControlEnabled=True,
            connectionControlTable="acme_team.adf_connection_control",
        )
    )
    assert "CREATE TABLE acme_team.adf_connection_control (" in sql
    assert "N'LS_sql_server_source', N'sql_server', N'LS_sql_server_source'" in sql
    assert "N'LS_adls_target', N'azure_data_lake_storage_gen2', N'LS_adls_target'" in sql


def test_connection_control_table_splits_secret_ref_from_non_secret_properties():
    # `password` is the sql_server connector's isSecret field for sql_authentication —
    # its value (a Key Vault secret *name*, never the secret) belongs in SecretRef,
    # not Properties.
    draft = _draft(connectionControlEnabled=True)
    rows = _connection_control_sql(draft)
    insert_line = next(line for line in rows if line.startswith("  N'LS_sql_server_source'"))
    assert "N'sqlserver-password'" in insert_line
    properties_match = re.search(r", (N'\{.*\}'|NULL), 1$", insert_line)
    assert properties_match, insert_line
    properties_raw = properties_match.group(1)
    assert properties_raw != "NULL"
    properties_json = json.loads(properties_raw[2:-1])
    assert properties_json == {"database": "AdventureWorks", "server": "sqlserver01.contoso.com", "userName": "svc_adf"}
    assert "sqlserver-password" not in properties_json.values()


def test_connection_control_table_dedupes_a_shared_linked_service():
    """Source and target pointed at the same saved connection (§24.6
    pick-or-create) must produce one row, not a duplicate/UNIQUE-violating one."""
    shared = {
        "name": "LS_shared_sql",
        "connectorType": "sql_server",
        "authType": "sql_authentication",
        "fields": {"server": "shared.contoso.com", "database": "Shared", "userName": "svc", "password": "pw-secret"},
    }
    draft = _draft(connectionControlEnabled=True, sourceLinkedService=shared, targetLinkedService=shared)
    sql = _sql(draft)
    assert sql.count("INSERT INTO control.adf_connection_control (") == 1
