"""Scenario tests — §3 Workday (docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md).
One test per scenario ID (WD-01..WD-06). See test_scenario_sap.py's
docstring for the worker/backend split this file follows.
"""
from __future__ import annotations

import pytest

from app.connectors.schemas import ALL_CONNECTORS, CONNECTOR_SCHEMAS


def test_wd_01_workday_connector_selectable_with_report_entity_field():
    """WD-01 [REGRESSION]: `workday` is selectable, with `reportEntity`
    present as a field (not marked required — a soft, "feels required"
    field per the scenario's phrasing)."""
    assert "workday" in ALL_CONNECTORS
    fields = {f.key: f for f in CONNECTOR_SCHEMAS["workday"].fields}
    assert "reportEntity" in fields
    assert fields["reportEntity"].required is False


@pytest.mark.skip(reason="WD-02/[DESIGN]: whether Last_Modified should be expressed as a RaaS report parameter "
                          "vs. a SQL-style predicate is an undecided product question — worker's generator treats "
                          "every source uniformly (no source-type-specific watermark mechanism), so there is no "
                          "code path distinguishing the two today.")
def test_wd_02_last_modified_expressed_as_report_parameter_not_sql_predicate():
    pass


@pytest.mark.skip(reason="WD-03/[VERIFY]: no nested-object flattening function exists anywhere in worker/app "
                          "(grepped for flatten/nested — no matches) — confirmed real [GAP], not yet implemented.")
def test_wd_03_nested_worker_position_organization_flattening_does_not_collide():
    pass


@pytest.mark.skip(reason="WD-04/[VERIFY]: retryCount/retryIntervalInSeconds-are-per-draft is real, worker-only. "
                          "Real coverage: worker/tests/test_scenario_coverage_extra.py::"
                          "test_wd_04_api_02_retry_and_backoff_are_per_draft_not_hardcoded.")
def test_wd_04_oauth_token_expiry_retry_backoff_is_per_source():
    pass


@pytest.mark.skip(reason="WD-05/[VERIFY]: the Workday connector has one plain `url` field (the tenant segment is "
                          "just part of what the user types into it) — real, but there's no dedicated tenant "
                          "field and no save-time URL-shape validation to unit-test (confirmed: no regex/validator "
                          "on the url field anywhere in app/connectors/validation.py).")
def test_wd_05_tenant_url_segment_is_configurable_and_fails_fast_when_wrong():
    pass


def test_wd_06_pii_tag_required_on_worker_object(client: TestClient, seed: dict[str, str], db) -> None:
    """WD-06: a Workday Worker pipeline without the `pii` tag can't be pushed under a block policy."""
    from tests.test_scenario_governance import _seed_tagged_pipeline

    _seed_tagged_pipeline(db, keys=["pii"], annotations=["source-system:workday"])
    res = client.post("/api/v1/git/push", json={"pipelineId": "pl-tags", "featureBranch": "feature/worker"})
    assert res.status_code == 422 and "missing pii" in res.json()["error"]["details"]
