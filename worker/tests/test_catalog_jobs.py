"""§24.8 catalog refresh + §24.9/§24.10 catalog change jobs. No network: the
simulated virtual repo, or a stub adapter patched into ``app.jobs.catalog``."""
from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.base import Base
from app.gitops import PullRequestResult, PushResult, RepoFile
from app.jobs import JOB_CATALOG_CHANGE, JOB_CATALOG_REFRESH
from app.jobs.catalog import git_blob_sha, parse_repo_file
from app.jobs.runner import dispatch

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
_SessionLocal = sessionmaker(bind=_engine, autoflush=True, expire_on_commit=False, future=True)

ROOT = "adf/artifacts/"
PIPELINE = {
    "name": "PL_sales_master",
    "properties": {
        "parameters": {"ControlTable": {"type": "String"}, "TaskIdFilter": {"type": "Int"}},
        "activities": [
            {"name": "Run", "type": "ExecutePipeline",
             "typeProperties": {"pipeline": {"referenceName": "PL_sales_child", "type": "PipelineReference"}}},
            {"name": "Lookup", "type": "Lookup",
             "typeProperties": {"dataset": {"referenceName": "DS_control", "type": "DatasetReference"}}},
        ],
    },
}


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    monkeypatch.setattr("app.gitops.credential_vault.settings.git_token", "")
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


def _job(db: Session, job_type: str, payload: dict) -> models.Job:
    job = models.Job(id=f"job-{job_type}-{len(payload)}", type=job_type, status="running", payload=payload)
    db.add(job)
    db.flush()
    return job


def _refresh(db: Session, branch: str = "dev") -> dict:
    return dispatch(db, _job(db, JOB_CATALOG_REFRESH, {
        "projectId": "proj-a", "branch": branch, "provider": "github",
        "repositoryUrl": "https://github.com/contoso/adf", "artifactRoot": ROOT,
    }))


def _entries(db: Session) -> dict[str, models.GitCatalogEntry]:
    return {e.path: e for e in db.scalars(select(models.GitCatalogEntry))}


# --- parsing ---------------------------------------------------------------------


def test_parse_repo_file_extracts_type_name_references_and_parameters() -> None:
    parsed = parse_repo_file("adf/artifacts/pipeline/PL_sales_master.json", json.dumps(PIPELINE), ROOT)
    assert parsed["artifact_type"] == "pipeline"
    assert parsed["name"] == "PL_sales_master"
    assert parsed["references"] == [
        {"type": "DatasetReference", "name": "DS_control"},
        {"type": "PipelineReference", "name": "PL_sales_child"},
    ]
    assert parsed["parameters"] == ["ControlTable", "TaskIdFilter"]


def test_parse_repo_file_handles_metadata_and_bad_json() -> None:
    assert parse_repo_file("adf/artifacts/metadata/objects.csv", "a,b\r\n", ROOT)["artifact_type"] == "metadata"
    bad = parse_repo_file("adf/artifacts/dataset/DS_x.json", "{not json", ROOT)
    assert bad == {"artifact_type": "dataset", "name": "DS_x", "references": [], "parameters": []}


# --- refresh -----------------------------------------------------------------------


def _simulated_push(db: Session) -> None:
    db.add(models.PipelineDefinition(id="pl-1", name="sales", template_id="t", project_id="proj-a", team_id="team-a"))
    content = json.dumps(PIPELINE)
    db.add(models.Artifact(
        id="art-1", pipeline_id="pl-1", pipeline_name="sales", file_name="PL_sales_master.json",
        artifact_type="pipeline", layer="pipeline", version="v1", content=content,
        repository_path="adf/artifacts/pipeline/PL_sales_master.json",
    ))
    db.add(models.GitPushRequest(
        id="push-1", pipeline_id="pl-1", provider="github", repository_url="https://github.com/contoso/adf",
        feature_branch="feature/x", base_branch="dev", artifact_ids=["art-1"], status="pr_created", simulated=True,
    ))
    db.flush()


def test_simulated_refresh_indexes_what_this_app_pushed(db: Session) -> None:
    _simulated_push(db)
    result = _refresh(db)
    assert result == {"state": "succeeded", "branch": "dev", "count": 1, "simulated": True}
    entry = _entries(db)["adf/artifacts/pipeline/PL_sales_master.json"]
    assert entry.simulated and entry.artifact_type == "pipeline"
    assert entry.blob_sha == git_blob_sha(entry.content)
    assert [a.action for a in db.scalars(select(models.AuditEntry))] == ["catalog.refreshed"]


