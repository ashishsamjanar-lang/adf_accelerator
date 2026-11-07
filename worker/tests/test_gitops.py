"""pytest for app/gitops/* — SimulatedAdapter, GitHubAdapter, AzureReposAdapter,
GitLabAdapter (AGENTS.md §23.6, §23.12 gap #20).

GitHubAdapter/AzureReposAdapter/GitLabAdapter make real HTTP calls through a
private ``_call`` method — tests monkeypatch that one seam instead of mocking
``urllib`` directly, so they exercise the adapters' own request-sequencing
and response-parsing logic without ever touching the network.
"""
from __future__ import annotations

import pytest

from app.gitops.adapter import ArtifactBlob, GitOpsError, ProtectedBranchError, get_adapter
from app.gitops.azure_repos import AzureReposAdapter
from app.gitops.github import GitHubAdapter
from app.gitops.gitlab import GitLabAdapter
from app.gitops.simulated import SimulatedAdapter

FILES = [ArtifactBlob(path="adf/artifacts/PL_x.json", content='{"name": "PL_x"}')]


# ---------------------------------------------------------------------------
# SimulatedAdapter
# ---------------------------------------------------------------------------


def test_simulated_push_is_deterministic_for_the_same_input():
    a = SimulatedAdapter("https://github.com/acme/repo")
    b = SimulatedAdapter("https://github.com/acme/repo")
    r1 = a.push_artifacts(feature_branch="feat/x", base_branch="dev", files=FILES, commit_message="msg")
    r2 = b.push_artifacts(feature_branch="feat/x", base_branch="dev", files=FILES, commit_message="msg")
    assert r1.commit_sha == r2.commit_sha
    assert r1.simulated is True


def test_simulated_push_differs_for_different_input():
    a = SimulatedAdapter("https://github.com/acme/repo")
    r1 = a.push_artifacts(feature_branch="feat/x", base_branch="dev", files=FILES, commit_message="msg")
    r2 = a.push_artifacts(feature_branch="feat/y", base_branch="dev", files=FILES, commit_message="msg")
    assert r1.commit_sha != r2.commit_sha


def test_simulated_push_rejects_pushing_to_the_protected_base_branch():
    a = SimulatedAdapter("https://github.com/acme/repo")
    with pytest.raises(ProtectedBranchError):
        a.push_artifacts(feature_branch="dev", base_branch="dev", files=FILES, commit_message="msg")


def test_simulated_push_rejects_an_empty_feature_branch():
    a = SimulatedAdapter("https://github.com/acme/repo")
    with pytest.raises(GitOpsError):
        a.push_artifacts(feature_branch="", base_branch="dev", files=FILES, commit_message="msg")


def test_simulated_push_rejects_an_empty_file_set():
    a = SimulatedAdapter("https://github.com/acme/repo")
    with pytest.raises(GitOpsError):
        a.push_artifacts(feature_branch="feat/x", base_branch="dev", files=[], commit_message="msg")


def test_simulated_pr_is_flagged_simulated():
    a = SimulatedAdapter("https://github.com/acme/repo")
    pr = a.open_pull_request(source_branch="feat/x", target_branch="dev", title="t", body="b")
    assert pr.simulated is True
    assert pr.url and pr.url.startswith("https://github.com/acme/repo/pull/")


def test_simulated_pr_rejects_the_reverse_direction():
    a = SimulatedAdapter("https://github.com/acme/repo")
    with pytest.raises(ProtectedBranchError):
        a.open_pull_request(source_branch="dev", target_branch="dev", title="t", body="b")


# ---------------------------------------------------------------------------
# get_adapter factory — §23.12 gap #14
# ---------------------------------------------------------------------------


def test_get_adapter_falls_back_to_simulated_with_no_credential(monkeypatch):
    monkeypatch.delenv("GIT_TOKEN", raising=False)
    monkeypatch.delenv("KEY_VAULT_NAME", raising=False)
    adapter = get_adapter("github", "https://github.com/acme/repo")
    assert isinstance(adapter, SimulatedAdapter)


def test_get_adapter_uses_the_direct_token_when_given():
    adapter = get_adapter("github", "https://github.com/acme/repo", direct_token="ghp_test_token")
    assert isinstance(adapter, GitHubAdapter)


def test_get_adapter_falls_back_to_simulated_for_an_unknown_provider():
    adapter = get_adapter("some_unknown_provider", "https://example.com/x/y", direct_token="tok")
    assert isinstance(adapter, SimulatedAdapter)


