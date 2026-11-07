/**
 * Connector registry types: the ADF source/target connector catalog,
 * authentication kinds, and the linked-service/connector-info shapes that
 * describe them (AGENTS.md §22.x connector registry, §23.4b).
 */

// Source/Target types - ALL ADF connectors supported (100+)
export type SourceTargetType =
  // Azure Data Stores
  | "azure_blob_storage"
  | "azure_data_lake_storage_gen1"
  | "azure_data_lake_storage_gen2"
  | "azure_sql_database"
  | "azure_sql_managed_instance"
  | "azure_synapse_analytics"
  | "azure_cosmos_db"
  | "azure_cosmos_db_mongodb"
  | "azure_cosmos_db_postgresql"
  | "azure_database_mysql"
  | "azure_database_postgresql"
  | "azure_database_mariadb"
  | "azure_table_storage"
  | "azure_files"
  | "azure_data_explorer"
  | "azure_cognitive_search"
  | "azure_databricks_delta_lake"
  // Database Connectors
  | "sql_server"
  | "oracle"
  | "mysql"
  | "postgresql"
  | "db2"
  | "teradata"
  | "snowflake"
  | "amazon_redshift"
  | "google_big_query"
  | "sap_hana"
  | "sap_bw"
  | "sybase"
  | "informix"
  | "netezza"
  | "vertica"
  | "greenplum"
  | "apache_impala"
  | "apache_hbase"
  | "apache_phoenix"
  | "amazon_rds_sql_server"
  | "amazon_rds_oracle"
  | "microsoft_access"
  | "cassandra"
  | "mongodb"
  | "mongodb_atlas"
  | "couchbase"
  | "presto"
  | "drill"
  | "hive"
  // File Connectors
  | "amazon_s3"
  | "amazon_s3_compatible"
  | "file_system"
  | "ftp"
  | "sftp"
  | "google_cloud_storage"
  | "hdfs"
  | "oracle_cloud_storage"
  // Generic Protocol Connectors
  | "http"
  | "odata"
  | "odbc"
  | "rest"
  // Service & Application Connectors
  | "salesforce"
  | "salesforce_service_cloud"
  | "salesforce_marketing_cloud"
  | "dynamics_365"
  | "dynamics_ax"
  | "dynamics_crm"
  | "dataverse"
  | "sharepoint_online_list"
  | "microsoft_365"
  | "sap_cloud_for_customer"
  | "sap_ecc"
  | "servicenow"
  | "google_adwords"
  | "google_sheets"
  | "hubspot"
  | "jira"
  | "marketo"
  | "paypal"
  | "quickbooks"
  | "shopify"
  | "smartsheet"
  | "square"
  | "twilio"
  | "xero"
  | "zendesk"
  | "zoho"
  | "github"
  | "web_table"
  | "fabric_lakehouse"
  | "fabric_warehouse"
  // Sample-guide parity gap 7 — HR/Financials SaaS and streaming-event sources
  | "workday"
  | "azure_event_hub"
  // LLM / model-serving endpoints (called from ADF via Web / REST activities;
  // modeled as RestService linked services, secrets always Key Vault refs)
  | "azure_openai"
  | "openai"
  | "anthropic"
  | "aws_bedrock"
  | "google_vertex_ai"
  | "cohere"
  | "mistral_ai"
  | "databricks_model_serving"
  | "hugging_face_inference"
  | "azure_ai_foundry";

// Authentication types
export type AuthenticationType =
  | "account_key"
  | "service_principal"
  | "managed_identity"
  | "sas_uri"
  | "sql_authentication"
  | "windows_authentication"
  | "basic"
  | "oauth2"
  | "api_key"
  | "client_certificate"
  | "access_key"
  | "instance_profile"
  | "assumed_role"
  | "ssh_public_key"
  | "personal_access_token"
  | "system_assigned_managed_identity"
  | "user_assigned_managed_identity"
  | "anonymous"
  | "office365"
  | "username_password"
  | "security_token"
  | "amazon_document_db"
  | "connection_string"
  | "bearer_token"
  | "aws_access_key"
  | "service_account_key"
  | "key_pair";

// Integration Runtime types
export type IntegrationRuntimeType = "azure" | "self_hosted" | "azure_ssis";

// Connector information
export interface ConnectorInfo {
  id: string;
  name: string;
  type: SourceTargetType;
  category: "Azure" | "Database" | "File" | "Generic" | "Service";
  authenticationTypes: AuthenticationType[];
  supportedCopyModes: ("Sync" | "Async")[];
  icon?: string;
  description: string;
}

// Linked service definition
export interface LinkedServiceDefinition {
  id: string;
  name: string;
  type: string;
  description: string;
  properties: Record<string, unknown>;
  connectVia?: {
    referenceName: string;
    type: "IntegrationRuntimeReference";
  };
  parameters?: Record<string, { type: string; defaultValue?: unknown }>;
  annotations?: string[];
}
