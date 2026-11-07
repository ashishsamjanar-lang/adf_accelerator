"""Metadata-driven connector registry (VENDORED COPY).

Canonical source: ``worker/app/connectors/schemas.py`` -- keep this file
byte-identical to it (the container build contexts are separate, so the module
is vendored rather than imported). ``python -m app.connectors.schemas`` in each
tree prints a fingerprint; the values must match.

Mirrors ``frontend/lib/adf/connections.ts`` (AGENTS.md S23.4, S23.4b, S23.12
gap 2). Add a connector by adding a :class:`ConnectorSchema`, never a bespoke
form (AGENTS.md S2, S22.10).

Owns:
  * the connector schema registry (fields per connector / per auth mechanism);
  * derived connector groupings for pickers.

Secrets rule (AGENTS.md S1.6, S25.5): ``is_secret`` fields carry the *Key Vault
secret name* only -- never a secret value. The JSON projection in
:mod:`app.connectors.linked_service` renders them as ``AzureKeyVaultSecret``
dynamic content.

Canonical source is ``worker/app/connectors/schemas.py`` (separate container
build contexts); :func:`registry_fingerprint` must match between the two.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

REGISTRY_VERSION = "2026-09-11"

FieldType = Literal["text", "password", "select", "number", "boolean"]
ConnectorKind = Literal["database", "file", "service", "llm"]
ConnectorCategory = Literal["Azure", "Database", "File", "Service", "AI", "Generic"]


@dataclass(frozen=True)
class ConnectionField:
    """A single linked-service form field.

    ``is_secret`` fields never hold the secret itself: the user enters the Azure
    Key Vault *secret name* and the generated ADF JSON references it via an
    ``AzureKeyVaultSecret`` block.
    """

    key: str
    label: str
    type: FieldType = "text"
    required: bool = False
    required_for: tuple[str, ...] = ()
    placeholder: str = ""
    help: str = ""
    options: tuple[tuple[str, str], ...] = ()  # (label, value)
    is_secret: bool = False


@dataclass(frozen=True)
class ConnectorAuthOption:
    id: str
    label: str
    description: str = ""
    # §32.6 — true only where ADF's `credential` reference block (user-assigned managed
    # identity) is actually valid for this connector; conservatively False everywhere else
    # until verified against the live ADF schema (§32.10 open assumption #1). Never True on
    # an on-prem/self-hosted-IR-only auth option (no Azure AD trust relationship to a UAMI).
    supports_user_assigned_identity: bool = False


@dataclass(frozen=True)
class ConnectorSchema:
    id: str
    display_name: str
    adf_type: str
    dataset_type: str
    kind: ConnectorKind
    default_auth: str
    auth_options: tuple[ConnectorAuthOption, ...]
    fields: tuple[ConnectionField, ...]
    dataset_location_type: str | None = None
    category: ConnectorCategory | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# Shared reusable field fragments
# ---------------------------------------------------------------------------

CLOUD_TYPE_FIELD = ConnectionField(
    key="azureCloudType",
    label="Azure cloud type",
    type="select",
    placeholder="AzurePublic",
    options=(
        ("AzurePublic", "AzurePublic"),
        ("AzureChina", "AzureChina"),
        ("AzureUsGovernment", "AzureUsGovernment"),
    ),
)

USERNAME_FIELD = ConnectionField(key="userName", label="User name", required=True, placeholder="dbuser")

PASSWORD_FIELD = ConnectionField(
    key="password",
    label="Password secret name",
    is_secret=True,
    required=True,
    placeholder="db-password",
    help="Name of the Key Vault secret holding the password.",
)

SPN_FIELDS: tuple[ConnectionField, ...] = (
    ConnectionField(key="servicePrincipalId", label="Service principal ID", required_for=("service_principal",), placeholder="00000000-0000-0000-0000-000000000000"),
    ConnectionField(key="servicePrincipalKey", label="SPN key secret name", is_secret=True, required_for=("service_principal",), placeholder="spn-key", help="Name of the Key Vault secret holding the SPN key."),
    ConnectionField(key="tenant", label="Tenant ID", required_for=("service_principal",), placeholder="tenant-id"),
)

_AZURE_SQL_FIELDS: tuple[ConnectionField, ...] = (
    ConnectionField(key="server", label="Server name", required=True, placeholder="myserver.database.windows.net", help="Fully-qualified server host name."),
    ConnectionField(key="database", label="Database name", required=True, placeholder="mydatabase"),
    ConnectionField(key="encrypt", label="Encryption method", type="select", options=(("Strict (recommended)", "Strict"), ("Mandatory", "Mandatory"), ("Optional", "Optional"))),
    ConnectionField(key="trustServerCertificate", label="Trust server certificate", type="boolean", help="Maps to TrustServerCertificate in the connection."),
    ConnectionField(key="userName", label="User name", required_for=("sql_authentication",), placeholder="sqladmin"),
    ConnectionField(key="password", label="Password secret name", is_secret=True, required_for=("sql_authentication",), placeholder="sqlserver-password", help="Name of the Key Vault secret holding the password."),
    *SPN_FIELDS,
    CLOUD_TYPE_FIELD,
)

_SQL_SERVER_FIELDS: tuple[ConnectionField, ...] = (
    ConnectionField(key="server", label="Server name", required=True, placeholder="sqlserver01.contoso.com"),
    ConnectionField(key="database", label="Database name", required=True, placeholder="AdventureWorks"),
    ConnectionField(key="encrypt", label="Encryption method", type="select", options=(("Mandatory", "Mandatory"), ("Strict", "Strict"), ("Optional", "Optional"))),
    ConnectionField(key="trustServerCertificate", label="Trust server certificate", type="boolean"),
    ConnectionField(key="userName", label="User name", required_for=("sql_authentication", "windows_authentication"), placeholder="sa"),
    ConnectionField(key="password", label="Password secret name", is_secret=True, required_for=("sql_authentication", "windows_authentication"), placeholder="sqlserver-password", help="Name of the Key Vault secret holding the password."),
)

_STORAGE_FIELDS: tuple[ConnectionField, ...] = (
    ConnectionField(key="accountName", label="Storage account name", required=True, placeholder="mystorageaccount"),
    ConnectionField(key="url", label="Endpoint URL", required=True, placeholder="https://mystorageaccount.dfs.core.windows.net", help="DFS endpoint for ADLS Gen2 / blob endpoint for Blob Storage."),
    ConnectionField(key="accountKey", label="Account key secret name", is_secret=True, required_for=("account_key",), placeholder="storage-account-key"),
    ConnectionField(key="sasToken", label="SAS token secret name", is_secret=True, required_for=("sas_uri",), placeholder="storage-sas-token"),
    ConnectionField(key="sasUri", label="SAS URI", placeholder="https://account.blob.core.windows.net?sv=..."),
    *SPN_FIELDS,
    CLOUD_TYPE_FIELD,
)

_STORAGE_AUTH_OPTIONS: tuple[ConnectorAuthOption, ...] = (
    ConnectorAuthOption("account_key", "Storage account key", "Account key stored in Key Vault."),
    ConnectorAuthOption("sas_uri", "SAS token", "SAS token stored in Key Vault."),
    ConnectorAuthOption("service_principal", "Service principal"),
    ConnectorAuthOption("managed_identity", "Managed identity", supports_user_assigned_identity=True),
    ConnectorAuthOption("connection_string", "Connection string", "Full connection string (no inline secrets)."),
)

_DATABASE_AUTH_OPTIONS: tuple[ConnectorAuthOption, ...] = (
    ConnectorAuthOption("sql_authentication", "SQL authentication", "User name + password stored in Key Vault."),
    ConnectorAuthOption("windows_authentication", "Windows authentication"),
    ConnectorAuthOption(
        "managed_identity", "Managed identity", "System-assigned managed identity of the factory.",
        supports_user_assigned_identity=True,
    ),
)

BASIC_AUTH_ONLY: tuple[ConnectorAuthOption, ...] = (ConnectorAuthOption("basic", "Basic authentication"),)

_USER_PASS_AUTH = ("basic", "sql_authentication", "windows_authentication", "username_password")

OAUTH2_FIELDS: tuple[ConnectionField, ...] = (
    ConnectionField(key="tokenEndpoint", label="Token endpoint", required_for=("oauth2",), placeholder="https://login.example.com/oauth2/token"),
    ConnectionField(key="clientId", label="Client ID", required_for=("oauth2",), placeholder="client-id"),
    ConnectionField(key="clientSecret", label="Client secret name", is_secret=True, required_for=("oauth2",), placeholder="oauth-client-secret", help="Key Vault secret holding the OAuth client secret."),
    ConnectionField(key="scope", label="Scope / resource", placeholder="https://service.example.com/.default"),
)

API_KEY_FIELD = ConnectionField(
    key="apiKey",
    label="API key secret name",
    is_secret=True,
    required_for=("api_key", "bearer_token"),
    placeholder="service-api-key",
    help="Key Vault secret holding the API key / token. The value never leaves Key Vault.",
)

LLM_MODEL_FIELDS: tuple[ConnectionField, ...] = (
    ConnectionField(key="model", label="Default model / deployment", placeholder="gpt-4o / llama-3.1-70b / mistral-large", help="Referenced by Web/REST activities; per-call overrides come from the control table."),
    ConnectionField(key="apiVersion", label="API version", placeholder="2024-10-21"),
)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def relational_schema(
    *,
    id: str,
    display_name: str,
    adf_type: str,
    dataset_type: str,
    description: str,
    server_label: str = "Server name",
    server_placeholder: str = "host.internal",
    port: str | None = None,
    has_database: bool = True,
    has_schema: bool = False,
    auth_options: tuple[ConnectorAuthOption, ...] | None = None,
    default_auth: str = "basic",
    extra_fields: tuple[ConnectionField, ...] = (),
    category: ConnectorCategory | None = None,
) -> ConnectorSchema:
    """The common "server / port / database / user / password" relational store."""
    fields: list[ConnectionField] = [
        ConnectionField(key="server", label=server_label, required=True, placeholder=server_placeholder)
    ]
    if port is not None:
        fields.append(ConnectionField(key="port", label="Port", type="number", placeholder=port))
    if has_database:
        fields.append(ConnectionField(key="database", label="Database name", required=True, placeholder="mydb"))
    if has_schema:
        fields.append(ConnectionField(key="schema", label="Default schema", placeholder="public"))
    fields.append(ConnectionField(key="userName", label="User name", required_for=_USER_PASS_AUTH, placeholder="dbuser"))
    fields.append(ConnectionField(key="password", label="Password secret name", is_secret=True, required_for=_USER_PASS_AUTH, placeholder="db-password", help="Name of the Key Vault secret holding the password."))
    fields.extend(extra_fields)
    return ConnectorSchema(
        id=id,
        display_name=display_name,
        adf_type=adf_type,
        dataset_type=dataset_type,
        kind="database",
        category=category or "Database",
        default_auth=default_auth,
        auth_options=auth_options or BASIC_AUTH_ONLY,
        fields=tuple(fields),
        description=description,
    )


def file_store_schema(
    *,
    id: str,
    display_name: str,
    adf_type: str,
    dataset_location_type: str,
    auth_options: tuple[ConnectorAuthOption, ...],
    default_auth: str,
    fields: tuple[ConnectionField, ...],
    description: str,
) -> ConnectorSchema:
    return ConnectorSchema(
        id=id,
        display_name=display_name,
        adf_type=adf_type,
        dataset_type="DelimitedText",
        dataset_location_type=dataset_location_type,
        kind="file",
        category="File",
        default_auth=default_auth,
        auth_options=auth_options,
        fields=fields,
        description=description,
    )


_DEFAULT_REST_AUTH_OPTIONS: tuple[ConnectorAuthOption, ...] = (
    ConnectorAuthOption("api_key", "API key", "API key stored in Key Vault."),
    ConnectorAuthOption("oauth2", "OAuth 2.0 (client credentials)"),
    ConnectorAuthOption("anonymous", "Anonymous"),
)


def rest_service_schema(
    *,
    id: str,
    display_name: str,
    base_url_placeholder: str,
    description: str,
    adf_type: str = "RestService",
    dataset_type: str = "RestResource",
    kind: Literal["service", "llm"] = "service",
    base_url_label: str = "Base URL",
    auth_options: tuple[ConnectorAuthOption, ...] | None = None,
    default_auth: str = "api_key",
    extra_fields: tuple[ConnectionField, ...] = (),
) -> ConnectorSchema:
    """REST-backed SaaS / application and LLM connectors.

    ADF calls these through Web / REST activities; the linked service is
    ``RestService`` (or a native type where one exists) and every credential is
    a Key Vault secret *name*.
    """
    fields: tuple[ConnectionField, ...] = (
        ConnectionField(key="url", label=base_url_label, required=True, placeholder=base_url_placeholder),
        API_KEY_FIELD,
        ConnectionField(
            key="authHeaderName",
            label="Auth header name",
            placeholder="Authorization" if kind == "llm" else "x-api-key",
            help="Header the API key is sent in (e.g. Authorization, api-key, x-api-key).",
        ),
        *OAUTH2_FIELDS,
        ConnectionField(key="userName", label="User name", required_for=("basic",), placeholder="user"),
        ConnectionField(key="password", label="Password secret name", is_secret=True, required_for=("basic",), placeholder="service-password"),
        *SPN_FIELDS,
        *extra_fields,
    )
    return ConnectorSchema(
        id=id,
        display_name=display_name,
        adf_type=adf_type,
        dataset_type=dataset_type,
        kind=kind,
        category="AI" if kind == "llm" else "Service",
        default_auth=default_auth,
        auth_options=auth_options or _DEFAULT_REST_AUTH_OPTIONS,
        fields=fields,
        description=description,
    )


def saas_schema(
    id: str,
    display_name: str,
    adf_type: str,
    dataset_type: str,
    base_url_placeholder: str,
    description: str,
    extra_fields: tuple[ConnectionField, ...] = (),
) -> ConnectorSchema:
    """Standard "API key or OAuth" object connector for a SaaS product."""
    return rest_service_schema(
        id=id,
        display_name=display_name,
        adf_type=adf_type,
        dataset_type=dataset_type,
        base_url_label="Endpoint / instance URL",
        base_url_placeholder=base_url_placeholder,
        auth_options=(
            ConnectorAuthOption("oauth2", "OAuth 2.0"),
            ConnectorAuthOption("api_key", "API key / token"),
            ConnectorAuthOption("basic", "Basic authentication"),
        ),
        default_auth="oauth2",
        extra_fields=extra_fields,
        description=description,
    )


GENERIC_SCHEMA = ConnectorSchema(
    id="generic",
    display_name="Generic connector",
    adf_type="Generic",
    dataset_type="GenericDataset",
    kind="database",
    default_auth="basic",
    auth_options=(ConnectorAuthOption("basic", "Basic authentication"),),
    fields=(
        ConnectionField(key="url", label="Endpoint URL", placeholder="https://..."),
        USERNAME_FIELD,
        PASSWORD_FIELD,
    ),
    description="Minimal connection form; extra values are passed through to typeProperties.",
)


# ---------------------------------------------------------------------------
# Registry — database / analytics connectors
# ---------------------------------------------------------------------------

_DATABASE_SCHEMAS: dict[str, ConnectorSchema] = {
    "azure_sql_database": ConnectorSchema(
        id="azure_sql_database", display_name="Azure SQL Database", adf_type="AzureSqlDatabase",
        dataset_type="AzureSqlTable", kind="database", default_auth="sql_authentication",
        auth_options=(_DATABASE_AUTH_OPTIONS[0], ConnectorAuthOption("service_principal", "Service principal"), _DATABASE_AUTH_OPTIONS[2]),
        fields=_AZURE_SQL_FIELDS, description="Fully managed PaaS SQL database on Azure.",
    ),
    "azure_sql_managed_instance": ConnectorSchema(
        id="azure_sql_managed_instance", display_name="Azure SQL Managed Instance", adf_type="AzureSqlMI",
        dataset_type="AzureSqlMITable", kind="database", default_auth="sql_authentication",
        # No windows_authentication — MI is Azure-hosted, there's no on-prem Windows domain to
        # authenticate against. service_principal added (MI supports Azure AD/SPN auth like Azure
        # SQL Database does) — needs the SPN fields, hence _AZURE_SQL_FIELDS not _SQL_SERVER_FIELDS
        # (§23.12 gap #22).
        auth_options=(_DATABASE_AUTH_OPTIONS[0], ConnectorAuthOption("service_principal", "Service principal"), _DATABASE_AUTH_OPTIONS[2]),
        fields=_AZURE_SQL_FIELDS,
        description="Managed SQL Server instance on Azure.",
    ),
    "sql_server": ConnectorSchema(
        id="sql_server", display_name="SQL Server", adf_type="SqlServer", dataset_type="SqlServerTable",
        kind="database", default_auth="sql_authentication",
        # No managed_identity — this targets an on-prem/IaaS box behind a self-hosted IR; there's no
        # factory managed identity to authenticate as there (§23.12 gap #22).
        auth_options=(_DATABASE_AUTH_OPTIONS[0], _DATABASE_AUTH_OPTIONS[1]),
        fields=_SQL_SERVER_FIELDS, description="On-premises or IaaS SQL Server (self-hosted IR).",
    ),
    "azure_synapse_analytics": ConnectorSchema(
        id="azure_synapse_analytics", display_name="Azure Synapse Analytics", adf_type="AzureSqlDW",
        dataset_type="AzureSqlDWTable", kind="database", default_auth="sql_authentication",
        auth_options=(_DATABASE_AUTH_OPTIONS[0], ConnectorAuthOption("service_principal", "Service principal"), _DATABASE_AUTH_OPTIONS[2]),
        fields=_AZURE_SQL_FIELDS, description="Dedicated SQL pool in Synapse workspaces.",
    ),
    "oracle": ConnectorSchema(
        id="oracle", display_name="Oracle", adf_type="Oracle", dataset_type="OracleTable", kind="database",
        default_auth="basic", auth_options=BASIC_AUTH_ONLY,
        fields=(
            ConnectionField(key="server", label="Server / TNS alias", required=True, placeholder="oracle01:1521/ORCLPDB1"),
            USERNAME_FIELD, PASSWORD_FIELD,
        ),
        description="Oracle database connector.",
    ),
    "postgresql": ConnectorSchema(
        id="postgresql", display_name="PostgreSQL", adf_type="PostgreSql", dataset_type="PostgreSqlTable",
        kind="database", default_auth="basic", auth_options=BASIC_AUTH_ONLY,
        fields=(
            ConnectionField(key="server", label="Server name", required=True, placeholder="pg01.internal"),
            ConnectionField(key="port", label="Port", type="number", placeholder="5432"),
            ConnectionField(key="database", label="Database name", required=True, placeholder="postgres"),
            ConnectionField(key="schema", label="Default schema", placeholder="public"),
            USERNAME_FIELD, PASSWORD_FIELD,
        ),
        description="On-premises or IaaS PostgreSQL.",
    ),
    "azure_database_postgresql": ConnectorSchema(
        id="azure_database_postgresql", display_name="Azure Database for PostgreSQL", adf_type="AzurePostgreSql",
        dataset_type="AzurePostgreSqlTable", kind="database", default_auth="basic", auth_options=BASIC_AUTH_ONLY,
        fields=(
            ConnectionField(key="server", label="Server name", required=True, placeholder="mypg.postgres.database.azure.com"),
            ConnectionField(key="port", label="Port", type="number", placeholder="5432"),
            ConnectionField(key="database", label="Database name", required=True, placeholder="postgres"),
            USERNAME_FIELD, PASSWORD_FIELD,
        ),
        description="Managed PostgreSQL on Azure.",
    ),
    "mysql": ConnectorSchema(
        id="mysql", display_name="MySQL", adf_type="MySql", dataset_type="MySqlTable", kind="database",
        default_auth="basic", auth_options=BASIC_AUTH_ONLY,
        fields=(
            ConnectionField(key="server", label="Server name", required=True, placeholder="mysql01.internal"),
            ConnectionField(key="port", label="Port", type="number", placeholder="3306"),
            ConnectionField(key="database", label="Database name", required=True, placeholder="sales"),
            USERNAME_FIELD, PASSWORD_FIELD,
        ),
        description="On-premises or IaaS MySQL.",
    ),
    "azure_database_mysql": ConnectorSchema(
        id="azure_database_mysql", display_name="Azure Database for MySQL", adf_type="AzureMySql",
        dataset_type="AzureMySqlTable", kind="database", default_auth="basic", auth_options=BASIC_AUTH_ONLY,
        fields=(
            ConnectionField(key="server", label="Server name", required=True, placeholder="myysql.mysql.database.azure.com"),
            ConnectionField(key="port", label="Port", type="number", placeholder="3306"),
            ConnectionField(key="database", label="Database name", required=True, placeholder="sales"),
            USERNAME_FIELD, PASSWORD_FIELD,
        ),
        description="Managed MySQL on Azure.",
    ),
    "snowflake": ConnectorSchema(
        # The legacy "Snowflake" linked service type is deprecated in favor of SnowflakeV2, which is
        # also the only one of the two that supports key-pair/AAD service-principal auth — this was
        # stuck on the old type with password-only auth (§23.12 gap #25).
        id="snowflake", display_name="Snowflake", adf_type="SnowflakeV2", dataset_type="SnowflakeV2Table",
        kind="database", default_auth="username_password",
        auth_options=(
            ConnectorAuthOption("username_password", "User / password authentication"),
            ConnectorAuthOption("key_pair", "Key pair authentication", "RSA private key stored in Key Vault."),
            ConnectorAuthOption("service_principal", "Service principal (Azure AD)"),
        ),
        fields=(
            ConnectionField(key="accountIdentifier", label="Account identifier", required=True, placeholder="xy12345.east-us-2.azure"),
            ConnectionField(key="warehouse", label="Warehouse", required=True, placeholder="COMPUTE_WH"),
            ConnectionField(key="database", label="Database name", required=True, placeholder="SNOWFLAKE_DB"),
            ConnectionField(key="schema", label="Default schema", placeholder="PUBLIC"),
            ConnectionField(key="role", label="Role", placeholder="LOADER"),
            ConnectionField(key="user", label="User name", required_for=("username_password", "key_pair"), placeholder="svc_loader"),
            ConnectionField(key="password", label="Password secret name", is_secret=True, required_for=("username_password",), placeholder="snowflake-password"),
            ConnectionField(key="privateKey", label="Private key secret name", is_secret=True, required_for=("key_pair",), placeholder="snowflake-private-key", help="PEM-encoded RSA private key stored in Key Vault."),
            ConnectionField(key="privateKeyPassphrase", label="Private key passphrase secret name", is_secret=True, placeholder="snowflake-key-passphrase", help="Only if the private key above is passphrase-protected."),
            ConnectionField(key="clientId", label="Service principal (client) ID", required_for=("service_principal",), placeholder="00000000-0000-0000-0000-000000000000"),
            ConnectionField(key="servicePrincipalCredential", label="Service principal credential secret name", is_secret=True, required_for=("service_principal",), placeholder="snowflake-spn-credential"),
            ConnectionField(key="tenantId", label="Tenant ID", required_for=("service_principal",), placeholder="tenant-id"),
        ),
        description="Snowflake data warehouse (SnowflakeV2 linked service).",
    ),
    "db2": relational_schema(
        id="db2", display_name="IBM DB2", adf_type="Db2", dataset_type="Db2Table",
        port="50000", has_schema=True, description="IBM DB2 (Db2 for LUW / z/OS) via self-hosted IR.",
    ),
    "teradata": relational_schema(
        id="teradata", display_name="Teradata", adf_type="Teradata", dataset_type="TeradataTable",
        has_database=False, has_schema=True, server_placeholder="teradata.contoso.com",
        description="Teradata Vantage / on-prem via self-hosted IR.",
    ),
    "amazon_redshift": relational_schema(
        id="amazon_redshift", display_name="Amazon Redshift", adf_type="AmazonRedshift", dataset_type="AmazonRedshiftTable",
        port="5439", has_schema=True, server_placeholder="cluster.abc123.us-east-1.redshift.amazonaws.com",
        description="Amazon Redshift cluster (direct query or UNLOAD via S3 staging).",
    ),
    "google_big_query": relational_schema(
        id="google_big_query", display_name="Google BigQuery", adf_type="GoogleBigQueryV2", dataset_type="GoogleBigQueryV2Object",
        has_database=False, server_label="Project ID", server_placeholder="my-gcp-project",
        default_auth="service_account_key",
        auth_options=(
            ConnectorAuthOption("service_account_key", "Service account key", "Key JSON stored in Key Vault."),
            ConnectorAuthOption("oauth2", "User authentication (OAuth)"),
        ),
        extra_fields=(
            ConnectionField(key="clientEmail", label="Service account email", required_for=("service_account_key",), placeholder="svc@my-project.iam.gserviceaccount.com"),
            ConnectionField(key="privateKey", label="Private key secret name", is_secret=True, required_for=("service_account_key",), placeholder="bigquery-sa-key"),
            ConnectionField(key="refreshToken", label="Refresh token secret name", is_secret=True, required_for=("oauth2",), placeholder="bigquery-refresh-token"),
        ),
        description="Google BigQuery (V2 connector).",
    ),
    "sap_hana": relational_schema(
        id="sap_hana", display_name="SAP HANA", adf_type="SapHana", dataset_type="SapHanaTable",
        port="30015", has_database=False, server_placeholder="hana.contoso.com",
        auth_options=(ConnectorAuthOption("basic", "Basic authentication"), ConnectorAuthOption("windows_authentication", "Windows authentication")),
        description="SAP HANA database via self-hosted IR.",
    ),
    "sap_bw": relational_schema(
        id="sap_bw", display_name="SAP BW (Open Hub)", adf_type="SapOpenHub", dataset_type="SapOpenHubTable",
        has_database=False, server_placeholder="bw.contoso.com",
        extra_fields=(
            ConnectionField(key="systemNumber", label="System number", required=True, placeholder="00"),
            ConnectionField(key="clientId", label="Client ID", required=True, placeholder="800"),
            ConnectionField(key="language", label="Logon language", placeholder="EN"),
        ),
        description="SAP Business Warehouse via Open Hub Destination.",
    ),
    "sybase": relational_schema(
        id="sybase", display_name="Sybase", adf_type="Sybase", dataset_type="SybaseTable",
        port="5000", has_schema=True, description="SAP Sybase ASE via self-hosted IR.",
    ),
    "informix": relational_schema(
        id="informix", display_name="IBM Informix", adf_type="Informix", dataset_type="InformixTable",
        has_database=True, description="IBM Informix via ODBC on a self-hosted IR.",
    ),
    "netezza": relational_schema(
        id="netezza", display_name="Netezza", adf_type="Netezza", dataset_type="NetezzaTable",
        port="5480", has_schema=True, description="IBM Netezza / PureData (read-only source).",
    ),
    "vertica": relational_schema(
        id="vertica", display_name="Vertica", adf_type="Vertica", dataset_type="VerticaTable",
        port="5433", has_schema=True, description="OpenText / Micro Focus Vertica.",
    ),
    "greenplum": relational_schema(
        id="greenplum", display_name="Greenplum", adf_type="Greenplum", dataset_type="GreenplumTable",
        port="5432", has_schema=True, description="VMware Tanzu Greenplum (PostgreSQL-compatible).",
    ),
    "apache_impala": relational_schema(
        id="apache_impala", display_name="Apache Impala", adf_type="Impala", dataset_type="ImpalaObject",
        port="21050", has_database=False, description="Apache Impala (read-only source).",
    ),
    "apache_hbase": relational_schema(
        id="apache_hbase", display_name="Apache HBase", adf_type="HBase", dataset_type="HBaseObject",
        port="9090", has_database=False, description="Apache HBase via the Thrift gateway (read-only source).",
    ),
    "apache_phoenix": relational_schema(
        id="apache_phoenix", display_name="Apache Phoenix", adf_type="Phoenix", dataset_type="PhoenixObject",
        port="8765", has_database=False, description="Apache Phoenix over HBase (read-only source).",
    ),
    "hive": relational_schema(
        id="hive", display_name="Apache Hive", adf_type="Hive", dataset_type="HiveObject",
        port="10000", has_database=False, description="Apache Hive via HiveServer2 (read-only source).",
    ),
    "presto": relational_schema(
        id="presto", display_name="Presto", adf_type="Presto", dataset_type="PrestoObject",
        port="8080", has_database=True, description="Presto / Trino distributed SQL (read-only source).",
    ),
    "drill": relational_schema(
        id="drill", display_name="Apache Drill", adf_type="Drill", dataset_type="DrillTable",
        port="8047", has_database=False, description="Apache Drill (read-only source).",
    ),
    "couchbase": relational_schema(
        id="couchbase", display_name="Couchbase", adf_type="Couchbase", dataset_type="CouchbaseTable",
        port="8091", has_database=False, description="Couchbase via the Analytics / N1QL service (read-only source).",
    ),
    "cassandra": relational_schema(
        id="cassandra", display_name="Cassandra", adf_type="Cassandra", dataset_type="CassandraTable",
        port="9042", has_database=False, has_schema=True,
        auth_options=(ConnectorAuthOption("basic", "Basic authentication"), ConnectorAuthOption("anonymous", "Anonymous")),
        description="Apache Cassandra (keyspace = schema).",
    ),
    "amazon_rds_sql_server": relational_schema(
        id="amazon_rds_sql_server", display_name="Amazon RDS for SQL Server", adf_type="AmazonRdsForSqlServer",
        dataset_type="AmazonRdsForSqlServerTable", port="1433", has_schema=True,
        auth_options=(ConnectorAuthOption("sql_authentication", "SQL authentication"), ConnectorAuthOption("windows_authentication", "Windows authentication")),
        default_auth="sql_authentication", server_placeholder="db.abc123.us-east-1.rds.amazonaws.com",
        description="SQL Server on Amazon RDS.",
    ),
    "amazon_rds_oracle": relational_schema(
        id="amazon_rds_oracle", display_name="Amazon RDS for Oracle", adf_type="AmazonRdsForOracle",
        dataset_type="AmazonRdsForOracleTable", has_database=False, server_label="Server / TNS",
        server_placeholder="db.abc123.us-east-1.rds.amazonaws.com:1521/ORCL", description="Oracle on Amazon RDS.",
    ),
    "microsoft_access": relational_schema(
        id="microsoft_access", display_name="Microsoft Access", adf_type="MicrosoftAccess",
        dataset_type="MicrosoftAccessTable", has_database=False, server_label="Connection string",
        server_placeholder="Driver={Microsoft Access Driver (*.mdb, *.accdb)};Dbq=\\\\share\\db.accdb;",
        description="Microsoft Access database via ODBC on a self-hosted IR.",
    ),
    "mongodb": ConnectorSchema(
        id="mongodb", display_name="MongoDB", adf_type="MongoDbV2", dataset_type="MongoDbV2Collection",
        kind="database", category="Database", default_auth="connection_string",
        auth_options=(ConnectorAuthOption("connection_string", "Connection string", "mongodb:// URI with the secret referenced from Key Vault."),),
        fields=(
            ConnectionField(key="connectionString", label="Connection string secret name", is_secret=True, required=True, placeholder="mongodb-connection-string"),
            ConnectionField(key="database", label="Database name", required=True, placeholder="appdb"),
        ),
        description="MongoDB (V2 connector); collections are the objects.",
    ),
    "mongodb_atlas": ConnectorSchema(
        id="mongodb_atlas", display_name="MongoDB Atlas", adf_type="MongoDbAtlas", dataset_type="MongoDbAtlasCollection",
        kind="database", category="Database", default_auth="connection_string",
        auth_options=(ConnectorAuthOption("connection_string", "Connection string", "mongodb+srv:// URI referenced from Key Vault."),),
        fields=(
            ConnectionField(key="connectionString", label="Connection string secret name", is_secret=True, required=True, placeholder="mongodb-atlas-connection-string"),
            ConnectionField(key="database", label="Database name", required=True, placeholder="appdb"),
        ),
        description="MongoDB Atlas cloud database.",
    ),
    "azure_cosmos_db": ConnectorSchema(
        id="azure_cosmos_db", display_name="Azure Cosmos DB (NoSQL)", adf_type="CosmosDb",
        dataset_type="CosmosDbSqlApiCollection", kind="database", category="Azure", default_auth="account_key",
        auth_options=(
            ConnectorAuthOption("account_key", "Account key", "Key stored in Key Vault."),
            ConnectorAuthOption("service_principal", "Service principal"),
            ConnectorAuthOption("managed_identity", "Managed identity", supports_user_assigned_identity=True),
        ),
        fields=(
            ConnectionField(key="accountEndpoint", label="Account endpoint", required=True, placeholder="https://myacct.documents.azure.com:443/"),
            ConnectionField(key="database", label="Database name", required=True, placeholder="appdb"),
            ConnectionField(key="accountKey", label="Account key secret name", is_secret=True, required_for=("account_key",), placeholder="cosmos-account-key"),
            *SPN_FIELDS, CLOUD_TYPE_FIELD,
        ),
        description="Azure Cosmos DB for NoSQL (SQL API); containers are the objects.",
    ),
    "azure_cosmos_db_mongodb": ConnectorSchema(
        id="azure_cosmos_db_mongodb", display_name="Azure Cosmos DB (MongoDB API)", adf_type="CosmosDbMongoDbApi",
        dataset_type="CosmosDbMongoDbApiCollection", kind="database", category="Azure", default_auth="connection_string",
        auth_options=(ConnectorAuthOption("connection_string", "Connection string", "Referenced from Key Vault."),),
        fields=(
            ConnectionField(key="connectionString", label="Connection string secret name", is_secret=True, required=True, placeholder="cosmos-mongo-connection-string"),
            ConnectionField(key="database", label="Database name", required=True, placeholder="appdb"),
        ),
        description="Azure Cosmos DB for MongoDB.",
    ),
    "azure_cosmos_db_postgresql": relational_schema(
        id="azure_cosmos_db_postgresql", display_name="Azure Cosmos DB for PostgreSQL", adf_type="AzurePostgreSql",
        dataset_type="AzurePostgreSqlTable", port="5432", has_schema=True,
        server_placeholder="c-mycluster.abc.postgres.cosmos.azure.com", category="Azure",
        description="Azure Cosmos DB for PostgreSQL (Citus) via the Azure Database for PostgreSQL connector.",
    ),
    "azure_database_mariadb": relational_schema(
        id="azure_database_mariadb", display_name="Azure Database for MariaDB", adf_type="MariaDB",
        dataset_type="MariaDBTable", port="3306", category="Azure",
        server_placeholder="mymaria.mariadb.database.azure.com",
        description="Managed MariaDB on Azure (also covers on-prem MariaDB).",
    ),
    "azure_data_explorer": ConnectorSchema(
        id="azure_data_explorer", display_name="Azure Data Explorer (Kusto)", adf_type="AzureDataExplorer",
        dataset_type="AzureDataExplorerTable", kind="database", category="Azure", default_auth="service_principal",
        auth_options=(
            ConnectorAuthOption("service_principal", "Service principal"),
            ConnectorAuthOption("managed_identity", "Managed identity", supports_user_assigned_identity=True),
        ),
        fields=(
            ConnectionField(key="endpoint", label="Cluster URI", required=True, placeholder="https://mycluster.westus.kusto.windows.net"),
            ConnectionField(key="database", label="Database name", required=True, placeholder="telemetry"),
            *SPN_FIELDS,
        ),
        description="Azure Data Explorer / Kusto; tables are the objects.",
    ),
    "azure_databricks_delta_lake": ConnectorSchema(
        id="azure_databricks_delta_lake", display_name="Azure Databricks Delta Lake", adf_type="AzureDatabricksDeltaLake",
        dataset_type="AzureDatabricksDeltaLakeDataset", kind="database", category="Azure", default_auth="personal_access_token",
        auth_options=(
            ConnectorAuthOption("personal_access_token", "Personal access token", "PAT stored in Key Vault."),
            ConnectorAuthOption("managed_identity", "Managed identity", supports_user_assigned_identity=True),
        ),
        fields=(
            ConnectionField(key="domain", label="Workspace URL", required=True, placeholder="https://adb-1234.5.azuredatabricks.net"),
            ConnectionField(key="clusterId", label="Cluster ID", required=True, placeholder="0101-123456-abcd7890"),
            ConnectionField(key="accessToken", label="Access token secret name", is_secret=True, required_for=("personal_access_token",), placeholder="databricks-pat"),
            ConnectionField(key="database", label="Database / schema", placeholder="default"),
        ),
        description="Delta Lake tables in an Azure Databricks workspace.",
    ),
    "fabric_warehouse": relational_schema(
        id="fabric_warehouse", display_name="Microsoft Fabric Warehouse", adf_type="Warehouse", dataset_type="WarehouseTable",
        has_database=True, has_schema=True, server_label="SQL connection string",
        server_placeholder="xxxx.datawarehouse.fabric.microsoft.com", default_auth="service_principal",
        auth_options=(
            ConnectorAuthOption("service_principal", "Service principal"),
            ConnectorAuthOption("managed_identity", "Managed identity", supports_user_assigned_identity=True),
        ),
        extra_fields=(
            ConnectionField(key="workspaceId", label="Workspace ID", required=True, placeholder="00000000-0000-0000-0000-000000000000"),
            ConnectionField(key="artifactId", label="Warehouse ID", required=True, placeholder="00000000-0000-0000-0000-000000000000"),
            *SPN_FIELDS,
        ),
        description="Microsoft Fabric Warehouse (T-SQL endpoint).",
    ),
}


# ---------------------------------------------------------------------------
# Registry — file / object-storage connectors
# ---------------------------------------------------------------------------

_FILE_SCHEMAS: dict[str, ConnectorSchema] = {
    "azure_blob_storage": ConnectorSchema(
        id="azure_blob_storage", display_name="Azure Blob Storage", adf_type="AzureBlobStorage",
        dataset_type="DelimitedText", dataset_location_type="AzureBlobStorageLocation", kind="file",
        default_auth="account_key", auth_options=_STORAGE_AUTH_OPTIONS, fields=_STORAGE_FIELDS,
        description="Blob Storage account (V2 connector).",
    ),
    "azure_data_lake_storage_gen2": ConnectorSchema(
        id="azure_data_lake_storage_gen2", display_name="Azure Data Lake Storage Gen2", adf_type="AzureBlobFS",
        dataset_type="DelimitedText", dataset_location_type="AzureBlobFSLocation", kind="file",
        default_auth="account_key", auth_options=_STORAGE_AUTH_OPTIONS, fields=_STORAGE_FIELDS,
        description="ADLS Gen2 (hierarchical namespace) storage.",
    ),
    "azure_data_lake_storage_gen1": ConnectorSchema(
        id="azure_data_lake_storage_gen1", display_name="Azure Data Lake Storage Gen1", adf_type="AzureDataLakeStore",
        dataset_type="DelimitedText", dataset_location_type="AzureDataLakeStoreLocation", kind="file",
        default_auth="service_principal",
        # ADF's AzureDataLakeStoreLinkedService has no account-key auth at all (Gen1 never had one —
        # dropped here) but does support the factory's managed identity, which was missing (§23.12
        # gap #23).
        auth_options=(
            ConnectorAuthOption("service_principal", "Service principal"),
            ConnectorAuthOption(
                "managed_identity", "Managed identity", "System-assigned managed identity of the factory.",
                supports_user_assigned_identity=True,
            ),
        ),
        fields=(
            ConnectionField(key="dataLakeStoreUri", label="Data Lake Store URI", required=True, placeholder="https://myadls.azuredatalakestore.net/webhdfs/v1"),
            ConnectionField(key="accountName", label="Account name", placeholder="myadls"),
            ConnectionField(key="subscriptionId", label="Subscription ID"),
            ConnectionField(key="resourceGroupName", label="Resource group"),
            *SPN_FIELDS,
        ),
        description="Legacy ADLS Gen1 store. Service principal or the factory's managed identity — no account-key auth (ADF has none for Gen1).",
    ),
    "amazon_s3": ConnectorSchema(
        id="amazon_s3", display_name="Amazon S3", adf_type="AmazonS3", dataset_type="DelimitedText",
        dataset_location_type="AmazonS3Location", kind="file", default_auth="access_key",
        auth_options=(
            ConnectorAuthOption("access_key", "Access key (IAM user)"),
            ConnectorAuthOption("assumed_role", "IAM role", "Uses the factory's IAM role."),
        ),
        fields=(
            ConnectionField(key="serviceUrl", label="Service URL", placeholder="https://s3.amazonaws.com"),
            ConnectionField(key="accessKeyId", label="Access key ID", required_for=("access_key",), placeholder="AKIA..."),
            ConnectionField(key="secretAccessKey", label="Secret access key name", is_secret=True, required_for=("access_key",), placeholder="aws-secret-key"),
            ConnectionField(key="arn", label="IAM role ARN", required_for=("assumed_role",), placeholder="arn:aws:iam::123456789012:role/adf"),
        ),
        description="Amazon S3 object storage.",
    ),
    "google_cloud_storage": ConnectorSchema(
        id="google_cloud_storage", display_name="Google Cloud Storage", adf_type="GoogleCloudStorage",
        dataset_type="DelimitedText", dataset_location_type="GoogleCloudStorageLocation", kind="file",
        default_auth="access_key", auth_options=(ConnectorAuthOption("access_key", "HMAC access key"),),
        fields=(
            ConnectionField(key="serviceUrl", label="Service URL", placeholder="https://storage.googleapis.com"),
            ConnectionField(key="accessKeyId", label="Access key ID", required=True, placeholder="GOOG..."),
            ConnectionField(key="secretAccessKey", label="Secret access key name", is_secret=True, required=True, placeholder="gcs-secret-key"),
        ),
        description="Google Cloud Storage bucket.",
    ),
    "file_system": ConnectorSchema(
        id="file_system", display_name="File system (on-prem)", adf_type="FileServer", dataset_type="DelimitedText",
        dataset_location_type="FileServerLocation", kind="file", default_auth="windows_authentication",
        auth_options=(ConnectorAuthOption("windows_authentication", "Windows / basic authentication"),),
        fields=(
            ConnectionField(key="host", label="Host / UNC path", required=True, placeholder="\\\\fileserver01\\share"),
            ConnectionField(key="userid", label="User name", required=True, placeholder="DOMAIN\\svc_adf"),
            ConnectionField(key="password", label="Password secret name", is_secret=True, required=True, placeholder="fileshare-password"),
            ConnectionField(key="enableSsl", label="Enable SSL", type="boolean"),
        ),
        description="Windows file share via self-hosted IR.",
    ),
    "sftp": ConnectorSchema(
        id="sftp", display_name="SFTP", adf_type="Sftp", dataset_type="DelimitedText",
        dataset_location_type="SftpLocation", kind="file", default_auth="basic",
        auth_options=(
            ConnectorAuthOption("basic", "Basic (user / password)"),
            ConnectorAuthOption("ssh_public_key", "SSH public key"),
        ),
        fields=(
            ConnectionField(key="host", label="Host name", required=True, placeholder="sftp.partner.com"),
            ConnectionField(key="port", label="Port", type="number", placeholder="22"),
            ConnectionField(key="userName", label="User name", required=True, placeholder="svc_sftp"),
            ConnectionField(key="password", label="Password secret name", is_secret=True, required_for=("basic",), placeholder="sftp-password"),
            # Either a path on the self-hosted IR's filesystem, or the key content itself stored in
            # Key Vault — ADF supports both for SshPublicKey auth; only the path was exposed here, so
            # a setup with no self-hosted IR filesystem access had no way to configure a key at all
            # (§23.12 gap #26). Deliberately not `required_for` on either — it's one-of, not both.
            ConnectionField(key="privateKeyPath", label="Private key path (on the self-hosted IR)", placeholder="/opt/keys/id_rsa", help="Use this OR the private key content below — not both."),
            ConnectionField(key="privateKeyContent", label="Private key content secret name", is_secret=True, placeholder="sftp-private-key", help="PEM-encoded private key stored in Key Vault. Use this OR the path above — not both."),
            ConnectionField(key="privateKeyPassphrase", label="Key passphrase secret name", is_secret=True, placeholder="sftp-passphrase"),
            ConnectionField(key="skipHostKeyValidation", label="Skip host key validation", type="boolean"),
            ConnectionField(key="hostKeyFingerprint", label="Host key fingerprint"),
        ),
        description="Secure file transfer over SSH.",
    ),
    "ftp": ConnectorSchema(
        id="ftp", display_name="FTP", adf_type="FtpServer", dataset_type="DelimitedText",
        dataset_location_type="FtpServerLocation", kind="file", default_auth="basic",
        auth_options=(
            ConnectorAuthOption("basic", "Basic authentication"),
            ConnectorAuthOption("anonymous", "Anonymous"),
        ),
        fields=(
            ConnectionField(key="host", label="Host name", required=True, placeholder="ftp.partner.com"),
            ConnectionField(key="port", label="Port", type="number", placeholder="21"),
            ConnectionField(key="enableSsl", label="Enable SSL", type="boolean"),
            ConnectionField(key="enableServerCertificateValidation", label="Validate server certificate", type="boolean"),
            ConnectionField(key="userName", label="User name", required_for=("basic",), placeholder="svc_ftp"),
            ConnectionField(key="password", label="Password secret name", is_secret=True, required_for=("basic",), placeholder="ftp-password"),
        ),
        description="Plain FTP server.",
    ),
    "http": ConnectorSchema(
        id="http", display_name="HTTP / HTTPS", adf_type="HttpServer", dataset_type="DelimitedText",
        dataset_location_type="HttpServerLocation", kind="file", default_auth="anonymous",
        # client_certificate added — ADF's HttpServerLinkedService supports it alongside
        # Anonymous/Basic and it was simply missing here (§23.12 gap #24).
        auth_options=(
            ConnectorAuthOption("anonymous", "Anonymous"),
            ConnectorAuthOption("basic", "Basic authentication"),
            ConnectorAuthOption("client_certificate", "Client certificate", "PFX client certificate stored in Key Vault."),
        ),
        fields=(
            ConnectionField(key="url", label="Base URL", required=True, placeholder="https://api.partner.com/files/"),
            ConnectionField(key="userName", label="User name", required_for=("basic",), placeholder="user"),
            ConnectionField(
                key="password", label="Password / certificate passphrase secret name", is_secret=True,
                required_for=("basic", "client_certificate"), placeholder="http-password",
                help="Basic auth password, or the client certificate's passphrase.",
            ),
            ConnectionField(
                key="embeddedCertData", label="Client certificate (base64 PFX) secret name", is_secret=True,
                required_for=("client_certificate",), placeholder="http-client-cert",
            ),
        ),
        description="Files over HTTP/HTTPS endpoints.",
    ),
    "azure_files": file_store_schema(
        id="azure_files", display_name="Azure Files", adf_type="AzureFileStorage", dataset_location_type="AzureFileStorageLocation",
        default_auth="account_key",
        auth_options=(ConnectorAuthOption("account_key", "Account key"), ConnectorAuthOption("sas_uri", "SAS token")),
        fields=(
            ConnectionField(key="host", label="File share host", required=True, placeholder="\\\\myacct.file.core.windows.net\\share"),
            ConnectionField(key="accountName", label="Storage account name", required=True, placeholder="myacct"),
            ConnectionField(key="accountKey", label="Account key secret name", is_secret=True, required_for=("account_key",), placeholder="azurefiles-account-key"),
            ConnectionField(key="sasToken", label="SAS token secret name", is_secret=True, required_for=("sas_uri",), placeholder="azurefiles-sas-token"),
        ),
        description="Azure Files SMB share.",
    ),
    "azure_table_storage": ConnectorSchema(
        id="azure_table_storage", display_name="Azure Table Storage", adf_type="AzureTableStorage",
        dataset_type="AzureTable", kind="database", category="Azure", default_auth="account_key",
        auth_options=(
            ConnectorAuthOption("account_key", "Account key"),
            ConnectorAuthOption("sas_uri", "SAS token"),
            ConnectorAuthOption("managed_identity", "Managed identity"),
        ),
        fields=(
            ConnectionField(key="accountName", label="Storage account name", required=True, placeholder="myacct"),
            ConnectionField(key="url", label="Table endpoint", placeholder="https://myacct.table.core.windows.net"),
            ConnectionField(key="accountKey", label="Account key secret name", is_secret=True, required_for=("account_key",), placeholder="table-account-key"),
            ConnectionField(key="sasToken", label="SAS token secret name", is_secret=True, required_for=("sas_uri",), placeholder="table-sas-token"),
        ),
        description="Azure Table Storage; each table is an object.",
    ),
    "hdfs": file_store_schema(
        id="hdfs", display_name="HDFS", adf_type="Hdfs", dataset_location_type="HdfsLocation",
        default_auth="anonymous",
        auth_options=(ConnectorAuthOption("anonymous", "Anonymous"), ConnectorAuthOption("windows_authentication", "Windows / Kerberos")),
        fields=(
            ConnectionField(key="url", label="NameNode URL", required=True, placeholder="http://namenode:50070/webhdfs/v1/"),
            ConnectionField(key="userName", label="User name", required_for=("windows_authentication",), placeholder="hdfs"),
            ConnectionField(key="password", label="Password secret name", is_secret=True, required_for=("windows_authentication",), placeholder="hdfs-password"),
        ),
        description="Hadoop HDFS via WebHDFS (self-hosted IR).",
    ),
    "amazon_s3_compatible": file_store_schema(
        id="amazon_s3_compatible", display_name="Amazon S3 Compatible Storage", adf_type="AmazonS3Compatible",
        dataset_location_type="AmazonS3CompatibleLocation", default_auth="access_key",
        auth_options=(ConnectorAuthOption("access_key", "Access key"),),
        fields=(
            ConnectionField(key="serviceUrl", label="Service URL", required=True, placeholder="https://minio.internal:9000"),
            ConnectionField(key="accessKeyId", label="Access key ID", required=True, placeholder="AKIA..."),
            ConnectionField(key="secretAccessKey", label="Secret access key name", is_secret=True, required=True, placeholder="s3compat-secret-key"),
            ConnectionField(key="forcePathStyle", label="Force path-style access", type="boolean"),
        ),
        description="MinIO / Wasabi / other S3-compatible object stores.",
    ),
    "oracle_cloud_storage": file_store_schema(
        id="oracle_cloud_storage", display_name="Oracle Cloud Storage", adf_type="OracleCloudStorage",
        dataset_location_type="OracleCloudStorageLocation", default_auth="access_key",
        auth_options=(ConnectorAuthOption("access_key", "HMAC access key"),),
        fields=(
            ConnectionField(key="serviceUrl", label="Service URL", required=True, placeholder="https://<namespace>.compat.objectstorage.<region>.oraclecloud.com"),
            ConnectionField(key="accessKeyId", label="Access key ID", required=True, placeholder="ocid1..."),
            ConnectionField(key="secretAccessKey", label="Secret access key name", is_secret=True, required=True, placeholder="ocs-secret-key"),
        ),
        description="Oracle Cloud Infrastructure Object Storage (S3 compatibility).",
    ),
    "fabric_lakehouse": file_store_schema(
        id="fabric_lakehouse", display_name="Microsoft Fabric Lakehouse (Files)", adf_type="Lakehouse",
        dataset_location_type="LakehouseLocation", default_auth="service_principal",
        auth_options=(
            ConnectorAuthOption("service_principal", "Service principal"),
            ConnectorAuthOption("managed_identity", "Managed identity", supports_user_assigned_identity=True),
        ),
        fields=(
            ConnectionField(key="workspaceId", label="Workspace ID", required=True, placeholder="00000000-0000-0000-0000-000000000000"),
            ConnectionField(key="artifactId", label="Lakehouse ID", required=True, placeholder="00000000-0000-0000-0000-000000000000"),
            *SPN_FIELDS,
        ),
        description="Files area of a Microsoft Fabric Lakehouse.",
    ),
    "sharepoint_online_list": ConnectorSchema(
        id="sharepoint_online_list", display_name="SharePoint Online List", adf_type="SharePointOnlineList",
        dataset_type="SharePointOnlineListResource", kind="service", category="Service", default_auth="service_principal",
        auth_options=(ConnectorAuthOption("service_principal", "Service principal (Azure AD app)"),),
        fields=(
            ConnectionField(key="siteUrl", label="Site URL", required=True, placeholder="https://contoso.sharepoint.com/sites/team"),
            ConnectionField(key="tenantId", label="Tenant ID", required=True, placeholder="tenant-id"),
            ConnectionField(key="servicePrincipalId", label="Client ID", required=True, placeholder="app-client-id"),
            ConnectionField(key="servicePrincipalKey", label="Client secret name", is_secret=True, required=True, placeholder="sharepoint-client-secret"),
        ),
        description="Items from a SharePoint Online list.",
    ),
    "web_table": ConnectorSchema(
        id="web_table", display_name="Web Table (HTML)", adf_type="Web", dataset_type="WebTable",
        kind="file", category="File", default_auth="anonymous",
        auth_options=(
            ConnectorAuthOption("anonymous", "Anonymous"),
            ConnectorAuthOption("basic", "Basic authentication"),
        ),
        fields=(
            ConnectionField(key="url", label="Page URL", required=True, placeholder="https://example.com/data.html"),
            ConnectionField(key="userName", label="User name", required_for=("basic",), placeholder="user"),
            ConnectionField(key="password", label="Password secret name", is_secret=True, required_for=("basic",), placeholder="web-password"),
        ),
        description="Tables parsed from an HTML page (self-hosted IR).",
    ),
    "odata": rest_service_schema(
        id="odata", display_name="OData", adf_type="OData", dataset_type="ODataResource",
        base_url_label="Service root URL", base_url_placeholder="https://services.odata.org/V4/Northwind/Northwind.svc/",
        auth_options=(
            ConnectorAuthOption("anonymous", "Anonymous"),
            ConnectorAuthOption("basic", "Basic authentication"),
            ConnectorAuthOption("oauth2", "OAuth 2.0 (AAD / client credentials)"),
            ConnectorAuthOption("managed_identity", "Managed identity"),
        ),
        default_auth="anonymous", description="Any OData v3/v4 feed.",
    ),
    "odbc": ConnectorSchema(
        id="odbc", display_name="ODBC", adf_type="Odbc", dataset_type="OdbcTable", kind="database",
        category="Generic", default_auth="basic",
        auth_options=(
            ConnectorAuthOption("basic", "Basic authentication"),
            ConnectorAuthOption("anonymous", "Anonymous (DSN handles auth)"),
        ),
        fields=(
            ConnectionField(key="connectionString", label="Connection string", required=True, placeholder="Driver={...};Server=...;Database=...;"),
            ConnectionField(key="userName", label="User name", required_for=("basic",), placeholder="user"),
            ConnectionField(key="password", label="Password secret name", is_secret=True, required_for=("basic",), placeholder="odbc-password"),
        ),
        description="Generic ODBC driver on a self-hosted IR.",
    ),
    "rest": rest_service_schema(
        id="rest", display_name="REST", base_url_placeholder="https://api.partner.com/v1/",
        auth_options=(
            ConnectorAuthOption("anonymous", "Anonymous"),
            ConnectorAuthOption("basic", "Basic authentication"),
            ConnectorAuthOption("api_key", "API key header"),
            ConnectorAuthOption("oauth2", "OAuth 2.0 (client credentials)"),
            ConnectorAuthOption("managed_identity", "AAD managed identity"),
            ConnectorAuthOption("service_principal", "AAD service principal"),
        ),
        default_auth="anonymous",
        extra_fields=(ConnectionField(key="aadResourceId", label="AAD resource / audience", placeholder="https://management.azure.com/"),),
        description="Generic REST endpoint (paged JSON) via the REST connector.",
    ),
}


# ---------------------------------------------------------------------------
# Registry — SaaS / application connectors
# ---------------------------------------------------------------------------

_SERVICE_SCHEMAS: dict[str, ConnectorSchema] = {
    "salesforce": saas_schema("salesforce", "Salesforce", "SalesforceV2", "SalesforceV2Object", "https://login.salesforce.com", "Salesforce (V2 connector); SObjects are the objects.", (ConnectionField(key="apiVersion", label="API version", placeholder="59.0"),)),
    "salesforce_service_cloud": saas_schema("salesforce_service_cloud", "Salesforce Service Cloud", "SalesforceServiceCloudV2", "SalesforceServiceCloudV2Object", "https://login.salesforce.com", "Salesforce Service Cloud (V2 connector)."),
    "salesforce_marketing_cloud": saas_schema("salesforce_marketing_cloud", "Salesforce Marketing Cloud", "SalesforceMarketingCloud", "SalesforceMarketingCloudObject", "https://YOUR_SUBDOMAIN.rest.marketingcloudapis.com", "Salesforce Marketing Cloud REST API."),
    "dynamics_365": saas_schema("dynamics_365", "Dynamics 365", "CommonDataServiceForApps", "CommonDataServiceForAppsEntity", "https://org.crm.dynamics.com", "Dynamics 365 / Dataverse (entities are the objects)."),
    "dynamics_crm": saas_schema("dynamics_crm", "Dynamics CRM", "DynamicsCrm", "DynamicsCrmEntity", "https://org.crm.dynamics.com", "Dynamics CRM online / on-prem."),
    "dynamics_ax": saas_schema("dynamics_ax", "Dynamics AX", "DynamicsAX", "DynamicsAXResource", "https://org.operations.dynamics.com/data", "Dynamics AX / Finance & Operations OData."),
    "dataverse": saas_schema("dataverse", "Microsoft Dataverse", "CommonDataServiceForApps", "CommonDataServiceForAppsEntity", "https://org.crm.dynamics.com", "Microsoft Dataverse (Common Data Service)."),
    "servicenow": saas_schema("servicenow", "ServiceNow", "ServiceNowV2", "ServiceNowV2Object", "https://instance.service-now.com", "ServiceNow (V2 connector); tables are the objects."),
    "sap_ecc": saas_schema("sap_ecc", "SAP ECC", "SapEcc", "SapEccResource", "https://sapecc.contoso.com/sap/opu/odata/sap/", "SAP ECC via OData services."),
    "sap_cloud_for_customer": saas_schema("sap_cloud_for_customer", "SAP Cloud for Customer", "SapCloudForCustomer", "SapCloudForCustomerResource", "https://my.c4c.contoso.com", "SAP Cloud for Customer (C4C) OData."),
    "google_adwords": saas_schema("google_adwords", "Google Ads", "GoogleAdWords", "GoogleAdWordsObject", "https://googleads.googleapis.com", "Google Ads (AdWords) reporting API."),
    "google_sheets": saas_schema("google_sheets", "Google Sheets", "RestService", "RestResource", "https://sheets.googleapis.com/v4/spreadsheets/", "Google Sheets via the REST connector."),
    "hubspot": saas_schema("hubspot", "HubSpot", "Hubspot", "HubspotObject", "https://api.hubapi.com", "HubSpot CRM / Marketing API."),
    "jira": saas_schema("jira", "Jira", "Jira", "JiraObject", "https://your-domain.atlassian.net", "Atlassian Jira Cloud / Server."),
    "marketo": saas_schema("marketo", "Marketo", "Marketo", "MarketoObject", "https://123-ABC-456.mktorest.com", "Adobe Marketo Engage REST API."),
    "paypal": saas_schema("paypal", "PayPal", "Paypal", "PaypalObject", "https://api.paypal.com", "PayPal transaction / reporting API."),
    "quickbooks": saas_schema("quickbooks", "QuickBooks", "QuickBooks", "QuickBooksObject", "https://quickbooks.api.intuit.com", "Intuit QuickBooks Online."),
    "shopify": saas_schema("shopify", "Shopify", "Shopify", "ShopifyObject", "https://YOUR_STORE.myshopify.com", "Shopify Admin API."),
    "smartsheet": saas_schema("smartsheet", "Smartsheet", "RestService", "RestResource", "https://api.smartsheet.com/2.0/", "Smartsheet via the REST connector."),
    "square": saas_schema("square", "Square", "Square", "SquareObject", "https://connect.squareup.com", "Square payments / catalog API."),
    "twilio": saas_schema("twilio", "Twilio", "RestService", "RestResource", "https://api.twilio.com/2010-04-01/", "Twilio messaging / usage API via REST."),
    "xero": saas_schema("xero", "Xero", "Xero", "XeroObject", "https://api.xero.com/api.xro/2.0/", "Xero accounting API."),
    "zendesk": saas_schema("zendesk", "Zendesk", "Zendesk", "ZendeskObject", "https://YOUR_SUBDOMAIN.zendesk.com", "Zendesk Support API."),
    "zoho": saas_schema("zoho", "Zoho", "Zoho", "ZohoObject", "https://www.zohoapis.com", "Zoho CRM / Analytics API."),
    "github": saas_schema("github", "GitHub", "RestService", "RestResource", "https://api.github.com", "GitHub REST API (repos, issues, actions)."),
    "microsoft_365": ConnectorSchema(
        id="microsoft_365", display_name="Microsoft 365", adf_type="Office365", dataset_type="Office365Table",
        kind="service", category="Service", default_auth="service_principal",
        auth_options=(ConnectorAuthOption("service_principal", "Service principal (Azure AD app)"),),
        fields=(
            ConnectionField(key="office365TenantId", label="Office 365 tenant ID", required=True, placeholder="tenant-id"),
            ConnectionField(key="servicePrincipalTenantId", label="SPN tenant ID", required=True, placeholder="tenant-id"),
            ConnectionField(key="servicePrincipalId", label="Client ID", required=True, placeholder="app-client-id"),
            ConnectionField(key="servicePrincipalKey", label="Client secret name", is_secret=True, required=True, placeholder="m365-client-secret"),
        ),
        description="Microsoft Graph data access (Microsoft 365 / Office 365).",
    ),
    "azure_cognitive_search": ConnectorSchema(
        id="azure_cognitive_search", display_name="Azure AI Search", adf_type="AzureSearch", dataset_type="AzureSearchIndex",
        kind="service", category="Azure", default_auth="api_key",
        auth_options=(ConnectorAuthOption("api_key", "Admin key", "Admin key stored in Key Vault."),),
        fields=(
            ConnectionField(key="url", label="Search service URL", required=True, placeholder="https://myservice.search.windows.net"),
            ConnectionField(key="key", label="Admin key secret name", is_secret=True, required=True, placeholder="search-admin-key"),
        ),
        description="Azure AI Search (formerly Cognitive Search); write index documents as a sink.",
    ),
    # Sample-guide parity gap 7 — both were entirely absent from the registry;
    # guide 2's Workday HR feed and Event-Hub-fed clickstream source could not
    # be wired up in Connections at all before this.
    "workday": saas_schema(
        id="workday",
        display_name="Workday",
        adf_type="Workday",
        dataset_type="WorkdayObject",
        base_url_placeholder="https://wd2-services1.workday.com/ccx/service/tenant",
        description="Workday HR / Financials via REST report-as-a-service, OAuth 2.0 client-credentials.",
        extra_fields=(ConnectionField(key="reportEntity", label="RaaS report entity", placeholder="Workers"),),
    ),
    "azure_event_hub": ConnectorSchema(
        id="azure_event_hub", display_name="Azure Event Hubs", adf_type="AzureEventHubs", dataset_type="AvroFormat",
        kind="service", category="Azure", default_auth="connection_string",
        auth_options=(
            ConnectorAuthOption("connection_string", "Connection string", "Namespace/Event Hub connection string, referenced from Key Vault."),
            ConnectorAuthOption("managed_identity", "Managed identity"),
        ),
        fields=(
            ConnectionField(key="connectionString", label="Connection string secret name", is_secret=True, placeholder="eventhub-connection-string"),
            ConnectionField(key="eventHubName", label="Event Hub name", required=True, placeholder="clickstream"),
        ),
        description="Azure Event Hubs; captured events land as Avro and are read as a Copy source (streaming ingestion itself is out of scope, §20).",
    ),
}


# ---------------------------------------------------------------------------
# Registry — LLM / model-serving connectors
# ---------------------------------------------------------------------------
#
# ADF has no native "LLM" linked service. These are modeled as RestService
# linked services that ADF calls from Web / REST activities (enrich a row,
# classify, embed, summarise). Every credential is a Key Vault secret *name*;
# the endpoint, model / deployment and API version are user input.

_LLM_SCHEMAS: dict[str, ConnectorSchema] = {
    "azure_openai": rest_service_schema(
        id="azure_openai", display_name="Azure OpenAI", kind="llm",
        base_url_label="Resource endpoint", base_url_placeholder="https://my-aoai.openai.azure.com",
        auth_options=(
            ConnectorAuthOption("api_key", "API key", "Key stored in Key Vault."),
            ConnectorAuthOption("managed_identity", "Managed identity"),
            ConnectorAuthOption("service_principal", "Service principal"),
        ),
        default_auth="api_key",
        extra_fields=(
            ConnectionField(key="deployment", label="Deployment name", required=True, placeholder="gpt-4o"),
            ConnectionField(key="apiVersion", label="API version", required=True, placeholder="2024-10-21"),
            ConnectionField(key="aadResourceId", label="AAD resource", placeholder="https://cognitiveservices.azure.com/"),
        ),
        description="Azure OpenAI Service (chat / completions / embeddings).",
    ),
    "azure_ai_foundry": rest_service_schema(
        id="azure_ai_foundry", display_name="Azure AI Foundry", kind="llm",
        base_url_label="Model endpoint", base_url_placeholder="https://my-project.services.ai.azure.com/models",
        auth_options=(
            ConnectorAuthOption("api_key", "API key"),
            ConnectorAuthOption("managed_identity", "Managed identity"),
            ConnectorAuthOption("service_principal", "Service principal"),
        ),
        default_auth="api_key",
        extra_fields=(
            ConnectionField(key="model", label="Model name", required=True, placeholder="Phi-3.5-MoE-instruct"),
            ConnectionField(key="apiVersion", label="API version", placeholder="2024-05-01-preview"),
        ),
        description="Azure AI Foundry model inference endpoint (Models-as-a-Service).",
    ),
    "openai": rest_service_schema(
        id="openai", display_name="OpenAI", kind="llm", base_url_placeholder="https://api.openai.com/v1",
        auth_options=(ConnectorAuthOption("api_key", "API key (Bearer)"),), default_auth="api_key",
        extra_fields=(*LLM_MODEL_FIELDS, ConnectionField(key="organization", label="Organization ID", placeholder="org-...")),
        description="OpenAI platform API (chat / responses / embeddings).",
    ),
    "anthropic": rest_service_schema(
        id="anthropic", display_name="Anthropic", kind="llm", base_url_placeholder="https://api.anthropic.com/v1",
        auth_options=(ConnectorAuthOption("api_key", "API key (x-api-key)"),), default_auth="api_key",
        extra_fields=(
            ConnectionField(key="model", label="Default model", placeholder="model-id"),
            ConnectionField(key="anthropicVersion", label="Anthropic-Version header", placeholder="2023-06-01"),
        ),
        description="Anthropic Messages API.",
    ),
    "aws_bedrock": rest_service_schema(
        id="aws_bedrock", display_name="AWS Bedrock", kind="llm",
        base_url_label="Runtime endpoint", base_url_placeholder="https://bedrock-runtime.us-east-1.amazonaws.com",
        auth_options=(
            ConnectorAuthOption("aws_access_key", "Access key (SigV4)"),
            ConnectorAuthOption("assumed_role", "IAM role"),
        ),
        default_auth="aws_access_key",
        extra_fields=(
            ConnectionField(key="region", label="AWS region", required=True, placeholder="us-east-1"),
            ConnectionField(key="accessKeyId", label="Access key ID", required_for=("aws_access_key",), placeholder="AKIA..."),
            ConnectionField(key="secretAccessKey", label="Secret access key name", is_secret=True, required_for=("aws_access_key",), placeholder="bedrock-secret-key"),
            ConnectionField(key="roleArn", label="IAM role ARN", required_for=("assumed_role",), placeholder="arn:aws:iam::123456789012:role/adf-bedrock"),
            ConnectionField(key="model", label="Model ID", placeholder="provider.model-id-v1:0"),
        ),
        description="Amazon Bedrock model runtime (InvokeModel / Converse).",
    ),
    "google_vertex_ai": rest_service_schema(
        id="google_vertex_ai", display_name="Google Vertex AI", kind="llm",
        base_url_label="API host", base_url_placeholder="https://us-central1-aiplatform.googleapis.com",
        auth_options=(
            ConnectorAuthOption("service_account_key", "Service account key"),
            ConnectorAuthOption("oauth2", "OAuth 2.0"),
        ),
        default_auth="service_account_key",
        extra_fields=(
            ConnectionField(key="projectId", label="GCP project ID", required=True, placeholder="my-gcp-project"),
            ConnectionField(key="location", label="Location / region", required=True, placeholder="us-central1"),
            ConnectionField(key="clientEmail", label="Service account email", required_for=("service_account_key",), placeholder="svc@my-project.iam.gserviceaccount.com"),
            ConnectionField(key="privateKey", label="Private key secret name", is_secret=True, required_for=("service_account_key",), placeholder="vertex-sa-key"),
            ConnectionField(key="model", label="Model / publisher model", placeholder="gemini-1.5-pro"),
        ),
        description="Google Vertex AI generative models (generateContent / predict).",
    ),
    "cohere": rest_service_schema(
        id="cohere", display_name="Cohere", kind="llm", base_url_placeholder="https://api.cohere.com/v2",
        auth_options=(ConnectorAuthOption("api_key", "API key (Bearer)"),), default_auth="api_key",
        extra_fields=(ConnectionField(key="model", label="Default model", placeholder="command-r-plus"),),
        description="Cohere chat / embed / rerank API.",
    ),
    "mistral_ai": rest_service_schema(
        id="mistral_ai", display_name="Mistral AI", kind="llm", base_url_placeholder="https://api.mistral.ai/v1",
        auth_options=(ConnectorAuthOption("api_key", "API key (Bearer)"),), default_auth="api_key",
        extra_fields=(ConnectionField(key="model", label="Default model", placeholder="mistral-large-latest"),),
        description="Mistral AI chat / embeddings API.",
    ),
    "databricks_model_serving": rest_service_schema(
        id="databricks_model_serving", display_name="Databricks Model Serving", kind="llm",
        base_url_label="Workspace URL", base_url_placeholder="https://adb-1234.5.azuredatabricks.net",
        auth_options=(
            ConnectorAuthOption("personal_access_token", "Personal access token"),
            ConnectorAuthOption("oauth2", "OAuth (service principal)"),
        ),
        default_auth="personal_access_token",
        extra_fields=(
            ConnectionField(key="endpointName", label="Serving endpoint name", required=True, placeholder="databricks-dbrx-instruct"),
            ConnectionField(key="accessToken", label="Access token secret name", is_secret=True, required_for=("personal_access_token",), placeholder="databricks-serving-pat"),
        ),
        description="Databricks Mosaic AI Model Serving / Foundation Model APIs.",
    ),
    "hugging_face_inference": rest_service_schema(
        id="hugging_face_inference", display_name="Hugging Face Inference", kind="llm",
        base_url_placeholder="https://api-inference.huggingface.co/models/",
        auth_options=(ConnectorAuthOption("api_key", "Access token (Bearer)"),), default_auth="api_key",
        extra_fields=(ConnectionField(key="model", label="Model repo ID", placeholder="meta-llama/Llama-3.1-8B-Instruct"),),
        description="Hugging Face Inference API / Inference Endpoints.",
    ),
}


# ---------------------------------------------------------------------------
# Merged registry + derived views
# ---------------------------------------------------------------------------

CONNECTOR_SCHEMAS: dict[str, ConnectorSchema] = {
    **_DATABASE_SCHEMAS,
    **_FILE_SCHEMAS,
    **_SERVICE_SCHEMAS,
    **_LLM_SCHEMAS,
}

ALL_CONNECTORS: list[str] = sorted(
    CONNECTOR_SCHEMAS, key=lambda k: CONNECTOR_SCHEMAS[k].display_name.lower()
)
DATABASE_CONNECTORS: list[str] = [k for k in ALL_CONNECTORS if CONNECTOR_SCHEMAS[k].kind == "database"]
FILE_CONNECTORS: list[str] = [k for k in ALL_CONNECTORS if CONNECTOR_SCHEMAS[k].kind == "file"]
SERVICE_CONNECTORS: list[str] = [k for k in ALL_CONNECTORS if CONNECTOR_SCHEMAS[k].kind == "service"]
LLM_CONNECTORS: list[str] = [k for k in ALL_CONNECTORS if CONNECTOR_SCHEMAS[k].kind == "llm"]

_CATEGORY_ORDER: tuple[ConnectorCategory, ...] = ("Azure", "Database", "File", "Service", "AI", "Generic")
_KIND_TO_CATEGORY: dict[ConnectorKind, ConnectorCategory] = {
    "llm": "AI", "service": "Service", "file": "File", "database": "Database",
}


def connector_schema(connector_type: str) -> ConnectorSchema:
    """Return the schema for a connector key, or the generic fallback."""
    schema = CONNECTOR_SCHEMAS.get(connector_type)
    if schema is not None:
        return schema
    return dc_replace_id(GENERIC_SCHEMA, "generic")


def dc_replace_id(schema: ConnectorSchema, new_id: str) -> ConnectorSchema:
    from dataclasses import replace

    return replace(schema, id=new_id)


def is_file_connector(connector_type: str) -> bool:
    return connector_schema(connector_type).kind == "file"


def linked_service_adf_type(connector_type: str) -> str:
    return connector_schema(connector_type).adf_type


def dataset_adf_type(connector_type: str) -> str:
    return connector_schema(connector_type).dataset_type


def connector_category(connector_type: str) -> ConnectorCategory:
    schema = connector_schema(connector_type)
    if schema.category:
        return schema.category
    return _KIND_TO_CATEGORY.get(schema.kind, "Generic")


CONNECTOR_GROUPS: list[dict[str, object]] = [
    {"category": category, "connectors": [k for k in ALL_CONNECTORS if connector_category(k) == category]}
    for category in _CATEGORY_ORDER
    if any(connector_category(k) == category for k in ALL_CONNECTORS)
]


def required_fields(schema: ConnectorSchema, auth_type: str) -> list[ConnectionField]:
    """Fields the user must fill: always-required plus auth-specific ones."""
    return [f for f in schema.fields if f.required or auth_type in f.required_for]


def visible_fields(schema: ConnectorSchema, auth_type: str) -> list[ConnectionField]:
    """Form fields shown for the selected auth (secret names always editable)."""
    return [
        f for f in schema.fields
        if f.required or f.is_secret or not f.required_for or auth_type in f.required_for
    ]


def uses_secret_fields(schema: ConnectorSchema, auth_type: str) -> bool:
    return any(f.is_secret for f in required_fields(schema, auth_type))


# ---------------------------------------------------------------------------
# Drift guard — the backend vendors a byte-identical copy of this module.
# ---------------------------------------------------------------------------

def registry_fingerprint() -> str:
    """Stable hash of the registry shape (ids, kinds, auth, field keys).

    ``worker`` and ``backend`` vendor byte-identical copies of this module; run
    ``python -m app.connectors.schemas`` in each tree and compare the printed
    fingerprint to confirm they have not drifted.
    """
    payload = [
        {
            "id": s.id,
            "adf_type": s.adf_type,
            "dataset_type": s.dataset_type,
            "kind": s.kind,
            "category": s.category,
            "default_auth": s.default_auth,
            "auth": [[a.id, a.supports_user_assigned_identity] for a in s.auth_options],
            "fields": [[f.key, f.type, f.required, list(f.required_for), f.is_secret] for f in s.fields],
        }
        for _, s in sorted(CONNECTOR_SCHEMAS.items())
    ]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


if __name__ == "__main__":  # pragma: no cover - manual parity check
    print(f"REGISTRY_VERSION = {REGISTRY_VERSION}")
    print(f"connectors       = {len(CONNECTOR_SCHEMAS)}")
    print(f"fingerprint      = {registry_fingerprint()}")
