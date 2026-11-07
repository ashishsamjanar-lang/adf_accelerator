"""§29.3 — every validator issue carries a stable ruleId, and known rules a fixHint."""

from app.generator.codegen import GeneratedArtifact
from app.validator.rules import Issue, validate_artifacts, validate_git


def _artifact(name: str, content: str) -> GeneratedArtifact:
    return GeneratedArtifact(file_name=name, file_type="json", artifact_type="linked_service", layer="linked_service", content=content)


def test_secret_issue_uses_the_standards_catalogue_id_and_a_fix_hint():
    issues = validate_artifacts([_artifact("LS_x.json", '{"name": "LS_x", "connectionString": "Server=a;Password=hunter22;"}')])
    secret = next(i.to_dict() for i in issues if i.layer == "security")
    assert secret["ruleId"] == "security.kv"
    assert "Key Vault" in secret["fixHint"]


def test_git_base_branch_rule_id():
    issues = validate_git(repository_url="https://github.com/o/r", feature_branch="dev", base_branch="dev")
    assert [i.to_dict()["ruleId"] for i in issues] == ["git.base"]


def test_issue_without_rule_id_omits_both_keys():
    out = Issue("warning", "x").to_dict()
    assert "ruleId" not in out and "fixHint" not in out
