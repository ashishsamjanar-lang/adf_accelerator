"""Worker connector-engine tests (AGENTS.md §18, §23.11).

Assert the registry covers the whole ``SourceTargetType`` union and that the
JSON projection matches the frontend reference contract — in particular that a
secret value never appears in generated linked-service JSON (§1.6, §23.5).
"""
from __future__ import annotations

import json

import pytest

from app.connectors import (
    ALL_CONNECTORS,
    CONNECTOR_GROUPS,
    DATABASE_CONNECTORS,
    FILE_CONNECTORS,
    LLM_CONNECTORS,
    SERVICE_CONNECTORS,
    DatasetDefaults,
    DatasetDraft,
    LinkedServiceDraft,
    build_dataset_properties,
    build_linked_service_properties,
    connector_schema,
    default_linked_service_draft,
    registry_payload,
    schema_to_dict,
    validate_linked_service_draft,
)
from app.connectors.schemas import registry_fingerprint

# The full SourceTargetType union from frontend/types/connectors.ts (100 members).
SOURCE_TARGET_TYPES = [
    "azure_blob_storage", "azure_data_lake_storage_gen1", "azure_data_lake_storage_gen2",
    "azure_sql_database", "azure_sql_managed_instance", "azure_synapse_analytics",
    "azure_cosmos_db", "azure_cosmos_db_mongodb", "azure_cosmos_db_postgresql",
    "azure_database_mysql", "azure_database_postgresql", "azure_database_mariadb",
    "azure_table_storage", "azure_files", "azure_data_explorer", "azure_cognitive_search",
    "azure_databricks_delta_lake", "sql_server", "oracle", "mysql", "postgresql", "db2",
    "teradata", "snowflake", "amazon_redshift", "google_big_query", "sap_hana", "sap_bw",
    "sybase", "informix", "netezza", "vertica", "greenplum", "apache_impala", "apache_hbase",
    "apache_phoenix", "amazon_rds_sql_server", "amazon_rds_oracle", "microsoft_access",
    "cassandra", "mongodb", "mongodb_atlas", "couchbase", "presto", "drill", "hive",
    "amazon_s3", "amazon_s3_compatible", "file_system", "ftp", "sftp", "google_cloud_storage",
    "hdfs", "oracle_cloud_storage", "http", "odata", "odbc", "rest", "salesforce",
    "salesforce_service_cloud", "salesforce_marketing_cloud", "dynamics_365", "dynamics_ax",
    "dynamics_crm", "dataverse", "sharepoint_online_list", "microsoft_365",
    "sap_cloud_for_customer", "sap_ecc", "servicenow", "google_adwords", "google_sheets",
    "hubspot", "jira", "marketo", "paypal", "quickbooks", "shopify", "smartsheet", "square",
    "twilio", "xero", "zendesk", "zoho", "github", "web_table", "fabric_lakehouse",
    "fabric_warehouse", "azure_openai", "openai", "anthropic", "aws_bedrock",
    "google_vertex_ai", "cohere", "mistral_ai", "databricks_model_serving",
    "hugging_face_inference", "azure_ai_foundry", "workday", "azure_event_hub",
]

LLM_IDS = {
    "azure_openai", "openai", "anthropic", "aws_bedrock", "google_vertex_ai", "cohere",
    "mistral_ai", "databricks_model_serving", "hugging_face_inference", "azure_ai_foundry",
}


def test_every_source_target_type_has_a_schema():
    missing = [c for c in SOURCE_TARGET_TYPES if c not in ALL_CONNECTORS]
    extra = [c for c in ALL_CONNECTORS if c not in SOURCE_TARGET_TYPES]
    assert not missing, f"connectors without a schema: {missing}"
    assert not extra, f"schemas without a SourceTargetType: {extra}"
    assert len(ALL_CONNECTORS) == len(SOURCE_TARGET_TYPES) == 100


