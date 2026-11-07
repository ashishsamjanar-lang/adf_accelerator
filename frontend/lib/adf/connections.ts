/**
 * Metadata-driven connector registry and connection/dataset builders.
 *
 * Aligned with AGENTS.md section 2: "Connector schemas should be metadata-driven
 * and rendered dynamically from definitions" and "secrets stay in Key Vault —
 * parameterize the secret *name*, never the secret".
 *
 * This module owns:
 *  - the connector schema registry (required fields per connector and per
 *    authentication mechanism);
 *  - linked-service drafts (user input) and their ADF JSON projection
 *    (secrets always rendered as AzureKeyVaultSecret dynamic content);
 *  - dataset drafts and their ADF JSON projection (dataset parameters for
 *    metadata-driven parameterization);
 *  - the table/input-file list parser used by the "Upload file" mode of the
 *    create-pipeline wizard.
 */
import type {
  AuthenticationType,
  SourceObjectEntry,
  SourceTargetType,
  ValidationIssue,
} from "@/types";

// =============================================================================
// Connector schema registry — types
// =============================================================================

export type ConnectionFieldType = "text" | "password" | "select" | "number" | "boolean";

/**
 * A single linked-service form field.
 * `isSecret` fields never hold the secret itself: the user enters the
 * *Azure Key Vault secret name* and the generated ADF JSON references it
 * via an AzureKeyVaultSecret dynamic-content block.
 */
export interface ConnectionField {
  key: string;
  label: string;
  type: ConnectionFieldType;
  /** Always required regardless of the authentication mechanism. */
  required?: boolean;
  /** Required only when the selected authentication mechanism matches. */
  requiredFor?: AuthenticationType[];
  placeholder?: string;
  help?: string;
  options?: Array<{ label: string; value: string }>;
  /** Field holds a Key Vault *secret name* (never the secret value). */
  isSecret?: boolean;
}

export interface ConnectorAuthOption {
  id: AuthenticationType;
  label: string;
  description?: string;
}

export interface ConnectorSchema {
  id: SourceTargetType | "generic";
  displayName: string;
  /** ADF linked service `type`, e.g. AzureSqlDatabase. */
  adfType: string;
  /** ADF dataset type for relational stores, e.g. AzureSqlTable. */
  datasetType: string;
  /** Dataset location type for file-based stores, e.g. AzureBlobFSLocation. */
  datasetLocationType?: string;
  /**
   * Store family, drives dataset shape + form rendering:
   *  - `database`  relational (schema/table datasets, SQL-style);
   *  - `file`      object/file storage (folder/file datasets, delimited-text format);
   *  - `service`   SaaS / application connectors (object-name datasets);
   *  - `llm`       model-serving / LLM endpoints (RestService linked service,
   *                called from ADF via Web / REST activities, never a Copy sink).
   */
  kind: "database" | "file" | "service" | "llm";
  /** Coarse grouping for connector pickers. */
  category?: "Azure" | "Database" | "File" | "Service" | "AI" | "Generic";
  defaultAuth: AuthenticationType;
  authOptions: ConnectorAuthOption[];
  fields: ConnectionField[];
  description?: string;
}

/** Shared reusable field fragments for the registry below. */
const CLOUD_TYPE_FIELD: ConnectionField = {
  key: "azureCloudType",
  label: "Azure cloud type",
  type: "select",
  placeholder: "AzurePublic",
  options: [
    { label: "AzurePublic", value: "AzurePublic" },
    { label: "AzureChina", value: "AzureChina" },
    { label: "AzureUsGovernment", value: "AzureUsGovernment" },
  ],
};

const USERNAME_FIELD: ConnectionField = {
  key: "userName",
  label: "User name",
  type: "text",
  required: true,
  placeholder: "dbuser",
};

const PASSWORD_FIELD: ConnectionField = {
  key: "password",
  label: "Password secret name",
  type: "text",
  isSecret: true,
  required: true,
  placeholder: "db-password",
  help: "Name of the Key Vault secret holding the password.",
};

const SPN_FIELDS: ConnectionField[] = [
  { key: "servicePrincipalId", label: "Service principal ID", type: "text", requiredFor: ["service_principal"], placeholder: "00000000-0000-0000-0000-000000000000" },
  { key: "servicePrincipalKey", label: "SPN key secret name", type: "text", isSecret: true, requiredFor: ["service_principal"], placeholder: "spn-key", help: "Name of the Key Vault secret holding the SPN key." },
  { key: "tenant", label: "Tenant ID", type: "text", requiredFor: ["service_principal"], placeholder: "tenant-id" },
];

const azureSqlFields: ConnectionField[] = [
  { key: "server", label: "Server name", type: "text", required: true, placeholder: "myserver.database.windows.net", help: "Fully-qualified server host name." },
  { key: "database", label: "Database name", type: "text", required: true, placeholder: "mydatabase" },
  {
    key: "encrypt",
    label: "Encryption method",
    type: "select",
    options: [
      { label: "Strict (recommended)", value: "Strict" },
      { label: "Mandatory", value: "Mandatory" },
      { label: "Optional", value: "Optional" },
    ],
  },
  { key: "trustServerCertificate", label: "Trust server certificate", type: "boolean", help: "Maps to TrustServerCertificate in the connection." },
  { key: "userName", label: "User name", type: "text", requiredFor: ["sql_authentication"], placeholder: "sqladmin" },
  { key: "password", label: "Password secret name", type: "text", isSecret: true, requiredFor: ["sql_authentication"], placeholder: "sqlserver-password", help: "Name of the Key Vault secret holding the password." },
  ...SPN_FIELDS,
  CLOUD_TYPE_FIELD,
];

const sqlServerFields: ConnectionField[] = [
  { key: "server", label: "Server name", type: "text", required: true, placeholder: "sqlserver01.contoso.com" },
  { key: "database", label: "Database name", type: "text", required: true, placeholder: "AdventureWorks" },
  {
    key: "encrypt",
    label: "Encryption method",
    type: "select",
    options: [
      { label: "Mandatory", value: "Mandatory" },
      { label: "Strict", value: "Strict" },
      { label: "Optional", value: "Optional" },
    ],
  },
  { key: "trustServerCertificate", label: "Trust server certificate", type: "boolean" },
  { key: "userName", label: "User name", type: "text", requiredFor: ["sql_authentication", "windows_authentication"], placeholder: "sa" },
  { key: "password", label: "Password secret name", type: "text", isSecret: true, requiredFor: ["sql_authentication", "windows_authentication"], placeholder: "sqlserver-password", help: "Name of the Key Vault secret holding the password." },
];

const storageFields: ConnectionField[] = [
  { key: "accountName", label: "Storage account name", type: "text", required: true, placeholder: "mystorageaccount" },
  { key: "url", label: "Endpoint URL", type: "text", required: true, placeholder: "https://mystorageaccount.dfs.core.windows.net", help: "DFS endpoint for ADLS Gen2 / blob endpoint for Blob Storage." },
  { key: "accountKey", label: "Account key secret name", type: "text", isSecret: true, requiredFor: ["account_key"], placeholder: "storage-account-key" },
  { key: "sasToken", label: "SAS token secret name", type: "text", isSecret: true, requiredFor: ["sas_uri"], placeholder: "storage-sas-token" },
  { key: "sasUri", label: "SAS URI", type: "text", placeholder: "https://account.blob.core.windows.net?sv=..." },
  ...SPN_FIELDS,
  CLOUD_TYPE_FIELD,
];

const storageAuthOptions: ConnectorAuthOption[] = [
  { id: "account_key", label: "Storage account key", description: "Account key stored in Key Vault." },
  { id: "sas_uri", label: "SAS token", description: "SAS token stored in Key Vault." },
  { id: "service_principal", label: "Service principal" },
  { id: "managed_identity", label: "Managed identity" },
  { id: "connection_string", label: "Connection string", description: "Full connection string (no inline secrets)." },
];

const databaseAuthOptions: ConnectorAuthOption[] = [
  { id: "sql_authentication", label: "SQL authentication", description: "User name + password stored in Key Vault." },
  { id: "windows_authentication", label: "Windows authentication" },
  { id: "managed_identity", label: "Managed identity", description: "System-assigned managed identity of the factory." },
];

const BASIC_AUTH_ONLY: ConnectorAuthOption[] = [{ id: "basic", label: "Basic authentication" }];

/**
 * Factory for the very common "server / port / database / user / password"
 * relational connector. Keeps the registry declarative — add a connector by
 * calling this, never by hand-authoring a form (AGENTS.md §2, §22.10).
 */
function relationalSchema(opts: {
  id: SourceTargetType;
  displayName: string;
  adfType: string;
  datasetType: string;
  serverLabel?: string;
  serverPlaceholder?: string;
  port?: string;
  hasDatabase?: boolean;
  hasSchema?: boolean;
  authOptions?: ConnectorAuthOption[];
  defaultAuth?: AuthenticationType;
  extraFields?: ConnectionField[];
  category?: ConnectorSchema["category"];
  description: string;
}): ConnectorSchema {
  const fields: ConnectionField[] = [
    {
      key: "server",
      label: opts.serverLabel ?? "Server name",
      type: "text",
      required: true,
      placeholder: opts.serverPlaceholder ?? "host.internal",
    },
  ];
  if (opts.port !== undefined) {
    fields.push({ key: "port", label: "Port", type: "number", placeholder: opts.port });
  }
  if (opts.hasDatabase !== false) {
    fields.push({ key: "database", label: "Database name", type: "text", required: true, placeholder: "mydb" });
  }
  if (opts.hasSchema) {
    fields.push({ key: "schema", label: "Default schema", type: "text", placeholder: "public" });
  }
  // User / password are only required for the credential-based auth mechanisms;
  // SPN / managed-identity / key-file connectors get their own fields via extraFields.
  const userPassAuth: AuthenticationType[] = ["basic", "sql_authentication", "windows_authentication", "username_password"];
  fields.push(
    { key: "userName", label: "User name", type: "text", requiredFor: userPassAuth, placeholder: "dbuser" },
    { key: "password", label: "Password secret name", type: "text", isSecret: true, requiredFor: userPassAuth, placeholder: "db-password", help: "Name of the Key Vault secret holding the password." },
    ...(opts.extraFields ?? []),
  );
  return {
    id: opts.id,
    displayName: opts.displayName,
    adfType: opts.adfType,
    datasetType: opts.datasetType,
    kind: "database",
    category: opts.category ?? "Database",
    defaultAuth: opts.defaultAuth ?? "basic",
    authOptions: opts.authOptions ?? BASIC_AUTH_ONLY,
    fields,
    description: opts.description,
  };
}

/** Factory for a file / object-store connector rendered from delimited-text fields. */
function fileStoreSchema(opts: {
  id: SourceTargetType;
  displayName: string;
  adfType: string;
  datasetLocationType: string;
  authOptions: ConnectorAuthOption[];
  defaultAuth: AuthenticationType;
  fields: ConnectionField[];
  description: string;
}): ConnectorSchema {
  return {
    id: opts.id,
    displayName: opts.displayName,
    adfType: opts.adfType,
    datasetType: "DelimitedText",
    datasetLocationType: opts.datasetLocationType,
    kind: "file",
    category: "File",
    defaultAuth: opts.defaultAuth,
    authOptions: opts.authOptions,
    fields: opts.fields,
    description: opts.description,
  };
}

/** OAuth2 client-credentials field fragment (SaaS + LLM connectors). */
const OAUTH2_FIELDS: ConnectionField[] = [
  { key: "tokenEndpoint", label: "Token endpoint", type: "text", requiredFor: ["oauth2"], placeholder: "https://login.example.com/oauth2/token" },
  { key: "clientId", label: "Client ID", type: "text", requiredFor: ["oauth2"], placeholder: "client-id" },
  { key: "clientSecret", label: "Client secret name", type: "text", isSecret: true, requiredFor: ["oauth2"], placeholder: "oauth-client-secret", help: "Key Vault secret holding the OAuth client secret." },
  { key: "scope", label: "Scope / resource", type: "text", placeholder: "https://service.example.com/.default" },
];

const API_KEY_FIELD: ConnectionField = {
  key: "apiKey",
  label: "API key secret name",
  type: "text",
  isSecret: true,
  requiredFor: ["api_key", "bearer_token"],
  placeholder: "service-api-key",
  help: "Key Vault secret holding the API key / token. The value never leaves Key Vault.",
};

/**
 * Factory for REST-backed SaaS/application and LLM connectors. ADF calls these
 * through Web / REST activities; the linked service is always `RestService`
 * (or a native connector type where one exists) and every credential is a
 * Key Vault secret *name*.
 */
function restServiceSchema(opts: {
  id: SourceTargetType;
  displayName: string;
  adfType?: string;
  datasetType?: string;
  kind?: "service" | "llm";
  baseUrlLabel?: string;
  baseUrlPlaceholder: string;
  authOptions?: ConnectorAuthOption[];
  defaultAuth?: AuthenticationType;
  extraFields?: ConnectionField[];
  description: string;
}): ConnectorSchema {
  const kind = opts.kind ?? "service";
  const fields: ConnectionField[] = [
    {
      key: "url",
      label: opts.baseUrlLabel ?? "Base URL",
      type: "text",
      required: true,
      placeholder: opts.baseUrlPlaceholder,
    },
    API_KEY_FIELD,
    {
      key: "authHeaderName",
      label: "Auth header name",
      type: "text",
      placeholder: kind === "llm" ? "Authorization" : "x-api-key",
      help: "Header the API key is sent in (e.g. Authorization, api-key, x-api-key).",
    },
    ...OAUTH2_FIELDS,
    { key: "userName", label: "User name", type: "text", requiredFor: ["basic"], placeholder: "user" },
    { key: "password", label: "Password secret name", type: "text", isSecret: true, requiredFor: ["basic"], placeholder: "service-password" },
    ...SPN_FIELDS,
    ...(opts.extraFields ?? []),
  ];
  return {
    id: opts.id,
    displayName: opts.displayName,
    adfType: opts.adfType ?? "RestService",
    datasetType: opts.datasetType ?? "RestResource",
    kind,
    category: kind === "llm" ? "AI" : "Service",
    defaultAuth: opts.defaultAuth ?? "api_key",
    authOptions: opts.authOptions ?? [
      { id: "api_key", label: "API key", description: "API key stored in Key Vault." },
      { id: "oauth2", label: "OAuth 2.0 (client credentials)" },
      { id: "anonymous", label: "Anonymous" },
    ],
    fields,
    description: opts.description,
  };
}

/** Model / deployment + API-version fields shared by LLM connectors. */
const LLM_MODEL_FIELDS: ConnectionField[] = [
  { key: "model", label: "Default model / deployment", type: "text", placeholder: "gpt-4o / llama-3.1-70b / mistral-large", help: "Referenced by Web/REST activities; per-call overrides come from the control table." },
  { key: "apiVersion", label: "API version", type: "text", placeholder: "2024-10-21" },
];

