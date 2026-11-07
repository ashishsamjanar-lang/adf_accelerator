"""Scenario tests — §12 Standards & Governance policy interplay
(docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md). One test per scenario ID
(GOV-01..GOV-10).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.rbac import ALL_ROLES


def test_gov_04_no_connector_allow_list_policy_exists(client: TestClient, seed: dict[str, str]) -> None:
    """GOV-04 [GAP]: confirmed real — there is no connector allow-list
    concept anywhere in the naming-convention document shape or
    backend/app/standards.py; PUT /projects/{id}/settings accepts any
    connector type with no policy check at all."""
    res = client.put(f"/api/v1/projects/{seed['project_id']}/settings", json={"customMetadata": []})
    assert res.status_code == 200
    assert "connectorAllowList" not in res.json()


def test_gov_06_a_raw_secret_with_a_space_or_slash_is_rejected_at_save_time(client: TestClient, seed: dict[str, str]) -> None:
    """GOV-06 [VERIFY]: real, working code — `_reject_secret_values`
    (app/api/projects.py) blocks a raw-looking value in any `password`/
    `secret`/`token`/`connectionString`-hinted field (or any `isSecret`
    field) that contains a space or slash, regardless of naming-convention
    configuration."""
    res = client.put(
        f"/api/v1/projects/{seed['project_id']}/settings",
        json={"environmentVariables": [{"key": "dbPassword", "value": "Sup3r Secret/Value", "isSecret": True}]},
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "secret_value_rejected"


def test_gov_06_a_short_space_free_secret_look_alike_is_not_caught_documents_a_real_gap(
    client: TestClient, seed: dict[str, str]
) -> None:
    """GOV-06 companion: documents a real, confirmed gap in the same
    function — the heuristic only rejects a value containing a space or
    slash (or >=128 chars); a realistic password with neither
    (`Tr0ub4dor&3xtra`) sails through untouched."""
    res = client.put(
        f"/api/v1/projects/{seed['project_id']}/settings",
        json={"environmentVariables": [{"key": "dbPassword", "value": "Tr0ub4dor&3xtra", "isSecret": True}]},
    )
    assert res.status_code == 200


@pytest.mark.skip(reason="GOV-01/[GAP]: confirmed real — `_enforce_naming_standard` (app/services.py) reads "
                          "`project.settings.document['namingConvention']` only, a flat per-project document with "
                          "no team-level inheritance/resolution logic anywhere in the codebase. A project-level "
                          "'off' always wins; there's no team-inherited override to conflict with.")
def test_gov_01_effective_policy_resolves_team_inheritance_over_project_off():
    pass


@pytest.mark.skip(reason="GOV-02/[VERIFY]: confirmed real gap — `_enforce_naming_standard` only evaluates "
                          "`enforcement: block` violations (it returns immediately otherwise); a `warn`-level "
                          "violation is never evaluated, never surfaced, and never written to the real audit-log "
                          "mechanism (repository.record_audit) at push time. There is no 'acknowledge with a "
                          "reason' concept anywhere in the push flow at all.")
def test_gov_02_acknowledged_warn_reason_is_persisted_to_audit_log():
    pass


@pytest.mark.skip(reason="GOV-03/[DESIGN]: confirmed real — there is no 'STANDARDS_ADMIN' role anywhere; "
                          f"app/rbac.py's ALL_ROLES is exactly {sorted(ALL_ROLES)!r}. No block-override or expiry "
                          "concept exists at all.")
def test_gov_03_block_override_requires_admin_and_has_real_expiry():
    pass


def test_gov_05_missing_required_tags_block_the_push(client: TestClient, seed: dict[str, str], db) -> None:
    """GOV-05: a `block` requiredTags policy rejects a push whose pipeline lacks a required tag;
    `key:value` annotations (the Build wizard's project-tag format) satisfy the key."""
    _seed_tagged_pipeline(db, keys=["data-domain", "pii", "source-system"], annotations=["data-domain:hr", "pii"])
    res = client.post("/api/v1/git/push", json={"pipelineId": "pl-tags", "featureBranch": "feature/tags"})
    assert res.status_code == 422
    body = res.json()["error"]
    assert body["code"] == "policy_violation" and "source-system" in body["details"] and "pii" not in body["details"].split("missing")[1]


def test_gov_05_complete_tags_or_a_warn_policy_push_normally(client: TestClient, seed: dict[str, str], db) -> None:
    _seed_tagged_pipeline(db, keys=["data-domain", "PII"], annotations=["data-domain:hr", "pii"])
    assert client.post("/api/v1/git/push", json={"pipelineId": "pl-tags", "featureBranch": "feature/tags"}).status_code == 202


def test_gov_05_warn_policy_never_blocks(client: TestClient, seed: dict[str, str], db) -> None:
    _seed_tagged_pipeline(db, keys=["pii"], annotations=[], enforcement="warn")
    assert client.post("/api/v1/git/push", json={"pipelineId": "pl-tags", "featureBranch": "feature/tags"}).status_code == 202


def test_gov_05_settings_reject_a_malformed_required_tags_policy(client: TestClient, seed: dict[str, str]) -> None:
    res = client.put(f"/api/v1/projects/{seed['project_id']}/settings", json={"requiredTags": {"enforcement": "always"}})
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_required_tags"


@pytest.mark.skip(reason="GOV-07/[VERIFY]: confirmed real gap — no '/standards/scan' or any compliance-scan "
                          "endpoint exists anywhere in backend/app/api (grepped all routers — no matches). "
                          "Standards are only ever evaluated inline at push time (block) or generate time "
                          "(worker's validate job) for artifacts actively being touched, never as a whole-catalog "
                          "sweep.")
def test_gov_07_compliance_scan_surfaces_all_catalog_drift_not_just_new():
    pass


@pytest.mark.skip(reason="GOV-08/[VERIFY]: no policy export/import endpoint exists anywhere in backend/app/api "
                          "(the naming convention lives inline in the free-form Project Settings document, with "
                          "no dedicated serialize/round-trip path) — confirmed real gap.")
def test_gov_08_policy_export_import_round_trips_identically():
    pass


@pytest.mark.skip(reason="GOV-09/[VERIFY]: no save-time contradiction check exists on the naming-convention "
                          "document shape — PUT /projects/{id}/settings accepts any prefix/suffix/case/maxLength "
                          "combination, including one that would force a hyphen into a linked-service name "
                          "(illegal under the always-on worker-only ADF hard rule) — confirmed real [GAP]: the "
                          "contradiction would only surface later, per-artifact, at generate/push time.")
def test_gov_09_self_contradictory_pattern_rejected_at_policy_save_time():
    pass


def test_gov_10_upsert_object_without_business_keys_is_blocked_at_onboarding(client: TestClient, seed: dict[str, str]) -> None:
    """GOV-10: the Bronze registry rejects an upsert/merge object with no upsert_keys, on the single
    and the bulk endpoint (`services._validate_onboarding_body`)."""
    url = "/api/v1/projects/proj-a/onboarding/objects"
    row = {"source_system": "erp", "source_object": "customer", "source_type": "table", "target_type": "table",
           "target_file_format": "Parquet", "loading_behavior": "FullLoad", "write_behavior": "upsert"}
    for resp in (client.post(url, json=row), client.post(f"{url}/bulk", json={"objects": [row]})):
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "onboarding_upsert_keys_required"
    assert client.post(url, json={**row, "upsert_keys": "customer_id"}).status_code in (200, 201)


def _seed_tagged_pipeline(db, *, keys: list[str], annotations: list[str], enforcement: str = "block") -> None:
    """A project with a `requiredTags` policy and one pipeline artifact carrying `annotations`."""
    import json

    from app import models

    db.add(models.Project(id="proj-tags", team_id="team-a", name="Tags Project", description=""))
    db.add(
        models.ProjectSettings(
            project_id="proj-tags",
            document={
                "projectId": "proj-tags",
                "gitDefaults": {"provider": "github", "repositoryUrl": "https://example.test/repo.git", "baseBranch": "dev"},
                "requiredTags": {"enforcement": enforcement, "keys": keys},
            },
            updated_by="Test Admin",
        )
    )
    db.add(
        models.PipelineDefinition(
            id="pl-tags", name="tags-pipeline", template_id="tmpl-a", project_id="proj-tags", team_id="team-a",
            state="GENERATED", draft={}, spec={}, latest_artifact_version="v1.0.0",
        )
    )
    content = json.dumps({"name": "PL_worker_child", "properties": {"annotations": annotations, "activities": []}})
    db.add(
        models.Artifact(
            id="art-tags", pipeline_id="pl-tags", pipeline_name="tags-pipeline", file_name="PL_worker_child.json",
            file_type="json", artifact_type="pipeline", layer="pipeline", version="v1.0.0", state="generated",
            bytes=len(content), content=content,
        )
    )
    db.commit()
