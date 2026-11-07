"""Template endpoint tests (AGENTS.md §6, §14, §23.12 gap #20)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_templates_only_returns_active(client: TestClient, seed: dict[str, str], db) -> None:
    from app import models

    db.add(
        models.PipelineTemplate(
            id="tmpl-inactive", name="Retired", version="0.9", description="", active=False, document={}
        )
    )
    db.commit()

    res = client.get("/api/v1/templates")
    assert res.status_code == 200
    ids = {t["id"] for t in res.json()}
    assert seed["template_id"] in ids
    assert "tmpl-inactive" not in ids


def test_get_template(client: TestClient, seed: dict[str, str]) -> None:
    res = client.get(f"/api/v1/templates/{seed['template_id']}")
    assert res.status_code == 200
    assert res.json()["id"] == seed["template_id"]


def test_get_unknown_template_404s(client: TestClient) -> None:
    res = client.get("/api/v1/templates/does-not-exist")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "template_not_found"


def test_every_template_document_is_schema_valid() -> None:
    """§18 "Template schema validation" (§6): every registry template has the keys the form renderer
    reads, a unique id, a semver version, a non-empty unique field allow-list, and defaults that name
    a supported source/target type."""
    import re

    from app.templates import TEMPLATE_DOCUMENTS

    required = {"id", "name", "version", "description", "supportedSourceTypes", "supportedTargetTypes", "fields", "defaults"}
    ids = [t["id"] for t in TEMPLATE_DOCUMENTS]
    assert len(ids) == len(set(ids))
    for template in TEMPLATE_DOCUMENTS:
        assert required <= template.keys(), template["id"]
        assert re.fullmatch(r"\d+\.\d+\.\d+", template["version"]), template["id"]
        assert template["fields"] and len(template["fields"]) == len(set(template["fields"])), template["id"]
        assert template["defaults"]["sourceType"] in template["supportedSourceTypes"], template["id"]
        assert template["defaults"]["targetType"] in template["supportedTargetTypes"], template["id"]


def test_served_template_carries_its_field_allow_list(client: TestClient, db) -> None:
    """The API serves the same field allow-list the registry declares, so the form renders only those fields."""
    from app.seed import _seed_templates
    from app.templates import TEMPLATE_DOCUMENTS

    _seed_templates(db)
    db.commit()
    served = {t["id"]: t for t in client.get("/api/v1/templates").json()}
    for template in TEMPLATE_DOCUMENTS:
        assert served[template["id"]]["fields"] == template["fields"]
        assert served[template["id"]]["defaults"] == template["defaults"]