/** Fallback schema for the long tail of connectors not explicitly modeled. */
export const GENERIC_SCHEMA: ConnectorSchema = {
  id: "generic",
  displayName: "Generic connector",
  adfType: "Generic",
  datasetType: "GenericDataset",
  kind: "database",
  defaultAuth: "basic",
  authOptions: [{ id: "basic", label: "Basic authentication" }],
  fields: [
    { key: "url", label: "Endpoint URL", type: "text", placeholder: "https://..." },
    USERNAME_FIELD,
    PASSWORD_FIELD,
  ],
  description: "Minimal connection form; extra values are passed through to typeProperties.",
};

export function connectorSchema(connectorType: string): ConnectorSchema {
  return CONNECTOR_SCHEMAS[connectorType] ?? { ...GENERIC_SCHEMA, id: "generic" };
}

/** True when the connector stores files rather than tables. */
export function isFileConnector(connectorType: string): boolean {
  return connectorSchema(connectorType).kind === "file";
}

/** ADF linked service type string for a connector key. */
export function linkedServiceAdfType(connectorType: string): string {
  return connectorSchema(connectorType).adfType;
}

/** ADF dataset type string for a connector key. */
export function datasetAdfType(connectorType: string): string {
  return connectorSchema(connectorType).datasetType;
}

/** Fields the user must fill: always-required plus auth-specific ones. */
export function requiredFields(schema: ConnectorSchema, authType: AuthenticationType): ConnectionField[] {
  return schema.fields.filter(
    (field) => field.required || (field.requiredFor ?? []).includes(authType),
  );
}

/** Visible form fields for the selected auth (secret names are always editable). */
export function visibleFields(schema: ConnectorSchema, authType: AuthenticationType): ConnectionField[] {
  return schema.fields.filter(
    (field) =>
      field.required ||
      field.isSecret ||
      !field.requiredFor ||
      field.requiredFor.includes(authType),
  );
}

function usesSecretFields(schema: ConnectorSchema, authType: AuthenticationType): boolean {
  return requiredFields(schema, authType).some((field) => field.isSecret);
}


// =============================================================================
// Registry — database connectors
// =============================================================================

export const CONNECTOR_SCHEMAS: Record<string, ConnectorSchema> = {
  azure_sql_database: {
    id: "azure_sql_database",
    displayName: "Azure SQL Database",
    adfType: "AzureSqlDatabase",
    datasetType: "AzureSqlTable",
    kind: "database",
    defaultAuth: "sql_authentication",
    authOptions: [databaseAuthOptions[0], { id: "service_principal", label: "Service principal" }, databaseAuthOptions[2]],
    fields: azureSqlFields,
    description: "Fully managed PaaS SQL database on Azure.",
  },
  azure_sql_managed_instance: {
    id: "azure_sql_managed_instance",
    displayName: "Azure SQL Managed Instance",
    adfType: "AzureSqlMI",
    datasetType: "AzureSqlMITable",
    kind: "database",
    defaultAuth: "sql_authentication",
    // No windows_authentication (MI is Azure-hosted, no on-prem domain); service_principal added
    // (MI supports it like Azure SQL Database), which needs azureSqlFields not sqlServerFields for
    // the SPN fields (AGENTS.md §23.12 gap #22).
    authOptions: [databaseAuthOptions[0], { id: "service_principal", label: "Service principal" }, databaseAuthOptions[2]],
    fields: azureSqlFields,
    description: "Managed SQL Server instance on Azure.",
  },
  sql_server: {
    id: "sql_server",
    displayName: "SQL Server",
    adfType: "SqlServer",
    datasetType: "SqlServerTable",
    kind: "database",
    defaultAuth: "sql_authentication",
    // No managed_identity — on-prem/IaaS box behind a self-hosted IR, no factory identity to
    // authenticate as there (AGENTS.md §23.12 gap #22).
    authOptions: [databaseAuthOptions[0], databaseAuthOptions[1]],
    fields: sqlServerFields,
    description: "On-premises or IaaS SQL Server (self-hosted IR).",
  },
  azure_synapse_analytics: {
    id: "azure_synapse_analytics",
    displayName: "Azure Synapse Analytics",
    adfType: "AzureSqlDW",
    datasetType: "AzureSqlDWTable",
    kind: "database",
    defaultAuth: "sql_authentication",
    authOptions: [databaseAuthOptions[0], { id: "service_principal", label: "Service principal" }, databaseAuthOptions[2]],
    fields: azureSqlFields,
    description: "Dedicated SQL pool in Synapse workspaces.",
  },
  oracle: {
    id: "oracle",
    displayName: "Oracle",
    adfType: "Oracle",
    datasetType: "OracleTable",
    kind: "database",
    defaultAuth: "basic",
    authOptions: [{ id: "basic", label: "Basic authentication" }],
    fields: [
      { key: "server", label: "Server / TNS alias", type: "text", required: true, placeholder: "oracle01:1521/ORCLPDB1" },
      USERNAME_FIELD,
      PASSWORD_FIELD,
    ],
    description: "Oracle database connector.",
  },
  postgresql: {
    id: "postgresql",
    displayName: "PostgreSQL",
    adfType: "PostgreSql",
    datasetType: "PostgreSqlTable",
    kind: "database",
    defaultAuth: "basic",
    authOptions: [{ id: "basic", label: "Basic authentication" }],
    fields: [
      { key: "server", label: "Server name", type: "text", required: true, placeholder: "pg01.internal" },
      { key: "port", label: "Port", type: "number", placeholder: "5432" },
      { key: "database", label: "Database name", type: "text", required: true, placeholder: "postgres" },
      { key: "schema", label: "Default schema", type: "text", placeholder: "public" },
      USERNAME_FIELD,
      PASSWORD_FIELD,
    ],
    description: "On-premises or IaaS PostgreSQL.",
  },
  azure_database_postgresql: {
    id: "azure_database_postgresql",
    displayName: "Azure Database for PostgreSQL",
    adfType: "AzurePostgreSql",
    datasetType: "AzurePostgreSqlTable",
    kind: "database",
    defaultAuth: "basic",
    authOptions: [{ id: "basic", label: "Basic authentication" }],
    fields: [
      { key: "server", label: "Server name", type: "text", required: true, placeholder: "mypg.postgres.database.azure.com" },
      { key: "port", label: "Port", type: "number", placeholder: "5432" },
      { key: "database", label: "Database name", type: "text", required: true, placeholder: "postgres" },
      USERNAME_FIELD,
      PASSWORD_FIELD,
    ],
    description: "Managed PostgreSQL on Azure.",
  },
  mysql: {
    id: "mysql",
    displayName: "MySQL",
    adfType: "MySql",
    datasetType: "MySqlTable",
    kind: "database",
    defaultAuth: "basic",
    authOptions: [{ id: "basic", label: "Basic authentication" }],
    fields: [
      { key: "server", label: "Server name", type: "text", required: true, placeholder: "mysql01.internal" },
      { key: "port", label: "Port", type: "number", placeholder: "3306" },
      { key: "database", label: "Database name", type: "text", required: true, placeholder: "sales" },
      USERNAME_FIELD,
      PASSWORD_FIELD,
    ],
    description: "On-premises or IaaS MySQL.",
  },
  azure_database_mysql: {
    id: "azure_database_mysql",
    displayName: "Azure Database for MySQL",
    adfType: "AzureMySql",
    datasetType: "AzureMySqlTable",
    kind: "database",
    defaultAuth: "basic",
    authOptions: [{ id: "basic", label: "Basic authentication" }],
    fields: [
      { key: "server", label: "Server name", type: "text", required: true, placeholder: "myysql.mysql.database.azure.com" },
      { key: "port", label: "Port", type: "number", placeholder: "3306" },
      { key: "database", label: "Database name", type: "text", required: true, placeholder: "sales" },
      USERNAME_FIELD,
      PASSWORD_FIELD,
    ],
    description: "Managed MySQL on Azure.",
  },
  snowflake: {
    // The legacy "Snowflake" linked service type is deprecated in favor of SnowflakeV2, which is
    // also the only one that supports key-pair/AAD service-principal auth — this was stuck on the
    // old type with password-only auth (AGENTS.md §23.12 gap #25).
    id: "snowflake",
    displayName: "Snowflake",
    adfType: "SnowflakeV2",
    datasetType: "SnowflakeV2Table",
    kind: "database",
    defaultAuth: "username_password",
    authOptions: [
      { id: "username_password", label: "User / password authentication" },
      { id: "key_pair", label: "Key pair authentication", description: "RSA private key stored in Key Vault." },
      { id: "service_principal", label: "Service principal (Azure AD)" },
    ],
    fields: [
      { key: "accountIdentifier", label: "Account identifier", type: "text", required: true, placeholder: "xy12345.east-us-2.azure" },
      { key: "warehouse", label: "Warehouse", type: "text", required: true, placeholder: "COMPUTE_WH" },
      { key: "database", label: "Database name", type: "text", required: true, placeholder: "SNOWFLAKE_DB" },
      { key: "schema", label: "Default schema", type: "text", placeholder: "PUBLIC" },
      { key: "role", label: "Role", type: "text", placeholder: "LOADER" },
      { key: "user", label: "User name", type: "text", requiredFor: ["username_password", "key_pair"], placeholder: "svc_loader" },
      { key: "password", label: "Password secret name", type: "text", isSecret: true, requiredFor: ["username_password"], placeholder: "snowflake-password" },
      { key: "privateKey", label: "Private key secret name", type: "text", isSecret: true, requiredFor: ["key_pair"], placeholder: "snowflake-private-key", help: "PEM-encoded RSA private key stored in Key Vault." },
      { key: "privateKeyPassphrase", label: "Private key passphrase secret name", type: "text", isSecret: true, placeholder: "snowflake-key-passphrase", help: "Only if the private key above is passphrase-protected." },
      { key: "clientId", label: "Service principal (client) ID", type: "text", requiredFor: ["service_principal"], placeholder: "00000000-0000-0000-0000-000000000000" },
      { key: "servicePrincipalCredential", label: "Service principal credential secret name", type: "text", isSecret: true, requiredFor: ["service_principal"], placeholder: "snowflake-spn-credential" },
      { key: "tenantId", label: "Tenant ID", type: "text", requiredFor: ["service_principal"], placeholder: "tenant-id" },
    ],
    description: "Snowflake data warehouse (SnowflakeV2 linked service).",
  },

  // --- Additional relational / analytics stores (factory-built) -------------
  db2: relationalSchema({
    id: "db2", displayName: "IBM DB2", adfType: "Db2", datasetType: "Db2Table",
    port: "50000", hasSchema: true, description: "IBM DB2 (Db2 for LUW / z/OS) via self-hosted IR.",
  }),
  teradata: relationalSchema({
    id: "teradata", displayName: "Teradata", adfType: "Teradata", datasetType: "TeradataTable",
    hasDatabase: false, hasSchema: true, serverPlaceholder: "teradata.contoso.com",
    description: "Teradata Vantage / on-prem via self-hosted IR.",
  }),
  amazon_redshift: relationalSchema({
    id: "amazon_redshift", displayName: "Amazon Redshift", adfType: "AmazonRedshift", datasetType: "AmazonRedshiftTable",
    port: "5439", hasSchema: true, serverPlaceholder: "cluster.abc123.us-east-1.redshift.amazonaws.com",
    description: "Amazon Redshift cluster (direct query or UNLOAD via S3 staging).",
  }),
  google_big_query: relationalSchema({
    id: "google_big_query", displayName: "Google BigQuery", adfType: "GoogleBigQueryV2", datasetType: "GoogleBigQueryV2Object",
    hasDatabase: false, serverLabel: "Project ID", serverPlaceholder: "my-gcp-project",
    defaultAuth: "service_account_key",
    authOptions: [
      { id: "service_account_key", label: "Service account key", description: "Key JSON stored in Key Vault." },
      { id: "oauth2", label: "User authentication (OAuth)" },
    ],
    extraFields: [
      { key: "clientEmail", label: "Service account email", type: "text", requiredFor: ["service_account_key"], placeholder: "svc@my-project.iam.gserviceaccount.com" },
      { key: "privateKey", label: "Private key secret name", type: "text", isSecret: true, requiredFor: ["service_account_key"], placeholder: "bigquery-sa-key" },
      { key: "refreshToken", label: "Refresh token secret name", type: "text", isSecret: true, requiredFor: ["oauth2"], placeholder: "bigquery-refresh-token" },
    ],
    description: "Google BigQuery (V2 connector).",
  }),
  sap_hana: relationalSchema({
    id: "sap_hana", displayName: "SAP HANA", adfType: "SapHana", datasetType: "SapHanaTable",
    port: "30015", hasDatabase: false, serverPlaceholder: "hana.contoso.com",
    authOptions: [
      { id: "basic", label: "Basic authentication" },
      { id: "windows_authentication", label: "Windows authentication" },
    ],
    description: "SAP HANA database via self-hosted IR.",
  }),
  sap_bw: relationalSchema({
    id: "sap_bw", displayName: "SAP BW (Open Hub)", adfType: "SapOpenHub", datasetType: "SapOpenHubTable",
    hasDatabase: false, serverPlaceholder: "bw.contoso.com",
    extraFields: [
      { key: "systemNumber", label: "System number", type: "text", required: true, placeholder: "00" },
      { key: "clientId", label: "Client ID", type: "text", required: true, placeholder: "800" },
      { key: "language", label: "Logon language", type: "text", placeholder: "EN" },
    ],
    description: "SAP Business Warehouse via Open Hub Destination.",
  }),
  sybase: relationalSchema({
    id: "sybase", displayName: "Sybase", adfType: "Sybase", datasetType: "SybaseTable",
    port: "5000", hasSchema: true, description: "SAP Sybase ASE via self-hosted IR.",
  }),
  informix: relationalSchema({
    id: "informix", displayName: "IBM Informix", adfType: "Informix", datasetType: "InformixTable",
    hasDatabase: true, description: "IBM Informix via ODBC on a self-hosted IR.",
  }),
  netezza: relationalSchema({
    id: "netezza", displayName: "Netezza", adfType: "Netezza", datasetType: "NetezzaTable",
    port: "5480", hasSchema: true, description: "IBM Netezza / PureData (read-only source).",
  }),
  vertica: relationalSchema({
    id: "vertica", displayName: "Vertica", adfType: "Vertica", datasetType: "VerticaTable",
    port: "5433", hasSchema: true, description: "OpenText / Micro Focus Vertica.",
  }),
  greenplum: relationalSchema({
    id: "greenplum", displayName: "Greenplum", adfType: "Greenplum", datasetType: "GreenplumTable",
    port: "5432", hasSchema: true, description: "VMware Tanzu Greenplum (PostgreSQL-compatible).",
  }),
  apache_impala: relationalSchema({
    id: "apache_impala", displayName: "Apache Impala", adfType: "Impala", datasetType: "ImpalaObject",
    port: "21050", hasDatabase: false, description: "Apache Impala (read-only source).",
  }),
  apache_hbase: relationalSchema({
    id: "apache_hbase", displayName: "Apache HBase", adfType: "HBase", datasetType: "HBaseObject",
    port: "9090", hasDatabase: false, description: "Apache HBase via the Thrift gateway (read-only source).",
  }),
  apache_phoenix: relationalSchema({
    id: "apache_phoenix", displayName: "Apache Phoenix", adfType: "Phoenix", datasetType: "PhoenixObject",
    port: "8765", hasDatabase: false, description: "Apache Phoenix over HBase (read-only source).",
  }),
  hive: relationalSchema({
    id: "hive", displayName: "Apache Hive", adfType: "Hive", datasetType: "HiveObject",
    port: "10000", hasDatabase: false, description: "Apache Hive via HiveServer2 (read-only source).",
  }),
  presto: relationalSchema({
    id: "presto", displayName: "Presto", adfType: "Presto", datasetType: "PrestoObject",
    port: "8080", hasDatabase: true, description: "Presto / Trino distributed SQL (read-only source).",
  }),
  drill: relationalSchema({
    id: "drill", displayName: "Apache Drill", adfType: "Drill", datasetType: "DrillTable",
    port: "8047", hasDatabase: false, description: "Apache Drill (read-only source).",
  }),
  couchbase: relationalSchema({
    id: "couchbase", displayName: "Couchbase", adfType: "Couchbase", datasetType: "CouchbaseTable",
    port: "8091", hasDatabase: false, description: "Couchbase via the Analytics / N1QL service (read-only source).",
  }),
  cassandra: relationalSchema({
    id: "cassandra", displayName: "Cassandra", adfType: "Cassandra", datasetType: "CassandraTable",
    port: "9042", hasDatabase: false, hasSchema: true,
    authOptions: [
      { id: "basic", label: "Basic authentication" },
      { id: "anonymous", label: "Anonymous" },
    ],
    description: "Apache Cassandra (keyspace = schema).",
  }),
  amazon_rds_sql_server: relationalSchema({
    id: "amazon_rds_sql_server", displayName: "Amazon RDS for SQL Server", adfType: "AmazonRdsForSqlServer", datasetType: "AmazonRdsForSqlServerTable",
    port: "1433", hasSchema: true,
    authOptions: [
      { id: "sql_authentication", label: "SQL authentication" },
      { id: "windows_authentication", label: "Windows authentication" },
    ],
    defaultAuth: "sql_authentication",
    serverPlaceholder: "db.abc123.us-east-1.rds.amazonaws.com",
    description: "SQL Server on Amazon RDS.",
  }),
  amazon_rds_oracle: relationalSchema({
    id: "amazon_rds_oracle", displayName: "Amazon RDS for Oracle", adfType: "AmazonRdsForOracle", datasetType: "AmazonRdsForOracleTable",
    hasDatabase: false, serverLabel: "Server / TNS", serverPlaceholder: "db.abc123.us-east-1.rds.amazonaws.com:1521/ORCL",
    description: "Oracle on Amazon RDS.",
  }),
  microsoft_access: relationalSchema({
    id: "microsoft_access", displayName: "Microsoft Access", adfType: "MicrosoftAccess", datasetType: "MicrosoftAccessTable",
    hasDatabase: false, serverLabel: "Connection string", serverPlaceholder: "Driver={Microsoft Access Driver (*.mdb, *.accdb)};Dbq=\\\\share\\db.accdb;",
    description: "Microsoft Access database via ODBC on a self-hosted IR.",
  }),
  mongodb: {
    id: "mongodb", displayName: "MongoDB", adfType: "MongoDbV2", datasetType: "MongoDbV2Collection",
    kind: "database", category: "Database", defaultAuth: "connection_string",
    authOptions: [{ id: "connection_string", label: "Connection string", description: "mongodb:// URI with the secret referenced from Key Vault." }],
    fields: [
      { key: "connectionString", label: "Connection string secret name", type: "text", isSecret: true, required: true, placeholder: "mongodb-connection-string" },
      { key: "database", label: "Database name", type: "text", required: true, placeholder: "appdb" },
    ],
    description: "MongoDB (V2 connector); collections are the objects.",
  },
  mongodb_atlas: {
    id: "mongodb_atlas", displayName: "MongoDB Atlas", adfType: "MongoDbAtlas", datasetType: "MongoDbAtlasCollection",
    kind: "database", category: "Database", defaultAuth: "connection_string",
    authOptions: [{ id: "connection_string", label: "Connection string", description: "mongodb+srv:// URI referenced from Key Vault." }],
    fields: [
      { key: "connectionString", label: "Connection string secret name", type: "text", isSecret: true, required: true, placeholder: "mongodb-atlas-connection-string" },
      { key: "database", label: "Database name", type: "text", required: true, placeholder: "appdb" },
    ],
    description: "MongoDB Atlas cloud database.",
  },
  azure_cosmos_db: {
    id: "azure_cosmos_db", displayName: "Azure Cosmos DB (NoSQL)", adfType: "CosmosDb", datasetType: "CosmosDbSqlApiCollection",
    kind: "database", category: "Azure", defaultAuth: "account_key",
    authOptions: [
      { id: "account_key", label: "Account key", description: "Key stored in Key Vault." },
      { id: "service_principal", label: "Service principal" },
      { id: "managed_identity", label: "Managed identity" },
    ],
    fields: [
      { key: "accountEndpoint", label: "Account endpoint", type: "text", required: true, placeholder: "https://myacct.documents.azure.com:443/" },
      { key: "database", label: "Database name", type: "text", required: true, placeholder: "appdb" },
      { key: "accountKey", label: "Account key secret name", type: "text", isSecret: true, requiredFor: ["account_key"], placeholder: "cosmos-account-key" },
      ...SPN_FIELDS,
      CLOUD_TYPE_FIELD,
    ],
    description: "Azure Cosmos DB for NoSQL (SQL API); containers are the objects.",
  },
  azure_cosmos_db_mongodb: {
    id: "azure_cosmos_db_mongodb", displayName: "Azure Cosmos DB (MongoDB API)", adfType: "CosmosDbMongoDbApi", datasetType: "CosmosDbMongoDbApiCollection",
    kind: "database", category: "Azure", defaultAuth: "connection_string",
    authOptions: [{ id: "connection_string", label: "Connection string", description: "Referenced from Key Vault." }],
    fields: [
      { key: "connectionString", label: "Connection string secret name", type: "text", isSecret: true, required: true, placeholder: "cosmos-mongo-connection-string" },
      { key: "database", label: "Database name", type: "text", required: true, placeholder: "appdb" },
    ],
    description: "Azure Cosmos DB for MongoDB.",
  },
  azure_cosmos_db_postgresql: relationalSchema({
    id: "azure_cosmos_db_postgresql", displayName: "Azure Cosmos DB for PostgreSQL", adfType: "AzurePostgreSql", datasetType: "AzurePostgreSqlTable",
    port: "5432", hasSchema: true, serverPlaceholder: "c-mycluster.abc.postgres.cosmos.azure.com",
    description: "Azure Cosmos DB for PostgreSQL (Citus) via the Azure Database for PostgreSQL connector.",
  }),
  azure_database_mariadb: relationalSchema({
    id: "azure_database_mariadb", displayName: "Azure Database for MariaDB", adfType: "MariaDB", datasetType: "MariaDBTable",
    port: "3306", category: "Azure", serverPlaceholder: "mymaria.mariadb.database.azure.com",
    description: "Managed MariaDB on Azure (also covers on-prem MariaDB).",
  }),
  azure_data_explorer: {
    id: "azure_data_explorer", displayName: "Azure Data Explorer (Kusto)", adfType: "AzureDataExplorer", datasetType: "AzureDataExplorerTable",
    kind: "database", category: "Azure", defaultAuth: "service_principal",
    authOptions: [
      { id: "service_principal", label: "Service principal" },
      { id: "managed_identity", label: "Managed identity" },
    ],
    fields: [
      { key: "endpoint", label: "Cluster URI", type: "text", required: true, placeholder: "https://mycluster.westus.kusto.windows.net" },
      { key: "database", label: "Database name", type: "text", required: true, placeholder: "telemetry" },
      ...SPN_FIELDS,
    ],
    description: "Azure Data Explorer / Kusto; tables are the objects.",
  },
  azure_databricks_delta_lake: {
    id: "azure_databricks_delta_lake", displayName: "Azure Databricks Delta Lake", adfType: "AzureDatabricksDeltaLake", datasetType: "AzureDatabricksDeltaLakeDataset",
    kind: "database", category: "Azure", defaultAuth: "personal_access_token",
    authOptions: [
      { id: "personal_access_token", label: "Personal access token", description: "PAT stored in Key Vault." },
      { id: "managed_identity", label: "Managed identity" },
    ],
    fields: [
      { key: "domain", label: "Workspace URL", type: "text", required: true, placeholder: "https://adb-1234.5.azuredatabricks.net" },
      { key: "clusterId", label: "Cluster ID", type: "text", required: true, placeholder: "0101-123456-abcd7890" },
      { key: "accessToken", label: "Access token secret name", type: "text", isSecret: true, requiredFor: ["personal_access_token"], placeholder: "databricks-pat" },
      { key: "database", label: "Database / schema", type: "text", placeholder: "default" },
    ],
    description: "Delta Lake tables in an Azure Databricks workspace.",
  },
  fabric_warehouse: relationalSchema({
    id: "fabric_warehouse", displayName: "Microsoft Fabric Warehouse", adfType: "Warehouse", datasetType: "WarehouseTable",
    hasDatabase: true, hasSchema: true, serverLabel: "SQL connection string", serverPlaceholder: "xxxx.datawarehouse.fabric.microsoft.com",
    defaultAuth: "service_principal",
    authOptions: [
      { id: "service_principal", label: "Service principal" },
      { id: "managed_identity", label: "Managed identity" },
    ],
    extraFields: [
      { key: "workspaceId", label: "Workspace ID", type: "text", required: true, placeholder: "00000000-0000-0000-0000-000000000000" },
      { key: "artifactId", label: "Warehouse ID", type: "text", required: true, placeholder: "00000000-0000-0000-0000-000000000000" },
      ...SPN_FIELDS,
    ],
    description: "Microsoft Fabric Warehouse (T-SQL endpoint).",
  }),
};


