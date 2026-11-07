"""ADF linked-service JSON projection.

Port of ``buildLinkedServiceProperties`` and its per-family ``typeProperties``
builders from ``frontend/lib/adf/connections.ts``. Secrets are always rendered
as ``AzureKeyVaultSecret`` dynamic content — the secret value never appears in
the generated JSON (AGENTS.md §1.6, §23.5).
"""
from __future__ import annotations

from typing import Any

from .drafts import DEFAULT_INTEGRATION_RUNTIME, DEFAULT_KEY_VAULT_LINKED_SERVICE, LinkedServiceDraft
from .schemas import ConnectionField, connector_schema

Params = dict[str, dict[str, Any]]


def key_vault_secret(store_name: str, secret_name: str) -> dict[str, Any]:
    """AzureKeyVaultSecret dynamic-content block (secrets never leave Key Vault)."""
    return {
        "type": "AzureKeyVaultSecret",
        "store": {"referenceName": store_name, "type": "LinkedServiceReference"},
        "secretName": secret_name,
    }


def _raw(ls: LinkedServiceDraft, fld: ConnectionField | None) -> str:
    return (ls.fields.get(fld.key, "") if fld else "").strip()


def _bool(ls: LinkedServiceDraft, fld: ConnectionField | None) -> str:
    return "true" if _raw(ls, fld) in ("true", "True", "1") else "false"


def _resolve(ls: LinkedServiceDraft, fld: ConnectionField, parameters: Params) -> dict[str, Any] | str:
    """Resolve one field into its typeProperties value.

    * secret fields -> AzureKeyVaultSecret (name parameterized when selected);
    * parameterized fields -> ``@{linkedService().key}`` with a parameter default;
    * anything else -> the literal value.
    """
    value = _raw(ls, fld)
    store = ls.key_vault_linked_service or DEFAULT_KEY_VAULT_LINKED_SERVICE
    if fld.is_secret:
        param_name = f"{fld.key}_secret_name"
        if fld.key in ls.parameterize:
            parameters[param_name] = {"type": "String", "defaultValue": value}
            return key_vault_secret(store, f"@{{linkedService().{param_name}}}")
        return key_vault_secret(store, value)
    if fld.key in ls.parameterize:
        parameters[fld.key] = {"type": "String", "defaultValue": value}
        return f"@{{linkedService().{fld.key}}}"
    return value


def _pick(ls: LinkedServiceDraft, key: str) -> ConnectionField | None:
    return next((f for f in connector_schema(ls.connector_type).fields if f.key == key), None)


# --- SQL family -------------------------------------------------------------

def sql_type_properties(ls: LinkedServiceDraft, parameters: Params) -> dict[str, Any]:
    props: dict[str, Any] = {
        "server": _resolve(ls, _pick(ls, "server"), parameters),
        "database": _resolve(ls, _pick(ls, "database"), parameters),
    }
    encrypt = _raw(ls, _pick(ls, "encrypt"))
    if encrypt:
        props["encrypt"] = encrypt
    if _raw(ls, _pick(ls, "trustServerCertificate")):
        props["trustServerCertificate"] = _bool(ls, _pick(ls, "trustServerCertificate"))
    if ls.auth_type in ("sql_authentication", "windows_authentication"):
        props["authenticationType"] = "SQL" if ls.auth_type == "sql_authentication" else "Windows"
        if _raw(ls, _pick(ls, "userName")):
            props["userName"] = _resolve(ls, _pick(ls, "userName"), parameters)
        if _raw(ls, _pick(ls, "password")):
            props["password"] = _resolve(ls, _pick(ls, "password"), parameters)
    elif ls.auth_type == "service_principal":
        props["authenticationType"] = "ServicePrincipal"
        for key in ("servicePrincipalId", "servicePrincipalKey", "tenant", "azureCloudType"):
            if _raw(ls, _pick(ls, key)):
                props[key] = _resolve(ls, _pick(ls, key), parameters)
    else:
        props["authenticationType"] = "ManagedIdentity"
    return props


# --- Storage (Blob / ADLS Gen2) -------------------------------------------