def test_get_adapter_routes_gitlab_to_the_gitlab_adapter_not_github():
    # Regression: "gitlab" used to be routed through GitHubAdapter, which
    # only ever parses github.com URLs and would fail on every real push.
    adapter = get_adapter("gitlab", "https://gitlab.com/acme/repo", direct_token="tok")
    assert isinstance(adapter, GitLabAdapter)


# ---------------------------------------------------------------------------
# GitHubAdapter
# ---------------------------------------------------------------------------


def test_github_adapter_parses_owner_and_repo():
    a = GitHubAdapter("https://github.com/acme/widgets", credential="tok")
    assert (a.owner, a.repo) == ("acme", "widgets")


def test_github_adapter_rejects_an_unparseable_url():
    with pytest.raises(GitOpsError):
        GitHubAdapter("https://not-github.example.com/acme/widgets", credential="tok")


def test_github_adapter_guards_branches_before_any_network_call(monkeypatch):
    a = GitHubAdapter("https://github.com/acme/widgets", credential="tok")

    def _fail_call(*args, **kwargs):
        raise AssertionError("should not have made an HTTP call")

    monkeypatch.setattr(a, "_call", _fail_call)
    with pytest.raises(ProtectedBranchError):
        a.push_artifacts(feature_branch="dev", base_branch="dev", files=FILES, commit_message="msg")
    with pytest.raises(GitOpsError):
        a.push_artifacts(feature_branch="feat/x", base_branch="dev", files=[], commit_message="msg")


def test_github_adapter_push_creates_a_new_branch_when_it_does_not_exist(monkeypatch):
    a = GitHubAdapter("https://github.com/acme/widgets", credential="tok")
    calls: list[tuple[str, str]] = []

    def fake_call(method, path, body=None):
        calls.append((method, path))
        if path == "/git/ref/heads/dev":
            return {"object": {"sha": "base-sha"}}
        if path == "/git/commits/base-sha":
            return {"tree": {"sha": "base-tree-sha"}}
        if path == "/git/blobs":
            return {"sha": f"blob-{body['content'][:4]}"}
        if path == "/git/trees":
            return {"sha": "new-tree-sha"}
        if path == "/git/commits":
            return {"sha": "new-commit-sha"}
        if path == "/git/ref/heads/feat/customer":
            raise GitOpsError("404")  # branch does not exist yet
        if path == "/git/refs":
            assert body["ref"] == "refs/heads/feat/customer"
            assert body["sha"] == "new-commit-sha"
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(a, "_call", fake_call)
    result = a.push_artifacts(feature_branch="feat/customer", base_branch="dev", files=FILES, commit_message="msg")
    assert result.commit_sha == "new-commit-sha"
    assert result.files == 1
    assert ("POST", "/git/refs") in calls


def test_github_adapter_push_updates_an_existing_branch(monkeypatch):
    a = GitHubAdapter("https://github.com/acme/widgets", credential="tok")

    def fake_call(method, path, body=None):
        if path == "/git/ref/heads/dev":
            return {"object": {"sha": "base-sha"}}
        if path == "/git/commits/base-sha":
            return {"tree": {"sha": "base-tree-sha"}}
        if path == "/git/blobs":
            return {"sha": "blob-sha"}
        if path == "/git/trees":
            return {"sha": "new-tree-sha"}
        if path == "/git/commits":
            return {"sha": "new-commit-sha"}
        if path == "/git/ref/heads/feat/customer":
            return {"object": {"sha": "old-commit-sha"}}  # branch already exists
        if path == "/git/refs/heads/feat/customer":
            assert method == "PATCH"
            assert body == {"sha": "new-commit-sha", "force": True}
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(a, "_call", fake_call)
    result = a.push_artifacts(feature_branch="feat/customer", base_branch="dev", files=FILES, commit_message="msg")
    assert result.commit_sha == "new-commit-sha"


def test_github_adapter_open_pull_request(monkeypatch):
    a = GitHubAdapter("https://github.com/acme/widgets", credential="tok")

    def fake_call(method, path, body=None):
        if path == "/pulls":
            assert body["head"] == "feat/customer"
            assert body["base"] == "dev"
            return {"number": 42, "html_url": "https://github.com/acme/widgets/pull/42"}
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(a, "_call", fake_call)
    pr = a.open_pull_request(source_branch="feat/customer", target_branch="dev", title="t", body="b")
    assert pr.number == 42
    assert pr.url == "https://github.com/acme/widgets/pull/42"