// =============================================================================
// Registry — file / object-storage connectors
// =============================================================================

const FILE_CONNECTOR_SCHEMAS: Record<string, ConnectorSchema> = {
  azure_blob_storage: {
    id: "azure_blob_storage",
    displayName: "Azure Blob Storage",
    adfType: "AzureBlobStorage",
    datasetType: "DelimitedText",
    datasetLocationType: "AzureBlobStorageLocation",
    kind: "file",
    defaultAuth: "account_key",
    authOptions: storageAuthOptions,
    fields: storageFields,
    description: "Blob Storage account (V2 connector).",
  },
  azure_data_lake_storage_gen2: {
    id: "azure_data_lake_storage_gen2",
    displayName: "Azure Data Lake Storage Gen2",
    adfType: "AzureBlobFS",
    datasetType: "DelimitedText",
    datasetLocationType: "AzureBlobFSLocation",
    kind: "file",
    defaultAuth: "account_key",
    authOptions: storageAuthOptions,
    fields: storageFields,
    description: "ADLS Gen2 (hierarchical namespace) storage.",
  },
  azure_data_lake_storage_gen1: {
    id: "azure_data_lake_storage_gen1",
    displayName: "Azure Data Lake Storage Gen1",
    adfType: "AzureDataLakeStore",
    datasetType: "DelimitedText",
    datasetLocationType: "AzureDataLakeStoreLocation",
    kind: "file",
    defaultAuth: "service_principal",
    // ADF's AzureDataLakeStoreLinkedService has no account-key auth at all (dropped here) but does
    // support the factory's managed identity, which was missing (AGENTS.md §23.12 gap #23).
    authOptions: [
      { id: "service_principal", label: "Service principal" },
      { id: "managed_identity", label: "Managed identity", description: "System-assigned managed identity of the factory." },
    ],
    fields: [
      { key: "dataLakeStoreUri", label: "Data Lake Store URI", type: "text", required: true, placeholder: "https://myadls.azuredatalakestore.net/webhdfs/v1" },
      { key: "accountName", label: "Account name", type: "text", placeholder: "myadls" },
      { key: "subscriptionId", label: "Subscription ID", type: "text" },
      { key: "resourceGroupName", label: "Resource group", type: "text" },
      ...SPN_FIELDS,
    ],
    description: "Legacy ADLS Gen1 store. Service principal or the factory's managed identity — no account-key auth (ADF has none for Gen1).",
  },
  amazon_s3: {
    id: "amazon_s3",
    displayName: "Amazon S3",
    adfType: "AmazonS3",
    datasetType: "DelimitedText",
    datasetLocationType: "AmazonS3Location",
    kind: "file",
    defaultAuth: "access_key",
    authOptions: [
      { id: "access_key", label: "Access key (IAM user)" },
      { id: "assumed_role", label: "IAM role", description: "Uses the factory's IAM role." },
    ],
    fields: [
      { key: "serviceUrl", label: "Service URL", type: "text", placeholder: "https://s3.amazonaws.com" },
      { key: "accessKeyId", label: "Access key ID", type: "text", requiredFor: ["access_key"], placeholder: "AKIA..." },
      { key: "secretAccessKey", label: "Secret access key name", type: "text", isSecret: true, requiredFor: ["access_key"], placeholder: "aws-secret-key" },
      { key: "arn", label: "IAM role ARN", type: "text", requiredFor: ["assumed_role"], placeholder: "arn:aws:iam::123456789012:role/adf" },
    ],
    description: "Amazon S3 object storage.",
  },
  google_cloud_storage: {
    id: "google_cloud_storage",
    displayName: "Google Cloud Storage",
    adfType: "GoogleCloudStorage",
    datasetType: "DelimitedText",
    datasetLocationType: "GoogleCloudStorageLocation",
    kind: "file",
    defaultAuth: "access_key",
    authOptions: [{ id: "access_key", label: "HMAC access key" }],
    fields: [
      { key: "serviceUrl", label: "Service URL", type: "text", placeholder: "https://storage.googleapis.com" },
      { key: "accessKeyId", label: "Access key ID", type: "text", required: true, placeholder: "GOOG..." },
      { key: "secretAccessKey", label: "Secret access key name", type: "text", isSecret: true, required: true, placeholder: "gcs-secret-key" },
    ],
    description: "Google Cloud Storage bucket.",
  },
  file_system: {
    id: "file_system",
    displayName: "File system (on-prem)",
    adfType: "FileServer",
    datasetType: "DelimitedText",
    datasetLocationType: "FileServerLocation",
    kind: "file",
    defaultAuth: "windows_authentication",
    authOptions: [{ id: "windows_authentication", label: "Windows / basic authentication" }],
    fields: [
      { key: "host", label: "Host / UNC path", type: "text", required: true, placeholder: "\\\\fileserver01\\share" },
      { key: "userid", label: "User name", type: "text", required: true, placeholder: "DOMAIN\\svc_adf" },
      { key: "password", label: "Password secret name", type: "text", isSecret: true, required: true, placeholder: "fileshare-password" },
      { key: "enableSsl", label: "Enable SSL", type: "boolean" },
    ],
    description: "Windows file share via self-hosted IR.",
  },
  sftp: {
    id: "sftp",
    displayName: "SFTP",
    adfType: "Sftp",
    datasetType: "DelimitedText",
    datasetLocationType: "SftpLocation",
    kind: "file",
    defaultAuth: "basic",
    authOptions: [
      { id: "basic", label: "Basic (user / password)" },
      { id: "ssh_public_key", label: "SSH public key" },
    ],
    fields: [
      { key: "host", label: "Host name", type: "text", required: true, placeholder: "sftp.partner.com" },
      { key: "port", label: "Port", type: "number", placeholder: "22" },
      { key: "userName", label: "User name", type: "text", required: true, placeholder: "svc_sftp" },
      { key: "password", label: "Password secret name", type: "text", isSecret: true, requiredFor: ["basic"], placeholder: "sftp-password" },
      // Either a path on the self-hosted IR's filesystem, or the key content itself stored in Key
      // Vault — ADF supports both for SshPublicKey auth; only the path was exposed here, so a setup
      // with no self-hosted IR filesystem access had no way to configure a key (AGENTS.md §23.12
      // gap #26). Deliberately not requiredFor on either — it's one-of, not both.
      { key: "privateKeyPath", label: "Private key path (on the self-hosted IR)", type: "text", placeholder: "/opt/keys/id_rsa", help: "Use this OR the private key content below — not both." },
      { key: "privateKeyContent", label: "Private key content secret name", type: "text", isSecret: true, placeholder: "sftp-private-key", help: "PEM-encoded private key stored in Key Vault. Use this OR the path above — not both." },
      { key: "privateKeyPassphrase", label: "Key passphrase secret name", type: "text", isSecret: true, placeholder: "sftp-passphrase" },
      { key: "skipHostKeyValidation", label: "Skip host key validation", type: "boolean" },
      { key: "hostKeyFingerprint", label: "Host key fingerprint", type: "text" },
    ],
    description: "Secure file transfer over SSH.",
  },
  ftp: {
    id: "ftp",
    displayName: "FTP",
    adfType: "FtpServer",
    datasetType: "DelimitedText",
    datasetLocationType: "FtpServerLocation",
    kind: "file",
    defaultAuth: "basic",
    authOptions: [
      { id: "basic", label: "Basic authentication" },
      { id: "anonymous", label: "Anonymous" },
    ],
    fields: [
      { key: "host", label: "Host name", type: "text", required: true, placeholder: "ftp.partner.com" },
      { key: "port", label: "Port", type: "number", placeholder: "21" },
      { key: "enableSsl", label: "Enable SSL", type: "boolean" },
      { key: "enableServerCertificateValidation", label: "Validate server certificate", type: "boolean" },
      { key: "userName", label: "User name", type: "text", requiredFor: ["basic"], placeholder: "svc_ftp" },
      { key: "password", label: "Password secret name", type: "text", isSecret: true, requiredFor: ["basic"], placeholder: "ftp-password" },
    ],
    description: "Plain FTP server.",
  },
  http: {
    id: "http",
    displayName: "HTTP / HTTPS",
    adfType: "HttpServer",
    datasetType: "DelimitedText",
    datasetLocationType: "HttpServerLocation",
    kind: "file",
    defaultAuth: "anonymous",
    // client_certificate added — ADF's HttpServerLinkedService supports it alongside
    // Anonymous/Basic and it was simply missing here (AGENTS.md §23.12 gap #24).
    authOptions: [
      { id: "anonymous", label: "Anonymous" },
      { id: "basic", label: "Basic authentication" },
      { id: "client_certificate", label: "Client certificate", description: "PFX client certificate stored in Key Vault." },
    ],
    fields: [
      { key: "url", label: "Base URL", type: "text", required: true, placeholder: "https://api.partner.com/files/" },
      { key: "userName", label: "User name", type: "text", requiredFor: ["basic"], placeholder: "user" },
      {
        key: "password", label: "Password / certificate passphrase secret name", type: "text", isSecret: true,
        requiredFor: ["basic", "client_certificate"], placeholder: "http-password",
        help: "Basic auth password, or the client certificate's passphrase.",
      },
      {
        key: "embeddedCertData", label: "Client certificate (base64 PFX) secret name", type: "text", isSecret: true,
        requiredFor: ["client_certificate"], placeholder: "http-client-cert",
      },
    ],
    description: "Files over HTTP/HTTPS endpoints.",
  },

  // --- Additional file / object stores (factory-built) ---------------------
  azure_files: fileStoreSchema({
    id: "azure_files", displayName: "Azure Files", adfType: "AzureFileStorage", datasetLocationType: "AzureFileStorageLocation",
    defaultAuth: "account_key",
    authOptions: [
      { id: "account_key", label: "Account key" },
      { id: "sas_uri", label: "SAS token" },
    ],
    fields: [
      { key: "host", label: "File share host", type: "text", required: true, placeholder: "\\\\myacct.file.core.windows.net\\share" },
      { key: "accountName", label: "Storage account name", type: "text", required: true, placeholder: "myacct" },
      { key: "accountKey", label: "Account key secret name", type: "text", isSecret: true, requiredFor: ["account_key"], placeholder: "azurefiles-account-key" },
      { key: "sasToken", label: "SAS token secret name", type: "text", isSecret: true, requiredFor: ["sas_uri"], placeholder: "azurefiles-sas-token" },
    ],
    description: "Azure Files SMB share.",
  }),
  azure_table_storage: {
    id: "azure_table_storage", displayName: "Azure Table Storage", adfType: "AzureTableStorage", datasetType: "AzureTable",
    kind: "database", category: "Azure", defaultAuth: "account_key",
    authOptions: [
      { id: "account_key", label: "Account key" },
      { id: "sas_uri", label: "SAS token" },
      { id: "managed_identity", label: "Managed identity" },
    ],
    fields: [
      { key: "accountName", label: "Storage account name", type: "text", required: true, placeholder: "myacct" },
      { key: "url", label: "Table endpoint", type: "text", placeholder: "https://myacct.table.core.windows.net" },
      { key: "accountKey", label: "Account key secret name", type: "text", isSecret: true, requiredFor: ["account_key"], placeholder: "table-account-key" },
      { key: "sasToken", label: "SAS token secret name", type: "text", isSecret: true, requiredFor: ["sas_uri"], placeholder: "table-sas-token" },
    ],
    description: "Azure Table Storage; each table is an object.",
  },
  hdfs: fileStoreSchema({
    id: "hdfs", displayName: "HDFS", adfType: "Hdfs", datasetLocationType: "HdfsLocation",
    defaultAuth: "anonymous",
    authOptions: [
      { id: "anonymous", label: "Anonymous" },
      { id: "windows_authentication", label: "Windows / Kerberos" },
    ],
    fields: [
      { key: "url", label: "NameNode URL", type: "text", required: true, placeholder: "http://namenode:50070/webhdfs/v1/" },
      { key: "userName", label: "User name", type: "text", requiredFor: ["windows_authentication"], placeholder: "hdfs" },
      { key: "password", label: "Password secret name", type: "text", isSecret: true, requiredFor: ["windows_authentication"], placeholder: "hdfs-password" },
    ],
    description: "Hadoop HDFS via WebHDFS (self-hosted IR).",
  }),
  amazon_s3_compatible: fileStoreSchema({
    id: "amazon_s3_compatible", displayName: "Amazon S3 Compatible Storage", adfType: "AmazonS3Compatible", datasetLocationType: "AmazonS3CompatibleLocation",
    defaultAuth: "access_key",
    authOptions: [{ id: "access_key", label: "Access key" }],
    fields: [
      { key: "serviceUrl", label: "Service URL", type: "text", required: true, placeholder: "https://minio.internal:9000" },
      { key: "accessKeyId", label: "Access key ID", type: "text", required: true, placeholder: "AKIA..." },
      { key: "secretAccessKey", label: "Secret access key name", type: "text", isSecret: true, required: true, placeholder: "s3compat-secret-key" },
      { key: "forcePathStyle", label: "Force path-style access", type: "boolean" },
    ],
    description: "MinIO / Wasabi / other S3-compatible object stores.",
  }),
  oracle_cloud_storage: fileStoreSchema({
    id: "oracle_cloud_storage", displayName: "Oracle Cloud Storage", adfType: "OracleCloudStorage", datasetLocationType: "OracleCloudStorageLocation",
    defaultAuth: "access_key",
    authOptions: [{ id: "access_key", label: "HMAC access key" }],
    fields: [
      { key: "serviceUrl", label: "Service URL", type: "text", required: true, placeholder: "https://<namespace>.compat.objectstorage.<region>.oraclecloud.com" },
      { key: "accessKeyId", label: "Access key ID", type: "text", required: true, placeholder: "ocid1..." },
      { key: "secretAccessKey", label: "Secret access key name", type: "text", isSecret: true, required: true, placeholder: "ocs-secret-key" },
    ],
    description: "Oracle Cloud Infrastructure Object Storage (S3 compatibility).",
  }),
  fabric_lakehouse: fileStoreSchema({
    id: "fabric_lakehouse", displayName: "Microsoft Fabric Lakehouse (Files)", adfType: "Lakehouse", datasetLocationType: "LakehouseLocation",
    defaultAuth: "service_principal",
    authOptions: [
      { id: "service_principal", label: "Service principal" },
      { id: "managed_identity", label: "Managed identity" },
    ],
    fields: [
      { key: "workspaceId", label: "Workspace ID", type: "text", required: true, placeholder: "00000000-0000-0000-0000-000000000000" },
      { key: "artifactId", label: "Lakehouse ID", type: "text", required: true, placeholder: "00000000-0000-0000-0000-000000000000" },
      ...SPN_FIELDS,
    ],
    description: "Files area of a Microsoft Fabric Lakehouse.",
  }),
  sharepoint_online_list: {
    id: "sharepoint_online_list", displayName: "SharePoint Online List", adfType: "SharePointOnlineList", datasetType: "SharePointOnlineListResource",
    kind: "service", category: "Service", defaultAuth: "service_principal",
    authOptions: [{ id: "service_principal", label: "Service principal (Azure AD app)" }],
    fields: [
      { key: "siteUrl", label: "Site URL", type: "text", required: true, placeholder: "https://contoso.sharepoint.com/sites/team" },
      { key: "tenantId", label: "Tenant ID", type: "text", required: true, placeholder: "tenant-id" },
      { key: "servicePrincipalId", label: "Client ID", type: "text", required: true, placeholder: "app-client-id" },
      { key: "servicePrincipalKey", label: "Client secret name", type: "text", isSecret: true, required: true, placeholder: "sharepoint-client-secret" },
    ],
    description: "Items from a SharePoint Online list.",
  },
  web_table: {
    id: "web_table", displayName: "Web Table (HTML)", adfType: "Web", datasetType: "WebTable",
    kind: "file", category: "File", defaultAuth: "anonymous",
    authOptions: [
      { id: "anonymous", label: "Anonymous" },
      { id: "basic", label: "Basic authentication" },
    ],
    fields: [
      { key: "url", label: "Page URL", type: "text", required: true, placeholder: "https://example.com/data.html" },
      { key: "userName", label: "User name", type: "text", requiredFor: ["basic"], placeholder: "user" },
      { key: "password", label: "Password secret name", type: "text", isSecret: true, requiredFor: ["basic"], placeholder: "web-password" },
    ],
    description: "Tables parsed from an HTML page (self-hosted IR).",
  },
  odata: restServiceSchema({
    id: "odata", displayName: "OData", adfType: "OData", datasetType: "ODataResource",
    baseUrlLabel: "Service root URL", baseUrlPlaceholder: "https://services.odata.org/V4/Northwind/Northwind.svc/",
    authOptions: [
      { id: "anonymous", label: "Anonymous" },
      { id: "basic", label: "Basic authentication" },
      { id: "oauth2", label: "OAuth 2.0 (AAD / client credentials)" },
      { id: "managed_identity", label: "Managed identity" },
    ],
    defaultAuth: "anonymous",
    description: "Any OData v3/v4 feed.",
  }),
  odbc: {
    id: "odbc", displayName: "ODBC", adfType: "Odbc", datasetType: "OdbcTable",
    kind: "database", category: "Generic", defaultAuth: "basic",
    authOptions: [
      { id: "basic", label: "Basic authentication" },
      { id: "anonymous", label: "Anonymous (DSN handles auth)" },
    ],
    fields: [
      { key: "connectionString", label: "Connection string", type: "text", required: true, placeholder: "Driver={...};Server=...;Database=...;" },
      { key: "userName", label: "User name", type: "text", requiredFor: ["basic"], placeholder: "user" },
      { key: "password", label: "Password secret name", type: "text", isSecret: true, requiredFor: ["basic"], placeholder: "odbc-password" },
    ],
    description: "Generic ODBC driver on a self-hosted IR.",
  },
  rest: restServiceSchema({
    id: "rest", displayName: "REST", baseUrlPlaceholder: "https://api.partner.com/v1/",
    authOptions: [
      { id: "anonymous", label: "Anonymous" },
      { id: "basic", label: "Basic authentication" },
      { id: "api_key", label: "API key header" },
      { id: "oauth2", label: "OAuth 2.0 (client credentials)" },
      { id: "managed_identity", label: "AAD managed identity" },
      { id: "service_principal", label: "AAD service principal" },
    ],
    defaultAuth: "anonymous",
    extraFields: [
      { key: "aadResourceId", label: "AAD resource / audience", type: "text", placeholder: "https://management.azure.com/" },
    ],
    description: "Generic REST endpoint (paged JSON) via the REST connector.",
  }),
};

