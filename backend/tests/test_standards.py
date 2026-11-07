"""Unit tests for app/standards.py — the naming-standard evaluator (§25.3, §25.6).

``test_git.py`` exercises this only indirectly, through the push-time
``policy_violation`` gate, and only ever with a "missing prefix" case. This
file tests the pure functions directly: every enforcement level, every rule
dimension (prefix/suffix/maxLength/case), and the case patterns themselves —
none of which had a unit test before.
"""
from __future__ import annotations

from app.standards import (
    ARTIFACT_TYPE_KEYS,
    build_expected_name,
    evaluate_artifact_names,
    evaluate_name,
    is_configured,
)


def test_is_configured_is_false_for_no_rule():
    assert is_configured(None) is False
    assert is_configured({}) is False


def test_is_configured_is_false_for_case_none():
    assert is_configured({"case": "none"}) is False


def test_is_configured_is_true_when_any_dimension_is_set():
    assert is_configured({"prefix": "PL_"}) is True
    assert is_configured({"suffix": "_v1"}) is True
    assert is_configured({"maxLength": 64}) is True
    assert is_configured({"case": "snake"}) is True


def test_build_expected_name_wraps_prefix_and_suffix():
    assert build_expected_name("customer", {"prefix": "PL_", "suffix": "_v1"}) == "PL_customer_v1"
    assert build_expected_name("customer", None) == "customer"


def test_evaluate_name_returns_none_when_enforcement_is_off():
    convention = {"enforcement": "off", "rules": {"pipeline": {"prefix": "PL_"}}}
    assert evaluate_name("customer_load", "pipeline", convention) is None


def test_evaluate_name_returns_none_when_no_convention_is_given():
    assert evaluate_name("customer_load", "pipeline", None) is None


def test_evaluate_name_returns_none_when_the_type_has_no_rule_configured():
    convention = {"enforcement": "block", "rules": {"dataset": {"prefix": "DS_"}}}
    assert evaluate_name("customer_load", "pipeline", convention) is None


def test_evaluate_name_flags_a_missing_prefix_as_block():
    convention = {"enforcement": "block", "rules": {"pipeline": {"prefix": "PL_"}}}
    violation = evaluate_name("customer_load", "pipeline", convention)
    assert violation is not None
    assert violation.level == "block"
    assert "must start with" in violation.message


def test_evaluate_name_flags_a_missing_prefix_as_warn_under_warn_enforcement():
    convention = {"enforcement": "warn", "rules": {"pipeline": {"prefix": "PL_"}}}
    violation = evaluate_name("customer_load", "pipeline", convention)
    assert violation is not None
    assert violation.level == "warn"


def test_evaluate_name_passes_a_compliant_name():
    convention = {"enforcement": "block", "rules": {"pipeline": {"prefix": "PL_"}}}
    assert evaluate_name("PL_customer_load", "pipeline", convention) is None


def test_evaluate_name_checks_suffix():
    convention = {"enforcement": "block", "rules": {"dataset": {"suffix": "_ds"}}}
    violation = evaluate_name("customer", "dataset", convention)
    assert violation is not None
    assert "must end with" in violation.message
    assert evaluate_name("customer_ds", "dataset", convention) is None


def test_evaluate_name_checks_max_length():
    convention = {"enforcement": "block", "rules": {"trigger": {"maxLength": 5}}}
    violation = evaluate_name("too_long_name", "trigger", convention)
    assert violation is not None
    assert "at most 5 characters" in violation.message
    assert evaluate_name("short", "trigger", convention) is None


def test_evaluate_name_checks_snake_case():
    convention = {"enforcement": "block", "rules": {"linkedService": {"case": "snake"}}}
    assert evaluate_name("ls_sql_server", "linkedService", convention) is None
    violation = evaluate_name("LS_SqlServer", "linkedService", convention)
    assert violation is not None
    assert "must be snake case" in violation.message


def test_evaluate_name_checks_pascal_case_ignoring_prefix_and_suffix():
    convention = {
        "enforcement": "block",
        "rules": {"pipeline": {"prefix": "PL_", "suffix": "_v1", "case": "pascal"}},
    }
    assert evaluate_name("PL_CustomerLoad_v1", "pipeline", convention) is None
    violation = evaluate_name("PL_customerLoad_v1", "pipeline", convention)
    assert violation is not None
    assert "must be pascal case" in violation.message


def test_evaluate_name_treats_a_blank_name_as_nothing_to_report():
    convention = {"enforcement": "block", "rules": {"pipeline": {"prefix": "PL_"}}}
    assert evaluate_name("   ", "pipeline", convention) is None
    assert evaluate_name("", "pipeline", convention) is None


def test_evaluate_artifact_names_batches_multiple_pairs():
    convention = {
        "enforcement": "block",
        "rules": {"pipeline": {"prefix": "PL_"}, "dataset": {"prefix": "DS_"}},
    }
    violations = evaluate_artifact_names(
        [("pipeline", "PL_customer_load"), ("pipeline", "customer_load"), ("dataset", "customer")],
        convention,
    )
    assert len(violations) == 2
    assert {v.name for v in violations} == {"customer_load", "customer"}


def test_artifact_type_keys_maps_snake_case_column_to_camel_case_rule_key():
    assert ARTIFACT_TYPE_KEYS["linked_service"] == "linkedService"
    assert ARTIFACT_TYPE_KEYS["pipeline"] == "pipeline"
