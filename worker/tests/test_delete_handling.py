"""Hard-delete handling (SpecDraft.delete_handling) — validation + generated activities.
Parity with the TS reference is covered by test_generator_parity.py's soft_delete/cdc_independent fixtures."""
import json
from pathlib import Path

from app.generator.codegen import _delete_handling_activities
from app.generator.spec import SpecDraft, validate_delete_handling

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _draft(name: str, **overrides) -> SpecDraft:
    data = json.loads((_FIXTURES / name).read_text())
    data.update(overrides)
    return SpecDraft.from_dict(data)


def test_none_emits_nothing_and_validates_clean():
    draft = _draft("spec_draft_incremental.json")
    assert validate_delete_handling(draft) == []
    assert _delete_handling_activities(draft, "customer", "CopyDeltaData", "S", "T") == ([], "CopyDeltaData")


def test_soft_delete_requires_keys():
    draft = _draft("spec_draft_incremental.json", deleteHandling="soft_delete")
    assert [i.field for i in validate_delete_handling(draft) if i.severity == "error"] == ["upsertKeyColumns"]


def test_soft_delete_activities_flag_missing_keys():
    draft = _draft("spec_draft_soft_delete.json")
    activities, final = _delete_handling_activities(draft, "customer", "CopyDeltaData", "S", "T")
    assert [a["name"] for a in activities] == ["StageSourceKeys", "ApplySoftDeletes"]
    assert final == "ApplySoftDeletes"
    text = json.dumps(activities)
    assert "NOT EXISTS" in text and "_is_deleted = 1" in text


def test_cdc_rejects_non_sql_source_and_non_datetime_watermark():
    draft = _draft(
        "spec_draft_cdc_independent.json",
        sourceType="oracle",
        watermarkColumns=[{"name": "id", "type": "int", "order": 1}],
    )
    fields = {i.field for i in validate_delete_handling(draft) if i.severity == "error"}
    assert {"deleteHandling", "watermarkColumns"} <= fields


def test_cdc_uses_latest_operation_per_key():
    draft = _draft("spec_draft_cdc_independent.json")
    activities, _ = _delete_handling_activities(draft, "vendor", "CopyDeltaData", "S", "T")
    stage = json.dumps(activities[0])
    assert activities[0]["name"] == "StageCdcChanges"
    assert "cdc.fn_cdc_get_all_changes_" in stage and "ROW_NUMBER()" in stage
    assert "is_removed = 1" in json.dumps(activities[1])


def test_wm_08_delete_handling_on_a_full_load_is_flagged():
    """WM-08: delete detection only runs on the DeltaLoad branch, so pairing it with a full load warns."""
    draft = _draft("spec_draft_soft_delete.json", loadTypeMode="full")
    assert any(
        i.severity == "warning" and i.field == "deleteHandling" and "full load" in i.message
        for i in validate_delete_handling(draft)
    )
