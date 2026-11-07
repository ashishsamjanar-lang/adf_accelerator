"""Worker side of §26.5 Onboarding sync-to-Git, §29.1 layer-6 live ARM check,
and the Purview lineage push. No network: Git runs through the offline
SimulatedAdapter (or a failing stub), Azure through monkeypatched
``get_token``/``http_json``."""
from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.azure import arm_validate, purview
from app.crypto import _fernet
from app.db import models
from app.db.base import Base
from app.gitops import GitOpsError
from app.jobs import JOB_LIVE_CHECK, JOB_ONBOARDING_SYNC, JOB_PURVIEW_PUSH
from app.jobs import runner
from app.jobs.runner import dispatch

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
_SessionLocal = sessionmaker(bind=_engine, autoflush=True, expire_on_commit=False, future=True)


@pytest.fixture()
def db() -> Iterator[Session]:
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    session = _SessionLocal()
    session.add(models.Team(id="team-a", name="A", description=""))
    session.add(models.Project(id="proj-a", team_id="team-a", name="Project A", description=""))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _job(db: Session, job_type: str, payload: dict, pipeline_id: str | None = None) -> models.Job:
    job = models.Job(id=f"job-{job_type}", type=job_type, status="running", payload=payload, pipeline_id=pipeline_id)
    db.add(job)
    db.flush()
    return job


# --- onboarding sync -----------------------------------------------------------


def _sync(db: Session) -> models.OnboardingSync:
    sync = models.OnboardingSync(
        id="osync-1", project_id="proj-a", provider="github", repository_url="https://github.com/contoso/adf",
        feature_branch="feature/sync", base_branch="dev", commit_message="sync", file_paths=[], snapshot={"a": {}},
    )
    db.add(sync)
    db.flush()
    return sync


def _sync_payload() -> dict:
    return {
        "syncId": "osync-1",
        "projectId": "proj-a",
        "files": [{"path": "adf/artifacts/metadata/objects.csv", "content": "object_key\r\n"}],
        "gitToken": None,
    }