// Merge both registries into the single lookup used across the app.
for (const [key, schema] of Object.entries(FILE_CONNECTOR_SCHEMAS)) {
  CONNECTOR_SCHEMAS[key] = schema;
}

// =============================================================================
// Registry — SaaS / application connectors
// =============================================================================

/** Standard "API key or OAuth" object connector for a SaaS product. */
function saasSchema(
  id: SourceTargetType,
  displayName: string,
  adfType: string,
  datasetType: string,
  baseUrlPlaceholder: string,
  description: string,
  extraFields: ConnectionField[] = [],
): ConnectorSchema {
  return restServiceSchema({
    id,
    displayName,
    adfType,
    datasetType,
    baseUrlLabel: "Endpoint / instance URL",
    baseUrlPlaceholder,
    authOptions: [
      { id: "oauth2", label: "OAuth 2.0" },
      { id: "api_key", label: "API key / token" },
      { id: "basic", label: "Basic authentication" },
    ],
    defaultAuth: "oauth2",
    extraFields,
    description,
  });
}

const SERVICE_CONNECTOR_SCHEMAS: Record<string, ConnectorSchema> = {
  salesforce: saasSchema("salesforce", "Salesforce", "SalesforceV2", "SalesforceV2Object", "https://login.salesforce.com", "Salesforce (V2 connector); SObjects are the objects.", [
    { key: "apiVersion", label: "API version", type: "text", placeholder: "59.0" },
  ]),
  salesforce_service_cloud: saasSchema("salesforce_service_cloud", "Salesforce Service Cloud", "SalesforceServiceCloudV2", "SalesforceServiceCloudV2Object", "https://login.salesforce.com", "Salesforce Service Cloud (V2 connector)."),
  salesforce_marketing_cloud: saasSchema("salesforce_marketing_cloud", "Salesforce Marketing Cloud", "SalesforceMarketingCloud", "SalesforceMarketingCloudObject", "https://YOUR_SUBDOMAIN.rest.marketingcloudapis.com", "Salesforce Marketing Cloud REST API."),
  dynamics_365: saasSchema("dynamics_365", "Dynamics 365", "CommonDataServiceForApps", "CommonDataServiceForAppsEntity", "https://org.crm.dynamics.com", "Dynamics 365 / Dataverse (entities are the objects)."),
  dynamics_crm: saasSchema("dynamics_crm", "Dynamics CRM", "DynamicsCrm", "DynamicsCrmEntity", "https://org.crm.dynamics.com", "Dynamics CRM online / on-prem."),
  dynamics_ax: saasSchema("dynamics_ax", "Dynamics AX", "DynamicsAX", "DynamicsAXResource", "https://org.operations.dynamics.com/data", "Dynamics AX / Finance & Operations OData."),
  dataverse: saasSchema("dataverse", "Microsoft Dataverse", "CommonDataServiceForApps", "CommonDataServiceForAppsEntity", "https://org.crm.dynamics.com", "Microsoft Dataverse (Common Data Service)."),
  servicenow: saasSchema("servicenow", "ServiceNow", "ServiceNowV2", "ServiceNowV2Object", "https://instance.service-now.com", "ServiceNow (V2 connector); tables are the objects."),
  sap_ecc: saasSchema("sap_ecc", "SAP ECC", "SapEcc", "SapEccResource", "https://sapecc.contoso.com/sap/opu/odata/sap/", "SAP ECC via OData services."),
  sap_cloud_for_customer: saasSchema("sap_cloud_for_customer", "SAP Cloud for Customer", "SapCloudForCustomer", "SapCloudForCustomerResource", "https://my.c4c.contoso.com", "SAP Cloud for Customer (C4C) OData."),
  google_adwords: saasSchema("google_adwords", "Google Ads", "GoogleAdWords", "GoogleAdWordsObject", "https://googleads.googleapis.com", "Google Ads (AdWords) reporting API."),
  google_sheets: saasSchema("google_sheets", "Google Sheets", "RestService", "RestResource", "https://sheets.googleapis.com/v4/spreadsheets/", "Google Sheets via the REST connector."),
  hubspot: saasSchema("hubspot", "HubSpot", "Hubspot", "HubspotObject", "https://api.hubapi.com", "HubSpot CRM / Marketing API."),
  jira: saasSchema("jira", "Jira", "Jira", "JiraObject", "https://your-domain.atlassian.net", "Atlassian Jira Cloud / Server."),
  marketo: saasSchema("marketo", "Marketo", "Marketo", "MarketoObject", "https://123-ABC-456.mktorest.com", "Adobe Marketo Engage REST API."),
  paypal: saasSchema("paypal", "PayPal", "Paypal", "PaypalObject", "https://api.paypal.com", "PayPal transaction / reporting API."),
  quickbooks: saasSchema("quickbooks", "QuickBooks", "QuickBooks", "QuickBooksObject", "https://quickbooks.api.intuit.com", "Intuit QuickBooks Online."),
  shopify: saasSchema("shopify", "Shopify", "Shopify", "ShopifyObject", "https://YOUR_STORE.myshopify.com", "Shopify Admin API."),
  smartsheet: saasSchema("smartsheet", "Smartsheet", "RestService", "RestResource", "https://api.smartsheet.com/2.0/", "Smartsheet via the REST connector."),
  square: saasSchema("square", "Square", "Square", "SquareObject", "https://connect.squareup.com", "Square payments / catalog API."),
  twilio: saasSchema("twilio", "Twilio", "RestService", "RestResource", "https://api.twilio.com/2010-04-01/", "Twilio messaging / usage API via REST."),
  xero: saasSchema("xero", "Xero", "Xero", "XeroObject", "https://api.xero.com/api.xro/2.0/", "Xero accounting API."),
  zendesk: saasSchema("zendesk", "Zendesk", "Zendesk", "ZendeskObject", "https://YOUR_SUBDOMAIN.zendesk.com", "Zendesk Support API."),
  zoho: saasSchema("zoho", "Zoho", "Zoho", "ZohoObject", "https://www.zohoapis.com", "Zoho CRM / Analytics API."),
  github: saasSchema("github", "GitHub", "RestService", "RestResource", "https://api.github.com", "GitHub REST API (repos, issues, actions)."),
  microsoft_365: {
    id: "microsoft_365", displayName: "Microsoft 365", adfType: "Office365", datasetType: "Office365Table",
    kind: "service", category: "Service", defaultAuth: "service_principal",
    authOptions: [{ id: "service_principal", label: "Service principal (Azure AD app)" }],
    fields: [
      { key: "office365TenantId", label: "Office 365 tenant ID", type: "text", required: true, placeholder: "tenant-id" },
      { key: "servicePrincipalTenantId", label: "SPN tenant ID", type: "text", required: true, placeholder: "tenant-id" },
      { key: "servicePrincipalId", label: "Client ID", type: "text", required: true, placeholder: "app-client-id" },
      { key: "servicePrincipalKey", label: "Client secret name", type: "text", isSecret: true, required: true, placeholder: "m365-client-secret" },
    ],
    description: "Microsoft Graph data access (Microsoft 365 / Office 365).",
  },
  azure_cognitive_search: {
    id: "azure_cognitive_search", displayName: "Azure AI Search", adfType: "AzureSearch", datasetType: "AzureSearchIndex",
    kind: "service", category: "Azure", defaultAuth: "api_key",
    authOptions: [{ id: "api_key", label: "Admin key", description: "Admin key stored in Key Vault." }],
    fields: [
      { key: "url", label: "Search service URL", type: "text", required: true, placeholder: "https://myservice.search.windows.net" },
      { key: "key", label: "Admin key secret name", type: "text", isSecret: true, required: true, placeholder: "search-admin-key" },
    ],
    description: "Azure AI Search (formerly Cognitive Search); write index documents as a sink.",
  },
  // Sample-guide parity gap 7 — both were entirely absent from the registry;
  // guide 2's Workday HR feed and Event-Hub-fed clickstream source could not
  // be wired up in Connections at all before this.
  workday: saasSchema(
    "workday",
    "Workday",
    "Workday",
    "WorkdayObject",
    "https://wd2-services1.workday.com/ccx/service/tenant",
    "Workday HR / Financials via REST report-as-a-service, OAuth 2.0 client-credentials.",
    [{ key: "reportEntity", label: "RaaS report entity", type: "text", placeholder: "Workers" }],
  ),
  azure_event_hub: {
    id: "azure_event_hub", displayName: "Azure Event Hubs", adfType: "AzureEventHubs", datasetType: "AvroFormat",
    kind: "service", category: "Azure", defaultAuth: "connection_string",
    authOptions: [
      { id: "connection_string", label: "Connection string", description: "Namespace/Event Hub connection string, referenced from Key Vault." },
      { id: "managed_identity", label: "Managed identity" },
    ],
    fields: [
      { key: "connectionString", label: "Connection string secret name", type: "text", isSecret: true, placeholder: "eventhub-connection-string" },
      { key: "eventHubName", label: "Event Hub name", type: "text", required: true, placeholder: "clickstream" },
    ],
    description: "Azure Event Hubs; captured events land as Avro and are read as a Copy source (streaming ingestion itself is out of scope, §20).",
  },
};

