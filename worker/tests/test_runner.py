"""pytest for app/jobs/runner.py's per-job-type handlers (AGENTS.md §23.4,
§23.12 gap #20).

``test_jobs_loop.py`` deliberately monkeypatches ``dispatch`` away to test the
claim/retry loop in isolation and says so in its own docstring: "the handlers
... have their own coverage need (tracked, not yet written)". This file is
that coverage — ``_handle_generate``/``_handle_validate``/``_handle_git_push``
and ``dispatch`` itself, exercised against a real in-memory metastore so the
DB side effects (artifact rows, pipeline state, audit log, PR rows) are
actually checked, not just the handler's return value.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base, new_id
from app.jobs import JOB_GENERATE, JOB_GIT_PUSH, JOB_VALIDATE
from app.jobs.runner import JobError, dispatch

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)

_MINIMAL_DRAFT = {
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
    # Required by `validate_draft()` (`app/connectors/schemas.py`'s per-connector field
    # contract) — without these, every `_handle_generate` call below fails domain-style
    # before ever reaching codegen, which silently no-ops the "succeeds" tests instead of
    # exercising them (see `test_e2e_lifecycle.py` for how this was caught).
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


@pytest.fixture(autouse=True)
def _fresh_schema() -> Iterator[None]:
    Base.metadata.create_all(_engine)
    yield
    Base.metadata.drop_all(_engine)


@pytest.fixture()
def db() -> Iterator[Session]:
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def pipeline(db: Session) -> models.PipelineDefinition:
    team = models.Team(id="team-a", name="Team A")
    project = models.Project(id="proj-a", team_id="team-a", name="Project A")
    template = models.PipelineTemplate(id="incremental_load", name="Incremental Load", version="2.0", document={})
    pl = models.PipelineDefinition(
        id="pl-a",
        name="customer_incremental",
        template_id="incremental_load",
        project_id="proj-a",
        team_id="team-a",
        state="DRAFT",
        draft=_MINIMAL_DRAFT,
        spec={},
        created_by="tester",
    )
    db.add_all([team, project, template, pl])
    db.flush()
    return pl


def _job(job_type: str, *, pipeline_id: str | None = None, payload: dict | None = None) -> models.Job:
    return models.Job(
        id=new_id("job"),
        type=job_type,
        status="running",
        pipeline_id=pipeline_id,
        payload=payload or {},
        created_by="tester",
    )


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def test_dispatch_raises_job_error_for_an_unknown_job_type(db: Session):
    job = _job("some_unknown_type")
    with pytest.raises(JobError):
        dispatch(db, job)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def test_handle_generate_succeeds_and_persists_artifacts_and_manifest(db: Session, pipeline: models.PipelineDefinition):
    job = _job(JOB_GENERATE, pipeline_id=pipeline.id, payload={"draft": _MINIMAL_DRAFT, "version": "v1.0.0"})
    result = dispatch(db, job)
    db.flush()

    assert result["state"] == "succeeded"
    assert result["version"] == "v1.0.0"
    assert result["artifactCount"] > 0
    assert pipeline.state == "GENERATED"
    assert pipeline.latest_artifact_version == "v1.0.0"
    assert pipeline.spec  # build_spec output was stored

    artifacts = db.scalars(
        select(models.Artifact).where(models.Artifact.pipeline_id == pipeline.id, models.Artifact.version == "v1.0.0")
    ).all()
    file_names = {a.file_name for a in artifacts}
    assert "manifest.json" in file_names
    manifest_row = next(a for a in artifacts if a.file_name == "manifest.json")
    assert json.loads(manifest_row.content)["version"] == "v1.0.0"

    versions = db.scalars(select(models.PipelineVersion).where(models.PipelineVersion.pipeline_id == pipeline.id)).all()
    assert len(versions) == 1
    assert versions[0].version == "v1.0.0"

    audit = db.scalars(select(models.AuditEntry).where(models.AuditEntry.action == "artifact.generated")).all()
    assert len(audit) == 1


def _global_parameters_artifact(db: Session, pipeline_id: str, version: str) -> dict:
    row = db.scalars(
        select(models.Artifact).where(
            models.Artifact.pipeline_id == pipeline_id,
            models.Artifact.version == version,
            models.Artifact.file_name == "globalParameters.json",
        )
    ).one()
    return json.loads(row.content)


def test_handle_generate_without_environment_keeps_placeholder_tokens(db: Session, pipeline: models.PipelineDefinition):
    """No `targetEnvironmentName` on the draft — today's unchanged, backward-compatible behavior."""
    job = _job(JOB_GENERATE, pipeline_id=pipeline.id, payload={"draft": _MINIMAL_DRAFT, "version": "v1.0.0"})
    dispatch(db, job)
    db.flush()

    global_parameters = _global_parameters_artifact(db, pipeline.id, "v1.0.0")
    assert global_parameters["name"] == "<FACTORY_NAME>"
    assert global_parameters["properties"]["globalParameters"]["Environment"]["value"] == "<ENVIRONMENT_NAME>"