def test_github_adapter_pull_request_reviewer_failure_is_best_effort(monkeypatch):
    a = GitHubAdapter("https://github.com/acme/widgets", credential="tok")

    def fake_call(method, path, body=None):
        if path == "/pulls":
            return {"number": 1, "html_url": "https://github.com/acme/widgets/pull/1"}
        if path == "/pulls/1/requested_reviewers":
            raise GitOpsError("reviewer does not exist")
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(a, "_call", fake_call)
    pr = a.open_pull_request(source_branch="feat/x", target_branch="dev", title="t", body="b", reviewers=["nobody"])
    assert pr.number == 1  # PR still created even though the reviewer call failed


# ---------------------------------------------------------------------------
# AzureReposAdapter
# ---------------------------------------------------------------------------


def test_azure_repos_adapter_parses_org_project_repo():
    a = AzureReposAdapter("https://dev.azure.com/acme/widgets/_git/adf-artifacts", credential="tok")
    assert (a.org, a.project, a.repo) == ("acme", "widgets", "adf-artifacts")


def test_azure_repos_adapter_rejects_an_unparseable_url():
    with pytest.raises(GitOpsError):
        AzureReposAdapter("https://example.com/not/azure/repos", credential="tok")


def test_azure_repos_adapter_push_uses_add_for_a_new_file(monkeypatch):
    a = AzureReposAdapter("https://dev.azure.com/acme/widgets/_git/adf-artifacts", credential="tok")

    def fake_call(method, path, body=None):
        if path.startswith("/refs?filter=heads/feat/customer"):
            return {"value": []}  # branch does not exist yet
        if path.startswith("/refs?filter=heads/dev"):
            return {"value": [{"name": "refs/heads/dev", "objectId": "base-object-id"}]}
        if path.startswith("/items?"):
            assert "versionDescriptor.version=dev" in path  # probed on the base the new branch starts from
            raise GitOpsError("Azure Repos GET /items failed (404): not found")
        if path == "/pushes":
            change = body["commits"][0]["changes"][0]
            assert change["changeType"] == "add"
            assert body["refUpdates"][0]["oldObjectId"] == "base-object-id"
            return {"commits": [{"commitId": "new-commit-id"}]}
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(a, "_call", fake_call)
    result = a.push_artifacts(feature_branch="feat/customer", base_branch="dev", files=FILES, commit_message="msg")
    assert result.commit_sha == "new-commit-id"


def test_azure_repos_adapter_push_uses_edit_for_an_existing_file(monkeypatch):
    a = AzureReposAdapter("https://dev.azure.com/acme/widgets/_git/adf-artifacts", credential="tok")

    def fake_call(method, path, body=None):
        if path.startswith("/refs?filter=heads/feat/customer"):
            return {"value": [{"name": "refs/heads/feat/customer", "objectId": "existing-object-id"}]}
        if path.startswith("/items?"):
            return {"objectId": "blob-sha"}  # the file already exists on the feature branch
        if path == "/pushes":
            change = body["commits"][0]["changes"][0]
            assert change["changeType"] == "edit"
            assert body["refUpdates"][0]["oldObjectId"] == "existing-object-id"
            return {"commits": [{"commitId": "new-commit-id"}]}
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(a, "_call", fake_call)
    result = a.push_artifacts(feature_branch="feat/customer", base_branch="dev", files=FILES, commit_message="msg")
    assert result.commit_sha == "new-commit-id"


def test_azure_repos_adapter_push_fails_when_base_branch_is_missing(monkeypatch):
    a = AzureReposAdapter("https://dev.azure.com/acme/widgets/_git/adf-artifacts", credential="tok")
    monkeypatch.setattr(a, "_call", lambda method, path, body=None: {"value": []})
    with pytest.raises(GitOpsError):
        a.push_artifacts(feature_branch="feat/customer", base_branch="dev", files=FILES, commit_message="msg")


def test_azure_repos_adapter_open_pull_request(monkeypatch):
    a = AzureReposAdapter("https://dev.azure.com/acme/widgets/_git/adf-artifacts", credential="tok")

    def fake_call(method, path, body=None):
        if path == "/pullrequests":
            assert body["sourceRefName"] == "refs/heads/feat/customer"
            assert body["targetRefName"] == "refs/heads/dev"
            return {"pullRequestId": 7}
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(a, "_call", fake_call)
    pr = a.open_pull_request(source_branch="feat/customer", target_branch="dev", title="t", body="b")
    assert pr.number == 7
    assert pr.url.endswith("/pullrequest/7")