for (const [key, schema] of Object.entries(SERVICE_CONNECTOR_SCHEMAS)) {
  CONNECTOR_SCHEMAS[key] = schema;
}

// =============================================================================
// Registry — LLM / model-serving connectors
// =============================================================================
//
// ADF has no native "LLM" linked service. These are modeled as RestService
// linked services that ADF calls from Web / REST activities (e.g. enrich a
// row, classify, embed, summarise). Every credential is a Key Vault secret
// *name*; the endpoint, model/deployment and API version are user input.

const LLM_CONNECTOR_SCHEMAS: Record<string, ConnectorSchema> = {
  azure_openai: restServiceSchema({
    id: "azure_openai", displayName: "Azure OpenAI", kind: "llm",
    baseUrlLabel: "Resource endpoint",
    baseUrlPlaceholder: "https://my-aoai.openai.azure.com",
    authOptions: [
      { id: "api_key", label: "API key", description: "Key stored in Key Vault." },
      { id: "managed_identity", label: "Managed identity" },
      { id: "service_principal", label: "Service principal" },
    ],
    defaultAuth: "api_key",
    extraFields: [
      { key: "deployment", label: "Deployment name", type: "text", required: true, placeholder: "gpt-4o" },
      { key: "apiVersion", label: "API version", type: "text", required: true, placeholder: "2024-10-21" },
      { key: "aadResourceId", label: "AAD resource", type: "text", placeholder: "https://cognitiveservices.azure.com/" },
    ],
    description: "Azure OpenAI Service (chat / completions / embeddings).",
  }),
  azure_ai_foundry: restServiceSchema({
    id: "azure_ai_foundry", displayName: "Azure AI Foundry", kind: "llm",
    baseUrlLabel: "Model endpoint",
    baseUrlPlaceholder: "https://my-project.services.ai.azure.com/models",
    authOptions: [
      { id: "api_key", label: "API key" },
      { id: "managed_identity", label: "Managed identity" },
      { id: "service_principal", label: "Service principal" },
    ],
    defaultAuth: "api_key",
    extraFields: [
      { key: "model", label: "Model name", type: "text", required: true, placeholder: "Phi-3.5-MoE-instruct" },
      { key: "apiVersion", label: "API version", type: "text", placeholder: "2024-05-01-preview" },
    ],
    description: "Azure AI Foundry model inference endpoint (Models-as-a-Service).",
  }),
  openai: restServiceSchema({
    id: "openai", displayName: "OpenAI", kind: "llm",
    baseUrlPlaceholder: "https://api.openai.com/v1",
    authOptions: [{ id: "api_key", label: "API key (Bearer)" }],
    defaultAuth: "api_key",
    extraFields: [
      ...LLM_MODEL_FIELDS,
      { key: "organization", label: "Organization ID", type: "text", placeholder: "org-..." },
    ],
    description: "OpenAI platform API (chat / responses / embeddings).",
  }),
  anthropic: restServiceSchema({
    id: "anthropic", displayName: "Anthropic", kind: "llm",
    baseUrlPlaceholder: "https://api.anthropic.com/v1",
    authOptions: [{ id: "api_key", label: "API key (x-api-key)" }],
    defaultAuth: "api_key",
    extraFields: [
      { key: "model", label: "Default model", type: "text", placeholder: "model-id" },
      { key: "anthropicVersion", label: "Anthropic-Version header", type: "text", placeholder: "2023-06-01" },
    ],
    description: "Anthropic Messages API.",
  }),
  aws_bedrock: restServiceSchema({
    id: "aws_bedrock", displayName: "AWS Bedrock", kind: "llm",
    baseUrlLabel: "Runtime endpoint",
    baseUrlPlaceholder: "https://bedrock-runtime.us-east-1.amazonaws.com",
    authOptions: [
      { id: "aws_access_key", label: "Access key (SigV4)" },
      { id: "assumed_role", label: "IAM role" },
    ],
    defaultAuth: "aws_access_key",
    extraFields: [
      { key: "region", label: "AWS region", type: "text", required: true, placeholder: "us-east-1" },
      { key: "accessKeyId", label: "Access key ID", type: "text", requiredFor: ["aws_access_key"], placeholder: "AKIA..." },
      { key: "secretAccessKey", label: "Secret access key name", type: "text", isSecret: true, requiredFor: ["aws_access_key"], placeholder: "bedrock-secret-key" },
      { key: "roleArn", label: "IAM role ARN", type: "text", requiredFor: ["assumed_role"], placeholder: "arn:aws:iam::123456789012:role/adf-bedrock" },
      { key: "model", label: "Model ID", type: "text", placeholder: "provider.model-id-v1:0" },
    ],
    description: "Amazon Bedrock model runtime (InvokeModel / Converse).",
  }),
  google_vertex_ai: restServiceSchema({
    id: "google_vertex_ai", displayName: "Google Vertex AI", kind: "llm",
    baseUrlLabel: "API host",
    baseUrlPlaceholder: "https://us-central1-aiplatform.googleapis.com",
    authOptions: [
      { id: "service_account_key", label: "Service account key" },
      { id: "oauth2", label: "OAuth 2.0" },
    ],
    defaultAuth: "service_account_key",
    extraFields: [
      { key: "projectId", label: "GCP project ID", type: "text", required: true, placeholder: "my-gcp-project" },
      { key: "location", label: "Location / region", type: "text", required: true, placeholder: "us-central1" },
      { key: "clientEmail", label: "Service account email", type: "text", requiredFor: ["service_account_key"], placeholder: "svc@my-project.iam.gserviceaccount.com" },
      { key: "privateKey", label: "Private key secret name", type: "text", isSecret: true, requiredFor: ["service_account_key"], placeholder: "vertex-sa-key" },
      { key: "model", label: "Model / publisher model", type: "text", placeholder: "gemini-1.5-pro" },
    ],
    description: "Google Vertex AI generative models (generateContent / predict).",
  }),
  cohere: restServiceSchema({
    id: "cohere", displayName: "Cohere", kind: "llm",
    baseUrlPlaceholder: "https://api.cohere.com/v2",
    authOptions: [{ id: "api_key", label: "API key (Bearer)" }],
    defaultAuth: "api_key",
    extraFields: [{ key: "model", label: "Default model", type: "text", placeholder: "command-r-plus" }],
    description: "Cohere chat / embed / rerank API.",
  }),
  mistral_ai: restServiceSchema({
    id: "mistral_ai", displayName: "Mistral AI", kind: "llm",
    baseUrlPlaceholder: "https://api.mistral.ai/v1",
    authOptions: [{ id: "api_key", label: "API key (Bearer)" }],
    defaultAuth: "api_key",
    extraFields: [{ key: "model", label: "Default model", type: "text", placeholder: "mistral-large-latest" }],
    description: "Mistral AI chat / embeddings API.",
  }),
  databricks_model_serving: restServiceSchema({
    id: "databricks_model_serving", displayName: "Databricks Model Serving", kind: "llm",
    baseUrlLabel: "Workspace URL",
    baseUrlPlaceholder: "https://adb-1234.5.azuredatabricks.net",
    authOptions: [
      { id: "personal_access_token", label: "Personal access token" },
      { id: "oauth2", label: "OAuth (service principal)" },
    ],
    defaultAuth: "personal_access_token",
    extraFields: [
      { key: "endpointName", label: "Serving endpoint name", type: "text", required: true, placeholder: "databricks-dbrx-instruct" },
      { key: "accessToken", label: "Access token secret name", type: "text", isSecret: true, requiredFor: ["personal_access_token"], placeholder: "databricks-serving-pat" },
    ],
    description: "Databricks Mosaic AI Model Serving / Foundation Model APIs.",
  }),
  hugging_face_inference: restServiceSchema({
    id: "hugging_face_inference", displayName: "Hugging Face Inference", kind: "llm",
    baseUrlPlaceholder: "https://api-inference.huggingface.co/models/",
    authOptions: [{ id: "api_key", label: "Access token (Bearer)" }],
    defaultAuth: "api_key",
    extraFields: [{ key: "model", label: "Model repo ID", type: "text", placeholder: "meta-llama/Llama-3.1-8B-Instruct" }],
    description: "Hugging Face Inference API / Inference Endpoints.",
  }),
};