def test_handle_generate_resolves_selected_adf_instance_into_global_parameters(
    db: Session, pipeline: models.PipelineDefinition
):
    """Multi-instance support (IMPLEMENTATION_PLAN.md A-1/A-2): picking an ADF instance fills in
    `globalParameters.json`'s real factory/environment name — and only that artifact, per the
    "parameter surfaces only" decision (every other artifact is untouched, covered by the parity
    test's byte-identical assertion since it never varies `targetEnvironmentName`)."""
    db.add(
        models.Environment(
            id="env-prod-eu",
            name="PROD_EU",
            label="Prod (EU)",
            status="connected",
            factory_name="contoso-adf-prod-eu",
            subscription_id="11111111-1111-1111-1111-111111111111",
            resource_group="rg-adf-prod-eu",
        )
    )
    db.flush()

    draft = {**_MINIMAL_DRAFT, "targetEnvironmentName": "PROD_EU"}
    job = _job(JOB_GENERATE, pipeline_id=pipeline.id, payload={"draft": draft, "version": "v1.0.0"})
    dispatch(db, job)
    db.flush()

    global_parameters = _global_parameters_artifact(db, pipeline.id, "v1.0.0")
    assert global_parameters["name"] == "contoso-adf-prod-eu"
    assert global_parameters["properties"]["globalParameters"]["Environment"]["value"] == "PROD_EU"

    versions = db.scalars(select(models.PipelineVersion).where(models.PipelineVersion.pipeline_id == pipeline.id)).all()
    assert versions[0].target_environment_name == "PROD_EU"


def test_handle_generate_with_unknown_environment_name_falls_back_to_placeholder_tokens(
    db: Session, pipeline: models.PipelineDefinition
):
    """A `targetEnvironmentName` that doesn't resolve (typo, or deleted after the draft was
    saved) degrades to the no-instance-selected behavior instead of failing the job."""
    draft = {**_MINIMAL_DRAFT, "targetEnvironmentName": "DOES_NOT_EXIST"}
    job = _job(JOB_GENERATE, pipeline_id=pipeline.id, payload={"draft": draft, "version": "v1.0.0"})
    result = dispatch(db, job)
    db.flush()
    assert result["state"] == "succeeded"

    global_parameters = _global_parameters_artifact(db, pipeline.id, "v1.0.0")
    assert global_parameters["name"] == "<FACTORY_NAME>"


def test_handle_generate_regeneration_replaces_the_same_version_artifacts(
    db: Session, pipeline: models.PipelineDefinition
):
    job = _job(JOB_GENERATE, pipeline_id=pipeline.id, payload={"draft": _MINIMAL_DRAFT, "version": "v1.0.0"})
    dispatch(db, job)
    db.flush()
    first_count = len(
        db.scalars(
            select(models.Artifact).where(models.Artifact.pipeline_id == pipeline.id, models.Artifact.version == "v1.0.0")
        ).all()
    )

    job2 = _job(JOB_GENERATE, pipeline_id=pipeline.id, payload={"draft": _MINIMAL_DRAFT, "version": "v1.0.0"})
    dispatch(db, job2)
    db.flush()
    second_count = len(
        db.scalars(
            select(models.Artifact).where(models.Artifact.pipeline_id == pipeline.id, models.Artifact.version == "v1.0.0")
        ).all()
    )
    assert second_count == first_count  # replaced, not appended


def test_handle_generate_fails_domain_style_on_an_invalid_draft(db: Session, pipeline: models.PipelineDefinition):
    bad_draft = {**_MINIMAL_DRAFT, "name": "", "targetPath": ""}
    job = _job(JOB_GENERATE, pipeline_id=pipeline.id, payload={"draft": bad_draft, "version": "v1.0.0"})
    result = dispatch(db, job)  # does not raise — a domain failure, not a transient one
    assert result["state"] == "failed"
    assert result["issues"]
    assert pipeline.state == "GENERATION_FAILED"


def test_handle_generate_raises_job_error_when_pipeline_is_gone(db: Session):
    job = _job(JOB_GENERATE, pipeline_id="pl-missing", payload={"draft": _MINIMAL_DRAFT})
    with pytest.raises(JobError):
        dispatch(db, job)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_handle_validate_marks_pipeline_validated_on_a_clean_spec(db: Session, pipeline: models.PipelineDefinition):
    generate_job = _job(JOB_GENERATE, pipeline_id=pipeline.id, payload={"draft": _MINIMAL_DRAFT, "version": "v1.0.0"})
    dispatch(db, generate_job)
    db.flush()

    validate_job = _job(JOB_VALIDATE, pipeline_id=pipeline.id, payload={"draft": _MINIMAL_DRAFT})
    result = dispatch(db, validate_job)
    db.flush()

    assert result["state"] == "passed"
    assert pipeline.state == "VALIDATED"
    validated = db.scalars(
        select(models.Artifact).where(models.Artifact.pipeline_id == pipeline.id, models.Artifact.version == "v1.0.0")
    ).all()
    assert all(a.state == "validated" for a in validated)
    audit = db.scalars(select(models.AuditEntry).where(models.AuditEntry.action == "pipeline.validated")).all()
    assert len(audit) == 1


