"""pytest for §32.5/§32.7's credential artifact (AGENTS.md §23.12 gap #21, phase MI2).

`test_connectors.py` covers `build_linked_service_properties`'s `credential` reference block in
isolation; this file proves the artifact set `build_generated_artifacts` actually returns includes
the matching `Microsoft.DataFactory/factories/credentials` resource, correctly ordered ahead of the
linked service that references it, and absent whenever no linked service asks for one (today's
default — no Build wizard UI sets `identity_kind` yet, MI3).
"""
from __future__ import annotations

import json

from app.generator.codegen import build_credential_artifact, build_generated_artifacts
from app.generator.manifest import order_artifacts
from app.generator.spec import SpecDraft

_BASE_DRAFT: dict = {
    "name": "customer_full",
    "projectId": "proj-a",
    "templateId": "full_load",
    "sourceType": "sql_server",
    "sourceObject": "dbo.customer",
    "targetType": "azure_blob_storage",
    "targetPath": "/customer/",
    "loadTypeMode": "full",
    "sourceLinkedService": {
        "name": "LS_sql_server_source",
        "connectorType": "sql_server",
        "authType": "sql_authentication",
        "fields": {
            "server": "sqlsrv01.database.windows.net",
            "database": "customerdb",
            "userName": "svc_reader",
            "password": "sqlserver-password",
        },
    },
    "targetLinkedService": {
        "name": "LS_blob_target",
        "connectorType": "azure_blob_storage",
        "authType": "managed_identity",
        "fields": {"accountName": "stgcustomer"},
    },
    "sourceDataset": {"name": "DS_customer_full_source", "linkedServiceName": "LS_sql_server_source"},
    "targetDataset": {"name": "DS_customer_full_target", "linkedServiceName": "LS_blob_target"},
}


def _draft(**overrides) -> SpecDraft:
    return SpecDraft.from_dict({**_BASE_DRAFT, **overrides})


def _by_name(artifacts, file_name: str):
    return next((a for a in artifacts if a.file_name == file_name), None)


def test_no_credential_artifact_when_every_linked_service_is_system_assigned():
    artifacts = build_generated_artifacts(_draft())
    assert not any(a.artifact_type == "credential" for a in artifacts)


def test_user_assigned_target_emits_one_credential_artifact_ordered_before_its_linked_service():
    draft = _draft(targetLinkedService={
        **_BASE_DRAFT["targetLinkedService"],
        "identityKind": "user",
        "credentialName": "CRED_shared_uami",
        "credentialResourceId": "/subscriptions/x/resourceGroups/y/providers/Microsoft.ManagedIdentity/userAssignedIdentities/uami1",
    })
    artifacts = build_generated_artifacts(draft)
    credential = _by_name(artifacts, "CRED_shared_uami.json")
    assert credential is not None
    assert credential.artifact_type == "credential"
    assert credential.layer == "connection"
    body = json.loads(credential.content)
    assert body["type"] == "Microsoft.DataFactory/factories/credentials"
    assert body["properties"]["type"] == "ManagedIdentity"
    assert body["properties"]["typeProperties"]["resourceId"].endswith("uami1")

    ordered = order_artifacts(artifacts)
    names = [a.file_name for a in ordered]
    assert names.index("CRED_shared_uami.json") < names.index("LS_blob_target.json")

    target_ls = json.loads(_by_name(artifacts, "LS_blob_target.json").content)
    assert target_ls["properties"]["credential"] == {"referenceName": "CRED_shared_uami", "type": "CredentialReference"}


def test_shared_credential_name_across_source_and_target_emits_only_one_artifact():
    draft = _draft(
        sourceLinkedService={
            **_BASE_DRAFT["sourceLinkedService"],
            "identityKind": "user",
            "credentialName": "CRED_shared_uami",
            "credentialResourceId": "/subscriptions/x/.../uami1",
        },
        targetLinkedService={
            **_BASE_DRAFT["targetLinkedService"],
            "identityKind": "user",
            "credentialName": "CRED_shared_uami",
            "credentialResourceId": "/subscriptions/x/.../uami1",
        },
    )
    artifacts = build_generated_artifacts(draft)
    credentials = [a for a in artifacts if a.artifact_type == "credential"]
    assert len(credentials) == 1


def test_build_credential_artifact_is_a_pure_projection_of_its_two_arguments():
    artifact = build_credential_artifact("CRED_x", "/subscriptions/x/.../uami1")
    assert artifact.file_name == "CRED_x.json"
    assert artifact.artifact_type == "credential"
    body = json.loads(artifact.content)
    assert body["name"] == "CRED_x"
    assert body["properties"]["typeProperties"]["resourceId"] == "/subscriptions/x/.../uami1"