for (const [key, schema] of Object.entries(LLM_CONNECTOR_SCHEMAS)) {
  CONNECTOR_SCHEMAS[key] = schema;
}

/** Every modeled connector key, ordered by display name (for pickers). */
export const ALL_CONNECTORS: string[] = Object.keys(CONNECTOR_SCHEMAS).sort((a, b) =>
  CONNECTOR_SCHEMAS[a].displayName.localeCompare(CONNECTOR_SCHEMAS[b].displayName),
);

/** Connector keys that store tables (relational), ordered by display name. */
export const DATABASE_CONNECTORS: string[] = ALL_CONNECTORS.filter(
  (key) => CONNECTOR_SCHEMAS[key].kind === "database",
);

/** Connector keys that store files / objects, ordered by display name. */
export const FILE_CONNECTORS: string[] = ALL_CONNECTORS.filter(
  (key) => CONNECTOR_SCHEMAS[key].kind === "file",
);

/** SaaS / application connector keys, ordered by display name. */
export const SERVICE_CONNECTORS: string[] = ALL_CONNECTORS.filter(
  (key) => CONNECTOR_SCHEMAS[key].kind === "service",
);

/** LLM / model-serving connector keys, ordered by display name. */
export const LLM_CONNECTORS: string[] = ALL_CONNECTORS.filter(
  (key) => CONNECTOR_SCHEMAS[key].kind === "llm",
);

/** Coarse category for a connector, for grouped pickers and icons. */
export function connectorCategory(connectorType: string): NonNullable<ConnectorSchema["category"]> {
  const schema = connectorSchema(connectorType);
  if (schema.category) return schema.category;
  switch (schema.kind) {
    case "llm":
      return "AI";
    case "service":
      return "Service";
    case "file":
      return "File";
    case "database":
      return "Database";
    default:
      return "Generic";
  }
}

/** Connector keys grouped by category, each list ordered by display name (for `<optgroup>` pickers). */
export const CONNECTOR_GROUPS: Array<{ category: NonNullable<ConnectorSchema["category"]>; connectors: string[] }> = (
  ["Azure", "Database", "File", "Service", "AI", "Generic"] as const
)
  .map((category) => ({
    category,
    connectors: ALL_CONNECTORS.filter((key) => connectorCategory(key) === category),
  }))
  .filter((group) => group.connectors.length > 0);


// =============================================================================
// Linked service / dataset drafts (user input)
// =============================================================================

/**
 * Editable linked-service state held in the wizard. Every value comes from
 * the user (prefilled from Project Settings where configured); nothing is
 * hard-coded at build time.
 */
export interface LinkedServiceDraft {
  /** ADF linked service name, e.g. LS_sql_server_source. */
  name: string;
  /** Connector registry key (SourceTargetType). */
  connectorType: SourceTargetType;
  /** Selected authentication mechanism. */
  authType: AuthenticationType;
  /** Integration runtime reference name. */
  integrationRuntime: string;
  /** Linked service that stores secrets referenced by this connection. */
  keyVaultLinkedService: string;
  /**
   * Field values keyed by ConnectionField.key.
   * Secret fields hold the *Key Vault secret name*, never the secret.
   */
  fields: Record<string, string>;
  /** Field keys rendered as @{linkedService().key} ADF parameters. */
  parameterize: string[];
  /**
   * §32.6 — "system" (default/undefined: the factory's own identity, no extra resource) or
   * "user" (a user-assigned managed identity referenced through a `credential` artifact).
   * Only meaningful when `authType` is `managed_identity`.
   */
  identityKind?: "system" | "user";
  /** Credential artifact name for a user-assigned identity. */
  credentialName?: string;
  /** The UAMI's ARM resource ID, carried on the draft so the generator stays pure. */
  credentialResourceId?: string;
}

/** True when this linked service references a user-assigned identity credential. */
export function usesUserAssignedIdentity(ls: LinkedServiceDraft): boolean {
  return ls.identityKind === "user" && !!ls.credentialName?.trim();
}

/** File-format settings for delimited-text datasets. */
export interface DatasetFormatSettings {
  columnDelimiter: string;
  rowDelimiter: string;
  quoteChar: string;
  escapeChar: string;
  firstRowAsHeader: boolean;
  encoding: string;
  compression: "none" | "gzip" | "bzip2" | "deflate";
}

/**
 * Editable dataset state held in the wizard. Datasets reference the
 * linked-service drafts configured in the same wizard step.
 */
export interface DatasetDraft {
  name: string;
  /** Linked service reference (must match a configured linked service name). */
  linkedServiceName: string;
  /** Relational datasets: schema + table (parameterizable). */
  schema: string;
  table: string;
  /** File datasets: folder path + file name (parameterizable). */
  folderPath: string;
  fileName: string;
  /** Render schema/table or folder/file as @{dataset().…} parameters. */
  parameterized: boolean;
  /** Delimited-text format settings (file datasets only). */
  format: DatasetFormatSettings;
}

export const DEFAULT_INTEGRATION_RUNTIME = "AutoResolveIntegrationRuntime";
export const DEFAULT_KEY_VAULT_LINKED_SERVICE = "LS_azure_key_vault";

/** Create a linked-service draft prefilled from the connector registry. */
export function defaultLinkedServiceDraft(kind: "source" | "target", connectorType: SourceTargetType): LinkedServiceDraft {
  const schema = connectorSchema(connectorType);
  return {
    name: `LS_${connectorType}_${kind}`,
    connectorType,
    authType: schema.defaultAuth,
    integrationRuntime: DEFAULT_INTEGRATION_RUNTIME,
    keyVaultLinkedService: DEFAULT_KEY_VAULT_LINKED_SERVICE,
    fields: {},
    parameterize: [],
  };
}

/** Create a dataset draft prefilled for the given side of the pipeline. */
export function defaultDatasetDraft(_kind: "source" | "target", _connectorType: SourceTargetType): DatasetDraft {
  return {
    name: "",
    linkedServiceName: "",
    schema: "",
    table: "",
    folderPath: "",
    fileName: "",
    parameterized: true,
    format: {
      columnDelimiter: ",",
      rowDelimiter: "\n",
      quoteChar: '"',
      escapeChar: "\\",
      firstRowAsHeader: true,
      encoding: "utf-8",
      compression: "none",
    },
  };
}


// =============================================================================
// ADF JSON projection — linked services
// =============================================================================

/** AzureKeyVaultSecret dynamic-content block (secrets never leave Key Vault). */
function keyVaultSecret(storeName: string, secretName: string): Record<string, unknown> {
  return {
    type: "AzureKeyVaultSecret",
    store: { referenceName: storeName, type: "LinkedServiceReference" },
    secretName,
  };
}

function rawValue(ls: LinkedServiceDraft, field: ConnectionField | undefined): string {
  return field ? (ls.fields[field.key] ?? "").trim() : "";
}

/** "true"/"1" -> "true", anything else -> "false" for boolean typeProperties. */
function boolValue(ls: LinkedServiceDraft, field: ConnectionField | undefined): string {
  const value = rawValue(ls, field);
  return value === "true" || value === "True" || value === "1" ? "true" : "false";
}

/**
 * Resolve one field into its typeProperties value:
 *  - secret fields -> AzureKeyVaultSecret (secret name parameterized when selected);
 *  - parameterized fields -> @{linkedService().key} with a parameter default;
 *  - anything else -> the literal value.
 */
function resolveField(
  ls: LinkedServiceDraft,
  field: ConnectionField,
  parameters: Record<string, { type: string; defaultValue?: unknown }>,
): Record<string, unknown> | string {
  const value = rawValue(ls, field);
  if (field.isSecret) {
    const parameterizedName = `${field.key}_secret_name`;
    const secretName = ls.parameterize.includes(field.key)
      ? `@{linkedService().${parameterizedName}}`
      : value;
    if (ls.parameterize.includes(field.key)) {
      parameters[parameterizedName] = { type: "String", defaultValue: value };
    }
    return keyVaultSecret(ls.keyVaultLinkedService || DEFAULT_KEY_VAULT_LINKED_SERVICE, secretName);
  }
  if (ls.parameterize.includes(field.key)) {
    parameters[field.key] = { type: "String", defaultValue: value };
    return `@{linkedService().${field.key}}`;
  }
  return value;
}

function pickField(ls: LinkedServiceDraft, key: string): ConnectionField {
  return connectorSchema(ls.connectorType).fields.find((f) => f.key === key) as ConnectionField;
}

/** Connector-specific typeProperties builder for the SQL family. */
function sqlTypeProperties(ls: LinkedServiceDraft, parameters: Record<string, { type: string; defaultValue?: unknown }>): Record<string, unknown> {
  const props: Record<string, unknown> = {
    server: resolveField(ls, pickField(ls, "server"), parameters),
    database: resolveField(ls, pickField(ls, "database"), parameters),
  };
  const encrypt = rawValue(ls, pickField(ls, "encrypt"));
  if (encrypt) props.encrypt = encrypt;
  const trust = rawValue(ls, pickField(ls, "trustServerCertificate"));
  if (trust) props.trustServerCertificate = boolValue(ls, pickField(ls, "trustServerCertificate"));
  if (ls.authType === "sql_authentication" || ls.authType === "windows_authentication") {
    props.authenticationType = ls.authType === "sql_authentication" ? "SQL" : "Windows";
    if (rawValue(ls, pickField(ls, "userName"))) props.userName = resolveField(ls, pickField(ls, "userName"), parameters);
    if (rawValue(ls, pickField(ls, "password"))) props.password = resolveField(ls, pickField(ls, "password"), parameters);
  } else if (ls.authType === "service_principal") {
    props.authenticationType = "ServicePrincipal";
    for (const key of ["servicePrincipalId", "servicePrincipalKey", "tenant", "azureCloudType"]) {
      if (rawValue(ls, pickField(ls, key))) props[key] = resolveField(ls, pickField(ls, key), parameters);
    }
  } else {
    props.authenticationType = "ManagedIdentity";
  }
  return props;
}

/** Storage (Blob / ADLS Gen2) typeProperties builder. */
function storageTypeProperties(ls: LinkedServiceDraft, parameters: Record<string, { type: string; defaultValue?: unknown }>): Record<string, unknown> {
  const props: Record<string, unknown> = {};
  if (rawValue(ls, pickField(ls, "url"))) props.url = resolveField(ls, pickField(ls, "url"), parameters);
  if (rawValue(ls, pickField(ls, "accountName"))) props.accountName = rawValue(ls, pickField(ls, "accountName"));
  if (rawValue(ls, pickField(ls, "sasUri"))) props.sasUri = resolveField(ls, pickField(ls, "sasUri"), parameters);
  if (ls.authType === "account_key") {
    if (rawValue(ls, pickField(ls, "accountKey"))) props.accountKey = resolveField(ls, pickField(ls, "accountKey"), parameters);
  } else if (ls.authType === "sas_uri") {
    if (rawValue(ls, pickField(ls, "sasToken"))) props.sasToken = resolveField(ls, pickField(ls, "sasToken"), parameters);
  } else if (ls.authType === "service_principal") {
    props.authenticationType = "ServicePrincipal";
    for (const key of ["servicePrincipalId", "servicePrincipalKey", "tenant", "azureCloudType"]) {
      if (rawValue(ls, pickField(ls, key))) props[key] = resolveField(ls, pickField(ls, key), parameters);
    }
  } else if (ls.authType === "managed_identity") {
    props.authenticationType = "ManagedIdentity";
  } else if (ls.authType === "connection_string") {
    const cs = pickField(ls, "connectionString");
    if (rawValue(ls, cs)) props.connectionString = resolveField(ls, cs, parameters);
  }
  return props;
}

/** Basic user/password typeProperties (Oracle, PostgreSQL, MySQL, SFTP, ...). */
function basicAuthTypeProperties(ls: LinkedServiceDraft, parameters: Record<string, { type: string; defaultValue?: unknown }>): Record<string, unknown> {
  const schema = connectorSchema(ls.connectorType);
  const props: Record<string, unknown> = {};
  for (const field of schema.fields) {
    const value = rawValue(ls, field);
    if (!value) continue;
    if (field.type === "boolean") {
      props[field.key] = boolValue(ls, field);
      continue;
    }
    if (field.type === "select") {
      props[field.key] = value;
      continue;
    }
    props[field.key] = resolveField(ls, field, parameters);
  }
  if (schema.id === "azure_database_postgresql" || schema.id === "azure_database_mysql") {
    props.authenticationType = "Basic";
  } else if (schema.id === "sftp") {
    props.authenticationType = ls.authType === "basic" ? "Basic" : "SshPublicKey";
  } else if (schema.id === "ftp") {
    props.authenticationType = ls.authType === "anonymous" ? "Anonymous" : "Basic";
  } else if (schema.id === "http") {
    // §23.12 gap #24 — client_certificate is a real HttpServerLinkedService auth type, not just
    // Basic/Anonymous.
    props.authenticationType =
      ls.authType === "basic" ? "Basic" : ls.authType === "client_certificate" ? "ClientCertificate" : "Anonymous";
  } else if (schema.id === "amazon_s3") {
    props.authenticationType = ls.authType === "assumed_role" ? "IAMRole" : "AccessKey";
  } else if (schema.id === "azure_data_lake_storage_gen1") {
    // §23.12 gap #23 — Gen1 has no account-key auth; it's service principal or the factory's
    // managed identity.
    props.authenticationType = ls.authType === "service_principal" ? "ServicePrincipal" : "ManagedServiceIdentity";
  } else if (schema.id === "snowflake") {
    // §23.12 gap #25 — SnowflakeV2's authenticationType enum (Basic/KeyPair/AADServicePrincipal).
    props.authenticationType =
      ls.authType === "key_pair" ? "KeyPair" : ls.authType === "service_principal" ? "AADServicePrincipal" : "Basic";
  }
  return props;
}