def test_handle_validate_reads_naming_convention_from_project_settings_and_can_fail(
    db: Session, pipeline: models.PipelineDefinition
):
    db.add(
        models.ProjectSettings(
            project_id="proj-a",
            document={
                "namingConvention": {
                    "enforcement": "block",
                    "rules": {"pipeline": {"prefix": "PL_MUST_NOT_MATCH_", "case": "none"}},
                }
            },
        )
    )
    db.flush()

    validate_job = _job(JOB_VALIDATE, pipeline_id=pipeline.id, payload={"draft": _MINIMAL_DRAFT})
    result = dispatch(db, validate_job)

    assert result["state"] == "failed"
    assert pipeline.state == "VALIDATION_FAILED"


def test_handle_validate_raises_job_error_when_pipeline_is_gone(db: Session):
    job = _job(JOB_VALIDATE, pipeline_id="pl-missing", payload={"draft": _MINIMAL_DRAFT})
    with pytest.raises(JobError):
        dispatch(db, job)


# ---------------------------------------------------------------------------
# git_push
# ---------------------------------------------------------------------------


@pytest.fixture()
def pushable(db: Session, pipeline: models.PipelineDefinition) -> models.GitPushRequest:
    generate_job = _job(JOB_GENERATE, pipeline_id=pipeline.id, payload={"draft": _MINIMAL_DRAFT, "version": "v1.0.0"})
    dispatch(db, generate_job)
    db.flush()
    artifact = db.scalars(
        select(models.Artifact).where(
            models.Artifact.pipeline_id == pipeline.id, models.Artifact.artifact_type == "pipeline"
        )
    ).first()
    push = models.GitPushRequest(
        id=new_id("push"),
        pipeline_id=pipeline.id,
        provider="github",
        repository_url="https://github.com/acme/widgets",
        feature_branch="feat/customer",
        base_branch="dev",
        artifact_ids=[artifact.id],
        commit_message="feat: customer incremental pipeline",
        status="pending",
    )
    db.add(push)
    db.flush()
    return push


def test_handle_git_push_succeeds_with_the_offline_simulated_adapter(
    db: Session, pipeline: models.PipelineDefinition, pushable: models.GitPushRequest
):
    # No GIT_TOKEN / credential_vault / direct_token resolves -> get_adapter
    # falls back to SimulatedAdapter (§23.12 gap #14) rather than a real call.
    job = _job(JOB_GIT_PUSH, pipeline_id=pipeline.id, payload={"pushRequestId": pushable.id})
    result = dispatch(db, job)
    db.flush()

    assert result["state"] == "succeeded"
    assert result["simulated"] is True
    assert result["pullRequest"]["number"]
    assert pushable.status == "pr_created"
    assert pushable.commit_sha
    assert pipeline.state == "PR_CREATED"

    pushed_artifact = db.get(models.Artifact, pushable.artifact_ids[0])
    assert pushed_artifact.state == "committed"
    assert pushed_artifact.repository_path == "adf/artifacts/pipeline/" + pushed_artifact.file_name

    pr = db.scalars(select(models.GitPullRequest).where(models.GitPullRequest.push_request_id == pushable.id)).first()
    assert pr is not None
    assert pr.status == "open"


def test_handle_git_push_pr_only_mode_skips_the_commit(
    db: Session, pipeline: models.PipelineDefinition, pushable: models.GitPushRequest
):
    job = _job(JOB_GIT_PUSH, pipeline_id=pipeline.id, payload={"pushRequestId": pushable.id, "prOnly": True})
    result = dispatch(db, job)

    assert result["state"] == "succeeded"
    assert pushable.commit_sha is None  # never set — the commit step was skipped
    assert pipeline.state == "PR_CREATED"


def test_handle_git_push_rejects_a_push_to_the_protected_base_branch(
    db: Session, pipeline: models.PipelineDefinition, pushable: models.GitPushRequest
):
    pushable.feature_branch = pushable.base_branch  # dev == dev
    db.flush()
    job = _job(JOB_GIT_PUSH, pipeline_id=pipeline.id, payload={"pushRequestId": pushable.id})
    result = dispatch(db, job)  # a GitOpsError is a domain failure, not a raise
    db.flush()  # this session is autoflush=False — the _audit() row below is still pending otherwise

    assert result["state"] == "failed"
    assert pushable.status == "failed"
    assert pipeline.state == "GIT_PUSH_FAILED"
    audit = db.scalars(select(models.AuditEntry).where(models.AuditEntry.action == "git.push_failed")).all()
    assert len(audit) == 1


def test_handle_git_push_raises_job_error_when_the_push_request_is_gone(db: Session, pipeline: models.PipelineDefinition):
    job = _job(JOB_GIT_PUSH, pipeline_id=pipeline.id, payload={"pushRequestId": "push-missing"})
    with pytest.raises(JobError):
        dispatch(db, job)