def test_simulated_catalog_delete_removes_the_file_on_the_next_refresh(db: Session) -> None:
    _simulated_push(db)
    db.add(models.GitCatalogChange(
        id="chg-1", project_id="proj-a", mode="delete", path="adf/artifacts/pipeline/PL_sales_master.json",
        provider="github", repository_url="https://github.com/contoso/adf", feature_branch="feature/del",
        base_branch="dev", files=[], delete_paths=["adf/artifacts/pipeline/PL_sales_master.json"],
    ))
    db.flush()
    change = dispatch(db, _job(db, JOB_CATALOG_CHANGE, {"changeId": "chg-1"}))
    assert change["state"] == "succeeded"
    assert _refresh(db)["count"] == 0


def test_refresh_replaces_the_branch_index_from_the_real_adapter(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    class Reader:
        def list_files(self, *, branch, prefix):
            seen.update(branch=branch, prefix=prefix)
            return [RepoFile(path="adf/artifacts/pipeline/PL_sales_master.json", sha="abc", content=json.dumps(PIPELINE))]

    monkeypatch.setattr("app.jobs.catalog.get_adapter", lambda *a, **k: Reader())
    db.add(models.GitCatalogEntry(project_id="proj-a", branch="main", path="stale.json", artifact_type="metadata", name="stale"))
    db.flush()
    assert _refresh(db, "main")["simulated"] is False
    assert seen == {"branch": "main", "prefix": ROOT}
    assert set(_entries(db)) == {"adf/artifacts/pipeline/PL_sales_master.json"}


# --- change --------------------------------------------------------------------------


def _change(db: Session, **overrides) -> models.GitCatalogChange:
    change = models.GitCatalogChange(
        id="chg-2", project_id="proj-a", mode="rename", path="adf/artifacts/dataset/DS_a.json",
        new_path="adf/artifacts/dataset/DS_b.json", provider="github",
        repository_url="https://github.com/contoso/adf", feature_branch="feature/rename", base_branch="dev",
        commit_message="rename DS_a", files=[{"path": "adf/artifacts/dataset/DS_b.json", "content": "{}"}],
        delete_paths=["adf/artifacts/dataset/DS_a.json"], base_blob_sha="sha-loaded", **overrides,
    )
    db.add(change)
    db.flush()
    return change


class _Writer:
    def __init__(self, current_sha: str | None) -> None:
        self.current_sha, self.pushed = current_sha, None

    def file_sha(self, *, path, ref):
        return self.current_sha

    def push_artifacts(self, **kwargs):
        self.pushed = kwargs
        return PushResult(commit_sha="c0ffee", branch=kwargs["feature_branch"], files=2)

    def open_pull_request(self, **kwargs):
        return PullRequestResult(number=9, url="https://github.com/contoso/adf/pull/9",
                                 source_branch=kwargs["source_branch"], target_branch=kwargs["target_branch"])


def test_change_commits_files_and_deletes_then_opens_the_pr(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _Writer("sha-loaded")
    monkeypatch.setattr("app.jobs.catalog.get_adapter", lambda *a, **k: writer)
    change = _change(db)
    assert dispatch(db, _job(db, JOB_CATALOG_CHANGE, {"changeId": "chg-2"}))["state"] == "succeeded"
    assert writer.pushed["deletes"] == ["adf/artifacts/dataset/DS_a.json"]
    assert [b.path for b in writer.pushed["files"]] == ["adf/artifacts/dataset/DS_b.json"]
    assert (change.status, change.pr_number, change.commit_sha) == ("pr_created", 9, "c0ffee")


def test_change_refuses_when_the_file_moved_since_it_was_loaded(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _Writer("sha-someone-else")
    monkeypatch.setattr("app.jobs.catalog.get_adapter", lambda *a, **k: writer)
    change = _change(db)
    result = dispatch(db, _job(db, JOB_CATALOG_CHANGE, {"changeId": "chg-2"}))
    assert result["code"] == "git_conflict" and result["theirSha"] == "sha-someone-else"
    assert change.status == "conflict" and writer.pushed is None
