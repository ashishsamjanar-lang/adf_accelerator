"""pytest for app/validator/naming.py (AGENTS.md §25.3/§25.4/§25.6).

Pure functions over a candidate name + a `namingConvention` dict — no
fixtures needed. Keep these in parity with the frontend
(`frontend/lib/adf/naming.ts`) and backend (`backend/app/standards.py`)
copies of the same evaluator.
"""
from __future__ import annotations

from app.validator.naming import build_expected_name, evaluate_name, is_configured


def _convention(enforcement: str = "block", **rule: object) -> dict:
    return {"enforcement": enforcement, "rules": {"pipeline": rule}} if rule else {"enforcement": enforcement}


def test_evaluate_name_returns_none_when_enforcement_is_off():
    assert evaluate_name("bad_name", "pipeline", _convention("off", prefix="PL_")) is None


def test_evaluate_name_returns_none_when_no_rule_is_configured_for_the_type():
    assert evaluate_name("anything", "dataset", _convention("block", prefix="PL_")) is None  # rule only set for pipeline


def test_evaluate_name_returns_none_for_an_empty_configured_rule():
    assert is_configured({}) is False
    assert evaluate_name("anything", "pipeline", _convention("block")) is None


def test_evaluate_name_flags_a_missing_prefix_as_block():
    violation = evaluate_name("customer_load", "pipeline", _convention("block", prefix="PL_"))
    assert violation is not None
    assert violation.level == "block"
    assert "PL_" in violation.message


def test_evaluate_name_flags_a_missing_suffix_as_warn():
    violation = evaluate_name("PL_customer_load", "pipeline", _convention("warn", suffix="_v1"))
    assert violation is not None
    assert violation.level == "warn"


def test_evaluate_name_flags_names_over_the_max_length():
    violation = evaluate_name("PL_" + "x" * 40, "pipeline", _convention("block", prefix="PL_", maxLength=20))
    assert violation is not None
    assert "at most 20" in violation.message


def test_evaluate_name_checks_case_style_of_the_body_between_prefix_and_suffix():
    convention = _convention("block", prefix="PL_", case="snake")
    assert evaluate_name("PL_customer_load", "pipeline", convention) is None
    violation = evaluate_name("PL_CustomerLoad", "pipeline", convention)
    assert violation is not None
    assert "snake case" in violation.message


def test_evaluate_name_accepts_a_fully_compliant_name():
    convention = _convention("block", prefix="PL_", suffix="_v1", case="snake", maxLength=64)
    assert evaluate_name("PL_customer_load_v1", "pipeline", convention) is None


def test_build_expected_name_wraps_the_base_with_prefix_and_suffix():
    assert build_expected_name("name", {"prefix": "PL_", "suffix": "_v1"}) == "PL_name_v1"
    assert build_expected_name("name", None) == "name"