def test_registry_partitions_are_disjoint_and_cover_everything():
    kinds = DATABASE_CONNECTORS + FILE_CONNECTORS + SERVICE_CONNECTORS + LLM_CONNECTORS
    assert sorted(kinds) == sorted(ALL_CONNECTORS)
    assert len(kinds) == len(set(kinds))


def test_llm_connectors_are_rest_service_linked_services():
    assert set(LLM_CONNECTORS) == LLM_IDS
    for cid in LLM_CONNECTORS:
        s = connector_schema(cid)
        assert s.kind == "llm"
        assert s.category == "AI"
        # ADF has no native LLM linked service — RestService (or a native REST type).
        assert s.adf_type in ("RestService",) or s.dataset_type == "RestResource"


def test_fingerprint_is_stable_hex():
    fp = registry_fingerprint()
    assert len(fp) == 64 and int(fp, 16) >= 0


@pytest.mark.parametrize("connector_type", SOURCE_TARGET_TYPES)
def test_schema_serializes_to_frontend_shape(connector_type):
    d = schema_to_dict(connector_schema(connector_type))
    assert d["id"] == connector_type
    assert d["kind"] in ("database", "file", "service", "llm")
    assert d["category"] in ("Azure", "Database", "File", "Service", "AI", "Generic")
    assert any(a["id"] == d["defaultAuth"] for a in d["authOptions"])
    for f in d["fields"]:
        assert {"key", "label", "type"} <= f.keys()
    json.dumps(d)  # must be JSON-serializable


def test_registry_payload_groups_cover_all_connectors():
    payload = registry_payload()
    assert payload["count"] == len(ALL_CONNECTORS)
    grouped = [c for g in payload["groups"] for c in g["connectors"]]
    assert sorted(grouped) == sorted(ALL_CONNECTORS)
    assert [g["category"] for g in CONNECTOR_GROUPS] == [g["category"] for g in payload["groups"]]


def test_api_key_llm_linked_service_keeps_secret_in_key_vault():
    ls = LinkedServiceDraft(
        name="LS_openai",
        connector_type="openai",
        auth_type="api_key",
        fields={"url": "https://api.openai.com/v1", "apiKey": "openai-api-key", "model": "gpt-4o"},
    )
    props = build_linked_service_properties(ls)
    blob = json.dumps(props)
    assert "openai-api-key" in blob  # the *secret name* is referenced ...
    tp = props["typeProperties"]
    assert tp["authenticationType"] == "Anonymous"
    header = tp["authHeaders"]["Authorization"]
    assert header["type"] == "AzureKeyVaultSecret"
    assert header["secretName"] == "openai-api-key"
    assert header["store"]["referenceName"] == "LS_azure_key_vault"
    assert props["type"] == "RestService"


def test_sql_linked_service_password_is_key_vault_reference():
    ls = LinkedServiceDraft(
        name="LS_sql",
        connector_type="azure_sql_database",
        auth_type="sql_authentication",
        fields={"server": "s.database.windows.net", "database": "db", "userName": "svc", "password": "sql-pw"},
    )
    tp = build_linked_service_properties(ls)["typeProperties"]
    assert tp["authenticationType"] == "SQL"
    assert tp["password"]["type"] == "AzureKeyVaultSecret"
    assert tp["password"]["secretName"] == "sql-pw"


def test_parameterized_secret_becomes_linked_service_parameter():
    ls = LinkedServiceDraft(
        name="LS_pg",
        connector_type="postgresql",
        auth_type="basic",
        fields={"server": "pg", "database": "db", "userName": "u", "password": "pg-pw"},
        parameterize=["password"],
    )
    props = build_linked_service_properties(ls)
    assert "password_secret_name" in props["parameters"]
    assert props["parameters"]["password_secret_name"]["defaultValue"] == "pg-pw"