/** RestService `authenticationType` for the selected mechanism. */
function restAuthType(authType: AuthenticationType): string {
  switch (authType) {
    case "basic":
      return "Basic";
    case "oauth2":
      return "OAuth2ClientCredential";
    case "service_principal":
      return "AadServicePrincipal";
    case "managed_identity":
    case "system_assigned_managed_identity":
    case "user_assigned_managed_identity":
      return "ManagedServiceIdentity";
    default:
      // api_key / bearer_token / aws_access_key / service_account_key / anonymous
      // → no native RestService auth; the credential rides in authHeaders.
      return "Anonymous";
  }
}

const REST_AUTH_KEYS = new Set([
  "url", "userName", "password", "tokenEndpoint", "clientId", "clientSecret",
  "scope", "resource", "servicePrincipalId", "servicePrincipalKey", "tenant",
  "aadResourceId", "authHeaderName",
]);

/**
 * typeProperties builder for REST-backed connectors: the generic REST/OData
 * connector, SaaS/application connectors, and LLM / model-serving endpoints.
 * Credentials are always Key Vault references; API keys ride in `authHeaders`.
 */
function restTypeProperties(
  ls: LinkedServiceDraft,
  parameters: Record<string, { type: string; defaultValue?: unknown }>,
): Record<string, unknown> {
  const schema = connectorSchema(ls.connectorType);
  const props: Record<string, unknown> = {};
  const field = (key: string) => schema.fields.find((f) => f.key === key);
  const emit = (key: string) => {
    const f = field(key);
    if (!f || !rawValue(ls, f)) return;
    props[key] = f.type === "boolean" ? boolValue(ls, f) : resolveField(ls, f, parameters);
  };

  emit("url");
  props.authenticationType = restAuthType(ls.authType);

  if (ls.authType === "basic") {
    emit("userName");
    emit("password");
  } else if (ls.authType === "oauth2") {
    ["tokenEndpoint", "clientId", "clientSecret", "scope", "resource"].forEach(emit);
  } else if (ls.authType === "service_principal") {
    ["servicePrincipalId", "servicePrincipalKey", "tenant", "aadResourceId"].forEach(emit);
  }

  // API key / bearer token → custom auth header, value stays in Key Vault.
  if (ls.authType === "api_key" || ls.authType === "bearer_token") {
    const secretField = schema.fields.find(
      (f) => f.isSecret && ["apiKey", "accessToken", "key"].includes(f.key),
    );
    if (secretField && rawValue(ls, secretField)) {
      const headerName = rawValue(ls, field("authHeaderName")) || (schema.kind === "llm" ? "Authorization" : "api-key");
      props.authHeaders = {
        [headerName]: keyVaultSecret(
          ls.keyVaultLinkedService || DEFAULT_KEY_VAULT_LINKED_SERVICE,
          rawValue(ls, secretField),
        ),
      };
    }
  }

  // Connector-specific descriptors (model, deployment, apiVersion, region,
  // projectId, endpointName, …) plus any remaining secret fields pass through.
  for (const f of schema.fields) {
    if (REST_AUTH_KEYS.has(f.key)) continue;
    if (props[f.key] !== undefined) continue;
    const value = rawValue(ls, f);
    if (!value) continue;
    props[f.key] = f.type === "boolean" ? boolValue(ls, f) : resolveField(ls, f, parameters);
  }
  return props;
}

/**
 * Build the full ADF linked service `properties` object from a draft.
 * This is the JSON a developer would commit to Git for this connection.
 */
export function buildLinkedServiceProperties(ls: LinkedServiceDraft): Record<string, unknown> {
  const schema = connectorSchema(ls.connectorType);
  const parameters: Record<string, { type: string; defaultValue?: unknown }> = {};
  let typeProperties: Record<string, unknown>;
  switch (schema.id) {
    case "azure_sql_database":
    case "azure_sql_managed_instance":
    case "azure_synapse_analytics":
    case "sql_server":
      typeProperties = sqlTypeProperties(ls, parameters);
      break;
    case "azure_blob_storage":
    case "azure_data_lake_storage_gen2":
      typeProperties = storageTypeProperties(ls, parameters);
      break;
    default:
      typeProperties =
        schema.kind === "llm" || schema.kind === "service"
          ? restTypeProperties(ls, parameters)
          : basicAuthTypeProperties(ls, parameters);
      break;
  }
  // Pass through user-supplied extra keys (metadata-driven extensibility).
  const knownKeys = new Set(schema.fields.map((f) => f.key));
  for (const [key, value] of Object.entries(ls.fields)) {
    if (!knownKeys.has(key) && key.startsWith("custom.") && value.trim()) {
      typeProperties[key.replace("custom.", "")] = value.trim();
    }
  }
  return {
    type: schema.adfType,
    parameters,
    typeProperties,
    connectVia: {
      referenceName: ls.integrationRuntime || DEFAULT_INTEGRATION_RUNTIME,
      type: "IntegrationRuntimeReference",
    },
    annotations: [],
    // §32.5/§32.7 — mirrors worker/app/connectors/linked_service.py: a root-level credential
    // reference only for a user-assigned identity; "system" output is unchanged.
    ...(usesUserAssignedIdentity(ls)
      ? { credential: { referenceName: ls.credentialName!.trim(), type: "CredentialReference" } }
      : {}),
  };
}


// =============================================================================
// ADF JSON projection — datasets
// =============================================================================

export interface DatasetDefaults {
  schema: string;
  table: string;
  folderPath: string;
  fileName: string;
  /** Fallback dataset name, e.g. DS_{pipeline}_source. */
  fallbackName: string;
}

function formatProperties(format: DatasetFormatSettings): Record<string, unknown> {
  const props: Record<string, unknown> = {
    columnDelimiter: format.columnDelimiter,
    rowDelimiter: format.rowDelimiter,
    quoteChar: format.quoteChar,
    escapeChar: format.escapeChar,
    encoding: format.encoding || "utf-8",
    firstRowAsHeader: format.firstRowAsHeader,
  };
  if (format.compression !== "none") props.compressionCodec = format.compression;
  return props;
}

/**
 * Build the ADF dataset `properties` object from a draft.
 * Parameterized datasets expose SchemaName/TableName (relational) or
 * FolderPath/FileName (file) as dataset parameters — the metadata-driven
 * pattern feeds them from the control table at runtime.
 */
export function buildDatasetProperties(
  ds: DatasetDraft,
  ls: LinkedServiceDraft,
  defaults: DatasetDefaults,
): Record<string, unknown> {
  const schema = connectorSchema(ls.connectorType);
  const parameters: Record<string, { type: string; defaultValue?: unknown }> = {};
  const props: Record<string, unknown> = {
    linkedServiceName: {
      referenceName: ls.name || ds.linkedServiceName,
      type: "LinkedServiceReference",
    },
    parameters,
    annotations: [],
  };
  if (schema.kind === "file") {
    props.type = schema.datasetType;
    props.format = formatProperties(ds.format);
    const folderValue = ds.folderPath.trim() || defaults.folderPath;
    const fileValue = ds.fileName.trim() || defaults.fileName;
    if (ds.parameterized) {
      parameters.FolderPath = { type: "String", defaultValue: folderValue };
      parameters.FileName = { type: "String", defaultValue: fileValue };
      props.location = {
        type: schema.datasetLocationType ?? "AzureBlobFSLocation",
        folderPath: "@{dataset().FolderPath}",
        fileName: "@{dataset().FileName}",
      };
    } else {
      props.location = {
        type: schema.datasetLocationType ?? "AzureBlobFSLocation",
        folderPath: folderValue,
        fileName: fileValue,
      };
    }
  } else if (schema.kind === "service" || schema.kind === "llm") {
    // REST / SaaS / LLM: an object-name (relativeUrl) dataset, no SQL shape.
    props.type = schema.datasetType;
    const objectValue = ds.table.trim() || ds.folderPath.trim() || defaults.table;
    if (ds.parameterized) {
      parameters.RelativeUrl = { type: "String", defaultValue: objectValue };
      props.relativeUrl = "@{dataset().RelativeUrl}";
    } else if (objectValue) {
      props.relativeUrl = objectValue;
    }
  } else {
    props.type = schema.datasetType;
    const schemaValue = ds.schema.trim() || defaults.schema;
    const tableValue = ds.table.trim() || defaults.table;
    if (ds.parameterized) {
      parameters.SchemaName = { type: "String", defaultValue: schemaValue };
      parameters.TableName = { type: "String", defaultValue: tableValue };
    }
    // RUN-07: ADF reads a table dataset's schema/table from typeProperties (properties.schema is
    // the column-structure array), and has no sqlTableType property.
    props.typeProperties = ds.parameterized
      ? { schema: "@{dataset().SchemaName}", table: "@{dataset().TableName}" }
      : { schema: schemaValue, table: tableValue };
  }
  return props;
}

/** Compute the effective dataset name (user-entered or generated fallback). */
export function datasetName(ds: DatasetDraft, defaults: DatasetDefaults): string {
  return ds.name.trim() || defaults.fallbackName;
}


// =============================================================================
// Table / input-file list parsing (Upload file mode)
// =============================================================================

export interface ParsedTablesFile {
  objects: SourceObjectEntry[];
  warnings: string[];
}

const OBJECT_KEYS = ["object", "table", "table_name", "tablename", "source", "name", "object_name", "source_object"];
const CATALOG_KEYS = ["catalog", "database", "catalog_name", "db", "source_catalog"];
const SCHEMA_KEYS = ["schema", "schema_name", "source_schema"];
const QUALIFIER_OVERRIDE_KEYS = ["qualifier_override", "qualifieroverride", "override", "identifier_override"];
const CASE_OVERRIDE_KEYS = ["case_override", "caseoverride"];
const WATERMARK_KEYS = ["watermark_column", "watermarkcolumn", "watermark", "watermarkcolumnname"];
const BEHAVIOR_KEYS = ["loading_behavior", "loadingbehavior", "behavior", "load_behavior", "loadingbehaviour"];

function normalizeBehavior(raw: string | undefined): "FullLoad" | "DeltaLoad" | undefined {
  if (!raw) return undefined;
  const clean = raw.trim().toLowerCase().replace(/[_\s-]/g, "");
  if (["full", "fullload", "snapshot", "truncate"].includes(clean)) return "FullLoad";
  if (["delta", "deltaload", "incremental", "watermark"].includes(clean)) return "DeltaLoad";
  return undefined;
}

function normalizeCaseOverride(raw: string | undefined): "as-typed" | undefined {
  const clean = raw?.trim().toLowerCase().replace(/[_\s-]/g, "");
  return clean === "astyped" ? "as-typed" : undefined;
}

/** Quote-aware single-line split for , ; | or tab delimited values. */
export function splitDelimited(line: string): string[] {
  const cells: string[] = [];
  let current = "";
  let inQuotes = false;
  let delimiter = "";
  for (const char of line) {
    if (char === '"') {
      inQuotes = !inQuotes;
      continue;
    }
    const isDelimiter = char === "," || char === ";" || char === "|" || char === "\t";
    if (!inQuotes && isDelimiter) {
      if (!delimiter && char !== "\t") delimiter = char;
      if (char === "\t" || char === delimiter) {
        cells.push(current);
        current = "";
      } else {
        current += char;
      }
      continue;
    }
    current += char;
  }
  cells.push(current);
  return cells.map((cell) => cell.trim());
}

function entryFromCells(cells: string[]): SourceObjectEntry {
  return {
    object: cells[0] ?? "",
    watermarkColumn: cells[1] || undefined,
    loadingBehavior: normalizeBehavior(cells[2]),
  };
}

function entryFromRecord(record: Record<string, unknown>): SourceObjectEntry {
  const lowered: Record<string, string> = {};
  for (const [key, value] of Object.entries(record)) {
    lowered[key.toLowerCase().replace(/[\s-]/g, "_")] = String(value ?? "").trim();
  }
  const objectKey = OBJECT_KEYS.find((key) => lowered[key]);
  const catalogKey = CATALOG_KEYS.find((key) => lowered[key]);
  const schemaKey = SCHEMA_KEYS.find((key) => lowered[key]);
  const qualifierOverrideKey = QUALIFIER_OVERRIDE_KEYS.find((key) => lowered[key]);
  const caseOverrideKey = CASE_OVERRIDE_KEYS.find((key) => lowered[key]);
  const watermarkKey = WATERMARK_KEYS.find((key) => lowered[key]);
  const behaviorKey = BEHAVIOR_KEYS.find((key) => lowered[key]);
  return {
    object: objectKey ? lowered[objectKey] : "",
    catalog: catalogKey ? lowered[catalogKey] : undefined,
    schema: schemaKey ? lowered[schemaKey] : undefined,
    qualifierOverride: qualifierOverrideKey ? lowered[qualifierOverrideKey] : undefined,
    caseOverride: caseOverrideKey ? normalizeCaseOverride(lowered[caseOverrideKey]) : undefined,
    watermarkColumn: watermarkKey ? lowered[watermarkKey] : undefined,
    loadingBehavior: behaviorKey ? normalizeBehavior(lowered[behaviorKey]) : undefined,
  };
}

function looksLikeHeader(cells: string[]): boolean {
  const headerTokens = [
    ...OBJECT_KEYS,
    ...CATALOG_KEYS,
    ...SCHEMA_KEYS,
    ...QUALIFIER_OVERRIDE_KEYS,
    ...CASE_OVERRIDE_KEYS,
    ...WATERMARK_KEYS,
    ...BEHAVIOR_KEYS,
  ];
  return cells.some((cell) => headerTokens.includes(cell.toLowerCase().replace(/[\s-]/g, "_")));
}

function parseDelimitedContent(content: string, warnings: string[]): SourceObjectEntry[] {
  const lines = content.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const objects: SourceObjectEntry[] = [];
  let seenContentLine = false;
  let header: string[] | null = null;
  lines.forEach((line, index) => {
    if (line.startsWith("#")) return;
    const cells = splitDelimited(line);
    const isFirstContentLine = !seenContentLine;
    seenContentLine = true;
    // Header detection only applies to the first non-comment line, so a
    // template's leading `# ...` documentation lines don't push the real
    // header row past index 0 and have it mistaken for a data row. Once a
    // header is found, every later row is mapped by column name (not
    // position) — a plain positional row is only assumed when no header line
    // was present at all (minimal headerless lists).
    if (isFirstContentLine && looksLikeHeader(cells)) {
      header = cells;
      return;
    }
    const entry = header
      ? entryFromRecord(Object.fromEntries(header.map((key, i) => [key, cells[i] ?? ""])))
      : entryFromCells(cells);
    if (!entry.object) {
      warnings.push(`Line ${index + 1}: empty object name — skipped.`);
      return;
    }
    objects.push(entry);
  });
  return objects;
}

function parseJsonContent(content: string, warnings: string[]): SourceObjectEntry[] {
  const parsed: unknown = JSON.parse(content);
  if (!Array.isArray(parsed)) throw new Error("JSON input must be an array.");
  const objects: SourceObjectEntry[] = [];
  parsed.forEach((item, index) => {
    if (typeof item === "string") {
      if (item.trim()) objects.push({ object: item.trim() });
      return;
    }
    if (item && typeof item === "object") {
      const entry = entryFromRecord(item as Record<string, unknown>);
      if (!entry.object) {
        warnings.push(`JSON row ${index + 1}: no object/table column found — skipped.`);
        return;
      }
      objects.push(entry);
    }
  });
  return objects;
}