def storage_type_properties(ls: LinkedServiceDraft, parameters: Params) -> dict[str, Any]:
    props: dict[str, Any] = {}
    if _raw(ls, _pick(ls, "url")):
        props["url"] = _resolve(ls, _pick(ls, "url"), parameters)
    if _raw(ls, _pick(ls, "accountName")):
        props["accountName"] = _raw(ls, _pick(ls, "accountName"))
    if _raw(ls, _pick(ls, "sasUri")):
        props["sasUri"] = _resolve(ls, _pick(ls, "sasUri"), parameters)
    if ls.auth_type == "account_key":
        if _raw(ls, _pick(ls, "accountKey")):
            props["accountKey"] = _resolve(ls, _pick(ls, "accountKey"), parameters)
    elif ls.auth_type == "sas_uri":
        if _raw(ls, _pick(ls, "sasToken")):
            props["sasToken"] = _resolve(ls, _pick(ls, "sasToken"), parameters)
    elif ls.auth_type == "service_principal":
        props["authenticationType"] = "ServicePrincipal"
        for key in ("servicePrincipalId", "servicePrincipalKey", "tenant", "azureCloudType"):
            if _raw(ls, _pick(ls, key)):
                props[key] = _resolve(ls, _pick(ls, key), parameters)
    elif ls.auth_type == "managed_identity":
        props["authenticationType"] = "ManagedIdentity"
    elif ls.auth_type == "connection_string":
        cs = _pick(ls, "connectionString")
        if _raw(ls, cs):
            props["connectionString"] = _resolve(ls, cs, parameters)
    return props


# --- Basic user/password (Oracle, PostgreSQL, MySQL, SFTP, ...) -----------

def basic_auth_type_properties(ls: LinkedServiceDraft, parameters: Params) -> dict[str, Any]:
    schema = connector_schema(ls.connector_type)
    props: dict[str, Any] = {}
    for fld in schema.fields:
        value = _raw(ls, fld)
        if not value:
            continue
        if fld.type == "boolean":
            props[fld.key] = _bool(ls, fld)
        elif fld.type == "select":
            props[fld.key] = value
        else:
            props[fld.key] = _resolve(ls, fld, parameters)
    if schema.id in ("azure_database_postgresql", "azure_database_mysql"):
        props["authenticationType"] = "Basic"
    elif schema.id == "sftp":
        props["authenticationType"] = "Basic" if ls.auth_type == "basic" else "SshPublicKey"
    elif schema.id == "ftp":
        props["authenticationType"] = "Anonymous" if ls.auth_type == "anonymous" else "Basic"
    elif schema.id == "http":
        # §23.12 gap #24 — client_certificate is a real HttpServerLinkedService auth type, not just
        # Basic/Anonymous.
        props["authenticationType"] = (
            "Basic" if ls.auth_type == "basic" else "ClientCertificate" if ls.auth_type == "client_certificate" else "Anonymous"
        )
    elif schema.id == "amazon_s3":
        props["authenticationType"] = "IAMRole" if ls.auth_type == "assumed_role" else "AccessKey"
    elif schema.id == "azure_data_lake_storage_gen1":
        # §23.12 gap #23 — Gen1 has no account-key auth; it's service principal or the factory's
        # managed identity.
        props["authenticationType"] = "ServicePrincipal" if ls.auth_type == "service_principal" else "ManagedServiceIdentity"
    elif schema.id == "snowflake":
        # §23.12 gap #25 — SnowflakeV2's authenticationType enum (Basic/KeyPair/AADServicePrincipal).
        props["authenticationType"] = {
            "username_password": "Basic",
            "key_pair": "KeyPair",
            "service_principal": "AADServicePrincipal",
        }.get(ls.auth_type, "Basic")
    return props


# --- REST-backed (generic REST / OData / SaaS / LLM) ----------------------

def rest_auth_type(auth_type: str) -> str:
    """RestService ``authenticationType`` for the selected mechanism."""
    return {
        "basic": "Basic",
        "oauth2": "OAuth2ClientCredential",
        "service_principal": "AadServicePrincipal",
        "managed_identity": "ManagedServiceIdentity",
        "system_assigned_managed_identity": "ManagedServiceIdentity",
        "user_assigned_managed_identity": "ManagedServiceIdentity",
    }.get(auth_type, "Anonymous")