def test_onboarding_sync_pushes_and_opens_pr(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.gitops.credential_vault.settings.git_token", "")
    sync = _sync(db)
    result = dispatch(db, _job(db, JOB_ONBOARDING_SYNC, _sync_payload()))
    assert result["state"] == "succeeded"
    assert sync.status == "pr_created" and sync.simulated is True
    assert sync.commit_sha and sync.pr_number is not None
    actions = [a.action for a in db.scalars(select(models.AuditEntry))]
    assert actions == ["onboarding.synced"]


def test_onboarding_sync_scrubs_token_and_records_git_failure(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    class Failing:
        def push_artifacts(self, **_: object):
            raise GitOpsError("branch protection says no")

    seen: dict = {}

    def fake_get_adapter(provider, url, *, credential_vault=None, direct_token=None):
        seen["token"] = direct_token
        return Failing()

    monkeypatch.setattr(runner, "get_adapter", fake_get_adapter)
    sync = _sync(db)
    job = _job(db, JOB_ONBOARDING_SYNC, {**_sync_payload(), "gitToken": _fernet().encrypt(b"ghp_secret").decode("utf-8")})
    result = dispatch(db, job)
    assert seen["token"] == "ghp_secret"
    assert job.payload["gitToken"] is None
    assert result == {"state": "failed", "error": "branch protection says no"}
    assert sync.status == "failed" and sync.error == "branch protection says no"


# --- layer 6: ARM template + live check ------------------------------------------


def _adf(name: str, properties: dict) -> str:
    return json.dumps({"name": name, "properties": properties})


ARTIFACTS = [
    ("linked_service", _adf("LS_src", {"type": "SqlServer", "typeProperties": {}})),
    ("dataset", _adf("DS_src", {"linkedServiceName": {"referenceName": "LS_src", "type": "LinkedServiceReference"}})),
    (
        "pipeline",
        _adf(
            "PL_x",
            {"activities": [{"name": "Copy", "inputs": [{"referenceName": "DS_src", "type": "DatasetReference"}],
                             "outputs": [{"referenceName": "DS_missing", "type": "DatasetReference"}]}]},
        ),
    ),
    ("factory", _adf("<FACTORY_NAME>", {"globalParameters": {}})),
    ("parameter", "{}"),
    ("control_table", "CREATE TABLE x"),
]


def test_arm_template_wraps_factory_children_with_depends_on() -> None:
    template = arm_validate.build_arm_template(ARTIFACTS, "adf-sbx", "westeurope")
    types = [r["type"] for r in template["resources"]]
    assert types == [
        "Microsoft.DataFactory/factories",
        "Microsoft.DataFactory/factories/linkedservices",
        "Microsoft.DataFactory/factories/datasets",
        "Microsoft.DataFactory/factories/pipelines",
    ]
    dataset, pipeline = template["resources"][2], template["resources"][3]
    assert dataset["name"] == "adf-sbx/DS_src"
    assert any("linkedservices', 'adf-sbx', 'LS_src'" in d for d in dataset["dependsOn"])
    # Only references resolvable inside the template become dependsOn (DS_missing does not).
    assert [d for d in pipeline["dependsOn"] if "datasets" in d] == [
        "[resourceId('Microsoft.DataFactory/factories/datasets', 'adf-sbx', 'DS_src')]"
    ]


LIVE_CONFIG = {"subscriptionId": "sub-1", "resourceGroup": "rg-sbx", "location": "westeurope", "factoryName": "adf-sbx"}


def _live_setup(db: Session) -> None:
    db.add(models.ProjectIntegration(project_id="proj-a", kind="live_validation", enabled=True, config=LIVE_CONFIG))
    db.add(models.PipelineDefinition(
        id="pl-a", name="pipeline-a", template_id="t", project_id="proj-a", team_id="team-a", state="VALIDATED",
        draft={}, spec={}, latest_artifact_version="v1",
    ))
    for i, (artifact_type, content) in enumerate(ARTIFACTS):
        db.add(models.Artifact(
            id=f"art-{i}", pipeline_id="pl-a", pipeline_name="pipeline-a", file_name=f"f{i}.json",
            artifact_type=artifact_type, layer="pipeline", version="v1", content=content,
        ))
    db.flush()


def test_live_check_passes_and_never_touches_pipeline_state(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _live_setup(db)
    calls: list = []
    monkeypatch.setattr(arm_validate, "get_token", lambda config, scope: "tok")

    def fake_http(method, url, token, body=None):
        calls.append((method, url, body))
        return 200, {"properties": {"provisioningState": "Succeeded"}}

    monkeypatch.setattr(arm_validate, "http_json", fake_http)
    result = dispatch(db, _job(db, JOB_LIVE_CHECK, {"projectId": "proj-a"}, pipeline_id="pl-a"))
    assert result["state"] == "succeeded" and result["passed"] is True
    assert result["resourceCount"] == 3
    method, url, body = calls[0]
    assert method == "POST"
    assert "/resourcegroups/rg-sbx/providers/Microsoft.Resources/deployments/" in url and url.split("?")[0].endswith("/validate")
    assert body["properties"]["mode"] == "Incremental"
    assert db.get(models.PipelineDefinition, "pl-a").state == "VALIDATED"


def test_live_check_reports_arm_findings_as_warnings(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _live_setup(db)
    monkeypatch.setattr(arm_validate, "get_token", lambda config, scope: "tok")
    error = {"error": {"code": "InvalidTemplate", "message": "outer", "details": [
        {"code": "InvalidPipelineReference", "message": "DS_missing not found", "target": "PL_x"},
    ]}}
    monkeypatch.setattr(arm_validate, "http_json", lambda *a, **k: (400, error))
    result = dispatch(db, _job(db, JOB_LIVE_CHECK, {"projectId": "proj-a"}, pipeline_id="pl-a"))
    assert result["passed"] is False
    assert result["issues"] == [{
        "severity": "warning", "layer": "live", "ruleId": "live.arm.InvalidPipelineReference",
        "message": "DS_missing not found", "target": "PL_x",
    }]


def test_live_check_disabled_or_denied_is_a_domain_failure(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _live_setup(db)
    db.get(models.ProjectIntegration, ("proj-a", "live_validation")).enabled = False
    job = _job(db, JOB_LIVE_CHECK, {"projectId": "proj-a"}, pipeline_id="pl-a")
    assert dispatch(db, job)["state"] == "failed"

    db.get(models.ProjectIntegration, ("proj-a", "live_validation")).enabled = True
    monkeypatch.setattr(arm_validate, "get_token", lambda config, scope: "tok")
    monkeypatch.setattr(arm_validate, "http_json", lambda *a, **k: (403, {"error": {"code": "AuthorizationFailed", "message": "no RBAC"}}))
    job.id = "job-2"
    result = dispatch(db, job)
    assert result == {"state": "failed", "error": "no RBAC"}


# --- Purview ------------------------------------------------------------------------

LINEAGE = {
    "jobs": [
        {"job": {"name": "bronze.erp_customer"}, "medallionLayer": "bronze",
         "inputs": [{"namespace": "source://erp", "name": "dbo.customer"}],
         "outputs": [{"namespace": "bronze://registry", "name": "raw.erp_customer"}]},
        {"job": {"name": "silver.customer"}, "medallionLayer": "silver",
         "inputs": [{"namespace": "bronze://registry", "name": "raw.erp_customer"}],
         "outputs": [{"namespace": "silver://registry", "name": "curated.customer"}]},
    ]
}


def test_purview_entities_dedupe_datasets_and_reference_by_qualified_name() -> None:
    datasets, processes = purview.build_entities(LINEAGE, "proj-a")
    assert sorted(d["attributes"]["qualifiedName"] for d in datasets) == [
        "bronze://registry/raw.erp_customer", "silver://registry/curated.customer", "source://erp/dbo.customer",
    ]
    assert processes[1]["attributes"]["qualifiedName"] == "adf-accelerator://proj-a/silver.customer"
    assert processes[1]["attributes"]["inputs"] == [
        {"typeName": purview.DATASET_TYPE, "uniqueAttributes": {"qualifiedName": "bronze://registry/raw.erp_customer"}}
    ]
    with pytest.raises(purview.AzureError):
        purview.build_entities({"jobs": []}, "proj-a")


def test_purview_push_job(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    db.add(models.ProjectIntegration(project_id="proj-a", kind="purview", enabled=True, config={"accountName": "contoso"}))
    calls: list = []
    monkeypatch.setattr(purview, "get_token", lambda config, scope: "tok")

    def fake_http(method, url, token, body=None):
        calls.append(url)
        if url.endswith("/types/typedefs"):
            return 409, {"errorMessage": "type exists"}  # already created on an earlier push
        return 200, {"mutatedEntities": {"CREATE": [{} for _ in body["entities"]]}}

    monkeypatch.setattr(purview, "http_json", fake_http)
    result = dispatch(db, _job(db, JOB_PURVIEW_PUSH, {"projectId": "proj-a", "lineage": LINEAGE}))
    assert result["state"] == "succeeded"
    assert (result["datasets"], result["processes"], result["created"]) == (3, 2, 5)
    assert calls[0] == "https://contoso.purview.azure.com/datamap/api/atlas/v2/types/typedefs"
    assert calls[1:] == ["https://contoso.purview.azure.com/datamap/api/atlas/v2/entity/bulk"] * 2
    assert [a.action for a in db.scalars(select(models.AuditEntry))] == ["lineage.purview_pushed"]
