"""Scenario tests — §19 Git / round-trip / CI-CD edge cases
(docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md). `test_git.py` already covers real
push-time behavior for real — see it for fixtures/conventions. One test per
scenario ID (GIT-01..GIT-10).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_git_04_push_endpoint_does_not_re_validate_every_artifact_before_accepting(
    client: TestClient, seed: dict[str, str]
) -> None:
    """GIT-04 [VERIFY]: documents a real, confirmed finding that's more
    nuanced than the scenario assumes — `request_git_push`
    (app/services.py) only checks (a) at least one artifact is selected and
    (b) the project's *opt-in* block-level naming convention (off by
    default). It does not re-run full ADF-artifact-set validation
    (worker/app/validator/rules.py's validate_artifacts) before accepting a
    push — that only happens in the separate `validate` job. So a "1 of 9
    artifacts is ADF-invalid" push is NOT rejected pre-write by this
    endpoint today unless a naming convention happens to be configured and
    set to block. Confirmed by reading the code, not by reproducing the
    seeded fixture's content (which is trivially valid `{}` JSON)."""
    res = client.post("/api/v1/git/push", json={"pipelineId": seed["pipeline_id"], "featureBranch": "feature/git-04"})
    assert res.status_code == 202  # accepted — no cross-artifact validation gate ran


@pytest.mark.skip(reason="GIT-01/[VERIFY]: real, worker-only. Real coverage: worker/tests/"
                          "test_scenario_coverage_extra.py::test_git_01_regenerating_an_unchanged_draft_is_byte_identical.")
def test_git_01_regenerating_unchanged_pipeline_is_byte_identical():
    pass


@pytest.mark.skip(reason="GIT-02/[VERIFY]: no drift-detection/banner concept (comparing a re-generated artifact's "
                          "hash against the repo's current hash) exists anywhere in backend/app — confirmed real "
                          "[GAP], no code path.")
def test_git_02_hand_edited_artifact_reopen_shows_drift_banner():
    pass


@pytest.mark.skip(reason="GIT-03/[VERIFY]: no 'foreign / no-stamp artifact' concept exists anywhere in "
                          "backend/app/models.py's Artifact model (no stamp/ownership field at all) — confirmed "
                          "real [GAP], every artifact is treated as tool-owned.")
def test_git_03_foreign_no_stamp_artifact_round_trips_without_reshaping():
    pass


@pytest.mark.skip(reason="GIT-05/[VERIFY]: real and already covered for real, not by this stub — see "
                          "test_git.py::test_git_push_rejects_protected_branch, which is the exact scenario "
                          "(feature branch == base branch 'dev' is rejected server-side with 422 protected_branch).")
def test_git_05_direct_push_to_dev_via_api_is_rejected_server_side():
    pass


@pytest.mark.skip(reason="GIT-06/[VERIFY]: force-push re-validation against the new head is a GitHub/provider-side "
                          "status-check concern (the actual provider calls run in the worker's git_push job, per "
                          "app/api/git.py's own docstring) — no status-check re-run logic exists in backend/app "
                          "to unit-test.")
def test_git_06_force_pushed_branch_reruns_status_check_against_new_head():
    pass


@pytest.mark.skip(reason="GIT-07/[VERIFY]: partially real — Artifact rows carry a UniqueConstraint on "
                          "(pipeline_id, file_name, version), so distinct versions genuinely coexist as separate "
                          "rows (confirmed: backend/app/models.py). Whether an existing version row is ever "
                          "mutated in place (true immutability) depends on the artifact-generation job's own "
                          "write path, which lives in worker/app/jobs, out of this file's reach.")
def test_git_07_every_generated_artifact_version_is_retained_immutably():
    pass


@pytest.mark.skip(reason="GIT-08/[VERIFY]: same root cause as GIT-06 — required-status-check scoping (only "
                          "re-validate changed artifacts) is a GitHub-side concern with no corresponding backend "
                          "code to unit-test.")
def test_git_08_workday_only_pr_does_not_revalidate_other_seven_sources():
    pass


def test_git_09_concurrent_edits_to_same_pipeline_conflict_not_last_write_wins(
    client: TestClient, seed: dict[str, str]
) -> None:
    """GIT-09 [VERIFY]: two editors load the same pipeline; the second save, built on the stale copy,
    409s `version_conflict` (§24.14 `expectedUpdatedAt`) instead of silently overwriting the first."""
    url = f"/api/v1/pipelines/{seed['pipeline_id']}"
    loaded = client.get(url).json()["updatedAt"]
    assert client.put(url, json={"draft": {"curatedEntity": "editor_a"}, "expectedUpdatedAt": loaded}).status_code == 200
    second = client.put(url, json={"draft": {"curatedEntity": "editor_b"}, "expectedUpdatedAt": loaded})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "version_conflict"
    assert client.get(url).json()["draft"]["curatedEntity"] == "editor_a"


@pytest.mark.skip(reason="GIT-10/[VERIFY]: confirmed by reading app/api/schemas.py's GitPushIn — `featureBranch` "
                          "is a plain free-text string field with no pattern/regex validator, so branch-naming "
                          "convention truly isn't enforced today, exactly as the scenario expects. Real, "
                          "reassuring (matches the doc's own claim), not yet a dedicated test.")
def test_git_10_branch_naming_convention_is_not_enforced_today():
    pass