_REST_AUTH_KEYS = frozenset({
    "url", "userName", "password", "tokenEndpoint", "clientId", "clientSecret",
    "scope", "resource", "servicePrincipalId", "servicePrincipalKey", "tenant",
    "aadResourceId", "authHeaderName",
})


def rest_type_properties(ls: LinkedServiceDraft, parameters: Params) -> dict[str, Any]:
    schema = connector_schema(ls.connector_type)
    props: dict[str, Any] = {}
    by_key = {f.key: f for f in schema.fields}

    def emit(key: str) -> None:
        fld = by_key.get(key)
        if not fld or not _raw(ls, fld):
            return
        props[key] = _bool(ls, fld) if fld.type == "boolean" else _resolve(ls, fld, parameters)

    emit("url")
    props["authenticationType"] = rest_auth_type(ls.auth_type)

    if ls.auth_type == "basic":
        emit("userName")
        emit("password")
    elif ls.auth_type == "oauth2":
        for key in ("tokenEndpoint", "clientId", "clientSecret", "scope", "resource"):
            emit(key)
    elif ls.auth_type == "service_principal":
        for key in ("servicePrincipalId", "servicePrincipalKey", "tenant", "aadResourceId"):
            emit(key)

    # API key / bearer token -> custom auth header, value stays in Key Vault.
    if ls.auth_type in ("api_key", "bearer_token"):
        secret_field = next(
            (f for f in schema.fields if f.is_secret and f.key in ("apiKey", "accessToken", "key")),
            None,
        )
        if secret_field and _raw(ls, secret_field):
            header_name = _raw(ls, by_key.get("authHeaderName")) or (
                "Authorization" if schema.kind == "llm" else "api-key"
            )
            props["authHeaders"] = {
                header_name: key_vault_secret(
                    ls.key_vault_linked_service or DEFAULT_KEY_VAULT_LINKED_SERVICE,
                    _raw(ls, secret_field),
                )
            }

    # Connector-specific descriptors (model, deployment, apiVersion, region,
    # projectId, endpointName, ...) plus any remaining fields pass through.
    for fld in schema.fields:
        if fld.key in _REST_AUTH_KEYS or fld.key in props:
            continue
        value = _raw(ls, fld)
        if not value:
            continue
        props[fld.key] = _bool(ls, fld) if fld.type == "boolean" else _resolve(ls, fld, parameters)
    return props


_SQL_IDS = frozenset({"azure_sql_database", "azure_sql_managed_instance", "azure_synapse_analytics", "sql_server"})
_STORAGE_IDS = frozenset({"azure_blob_storage", "azure_data_lake_storage_gen2"})


def build_linked_service_properties(ls: LinkedServiceDraft) -> dict[str, Any]:
    """Build the full ADF linked service ``properties`` object from a draft."""
    schema = connector_schema(ls.connector_type)
    parameters: Params = {}
    if schema.id in _SQL_IDS:
        type_properties = sql_type_properties(ls, parameters)
    elif schema.id in _STORAGE_IDS:
        type_properties = storage_type_properties(ls, parameters)
    elif schema.kind in ("llm", "service"):
        type_properties = rest_type_properties(ls, parameters)
    else:
        type_properties = basic_auth_type_properties(ls, parameters)

    known = {f.key for f in schema.fields}
    for key, value in ls.fields.items():
        if key not in known and key.startswith("custom.") and value.strip():
            type_properties[key.replace("custom.", "")] = value.strip()

    properties: dict[str, Any] = {
        "type": schema.adf_type,
        "parameters": parameters,
        "typeProperties": type_properties,
        "connectVia": {
            "referenceName": ls.integration_runtime or DEFAULT_INTEGRATION_RUNTIME,
            "type": "IntegrationRuntimeReference",
        },
        "annotations": [],
    }
    # §32.5/§32.7 — user-assigned managed identity: "system" (default) changes nothing; "user" adds a
    # root-level `credential` reference alongside `type`/`typeProperties`, the placement real ADF uses
    # for most Azure-native connectors (§32.10 open assumption #1 — verify per family before shipping
    # a connector not yet checked against the live ADF schema).
    if ls.identity_kind == "user" and ls.credential_name.strip():
        properties["credential"] = {"referenceName": ls.credential_name.strip(), "type": "CredentialReference"}
    return properties
