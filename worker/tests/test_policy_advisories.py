"""§29.1 layer 5 — advisory `runtime.retry`/`docs.description` checks at Validate time."""

import json

from app.generator.codegen import GeneratedArtifact, build_generated_artifacts
from app.generator.spec import SpecDraft
from app.validator.rules import run_validation, validate_policy_advisories


def _artifact(file_name: str, artifact_type: str, doc: dict) -> GeneratedArtifact:
    return GeneratedArtifact(file_name=file_name, artifact_type=artifact_type, layer="pipeline",
                             content=json.dumps(doc), file_type="json")


def test_flags_copy_without_retry_and_missing_description():
    doc = {"name": "PL_x", "properties": {"activities": [
        {"name": "CopyA", "type": "Copy", "policy": {"retry": 0}},
        {"name": "CopyB", "type": "Copy", "policy": {"retry": 2}},
    ]}}
    issues = validate_policy_advisories([_artifact("PL_x.json", "pipeline", doc)])
    assert {i.rule_id for i in issues} == {"runtime.retry", "docs.description"}
    assert all(i.severity == "warning" and i.layer == "policy" for i in issues)
    assert any("'CopyA'" in i.message for i in issues)
    assert not any("'CopyB'" in i.message for i in issues)


def test_described_resource_with_retry_is_clean_and_other_types_skipped():
    doc = {"name": "PL_x", "properties": {"description": "Loads orders.",
                                          "activities": [{"name": "C", "type": "Copy", "policy": {"retry": 1}}]}}
    trigger = {"name": "TR_x", "properties": {"type": "ScheduleTrigger"}}
    assert validate_policy_advisories([_artifact("PL_x.json", "pipeline", doc),
                                       _artifact("TR_x.json", "trigger", trigger)]) == []


def test_generated_artifacts_carry_their_own_descriptions():
    artifacts = build_generated_artifacts(SpecDraft.from_dict({"name": "orders"}))
    assert [i for i in validate_policy_advisories(artifacts) if i.rule_id == "docs.description"] == []


def test_policy_advisories_are_warnings_only():
    artifacts = build_generated_artifacts(SpecDraft.from_dict({"name": "orders", "retryCount": 0}))
    policy = [i for i in run_validation({}, artifacts).issues if i.rule_id in {"runtime.retry", "docs.description"}]
    assert all(i.severity == "warning" and i.layer == "policy" for i in policy)
