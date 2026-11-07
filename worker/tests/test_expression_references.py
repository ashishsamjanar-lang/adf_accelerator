"""§29.1 layer 3 — pipeline/dataset/linked-service expressions must name declared parameters/variables."""

import json
from pathlib import Path

import pytest

from app.generator.codegen import GeneratedArtifact, build_generated_artifacts
from app.generator.spec import SpecDraft
from app.validator.rules import validate_expression_references


def _artifact(name: str, artifact_type: str, doc: dict) -> GeneratedArtifact:
    return GeneratedArtifact(file_name=name, file_type="json", artifact_type=artifact_type, layer=artifact_type, content=json.dumps(doc))


def test_undeclared_pipeline_parameter_and_variable_are_errors():
    doc = {"name": "PL_x", "properties": {
        "parameters": {"TaskId": {"type": "Int"}},
        "variables": {"rows": {"type": "String"}},
        "activities": [{"name": "A", "type": "Wait", "typeProperties": {
            "x": "@pipeline().parameters.TaskId", "y": "@pipeline().parameters.Missing",
            "z": "@variables('rows')", "w": "@variables('nope')"}}]}}
    issues = validate_expression_references([_artifact("PL_x.json", "pipeline", doc)])
    assert [(i.severity, i.rule_id) for i in issues] == [("error", "semantic.expression")] * 2
    assert "'Missing'" in issues[0].message and "'nope'" in issues[1].message
    assert issues[0].to_dict()["fixHint"]



def test_dataset_and_linked_service_scopes():
    ds = {"name": "DS_x", "properties": {"parameters": {"Table": {"type": "String"}}, "typeProperties": {"table": "@dataset().Table", "schema": "@dataset().Schema"}}}
    ls = {"name": "LS_x", "properties": {"typeProperties": {"server": "@{linkedService().Server}"}}}
    issues = validate_expression_references([_artifact("DS_x.json", "dataset", ds), _artifact("LS_x.json", "linked_service", ls)])
    assert sorted((i.source, i.message.split("'")[1]) for i in issues) == [("DS_x.json", "Schema"), ("LS_x.json", "Server")]


def test_selection_filters_sources():
    doc = {"name": "PL_x", "properties": {"activities": [], "description": "@pipeline().parameters.Missing"}}
    assert validate_expression_references([_artifact("PL_x.json", "pipeline", doc)], selected=["other.json"]) == []


@pytest.mark.parametrize("fixture", sorted(Path(__file__).parent.glob("fixtures/spec_draft_*.json")), ids=lambda p: p.stem)
def test_every_generated_fixture_is_clean(fixture):
    with open(fixture, encoding="utf-8") as fh:
        artifacts = build_generated_artifacts(SpecDraft.from_dict(json.load(fh)))
    assert validate_expression_references(artifacts) == []