/**
 * Parse an uploaded table/input-file list. Supported formats:
 *  - JSON array of strings or objects (`object|table|table_name`, optional
 *    `watermark_column`, optional `loading_behavior`);
 *  - CSV / TSV with optional header row;
 *  - plain text lists (one object per line, optional , ; | separators).
 */
export function parseTablesFile(content: string, fileName: string): ParsedTablesFile {
  const warnings: string[] = [];
  const cleanName = fileName.toLowerCase();
  let objects: SourceObjectEntry[];
  try {
    if (cleanName.endsWith(".json")) {
      objects = parseJsonContent(content, warnings);
    } else {
      objects = parseDelimitedContent(content, warnings);
    }
  } catch (error) {
    return {
      objects: [],
      warnings: [`Could not parse ${fileName}: ${error instanceof Error ? error.message : "unknown error"}.`],
    };
  }
  const seen = new Set<string>();
  const unique: SourceObjectEntry[] = [];
  for (const entry of objects) {
    if (seen.has(entry.object)) {
      warnings.push(`Duplicate object "${entry.object}" — kept first occurrence.`);
      continue;
    }
    seen.add(entry.object);
    unique.push(entry);
  }
  if (!unique.length) warnings.push("No usable object rows were found in the file.");
  return { objects: unique, warnings };
}

// =============================================================================
// Downloadable templates — "Upload file" mode round-trips through these
// =============================================================================

/** Quote a single CSV cell only when it needs it (contains the delimiter, a quote, or whitespace at the edges). */
export function csvCell(value: string): string {
  if (/[",\r\n]/.test(value) || value !== value.trim()) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

export function csvRow(cells: string[]): string {
  return cells.map(csvCell).join(",");
}

/**
 * Build the downloadable CSV template for one connection's "Source objects
 * (tables / input files)" upload mode. Mirrors exactly what `parseTablesFile`
 * accepts: a header row (recognized by `looksLikeHeader`) followed by data
 * rows, with `#`-prefixed documentation lines ignored by the parser.
 *
 * Columns are every field `SourceObjectEntry` (`types/pipeline.ts`) carries
 * for one object under a single already-configured connection — the source/
 * target connection itself, dataset shape, and file format all come from the
 * connection being edited, not from this file. For a full source→target
 * registry row (different target/format/load behaviour per object), use
 * `buildOnboardingObjectsTemplate` (`lib/adf/onboarding.ts`) instead.
 */
export function buildSourceObjectsTemplate(): string {
  const lines = [
    "# Source object template — one row per table or input file for this connection.",
    "# object (required): bare name, e.g. customer, ORDERS, or file.csv — or a legacy dotted",
    "#   string (schema.table) when catalog/schema below are left blank.",
    "# catalog (optional): database/catalog segment — only for 3-part sources (Snowflake, SAP HANA with a tenant DB).",
    "# schema (optional): schema segment — leave blank for schema-less sources (SAP OData, Salesforce, ...).",
    "# qualifier_override (optional): exact identifier to query, bypassing catalog/schema parsing entirely.",
    "# case_override (optional): \"as-typed\" to skip automatic case-conformance (e.g. Oracle/SAP HANA upper-casing).",
    "# watermark_column (optional): column used to track a DeltaLoad watermark",
    "# loading_behavior (optional): FullLoad or DeltaLoad — leave blank to use the connection's default load settings",
    "# Delete these sample rows before uploading, or leave them and edit in place.",
    csvRow(["object", "catalog", "schema", "qualifier_override", "case_override", "watermark_column", "loading_behavior"]),
    csvRow(["customer", "", "dbo", "", "", "", "FullLoad"]),
    csvRow(["ORDERS", "MYDB", "SALES", "", "", "MODIFIED_DATE", "DeltaLoad"]),
    csvRow(["BusinessPartner", "", "", "", "", "", ""]),
  ];
  return lines.join("\r\n") + "\r\n";
}

// =============================================================================
// Linked service "Upload file" mode — template + parser
// =============================================================================

export interface ParsedLinkedServiceValues {
  name?: string;
  connectorType?: string;
  authType?: string;
  integrationRuntime?: string;
  keyVaultLinkedService?: string;
  /** Keyed by ConnectionField.key — only keys recognized by the connector's schema. */
  fields: Record<string, string>;
}

export interface ParsedLinkedServiceFile {
  /** One entry per data row — a file with N rows bulk-creates N connections. Empty when nothing parsed. */
  values: ParsedLinkedServiceValues[];
  warnings: string[];
}

const LS_NAME_KEYS = ["name", "linked_service_name", "ls_name", "connection_name"];
const LS_CONNECTOR_KEYS = ["connector_type", "connector", "connectortype", "type", "source_system"];
const LS_AUTH_KEYS = ["auth_type", "authentication", "authtype", "auth", "authentication_type"];
const LS_IR_KEYS = ["integration_runtime", "ir", "integrationruntime"];
const LS_KV_KEYS = ["key_vault_linked_service", "keyvault", "key_vault", "kv_linked_service", "vault"];
const LS_META_KEYS = [...LS_NAME_KEYS, ...LS_CONNECTOR_KEYS, ...LS_AUTH_KEYS, ...LS_IR_KEYS, ...LS_KV_KEYS];

function lowerKey(raw: string): string {
  return raw.trim().toLowerCase().replace(/[\s-]/g, "_");
}

function firstMatch(record: Record<string, string>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = record[key];
    if (value) return value;
  }
  return undefined;
}

/**
 * Build the downloadable template for a linked service's "Upload file" mode,
 * shaped for the connector + auth mechanism currently selected in the form
 * so every column the connector can use is present (fields not required by
 * `authType` may be left blank). Every data row is its own linked service —
 * add as many rows as connections you want to bulk-create (all sharing this
 * connector + auth type; a different connector needs its own template/file).
 */
export function buildLinkedServiceTemplate(connectorType: string, authType: AuthenticationType): string {
  const schema = connectorSchema(connectorType);
  const required = new Set(requiredFields(schema, authType).map((f) => f.key));
  const metaHeaders = ["name", "connector_type", "auth_type", "integration_runtime", "key_vault_linked_service"];
  const fieldHeaders = schema.fields.map((f) => f.key);
  const authList = schema.authOptions.map((o) => o.id).join(" | ");
  const requiredLabels = schema.fields.filter((f) => required.has(f.key)).map((f) => f.label);
  const sampleRow = (suffix: string) => [
    `LS_${connectorType}_${suffix}`,
    connectorType,
    authType,
    DEFAULT_INTEGRATION_RUNTIME,
    DEFAULT_KEY_VAULT_LINKED_SERVICE,
    ...schema.fields.map((f) => (f.isSecret ? "" : f.placeholder ?? "")),
  ];
  const lines = [
    `# Linked service template — ${schema.displayName} (${schema.adfType}).`,
    `# Every data row = one linked service; add rows to bulk-create several connections at once`,
    `#   (all rows must share this connector — a different connector needs its own file).`,
    `# auth_type options for this connector: ${authList}`,
    `# Required for auth_type "${authType}": ${requiredLabels.length ? requiredLabels.join(", ") : "none"}`,
    "# Secret-holding columns must contain a Key Vault *secret name*, never the actual secret value.",
    "# Delete these sample rows before uploading, or leave them and edit in place.",
    csvRow([...metaHeaders, ...fieldHeaders]),
    csvRow(sampleRow("source")),
    csvRow(sampleRow("source_2")),
  ];
  return lines.join("\r\n") + "\r\n";
}

function parsedLinkedServiceFromRecord(
  record: Record<string, string>,
  warnings: string[],
): ParsedLinkedServiceValues {
  const connectorType = firstMatch(record, LS_CONNECTOR_KEYS);
  const schema = connectorType ? connectorSchema(connectorType) : undefined;
  const fields: Record<string, string> = {};
  for (const [key, value] of Object.entries(record)) {
    if (LS_META_KEYS.includes(key) || !value) continue;
    if (schema && !schema.fields.some((f) => f.key.toLowerCase() === key)) {
      warnings.push(`Column "${key}" is not a field on this connector — ignored.`);
      continue;
    }
    // Field keys are camelCase; the template header is the raw key, so an
    // exact (case-insensitive) match is enough — no snake_case rewrite here.
    const matchedKey = schema?.fields.find((f) => f.key.toLowerCase() === key)?.key ?? key;
    fields[matchedKey] = value;
  }
  return {
    name: firstMatch(record, LS_NAME_KEYS),
    connectorType,
    authType: firstMatch(record, LS_AUTH_KEYS),
    integrationRuntime: firstMatch(record, LS_IR_KEYS),
    keyVaultLinkedService: firstMatch(record, LS_KV_KEYS),
    fields,
  };
}

function parseLinkedServiceJson(content: string, warnings: string[]): ParsedLinkedServiceValues[] {
  const parsed: unknown = JSON.parse(content);
  const items = Array.isArray(parsed) ? parsed : [parsed];
  const values: ParsedLinkedServiceValues[] = [];
  items.forEach((item, index) => {
    if (!item || typeof item !== "object") {
      warnings.push(`Entry ${index + 1}: not an object — skipped.`);
      return;
    }
    const record: Record<string, string> = {};
    for (const [key, value] of Object.entries(item as Record<string, unknown>)) {
      record[lowerKey(key)] = String(value ?? "").trim();
    }
    values.push(parsedLinkedServiceFromRecord(record, warnings));
  });
  if (!values.length) warnings.push("JSON input must be an object, or an array of objects.");
  return values;
}

function parseLinkedServiceDelimited(content: string, warnings: string[]): ParsedLinkedServiceValues[] {
  const lines = content.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  let header: string[] | null = null;
  const dataLines: string[][] = [];
  for (const line of lines) {
    if (line.startsWith("#")) continue;
    const cells = splitDelimited(line);
    if (!header) {
      header = cells.map(lowerKey);
      continue;
    }
    dataLines.push(cells);
  }
  if (!header || !dataLines.length) {
    warnings.push("No data row found — the file needs a header row plus at least one row of values.");
    return [];
  }
  return dataLines.map((cells) => {
    const record: Record<string, string> = {};
    header!.forEach((key, i) => {
      record[key] = (cells[i] ?? "").trim();
    });
    return parsedLinkedServiceFromRecord(record, warnings);
  });
}

/**
 * Parse an uploaded linked-service file (CSV/TSV/TXT header + one row per
 * connection, or a JSON object/array of objects). Recognizes `name`,
 * `connector_type`, `auth_type`, `integration_runtime`,
 * `key_vault_linked_service` (case-insensitive, a few aliases each) plus one
 * column per connector field key; unrecognized field columns are reported as
 * warnings rather than silently dropped. Every row/entry bulk-creates its own
 * connection — see `buildLinkedServiceTemplate`.
 */
export function parseLinkedServiceFile(content: string, fileName: string): ParsedLinkedServiceFile {
  const warnings: string[] = [];
  const cleanName = fileName.toLowerCase();
  try {
    const values = cleanName.endsWith(".json")
      ? parseLinkedServiceJson(content, warnings)
      : parseLinkedServiceDelimited(content, warnings);
    values.forEach((value, index) => {
      if (!value.name && !value.connectorType) {
        warnings.push(`Row ${index + 1}: no linked service name or connector type found.`);
      }
    });
    return { values, warnings };
  } catch (error) {
    return {
      values: [],
      warnings: [`Could not parse ${fileName}: ${error instanceof Error ? error.message : "unknown error"}.`],
    };
  }
}

// =============================================================================
// Connection / dataset validation
// =============================================================================

/** Validate a linked-service draft against its connector schema. */
export function validateLinkedServiceDraft(ls: LinkedServiceDraft, field: string): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const schema = connectorSchema(ls.connectorType);
  const label = ls.name.trim() || schema.displayName;
  if (!ls.name.trim()) {
    issues.push({ severity: "error", message: `Linked service name is required (${schema.displayName}).`, field });
  } else if (!/^[A-Za-z][A-Za-z0-9_]{1,63}$/.test(ls.name.trim())) {
    issues.push({
      severity: "error",
      message: `Linked service name "${ls.name}" must start with a letter and contain only letters, digits, and underscores (2–64 chars).`,
      field,
    });
  }
  if (!ls.integrationRuntime.trim()) {
    issues.push({ severity: "error", message: `Integration runtime is required for "${label}".`, field });
  }
  for (const req of requiredFields(schema, ls.authType)) {
    if (!rawValue(ls, req)) {
      issues.push({
        severity: "error",
        message: `${schema.displayName} linked service "${label}": "${req.label}" is required for ${ls.authType.replace(/_/g, " ")}.`,
        field,
      });
    }
  }
  if (usesSecretFields(schema, ls.authType) && !ls.keyVaultLinkedService.trim()) {
    issues.push({
      severity: "error",
      message: `A Key Vault linked service is required because "${label}" references secret names.`,
      field,
    });
  }
  if (ls.identityKind === "user" && !ls.credentialName?.trim()) {
    issues.push({
      severity: "error",
      message: `"${label}" uses a user-assigned managed identity — pick a registered credential.`,
      field,
    });
  }
  return issues;
}

/** Validate a dataset draft (name + effective parameter values). */
export function validateDatasetDraft(
  ds: DatasetDraft,
  ls: LinkedServiceDraft,
  defaults: DatasetDefaults,
  field: string,
): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const schema = connectorSchema(ls.connectorType);
  const name = datasetName(ds, defaults);
  if (!/^[A-Za-z][A-Za-z0-9_]{1,63}$/.test(name)) {
    issues.push({
      severity: "error",
      message: `Dataset name "${name || "(empty)"}" must start with a letter and contain only letters, digits, and underscores (2–64 chars).`,
      field,
    });
  }
  if (!ds.linkedServiceName.trim()) {
    issues.push({ severity: "error", message: `Dataset "${name}" must reference a linked service.`, field });
  }
  if (schema.kind === "file") {
    if (!(ds.folderPath.trim() || defaults.folderPath)) {
      issues.push({ severity: "error", message: `Dataset "${name}": folder path is required.`, field });
    }
    if (!(ds.fileName.trim() || defaults.fileName)) {
      issues.push({ severity: "error", message: `Dataset "${name}": file name is required.`, field });
    }
  } else if (schema.kind === "service" || schema.kind === "llm") {
    // REST / SaaS / LLM datasets only need an object name (relative URL); it can
    // also be supplied per-row from the control table, so this is a warning.
    if (!(ds.table.trim() || ds.folderPath.trim() || defaults.table)) {
      issues.push({ severity: "warning", message: `Dataset "${name}": no object / relative URL set — it must come from the control table.`, field });
    }
  } else if (!(ds.schema.trim() || defaults.schema) || !(ds.table.trim() || defaults.table)) {
    issues.push({ severity: "error", message: `Dataset "${name}": schema and table are required.`, field });
  }
  return issues;
}
