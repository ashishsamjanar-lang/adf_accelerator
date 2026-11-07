"""End-to-end pipeline lifecycle test (IMPLEMENTATION_PLAN.md "Cross-cutting
test coverage" #5 — "E2E: Build -> Generate -> Validate -> Push -> PR happy
path, plus one failure path per step. — not started.").

``test_runner.py`` already covers every one of these handlers *in isolation*
(each test builds its own fresh pipeline/fixture and calls exactly one
``dispatch``), including the three failure paths this item asks for:

  - generate: ``test_handle_generate_fails_domain_style_on_an_invalid_draft``
  - validate: ``test_handle_validate_reads_naming_convention_from_project_settings_and_can_fail``
  - git_push: ``test_handle_git_push_rejects_a_push_to_the_protected_base_branch``

This file deliberately does not repeat those — it is the one thing genuinely
missing: a *single* pipeline run through generate -> validate -> git_push (the
same real ``dispatch()`` the job-poll loop calls, against a real in-memory
metastore) end to end in one continuous flow, exactly the sequence a user
triggers from Build through PR. Chaining catches a class of bug the isolated
tests structurally cannot: one stage's output silently disagreeing with the
next stage's expectations (e.g. validate mutating artifact state in a way
git_push doesn't expect, or a spec/version mismatch surviving into the push).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base, new_id
from app.jobs import JOB_GENERATE, JOB_GIT_PUSH, JOB_VALIDATE
from app.jobs.runner import dispatch

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)

_DRAFT = {
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
    # A real Build submission always carries fully-configured linked
    # services/datasets (Connections/Build UI requires it) — unlike
    # `test_runner.py`'s `_MINIMAL_DRAFT`, which validate_draft would flag
    # as missing "Server name"/"Storage account name"/etc. (§26.2 connector
    # field contracts, `app/connectors/schemas.py`), this test's whole point
    # is a spec that actually clears generate -> validate -> push end to end.
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
    """The same DB shape the backend API creates via `POST /pipelines` (team,
    project, active template, a DRAFT pipeline) — this test picks up from
    there exactly where the API's `generate`/`validate`/`commit` endpoints
    would enqueue a job for the worker to claim."""
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
        draft=_DRAFT,
        spec={},
        created_by="tester",
    )
    db.add_all([team, project, template, pl])
    db.flush()
    return pl


def _job(job_type: str, pipeline_id: str, payload: dict) -> models.Job:
    return models.Job(id=new_id("job"), type=job_type, status="running", pipeline_id=pipeline_id, payload=payload, created_by="tester")


def test_build_generate_validate_push_pr_happy_path(db: Session, pipeline: models.PipelineDefinition) -> None:
    # 1. Build -> Generate: worker claims the generate job the API enqueued.
    generate_result = dispatch(db, _job(JOB_GENERATE, pipeline.id, {"draft": _DRAFT, "version": "v1.0.0"}))
    db.flush()

    assert generate_result["state"] == "succeeded"
    assert pipeline.state == "GENERATED"
    assert pipeline.latest_artifact_version == "v1.0.0"
    generated_spec = pipeline.spec
    assert generated_spec  # build_spec output persisted for the next stage to read

    artifacts = db.scalars(
        select(models.Artifact).where(models.Artifact.pipeline_id == pipeline.id, models.Artifact.version == "v1.0.0")
    ).all()
    assert len(artifacts) >= 2  # at least one pipeline artifact + manifest.json
    assert all(a.state == "generated" for a in artifacts)

    # 2. Validate: reads back the same spec/artifacts generate just wrote.
    validate_result = dispatch(db, _job(JOB_VALIDATE, pipeline.id, {"draft": _DRAFT}))
    db.flush()

    assert validate_result["state"] == "passed"
    # Advisory only (§25 Key Vault linked-service existence reminder, not a
    # correctness error) — `report.ok`/pipeline.state are what "passed" means.
    assert all(i["severity"] == "warning" for i in validate_result["issues"])
    assert pipeline.state == "VALIDATED"
    assert pipeline.spec == generated_spec  # validate doesn't clobber what generate produced
    validated_artifacts = db.scalars(
        select(models.Artifact).where(models.Artifact.pipeline_id == pipeline.id, models.Artifact.version == "v1.0.0")
    ).all()
    assert all(a.state == "validated" for a in validated_artifacts)

    # 3. Push + PR: commits every validated artifact and opens a PR in one job
    # (the offline SimulatedAdapter, since no Git credentials are configured).
    push = models.GitPushRequest(
        id=new_id("push"),
        pipeline_id=pipeline.id,
        provider="github",
        repository_url="https://github.com/acme/widgets",
        feature_branch="feat/customer-incremental",
        base_branch="dev",
        artifact_ids=[a.id for a in validated_artifacts],
        commit_message="feat: customer incremental pipeline",
        status="pending",
    )
    db.add(push)
    db.flush()

    push_result = dispatch(db, _job(JOB_GIT_PUSH, pipeline.id, {"pushRequestId": push.id}))
    db.flush()

    assert push_result["state"] == "succeeded"
    assert push_result["simulated"] is True
    assert push_result["pullRequest"]["number"]
    assert pipeline.state == "PR_CREATED"
    assert push.status == "pr_created"
    assert push.commit_sha

    pushed_artifacts = db.scalars(select(models.Artifact).where(models.Artifact.id.in_(push.artifact_ids))).all()
    assert all(a.state == "committed" for a in pushed_artifacts)
    assert all(a.repository_path for a in pushed_artifacts)

    pr = db.scalars(select(models.GitPullRequest).where(models.GitPullRequest.push_request_id == push.id)).first()
    assert pr is not None
    assert pr.status == "open"
    assert pr.source_branch == "feat/customer-incremental"
    assert pr.target_branch == "dev"

    # End-to-end audit trail: exactly one entry per stage (ids are random
    # UUIDs, not sortable, so this checks membership/count, not sequence).
    audit_actions = [a.action for a in db.scalars(select(models.AuditEntry)).all()]
    assert sorted(audit_actions) == sorted(["artifact.generated", "pipeline.validated", "pr.created"])
