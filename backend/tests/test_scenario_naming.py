"""Scenario tests — §11 ADF hard naming-rule edge cases
(docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md). One test per scenario ID
(NAME-01..NAME-12).

Important correction vs. this file's original stub docstring: the always-on
ADF *hard* rule (no hyphen/space/digit-leading-char, 128-char cap —
`ARTIFACT_NAME_RE` in `worker/app/validator/rules.py`) is **not** the same
thing as `backend/app/standards.py`'s `evaluate_name` — that only evaluates
the opt-in project *naming convention* (prefix/suffix/case/maxLength,
default "off"). Backend vendors the naming-convention evaluator and the
connector registry only, not the generator/hard-rule validator (see
DEVELOPMENT_NOTES.md's parity note), so:
  - scenarios about the always-on hard rule (NAME-01, 02, 03, 05, 07, 08) are
    tested for real in `worker/tests/test_scenario_coverage_extra.py`, not
    here — they're left skipped below with a pointer, not faked;
  - scenarios about the opt-in naming *convention* and its enforcement at
    push time (NAME-04, NAME-09) are real, backend-testable code
    (`app/standards.py.evaluate_name`, `app/services.py._enforce_naming_standard`)
    and are wired up for real below.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.standards import evaluate_name


def _set_naming_convention(db: Session, project_id: str, convention: dict) -> None:
    settings = db.query(models.ProjectSettings).filter(models.ProjectSettings.project_id == project_id).one()
    settings.document = {**settings.document, "namingConvention": convention}
    db.commit()


def test_name_04_maxlength_naming_convention_rule_is_enforced():
    """NAME-04: a configured `maxLength` naming-convention rule blocks a name
    that exceeds it — real code, `app/standards.py.evaluate_name`."""
    convention = {"enforcement": "block", "rules": {"pipeline": {"maxLength": 40}}}
    long_name = "PL_" + ("x" * 50)
    violation = evaluate_name(long_name, "pipeline", convention)
    assert violation is not None
    assert violation.level == "block"
    assert "40 characters" in violation.message


def test_name_09_warn_level_naming_violation_never_blocks_a_push(client: TestClient, seed: dict[str, str], db: Session) -> None:
    """NAME-09: a `warn`-only project naming violation must be pushable, with
    the violation left to be surfaced at validate time (§25.6), never
    blocked at push — real code, `app/services.py._enforce_naming_standard`
    only rejects the push for `enforcement: "block"`."""
    artifact = db.query(models.Artifact).filter(models.Artifact.id == seed["artifact_id"]).one()
    artifact.content = json.dumps({"name": "not_prefixed_pipeline"})
    _set_naming_convention(db, seed["project_id"], {"enforcement": "warn", "rules": {"pipeline": {"prefix": "PL_"}}})

    res = client.post("/api/v1/git/push", json={"pipelineId": seed["pipeline_id"], "featureBranch": "feature/name-09"})
    assert res.status_code == 202


def test_block_level_naming_violation_does_reject_the_push(client: TestClient, seed: dict[str, str], db: Session) -> None:
    """Companion to NAME-09 — confirms the other half of the same real code
    path: `enforcement: "block"` does reject the push with 422
    `policy_violation`, which is what makes the NAME-09 contrast meaningful."""
    artifact = db.query(models.Artifact).filter(models.Artifact.id == seed["artifact_id"]).one()
    artifact.content = json.dumps({"name": "not_prefixed_pipeline"})
    _set_naming_convention(db, seed["project_id"], {"enforcement": "block", "rules": {"pipeline": {"prefix": "PL_"}}})

    res = client.post("/api/v1/git/push", json={"pipelineId": seed["pipeline_id"], "featureBranch": "feature/name-09b"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "policy_violation"


@pytest.mark.skip(reason="NAME-01/[VERIFY]: the always-on ADF hard-name rule (hyphen forbidden) lives only in "
                          "worker/app/validator/rules.py (ARTIFACT_NAME_RE) — backend doesn't vendor it. Real "
                          "coverage: worker/tests/test_scenario_coverage_extra.py::test_name_01_*.")
def test_name_01_hyphen_in_linked_service_name_is_blocked():
    pass


@pytest.mark.skip(reason="NAME-02/[VERIFY]: same as NAME-01 — always-on ADF hard rule, worker-only. Real "
                          "coverage: worker/tests/test_scenario_coverage_extra.py::test_name_02_*.")
def test_name_02_leading_digit_is_blocked():
    pass


@pytest.mark.skip(reason="NAME-03/[VERIFY]: same as NAME-01 — always-on ADF hard rule, worker-only. Real "
                          "coverage: worker/tests/test_scenario_coverage_extra.py::test_name_03_*.")
def test_name_03_space_in_fallback_name_is_blocked():
    pass


@pytest.mark.skip(reason="NAME-05/[VERIFY]: same as NAME-01 — always-on ADF hard rule, worker-only. Real "
                          "coverage: worker/tests/test_scenario_coverage_extra.py::test_name_05_*.")
def test_name_05_non_ascii_vendor_name_is_blocked_not_transliterated():
    pass


@pytest.mark.skip(reason="NAME-06/[VERIFY]: no catalog-uniqueness check exists anywhere in the codebase (backend "
                          "or worker) beyond the per-push regex/convention check — a case-insensitive collision "
                          "against a *different* existing artifact is not detected. Confirmed real gap, not yet "
                          "unit-testable against any real code path.")
def test_name_06_case_insensitive_catalog_collision_is_flagged():
    pass


@pytest.mark.skip(reason="NAME-07/[VERIFY]: the factory-name rule set (FACTORY_NAME_RE) lives only in "
                          "worker/app/validator/rules.py — backend-only tests can't reach it. Real coverage: "
                          "worker/tests/test_scenario_coverage_extra.py::test_name_07_*.")
def test_name_07_factory_name_rule_set_is_not_swapped_with_ls_pipeline_rules():
    pass


@pytest.mark.skip(reason="NAME-08/[VERIFY]: pipeline parameter names (derived from column headers) are never run "
                          "through ARTIFACT_NAME_RE or any other check anywhere in worker/app/generator/codegen.py "
                          "— confirmed real gap: a parameter name containing a space is generated as-is into the "
                          "child pipeline's parameters array, uncaught by validate_artifacts.")
def test_name_08_parameter_name_with_space_is_blocked():
    pass


@pytest.mark.skip(reason="NAME-10/[VERIFY]: no `lockGeneratedName` field or client/server enforcement of it exists "
                          "anywhere in the codebase (SpecDraft has no such field) — real, confirmed [GAP], not a "
                          "product decision to encode yet.")
def test_name_10_locked_generated_name_is_enforced_server_side_too():
    pass


@pytest.mark.skip(reason="NAME-11/[VERIFY]: request_git_push (app/services.py) checks naming-convention violations "
                          "only within the artifacts being pushed — there is no cross-branch or cross-PR "
                          "uniqueness check against other open feature branches. Confirmed real gap.")
def test_name_11_uniqueness_checked_against_target_branch_not_current_branch():
    pass


@pytest.mark.skip(reason="NAME-12/[DESIGN]: there is no token-based naming *pattern* feature in the codebase at "
                          "all (only fixed prefix/suffix/case/maxLength rules) — draft-level vs. Project Settings "
                          "token precedence has no code to test against.")
def test_name_12_draft_level_token_override_wins_over_project_default():
    pass