# ---------------------------------------------------------------------------
# GitLabAdapter
# ---------------------------------------------------------------------------


def test_gitlab_adapter_parses_host_and_nested_project_path():
    a = GitLabAdapter("https://gitlab.example.com/group/subgroup/widgets.git", credential="tok")
    assert a.host == "https://gitlab.example.com"
    assert a.project_path == "group/subgroup/widgets"


def test_gitlab_adapter_rejects_an_unparseable_url():
    with pytest.raises(GitOpsError):
        GitLabAdapter("not-a-url", credential="tok")


def test_gitlab_adapter_guards_branches_before_any_network_call(monkeypatch):
    a = GitLabAdapter("https://gitlab.com/acme/widgets", credential="tok")

    def _fail_call(*args, **kwargs):
        raise AssertionError("should not have made an HTTP call")

    monkeypatch.setattr(a, "_call", _fail_call)
    with pytest.raises(ProtectedBranchError):
        a.push_artifacts(feature_branch="dev", base_branch="dev", files=FILES, commit_message="msg")
    with pytest.raises(GitOpsError):
        a.push_artifacts(feature_branch="feat/x", base_branch="dev", files=[], commit_message="msg")


def test_gitlab_adapter_push_starts_a_new_branch_off_base_when_it_does_not_exist(monkeypatch):
    a = GitLabAdapter("https://gitlab.com/acme/widgets", credential="tok")

    def fake_call(method, path, body=None):
        if path == "/repository/branches/feat%2Fcustomer":
            return None  # branch does not exist yet
        if path.startswith("/repository/files/"):
            return None  # none of the files exist on the base branch either
        if path == "/repository/commits":
            assert body["branch"] == "feat/customer"
            assert body["start_branch"] == "dev"
            assert all(a["action"] == "create" for a in body["actions"])
            return {"id": "new-commit-sha"}
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(a, "_call", fake_call)
    result = a.push_artifacts(feature_branch="feat/customer", base_branch="dev", files=FILES, commit_message="msg")
    assert result.commit_sha == "new-commit-sha"
    assert result.files == 1


def test_gitlab_adapter_push_updates_an_existing_branch_without_start_branch(monkeypatch):
    a = GitLabAdapter("https://gitlab.com/acme/widgets", credential="tok")

    def fake_call(method, path, body=None):
        if path == "/repository/branches/feat%2Fcustomer":
            return {"name": "feat/customer"}  # branch already exists
        if path.startswith("/repository/files/"):
            return {"file_path": "adf/artifacts/PL_x.json"}  # file already exists on that branch
        if path == "/repository/commits":
            assert "start_branch" not in body
            assert all(a["action"] == "update" for a in body["actions"])
            return {"id": "new-commit-sha"}
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(a, "_call", fake_call)
    result = a.push_artifacts(feature_branch="feat/customer", base_branch="dev", files=FILES, commit_message="msg")
    assert result.commit_sha == "new-commit-sha"


def test_gitlab_adapter_open_merge_request(monkeypatch):
    a = GitLabAdapter("https://gitlab.com/acme/widgets", credential="tok")

    def fake_call(method, path, body=None):
        if path == "/merge_requests":
            assert body["source_branch"] == "feat/customer"
            assert body["target_branch"] == "dev"
            return {"iid": 9, "web_url": "https://gitlab.com/acme/widgets/-/merge_requests/9"}
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(a, "_call", fake_call)
    pr = a.open_pull_request(source_branch="feat/customer", target_branch="dev", title="t", body="b")
    assert pr.number == 9
    assert pr.url.endswith("/merge_requests/9")


def test_gitlab_adapter_reviewer_lookup_failure_is_best_effort(monkeypatch):
    a = GitLabAdapter("https://gitlab.com/acme/widgets", credential="tok")

    def fake_call(method, path, body=None):
        if path == "/merge_requests":
            return {"iid": 1, "web_url": "https://gitlab.com/acme/widgets/-/merge_requests/1"}
        raise AssertionError(f"unexpected call {method} {path}")

    def fake_request(url, method, body):
        raise GitOpsError("user lookup failed")

    monkeypatch.setattr(a, "_call", fake_call)
    monkeypatch.setattr(a, "_request", fake_request)
    pr = a.open_pull_request(source_branch="feat/x", target_branch="dev", title="t", body="b", reviewers=["nobody"])
    assert pr.number == 1  # MR still created even though reviewer resolution failed
