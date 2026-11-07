"""Smoke tests: run the real generator/validator against dummy, realistic
project data drawn from docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md, and assert on
the actual generated ADF artifact JSON — not on hand-typed literals (contrast
with backend/tests/test_scenario_*.py, which are placeholder stubs pending
this kind of real wiring, tracked separately per file docstring there).

Two things every case here checks, because they're what "the same build
promoted unchanged to dev/qa/prod" (DEVELOPMENT_NOTES.md, AGENTS.md §23.7) actually
depends on:
  1. no environment name, hostname, or literal secret is ever baked into the
     artifact — every environment-specific value is either a `pipeline()`/
     `dataset()` parameter resolved from the control table at runtime, or an
     `AzureKeyVaultSecret` reference resolved by whichever Key Vault the
     target environment's linked service points at;
  2. a value that *would* produce ADF-illegal output is caught by the
     always-on validator (`validate_artifacts`) before it could ever reach a
     PR — not silently truncated or shipped broken.
"""
from __future__ import annotations

import json

from app.generator.codegen import build_generated_artifacts
from app.generator.spec import SpecDraft
from app.validator.rules import validate_artifacts

_BASE_COMPOSITE_WATERMARK = {
    # docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md SQL-08 / §14 WM-09 — guide 1's
    # dbo.Endorsement, composite watermark on (updated_at, endorsement_id),
    # the exact shape the 2026-09-12 lexicographic-tuple-predicate fix targeted.
    "name": "endorsement_composite_delta",
    "projectId": "proj-smoke-test",
    "templateId": "composite_watermark_load",
    "sourceType": "sql_server",
    "sourceObject": "dbo.endorsement",
    "targetType": "azure_data_lake_storage_gen2",
    "targetPath": "/endorsement/",
    "sourceObjectMode": "form",
    "sourceObjects": [{"object": "dbo.endorsement", "watermarkColumn": "updated_at"}],
    "watermarkEnabled": True,
    "watermarkType": "composite",
    "watermarkColumns": [
        {"name": "updated_at", "type": "datetime", "order": 1},
        {"name": "endorsement_id", "type": "bigint", "order": 2},
    ],
    "watermarkTable": "control.watermarks",
    "watermarkInitialValue": "1900-01-01",
    "loadTypeMode": "composite_watermark",
    "batchCount": 4,
    "parallelCopies": 8,
    "sinkFileFormat": "Parquet",
    "writeBehavior": "Upsert",
    "upsertKeyColumns": "endorsement_id",
    "sourceLinkedService": {
        "name": "LS_pas_sql_server",
        "connectorType": "sql_server",
        "authType": "sql_authentication",
        "fields": {
            "server": "pas-sql01.internal.contoso.com",
            "database": "PAS",
            "userName": "svc_adf_reader",
            "password": "pas-sql-svc-password",
        },
    },
}


def _child_pipeline(artifacts) -> dict:
    (artifact,) = [a for a in artifacts if a.file_name.endswith("_child.json")]
    return json.loads(artifact.content)


def _copy_delta_query(doc: dict) -> str:
    """The `CopyDeltaData` activity's source query expression (the incremental
    copy's WHERE clause), out of the default (DeltaLoad) branch of the child
    pipeline's `RouteByLoadingBehavior` switch."""
    switch = next(a for a in doc["properties"]["activities"] if a["type"] == "Switch")
    copy = next(a for a in switch["typeProperties"]["defaultActivities"] if a["type"] == "Copy")
    return copy["typeProperties"]["source"]["sqlReaderQuery"]["value"]


def test_sql08_composite_watermark_emits_the_documented_lexicographic_tuple_predicate():
    """SQL-08 [REGRESSION]: two rows sharing `updated_at` but differing on
    `endorsement_id` must both be captured — the delta copy's WHERE clause has
    to compare the *tuple*, not just re-run `col > last` per column."""
    draft = SpecDraft.from_dict(_BASE_COMPOSITE_WATERMARK)
    artifacts = build_generated_artifacts(draft)
    query = _copy_delta_query(_child_pipeline(artifacts))

    assert "updated_at > " in query
    assert "updated_at = " in query and "endorsement_id > " in query
    assert " OR (" in query  # the tuple-comparison shape, not two independent ANDs


def test_composite_watermark_artifacts_reference_the_password_via_key_vault_not_inline():
    """§1.6/§23.5: onboarding supplies only a Key Vault *secret name*
    ("pas-sql-svc-password", never the real password value, which this app
    never sees) — the generator must emit that as an `AzureKeyVaultSecret`
    reference, never as an inline credential a naive string field could leak."""
    draft = SpecDraft.from_dict(_BASE_COMPOSITE_WATERMARK)
    artifacts = build_generated_artifacts(draft)
    (ls_source,) = [a for a in artifacts if a.file_name == "LS_pas_sql_server.json"]
    password_prop = json.loads(ls_source.content)["properties"]["typeProperties"]["password"]
    assert password_prop == {
        "type": "AzureKeyVaultSecret",
        "store": {"referenceName": "LS_azure_key_vault", "type": "LinkedServiceReference"},
        "secretName": "pas-sql-svc-password",
    }
    for artifact in artifacts:
        # None of the *other* artifacts (datasets, pipelines, trigger, ...)
        # should reference the credential at all — only the one linked service does.
        if artifact.file_name != "LS_pas_sql_server.json":
            assert "pas-sql-svc-password" not in artifact.content


