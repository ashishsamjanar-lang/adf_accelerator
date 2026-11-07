"""Scenario tests — §7 REST APIs (docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md).
One test per scenario ID (API-01..API-06). See test_scenario_sap.py's
docstring for the worker/backend split this file follows.
"""
from __future__ import annotations

import pytest

from app.connectors.schemas import ALL_CONNECTORS, LLM_CONNECTORS


def test_api_06_llm_connectors_are_a_distinct_registry_category_from_copy_source_target_kinds():
    """API-06 [VERIFY]: real, backend-testable at the registry level — LLM
    connectors (`kind="llm"`) are a distinct category from database/file/
    service connectors. Whether Build's actual Copy source/target picker
    (a frontend concern, frontend/features/build/*) filters them out is not
    testable from backend, but the registry-level distinction they'd filter
    on is real."""
    assert set(LLM_CONNECTORS) <= set(ALL_CONNECTORS)
    assert "azure_openai" in LLM_CONNECTORS
    assert "sql_server" not in LLM_CONNECTORS
    assert "salesforce" not in LLM_CONNECTORS


@pytest.mark.skip(reason="API-01/[VERIFY]/[GAP]: confirmed real — no `paginationRules` (or any pagination "
                          "concept) is ever emitted anywhere in worker/app/generator/codegen.py's Copy-activity "
                          "source construction, for any connector kind. A paginated REST report gets no "
                          "generator-side help.")
def test_api_01_paginated_report_generates_pagination_rules():
    pass


@pytest.mark.skip(reason="API-02/[VERIFY]: retryCount/retryIntervalInSeconds applying uniformly to every Copy "
                          "activity (REST-sourced or not) is real, worker-only. Real coverage: worker/tests/"
                          "test_scenario_coverage_extra.py::test_wd_04_api_02_retry_and_backoff_are_per_draft_not_hardcoded.")
def test_api_02_rate_limit_429_retry_backoff_applies_to_rest_sources():
    pass


@pytest.mark.skip(reason="API-03/[VERIFY]/[GAP]: `_api_extract_window` is not a distinct, auto-generated column "
                          "anywhere in worker/app/generator/codegen.py — the metadata-column mechanism "
                          "(`_additional_columns_of`, confirmed real via REGR-01 coverage) only emits whatever "
                          "`customMetadata` entries the author manually configures. Confirmed real [GAP]: no "
                          "REST-specific metadata column is auto-added.")
def test_api_03_api_extract_window_metadata_column_is_generated():
    pass


@pytest.mark.skip(reason="API-04/[VERIFY]: overlapping report-window append-dedupe is a live-execution property "
                          "against real pulled data — no dedupe-key concept exists anywhere in the generator "
                          "(confirmed: no dedupe function in worker/app), so there's no code path to unit-test.")
def test_api_04_overlapping_report_window_pulls_do_not_double_count():
    pass


@pytest.mark.skip(reason="API-05/[VERIFY]: no nested-JSON flattening function exists anywhere in worker/app "
                          "(same root cause as WD-03/SF-06 — grepped for flatten, no matches) — confirmed real "
                          "[GAP].")
def test_api_05_nested_campaign_ad_group_ad_flattens_without_collision():
    pass