def test_llm_dataset_uses_relative_url_parameter():
    ls = default_linked_service_draft("source", "anthropic")
    ds = DatasetDraft(name="DS_llm", linked_service_name="LS_anthropic", table="messages", parameterized=True)
    props = build_dataset_properties(ds, ls, DatasetDefaults(fallback_name="DS_llm"))
    assert props["type"] == "RestResource"
    assert props["parameters"]["RelativeUrl"]["defaultValue"] == "messages"
    assert props["relativeUrl"] == "@{dataset().RelativeUrl}"


def test_file_dataset_uses_folder_and_file_parameters():
    ls = default_linked_service_draft("source", "azure_blob_storage")
    ds = DatasetDraft(name="DS_blob", linked_service_name="LS_blob", folder_path="raw/customer", file_name="c.csv")
    props = build_dataset_properties(ds, ls, DatasetDefaults(fallback_name="DS_blob"))
    assert props["location"]["type"] == "AzureBlobStorageLocation"
    assert props["parameters"]["FolderPath"]["defaultValue"] == "raw/customer"


def test_validation_flags_missing_required_field():
    ls = LinkedServiceDraft(name="LS_x", connector_type="sql_server", auth_type="sql_authentication", fields={})
    issues = validate_linked_service_draft(ls, "linkedService")
    messages = " ".join(i.message for i in issues)
    assert "Server name" in messages and "Database name" in messages
    assert all(i.severity == "error" for i in issues)


def test_unknown_connector_falls_back_to_generic():
    s = connector_schema("does_not_exist")
    assert s.id == "generic"
    assert s.kind == "database"


# --- §32.5/§32.7 — user-assigned managed identity (gap #21, phase MI2) -----


def test_system_assigned_identity_has_no_credential_block():
    """Default `identity_kind` ("system") is byte-identical to pre-MI2 behavior — no
    `credential` reference anywhere in the projected linked-service JSON."""
    ls = LinkedServiceDraft(
        name="LS_blob", connector_type="azure_blob_storage", auth_type="managed_identity",
        fields={"accountName": "myacct"},
    )
    props = build_linked_service_properties(ls)
    assert "credential" not in props
    assert props["typeProperties"]["authenticationType"] == "ManagedIdentity"


def test_user_assigned_identity_adds_credential_reference():
    ls = LinkedServiceDraft(
        name="LS_blob", connector_type="azure_blob_storage", auth_type="managed_identity",
        fields={"accountName": "myacct"}, identity_kind="user", credential_name="CRED_shared_uami",
        credential_resource_id="/subscriptions/x/resourceGroups/y/providers/Microsoft.ManagedIdentity/userAssignedIdentities/uami1",
    )
    props = build_linked_service_properties(ls)
    assert props["credential"] == {"referenceName": "CRED_shared_uami", "type": "CredentialReference"}
    # Auth type in `typeProperties` is unaffected by identity_kind — ADF still expects
    # `ManagedIdentity`/`ManagedServiceIdentity` there; the `credential` block is what tells it
    # *which* identity.
    assert props["typeProperties"]["authenticationType"] == "ManagedIdentity"


def test_user_assigned_identity_without_credential_name_is_ignored():
    """A half-filled draft (identity_kind flipped to "user" but no credential picked yet)
    must not emit a dangling `credential` reference to nothing."""
    ls = LinkedServiceDraft(
        name="LS_blob", connector_type="azure_blob_storage", auth_type="managed_identity",
        fields={"accountName": "myacct"}, identity_kind="user", credential_name="",
    )
    props = build_linked_service_properties(ls)
    assert "credential" not in props


def test_storage_managed_identity_option_supports_user_assigned():
    schema = connector_schema("azure_blob_storage")
    mi = next(a for a in schema.auth_options if a.id == "managed_identity")
    assert mi.supports_user_assigned_identity is True


def test_onprem_sql_server_has_no_managed_identity_option_at_all():
    """§23.12 gap #23 — no Azure AD trust relationship on-prem, so there's no
    `supports_user_assigned_identity` flag to even check here."""
    schema = connector_schema("sql_server")
    assert not any(a.id == "managed_identity" for a in schema.auth_options)
