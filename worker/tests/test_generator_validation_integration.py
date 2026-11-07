"""Generator -> validator integration suite (AGENTS.md §29, closes part of §23.12 #20).

``test_validator.py`` proves each validation rule works in isolation, against
hand-written JSON snippets. ``test_generator_parity.py`` proves the frontend
and worker generators agree with each other. Neither proves the thing a user
actually cares about: that when a *real* pipeline is generated — the full
9(+)-artifact set (linked services, datasets, the master/child pipeline,
trigger, factory parameters, control-table SQL) — the validator that runs
right after Generate actually passes it when it's clean and actually catches
it when something is wrong.

This suite closes that gap by running real ``SpecDraft`` fixtures through the
real generator (``build_spec`` + ``build_generated_artifacts``, the same calls
``app/jobs/runner.py`` makes) and then through the real validator
(``run_validation``), for every artifact type the generator emits — not just
the pipeline JSON.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from app.generator.codegen import GeneratedArtifact, build_generated_artifacts
from app.generator.spec import SpecDraft, build_spec
from app.validator.rules import (
    ARTIFACT_NAME_RE,
    run_validation,
    validate_artifacts,
    validate_key_vault_linked_service,
    validate_spec,
)

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# One fixture per pipeline template — the same set `test_generator_parity.py`
# uses, so this suite exercises every generation strategy the Build wizard
# offers, not just one.
_CLEAN_FIXTURES = [
    "spec_draft_full.json",
    "spec_draft_incremental.json",
    "spec_draft_initial.json",
    "spec_draft_composite_watermark.json",
    "spec_draft_metadata_driven.json",
]

# Every artifact type `build_generated_artifacts` emits, regardless of
# template (§23.4a's 9-artifact output set) — used to assert nothing silently
# drops an artifact type out of the set this suite validates.
_ALWAYS_EMITTED_ARTIFACT_TYPES = {"linked_service", "dataset", "pipeline", "trigger", "factory", "parameter"}


def _payload(fixture_name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES_DIR / fixture_name).read_text(encoding="utf-8"))


def _draft(fixture_name: str, **overrides: Any) -> SpecDraft:
    data = _payload(fixture_name)
    data.update(overrides)
    return SpecDraft.from_dict(data)


def _generate(draft: SpecDraft) -> tuple[dict[str, Any], list[GeneratedArtifact]]:
    return build_spec(draft), build_generated_artifacts(draft)


def _find(artifacts: list[GeneratedArtifact], *, artifact_type: str, name_contains: str = "") -> GeneratedArtifact:
    for artifact in artifacts:
        if artifact.artifact_type == artifact_type and name_contains in artifact.file_name:
            return artifact
    raise AssertionError(f"no {artifact_type} artifact containing {name_contains!r} in {[a.file_name for a in artifacts]}")


def _tampered(artifact: GeneratedArtifact, **changes: Any) -> GeneratedArtifact:
    """A modified copy of a real generated artifact — used to simulate a
    corruption/regression rather than hand-writing a fixture from scratch."""
    return dataclasses.replace(artifact, **changes)


# ---------------------------------------------------------------------------
# Baseline: a well-formed draft's real generated artifact set is clean, for
# every template and every artifact type it emits.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_name", _CLEAN_FIXTURES)
def test_a_well_formed_draft_generates_artifacts_that_pass_every_layer(fixture_name: str):
    draft = _draft(fixture_name)
    spec, artifacts = _generate(draft)
    report = run_validation(
        spec,
        artifacts,
        include_git=True,
        repository_url="https://github.com/acme/adf-repo",
        feature_branch=f"feature/{draft.name}",
        base_branch="dev",
    )
    assert report.ok, [i.to_dict() for i in report.issues]
    assert report.state == "passed"


@pytest.mark.parametrize("fixture_name", _CLEAN_FIXTURES)
def test_every_artifact_type_the_generator_emits_is_present_and_individually_clean(fixture_name: str):
    draft = _draft(fixture_name)
    _, artifacts = _generate(draft)
    types_present = {a.artifact_type for a in artifacts}
    assert _ALWAYS_EMITTED_ARTIFACT_TYPES.issubset(types_present)
    # adf_control_table.sql — the "other artifact" that isn't JSON at all —
    # must also pass the secret scan / file-name rules validate_artifacts runs
    # against every artifact regardless of file_type.
    control_table_sql = _find(artifacts, artifact_type="parameter", name_contains=".sql")
    assert control_table_sql.file_type == "sql"
    assert validate_artifacts([control_table_sql]) == []
    assert validate_artifacts(artifacts) == []


# ---------------------------------------------------------------------------
# A bad spec is caught before/alongside generation (§17.1-4), on real
# artifacts rather than a hand-typed spec dict.
# ---------------------------------------------------------------------------


def test_a_pipeline_name_with_illegal_characters_is_caught_at_both_the_spec_and_artifact_layer():
    # Real UI flow blocks this at `validateDraft`/`validate_draft` before
    # Generate is even reachable — this proves that if it ever did reach the
    # generator (a bypass, an older client, a direct API call), both
    # downstream layers still catch it independently, not just the one at the
    # front door.
    draft = _draft("spec_draft_incremental.json", name="customer-incremental!")
    spec, artifacts = _generate(draft)

    spec_issues = validate_spec(spec)
    assert any(i.field == "name" for i in spec_issues)

    master_pipeline = _find(artifacts, artifact_type="pipeline", name_contains="_master")
    artifact_issues = validate_artifacts([master_pipeline])
    assert any("violates the configured naming rule" in i.message for i in artifact_issues)
    assert not ARTIFACT_NAME_RE.match(json.loads(master_pipeline.content)["name"])


def test_a_draft_missing_its_target_path_fails_validate_spec_even_though_generation_still_runs():
    # `SpecDraft.from_dict` fills in sensible fallbacks for most fields once a
    # value is missing (e.g. an unset watermark table defaults to
    # "control.watermarks") — target path is one of the few that doesn't, so
    # this is a genuinely reachable "the user left a required field blank"
    # case, not a synthetic one.
    draft = _draft("spec_draft_full.json", targetPath="")
    spec, artifacts = _generate(draft)
    # The generator itself is permissive (it has to be, to let a user see
    # what an incomplete draft would produce before fixing it) — validation
    # is where the incomplete config actually gets flagged.
    assert artifacts  # generation didn't crash
    issues = validate_spec(spec)
    assert any(i.field == "target.path" for i in issues)
    report = run_validation(spec, artifacts)
    assert not report.ok


def test_an_unknown_template_id_on_a_real_draft_is_rejected():
    # The Build wizard blocks this before Generate is reachable
    # (`validate_draft`/`validateDraft` check the template exists) — this
    # proves `validate_spec` independently catches it too, against a real
    # generated artifact set, if that front-door check is ever bypassed.
    draft = _draft("spec_draft_full.json", templateId="not_a_real_template")
    spec, artifacts = _generate(draft)
    assert artifacts  # the generator doesn't gate on template existence itself
    issues = validate_spec(spec)
    assert any(i.field == "templateId" for i in issues)


# ---------------------------------------------------------------------------
# Tampered real artifacts — proves detection against actual generator output,
# not just synthetic content, for artifact types beyond the pipeline JSON.
# ---------------------------------------------------------------------------


def test_a_hardcoded_secret_injected_into_a_real_generated_dataset_is_flagged():
    draft = _draft("spec_draft_incremental.json")
    _, artifacts = _generate(draft)
    dataset = _find(artifacts, artifact_type="dataset", name_contains="source")
    body = json.loads(dataset.content)
    # Simulate a regression: something writes a literal connection string
    # into an annotation instead of going through the connector's
    # AzureKeyVaultSecret path.
    body["properties"]["annotations"] = ["Server=x;Database=y;Password=Sup3rSecret!;"]
    tampered = _tampered(dataset, content=json.dumps(body))

    assert validate_artifacts([dataset]) == []  # the real artifact is clean
    issues = validate_artifacts([tampered])
    assert any("hardcoded secret" in i.message.lower() for i in issues)


def test_a_hardcoded_secret_in_the_real_control_table_sql_is_flagged():
    draft = _draft("spec_draft_metadata_driven.json")
    _, artifacts = _generate(draft)
    control_table_sql = _find(artifacts, artifact_type="parameter", name_contains=".sql")
    tampered = _tampered(control_table_sql, content=control_table_sql.content + "\n-- Password=hunter2hunter2;\n")
    issues = validate_artifacts([tampered])
    assert any("hardcoded secret" in i.message.lower() for i in issues)


def test_a_renamed_dataset_artifact_that_breaks_the_declared_name_is_flagged():
    draft = _draft("spec_draft_full.json")
    _, artifacts = _generate(draft)
    dataset = _find(artifacts, artifact_type="dataset", name_contains="target")
    body = json.loads(dataset.content)
    body["name"] = "DS target with spaces!"
    tampered = _tampered(dataset, content=json.dumps(body))
    issues = validate_artifacts([tampered])
    assert any("violates the configured naming rule" in i.message for i in issues)


def test_corrupted_json_in_a_real_generated_linked_service_is_flagged():
    draft = _draft("spec_draft_initial.json")
    _, artifacts = _generate(draft)
    linked_service = _find(artifacts, artifact_type="linked_service", name_contains="source")
    tampered = _tampered(linked_service, content=linked_service.content.rstrip() + "}")  # unbalanced brace
    issues = validate_artifacts([tampered])
    assert any("invalid" in i.message.lower() for i in issues)


def test_a_path_like_file_name_on_a_real_artifact_is_rejected():
    draft = _draft("spec_draft_full.json")
    _, artifacts = _generate(draft)
    trigger = _find(artifacts, artifact_type="trigger")
    tampered = _tampered(trigger, file_name=f"../{trigger.file_name}")
    issues = validate_artifacts([tampered])
    assert any("bare file name" in i.message for i in issues)


# ---------------------------------------------------------------------------
# Key Vault cross-reference (IMPLEMENTATION_PLAN.md A-3) against a real
# secret-bearing linked service, not a hand-written one.
# ---------------------------------------------------------------------------


def _draft_with_kv_backed_password(**kv_overrides: Any) -> SpecDraft:
    return _draft(
        "spec_draft_incremental.json",
        sourceLinkedService={
            "name": "LS_sql_server_source",
            "connectorType": "sql_server",
            "authType": "sql_authentication",
            "fields": {"userName": "svc_etl", "password": "sqlserver-password"},
            **kv_overrides,
        },
    )


def test_warns_when_the_real_pipelines_key_vault_linked_service_is_not_in_the_push_selection():
    draft = _draft_with_kv_backed_password()  # defaults to LS_azure_key_vault, never generated
    _, artifacts = _generate(draft)
    source_ls = _find(artifacts, artifact_type="linked_service", name_contains="source")
    assert "AzureKeyVaultSecret" in source_ls.content

    warnings = validate_key_vault_linked_service(artifacts)
    assert len(warnings) == 1
    assert warnings[0].severity == "warning"
    assert "LS_azure_key_vault" in warnings[0].message


def test_no_warning_once_the_referenced_key_vault_linked_service_is_actually_generated():
    # Point the draft's own source linked service at itself as the KV store —
    # contrived, but it proves the "already generated" branch works against a
    # real artifact set, not just a hand-built list of names.
    draft = _draft_with_kv_backed_password(keyVaultLinkedService="LS_sql_server_source")
    _, artifacts = _generate(draft)
    assert validate_key_vault_linked_service(artifacts) == []


# ---------------------------------------------------------------------------
# §25 opt-in naming standard, enforced against a real generated pipeline name.
# ---------------------------------------------------------------------------


def test_a_block_level_naming_standard_fails_a_real_generated_pipeline_that_lacks_the_required_prefix():
    draft = _draft("spec_draft_full.json")
    _, artifacts = _generate(draft)
    pipelines = [a for a in artifacts if a.artifact_type == "pipeline"]
    convention = {"enforcement": "block", "rules": {"pipeline": {"prefix": "PL_ACME_"}}}
    issues = validate_artifacts(pipelines, naming_convention=convention)
    assert len(issues) == len(pipelines)
    assert all(i.severity == "error" and "Naming standard" in i.message for i in issues)


# ---------------------------------------------------------------------------
# Git rules (§17.7-8 / §23.6), exercised alongside a real generated set.
# ---------------------------------------------------------------------------


def test_run_validation_blocks_a_push_to_the_protected_base_branch_even_when_artifacts_are_clean():
    draft = _draft("spec_draft_incremental.json")
    spec, artifacts = _generate(draft)
    report = run_validation(
        spec,
        artifacts,
        include_git=True,
        repository_url="https://github.com/acme/adf-repo",
        feature_branch="dev",
        base_branch="dev",
    )
    assert not report.ok
    assert any("protected base branch" in i.message for i in report.issues)