def test_composite_watermark_artifacts_carry_no_environment_name():
    """AGENTS.md §23.7 'one build, promoted everywhere': nothing in the
    generated set should hardcode "dev"/"qa"/"prod" — every value that must
    differ per environment goes through a `pipeline()`/`dataset()` parameter
    or a Key Vault secret name, resolved by whichever environment runs it."""
    draft = SpecDraft.from_dict(_BASE_COMPOSITE_WATERMARK)
    artifacts = build_generated_artifacts(draft)
    for artifact in artifacts:
        lowered = artifact.content.lower()
        for env_token in ("\"dev\"", "\"qa\"", "\"prod\"", "-dev.", "-qa.", "-prod."):
            assert env_token not in lowered, f"{artifact.file_name} embeds an environment-specific token {env_token!r}"


def test_sap02_slash_named_bw_object_is_blocked_by_the_always_on_validator_not_silently_shipped():
    """SAP-02 [VERIFY]: `/BIC/AZCUSTOMER`-shaped SAP BW names are legal as the
    *source object* the connector actually calls, but ADF forbids `/` in an
    artifact name. When a naming pattern derives the pipeline base name
    straight from such an object (a realistic Onboarding-CSV mistake), the
    always-on hard rule must `block` before push — never truncate or emit
    invalid ADF JSON silently."""
    draft = SpecDraft.from_dict(
        {
            "name": "/BIC/AZCUSTOMER",
            "projectId": "proj-smoke-test",
            "templateId": "full_load",
            "sourceType": "sap_bw",
            "sourceObject": "/BIC/AZCUSTOMER",
            "targetType": "azure_data_lake_storage_gen2",
            "targetPath": "/sap/customer/",
            "sourceObjectMode": "form",
            "sourceObjects": [{"object": "/BIC/AZCUSTOMER"}],
            "watermarkEnabled": False,
            "loadTypeMode": "full",
            "fullLoad": True,
            "batchCount": 1,
            "sinkFileFormat": "Parquet",
            "writeBehavior": "Insert",
        }
    )
    artifacts = build_generated_artifacts(draft)
    issues = validate_artifacts(artifacts)

    blocking = [i for i in issues if i.severity == "error" and "PL_/BIC/AZCUSTOMER_master" in (i.source or "")]
    assert blocking, "expected the ADF hard-rule validator to block the slash-bearing pipeline name"


def test_sftp_style_file_source_default_filename_does_not_double_its_extension():
    """Regression for a real bug this smoke pass found: `source_dataset_defaults`
    treated every `sourceObject` as a bare DB table name and appended ".csv"
    unconditionally, so a file-kind source (SFTP/FTP/Blob/S3/SharePoint...)
    whose `sourceObject` is already a file name/pattern — exactly how guide 2's
    vendor-invoice scenario names it, `Acme_Invoice_09-12-2026.csv` — got a
    generated `FileName` default of `Acme_Invoice_09-12-2026.csv.csv`, which
    would never match a real file on the vendor's SFTP server. Fixed in both
    `worker/app/generator/spec.py::source_dataset_defaults` and
    `frontend/lib/adf/spec.ts::sourceDatasetDefaults` (parity)."""
    draft = SpecDraft.from_dict(
        {
            "name": "acme_vendor_invoice_daily",
            "projectId": "proj-smoke-test",
            "templateId": "full_load",
            "sourceType": "sftp",
            "sourceObject": "/outbound/Acme_Invoice_09-12-2026.csv",
            "targetType": "azure_data_lake_storage_gen2",
            "targetPath": "/vendor/acme/invoice/",
            "sourceObjectMode": "form",
            "sourceObjects": [{"object": "/outbound/Acme_Invoice_09-12-2026.csv"}],
            "watermarkEnabled": False,
            "loadTypeMode": "full",
            "fullLoad": True,
            "batchCount": 1,
            "sinkFileFormat": "Parquet",
            "writeBehavior": "Insert",
        }
    )
    artifacts = build_generated_artifacts(draft)
    (source_ds,) = [a for a in artifacts if a.file_name.startswith("DS_") and a.file_name.endswith("_source.json")]
    doc = json.loads(source_ds.content)
    file_name_default = doc["properties"]["parameters"]["FileName"]["defaultValue"]

    assert file_name_default == "Acme_Invoice_09-12-2026.csv"
    assert not file_name_default.endswith(".csv.csv")


def test_sql05_large_full_load_table_warns_but_never_blocks():
    """SQL-05: a 200M-row table on an unchunked Full Load warns (the guides' ~1M-row rule of thumb);
    no estimate, a small table, a delta load or chunking stays silent."""
    from app.generator.spec import validate_draft

    def sql05(**overrides):
        draft = SpecDraft.from_dict({**_BASE_COMPOSITE_WATERMARK, "loadTypeMode": "full", "fullLoad": True, **overrides})
        return [i for i in validate_draft(draft) if i.field == "estimatedSourceRows"]

    [issue] = sql05(estimatedSourceRows=200_000_000)
    assert issue.severity == "warning" and "200,000,000" in issue.message
    assert sql05() == [] and sql05(estimatedSourceRows=500_000) == []
    assert sql05(estimatedSourceRows=200_000_000, chunkingEnabled=True, chunkColumn="claim_id") == []
    assert sql05(estimatedSourceRows=200_000_000, loadTypeMode="incremental", fullLoad=False) == []
