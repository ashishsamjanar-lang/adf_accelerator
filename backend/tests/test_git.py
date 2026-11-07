"""Git push / PR endpoint tests (AGENTS.md §8, §9, §14, §23.6, §23.12 gaps #17/#20).

The push-token encryption test is the regression test for gap #17: a Git PAT
must never sit in ``jobs.payload`` as plaintext, even transiently before the
(nonexistent, in this suite) worker claims the job.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.crypto import decrypt_secret


def test_git_push_encrypts_the_token_at_rest(client: TestClient, seed: dict[str, str], db) -> None:
    from app import models

    plaintext_token = "ghp_super_secret_value"
    res = client.post(
        "/api/v1/git/push",
        json={"pipelineId": seed["pipeline_id"], "featureBranch": "feature/push-test", "gitToken": plaintext_token},
    )
    assert res.status_code == 202

    job = db.query(models.Job).filter(models.Job.type == "git_push").one()
    stored = job.payload["gitToken"]
    assert stored is not None
    assert stored != plaintext_token
    assert plaintext_token not in job.payload.values()
    assert decrypt_secret(stored) == plaintext_token


def test_git_push_rejects_protected_branch(client: TestClient, seed: dict[str, str]) -> None:
    res = client.post(
        "/api/v1/git/push", json={"pipelineId": seed["pipeline_id"], "featureBranch": "dev", "baseBranch": "dev"}
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "protected_branch"


def test_git_push_requires_at_least_one_artifact(client: TestClient, seed: dict[str, str]) -> None:
    empty_pipeline = client.post(
        "/api/v1/pipelines",
        json={"name": "no-artifacts-yet", "projectId": seed["project_id"], "templateId": seed["template_id"]},
    ).json()

    res = client.post(
        "/api/v1/git/push", json={"pipelineId": empty_pipeline["id"], "featureBranch": "feature/empty"}
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "no_artifacts"


def test_git_push_requires_a_configured_repository(client: TestClient, seed: dict[str, str], db) -> None:
    from app import models

    project = models.Project(id="proj-b", team_id=seed["team_id"], name="Project B", description="")
    pipeline = models.PipelineDefinition(
        id="pl-b",
        name="pipeline-b",
        template_id=seed["template_id"],
        project_id="proj-b",
        team_id=seed["team_id"],
        state="GENERATED",
        draft={},
        spec={},
        latest_artifact_version="v1.0.0",
    )
    artifact = models.Artifact(
        id="art-b",
        pipeline_id="pl-b",
        pipeline_name="pipeline-b",
        file_name="pipeline-b.json",
        file_type="json",
        artifact_type="pipeline",
        layer="pipeline",
        version="v1.0.0",
        state="generated",
        bytes=2,
        content="{}",
    )
    db.add_all([project, pipeline, artifact])
    db.commit()

    res = client.post("/api/v1/git/push", json={"pipelineId": "pl-b", "featureBranch": "feature/x"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "git_not_configured"


def test_get_push_roundtrip(client: TestClient, seed: dict[str, str]) -> None:
    pushed = client.post(
        "/api/v1/git/push", json={"pipelineId": seed["pipeline_id"], "featureBranch": "feature/get-test"}
    ).json()

    res = client.get(f"/api/v1/git/push/{pushed['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == pushed["id"]
    assert res.json()["status"] == "pending"


def test_get_unknown_push_404s(client: TestClient) -> None:
    res = client.get("/api/v1/git/push/does-not-exist")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "push_not_found"


def test_create_pull_request_requires_a_completed_push(client: TestClient, seed: dict[str, str], db) -> None:
    from app import models

    push = models.GitPushRequest(
        id="push-unpushed",
        pipeline_id=seed["pipeline_id"],
        provider="github",
        repository_url="https://example.test/repo.git",
        feature_branch="feature/not-pushed-yet",
        base_branch="dev",
        artifact_ids=[seed["artifact_id"]],
        commit_message="test",
        status="pending",
    )
    db.add(push)
    db.commit()

    res = client.post("/api/v1/git/pull-requests", json={"pushRequestId": "push-unpushed"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "push_not_ready"


def test_create_pull_request_unknown_push_404s(client: TestClient) -> None:
    res = client.post("/api/v1/git/pull-requests", json={"pushRequestId": "nope"})
    assert res.status_code == 404


def _seed_naming_pipeline(db, *, enforcement: str, declared_name: str) -> None:
    """A project with a "block"/"warn" naming standard and one pipeline
    artifact declaring `declared_name` — for the §25 pre-push gate tests."""
    from app import models

    db.add(models.Project(id="proj-naming", team_id="team-a", name="Naming Project", description=""))
    db.add(
        models.ProjectSettings(
            project_id="proj-naming",
            document={
                "projectId": "proj-naming",
                "gitDefaults": {
                    "provider": "github",
                    "repositoryUrl": "https://example.test/repo.git",
                    "baseBranch": "dev",
                },
                "namingConvention": {
                    "enforcement": enforcement,
                    "rules": {"pipeline": {"prefix": "PL_"}},
                },
            },
            updated_by="Test Admin",
        )
    )
    db.add(
        models.PipelineDefinition(
            id="pl-naming",
            name="naming-pipeline",
            template_id="tmpl-a",
            project_id="proj-naming",
            team_id="team-a",
            state="GENERATED",
            draft={},
            spec={},
            latest_artifact_version="v1.0.0",
        )
    )
    db.add(
        models.Artifact(
            id="art-naming",
            pipeline_id="pl-naming",
            pipeline_name="naming-pipeline",
            file_name="pipeline.json",
            file_type="json",
            artifact_type="pipeline",
            layer="pipeline",
            version="v1.0.0",
            state="generated",
            bytes=2,
            content=f'{{"name": "{declared_name}"}}',
        )
    )
    db.commit()


def test_git_push_rejects_a_block_level_naming_violation(client: TestClient, seed: dict[str, str], db) -> None:
    _seed_naming_pipeline(db, enforcement="block", declared_name="customer_load")  # missing the PL_ prefix

    res = client.post("/api/v1/git/push", json={"pipelineId": "pl-naming", "featureBranch": "feature/naming"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "policy_violation"


def test_git_push_allows_a_compliant_name_under_a_block_rule(client: TestClient, seed: dict[str, str], db) -> None:
    _seed_naming_pipeline(db, enforcement="block", declared_name="PL_customer_load")

    res = client.post("/api/v1/git/push", json={"pipelineId": "pl-naming", "featureBranch": "feature/naming"})
    assert res.status_code == 202


def test_git_push_allows_a_warn_level_naming_violation(client: TestClient, seed: dict[str, str], db) -> None:
    _seed_naming_pipeline(db, enforcement="warn", declared_name="customer_load")  # missing the PL_ prefix, but only "warn"

    res = client.post("/api/v1/git/push", json={"pipelineId": "pl-naming", "featureBranch": "feature/naming"})
    assert res.status_code == 202


def _add_artifact(db, artifact_id: str, file_name: str, version: str = "v1.0.0") -> None:
    from app import models

    db.add(
        models.Artifact(
            id=artifact_id, pipeline_id="pl-a", pipeline_name="pipeline-a", file_name=file_name, file_type="json",
            artifact_type="dataset", layer="dataset", version=version, state="generated", bytes=2, content="{}",
        )
    )
    db.commit()


def _pushed_ids(client: TestClient, artifact_ids: list[str]) -> list[str]:
    res = client.post(
        "/api/v1/git/push",
        json={"pipelineId": "pl-a", "featureBranch": "feature/select", "artifactIds": artifact_ids},
    )
    assert res.status_code == 202, res.json()
    return client.get(f"/api/v1/git/push/{res.json()['id']}").json()["artifactIds"]


def test_single_artifact_git_push_sends_only_the_selected_file(client: TestClient, seed: dict[str, str], db) -> None:
    """AGENTS.md §18 backend: single-artifact Git push."""
    _add_artifact(db, "art-b", "DS_b.json")
    assert _pushed_ids(client, ["art-b"]) == ["art-b"]


def test_multi_artifact_git_push_sends_every_selected_file(client: TestClient, seed: dict[str, str], db) -> None:
    """AGENTS.md §18 backend: multi-artifact Git push; no selection means every current-version artifact,
    and an artifact from an older generation is never included."""
    _add_artifact(db, "art-b", "DS_b.json")
    _add_artifact(db, "art-old", "DS_old.json", version="v0.9.0")
    assert _pushed_ids(client, ["art-a", "art-b"]) == ["art-a", "art-b"]
    assert sorted(_pushed_ids(client, [])) == ["art-a", "art-b"]


def test_git_push_rejects_an_unknown_or_stale_artifact_id(client: TestClient, seed: dict[str, str], db) -> None:
    _add_artifact(db, "art-old", "DS_old.json", version="v0.9.0")
    for bad in ("art-missing", "art-old"):
        res = client.post(
            "/api/v1/git/push", json={"pipelineId": "pl-a", "featureBranch": "feature/select", "artifactIds": [bad]}
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "artifact_not_found"


def test_artifacts_push_to_git_alias_queues_a_push(client: TestClient, seed: dict[str, str]) -> None:
    """§29.5 happy path: `/artifacts/push-to-git` is the same push as `/git/push`."""
    res = client.post(
        "/api/v1/artifacts/push-to-git", json={"pipelineId": seed["pipeline_id"], "featureBranch": "feature/alias"}
    )
    assert res.status_code == 202, res.text
    assert res.json()["featureBranch"] == "feature/alias" and res.json()["status"] == "pending"


def test_create_pull_request_after_a_completed_push_queues_a_pr_job(client: TestClient, seed: dict[str, str], db) -> None:
    """§29.5 happy path: a pushed (commit_sha set) request with no PR yet queues a PR-only job."""
    from app import models

    db.add(
        models.GitPushRequest(
            id="push-done",
            pipeline_id=seed["pipeline_id"],
            provider="github",
            repository_url="https://example.test/repo.git",
            feature_branch="feature/pushed",
            base_branch="dev",
            artifact_ids=[seed["artifact_id"]],
            commit_message="test",
            status="pushed",
            commit_sha="abc123",
        )
    )
    db.commit()
    res = client.post("/api/v1/git/pull-requests", json={"pushRequestId": "push-done", "title": "Add pipeline"})
    assert res.status_code == 202, res.text
    assert any(j.payload.get("prOnly") is True and j.payload.get("pushRequestId") == "push-done" for j in db.query(models.Job).all())
