"""Generator parity test (AGENTS.md §23.12 gap #20, first item of §29 target design).

Feeds one ``SpecDraft`` fixture per pipeline template (§24 — the 5
``PipelineTemplateId``s: full_load, incremental_load, initial_load,
composite_watermark_load, metadata_driven_load) through both generators —
``frontend/lib/adf/pipeline-codegen.ts`` (the reference implementation) and
``worker/app/generator/codegen.py`` (the running port) — and diffs their
output file-by-file. Before this test existed, nothing actually verified
DEVELOPMENT_NOTES.md's "keep them in parity" claim; this is the highest-priority missing
test per the gap table. Originally only the incremental_load fixture was
covered (2026-09-11) — the other 4 templates were added 2026-09-13 so every
template that exists in the Build wizard actually gets exercised, not just
the one that happened to be reached for first.

Requires Node + the frontend's devDependencies (``npm install`` in
``frontend/``) to run the reference generator via ``tsx``
(``frontend/scripts/export-generated-artifacts.ts``, a test-only harness —
nothing in the app imports it). Skipped, not failed, when that toolchain
isn't available, so it never blocks a Python-only `pytest worker/` run; CI's
``generator-parity`` job (``.github/workflows/ci.yml``) installs both
toolchains explicitly so this always actually runs there.

Note: ``metadata_driven_load``'s template ``fields`` list advertises
``validation.schemaValidation``, but no such field exists anywhere on
``SpecDraft``/``buildSpec``/the generator on either side — it's a Build-form
field name with no backing implementation (pre-existing gap, not introduced
or fixed here; the metadata-driven fixture below deliberately omits it since
there's nothing to set).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from app.generator.codegen import build_generated_artifacts
from app.generator.spec import SpecDraft

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIR = _REPO_ROOT / "frontend"
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_HARNESS = _FRONTEND_DIR / "scripts" / "export-generated-artifacts.ts"

# One fixture per PipelineTemplateId (frontend/lib/adf/spec.ts TEMPLATES /
# worker/app/generator/spec.py TEMPLATES) so every template is parity-tested,
# not just whichever one a fixture happened to exist for.
_FIXTURES = {
    "full_load": _FIXTURES_DIR / "spec_draft_full.json",
    "incremental_load": _FIXTURES_DIR / "spec_draft_incremental.json",
    "initial_load": _FIXTURES_DIR / "spec_draft_initial.json",
    "composite_watermark_load": _FIXTURES_DIR / "spec_draft_composite_watermark.json",
    "metadata_driven_load": _FIXTURES_DIR / "spec_draft_metadata_driven.json",
    # Not a 6th template — these two additionally exercise the qualifier
    # module (lib/adf/qualifier.ts / generator/qualifier.py) on the same
    # "full_load" template: a 3-part source (SAP HANA catalog.schema.table,
    # mixed case + a lowercase row to prove case-conformance) and a
    # schema-less source (SAP ECC OData, whose object legitimately contains
    # a dot). Both templates previously mis-parsed these (see DEVELOPMENT_NOTES.md's
    # naming-convention-edge-cases note).
    "full_load_sap_hana_3part": _FIXTURES_DIR / "spec_draft_full_sap_hana_3part.json",
    "full_load_sap_ecc_schemaless": _FIXTURES_DIR / "spec_draft_full_sap_ecc_schemaless.json",
    # Not a 6th/7th template either — these exercise the Bronze/Silver/Gold
    # medallion layer support (lib/adf/medallion.ts / generator/medallion.py):
    # Silver's conform_columns+dedupe transformation chain and Gold's
    # join+aggregate chain, both on top of metadata_driven_load.
    "metadata_driven_load_silver": _FIXTURES_DIR / "spec_draft_silver_gl_transaction.json",
    "metadata_driven_load_gold": _FIXTURES_DIR / "spec_draft_gold_finance_mart.json",
    # §33.3 widening regression fixtures: prove the newly-allowed
    # Silver+join / Gold+scd2 combinations generate byte-identical output
    # between the two generators, same as every pre-existing combination.
    "metadata_driven_load_silver_join": _FIXTURES_DIR / "spec_draft_silver_join.json",
    "metadata_driven_load_gold_scd2": _FIXTURES_DIR / "spec_draft_gold_scd2.json",
    # §33.7 P6: proves the two additive sibling control-table artifacts
    # (adf_curated_control_table.sql / adf_mart_control_table.sql) generate
    # byte-identical between the two generators when a pipeline was built
    # from a picked registry object (silverObjectRef/martObjectRef set).
    "metadata_driven_load_silver_registry_ref": _FIXTURES_DIR / "spec_draft_silver_registry_ref.json",
    "metadata_driven_load_gold_registry_ref": _FIXTURES_DIR / "spec_draft_gold_registry_ref.json",
    # Proves masterPipelineName/childPipelineName overrides (§24.5) generate byte-identical
    # PL_{override}.json file names + internal ExecutePipeline/trigger references between the two
    # generators, instead of the PL_{name}_master/_child fallback every other fixture exercises.
    "metadata_driven_load_master_child_names": _FIXTURES_DIR / "spec_draft_metadata_driven_master_child_names.json",
    # §24.3/§24.12 pipeline patterns (§23.12 gap #5): prove the three new non-master_child generation
    # strategies produce byte-identical output between the two generators. child_only/master_only aren't
    # separately fixtured — they're a trivial subset (just one of the two pipeline builders already
    # covered above, minus the other artifacts), so a parity break there would already show up as a
    # master_pipeline/child_pipeline content mismatch in the fixtures above.
    "single_pipeline": _FIXTURES_DIR / "spec_draft_single_pipeline.json",
    "independent_full": _FIXTURES_DIR / "spec_draft_independent_full.json",
    "independent_watermark": _FIXTURES_DIR / "spec_draft_independent_watermark.json",
    # §24.6 "use existing, don't regenerate" (found 2026-09-20): proves both generators omit the
    # source/target linkedService+dataset artifacts identically when sourceConnectionReused/
    # targetConnectionReused is set, while every other artifact (including the pipeline's own
    # LinkedServiceReference/DatasetReference by name) stays byte-identical to the equivalent
    # fixture without those flags.
    "reused_connections": _FIXTURES_DIR / "spec_draft_reused_connections.json",
    "child_dispatch": _FIXTURES_DIR / "spec_draft_child_dispatch.json",
    # §32.5/§32.6 (MI3 picker): a user-assigned managed identity on the target linked service — both
    # generators emit the same `credentials` artifact and root-level CredentialReference.
    "user_assigned_identity": _FIXTURES_DIR / "spec_draft_user_assigned_identity.json",
    # Silver on a database target: the Copy lands in raw.<source_system>_<object> (the table the chain
    # reads), FullLoad rows seed no default watermark, and the scd2 step reads one row per key.
    "silver_db_landing": _FIXTURES_DIR / "spec_draft_silver_db_landing.json",
    # Per-object column mappings: the control table's ColumnMapping column (translator JSON per row,
    # falling back to the pipeline-level mappings), the master passing it to its own child only (not the
    # dispatch case), and the single pipeline reading it via @item() — byte-identical in both generators.
    "per_object_mappings": _FIXTURES_DIR / "spec_draft_per_object_mappings.json",
    "single_pipeline_per_object_mappings": _FIXTURES_DIR / "spec_draft_single_pipeline_per_object_mappings.json",
    # Hard-delete handling: key-reconciliation soft delete on master/child and CDC on an independent
    # watermark pipeline — same staging Copy + ApplySoftDeletes Script in both generators.
    "soft_delete": _FIXTURES_DIR / "spec_draft_soft_delete.json",
    "cdc_independent": _FIXTURES_DIR / "spec_draft_cdc_independent.json",
}


def _node_bin(name: str) -> str | None:
    """Prefer the frontend's own installed binary over anything on PATH —
    this must run the exact `tsx`/generator version this repo pins.

    On Windows, ``node_modules/.bin/<name>`` (no extension) is a POSIX shell
    script — not a valid Win32 executable — while ``<name>.cmd`` is the real
    Windows entry point npm installs alongside it, so it must be tried first
    there (bug found running this test on Windows for the first time,
    2026-09-13: ``subprocess.run`` raised ``OSError: [WinError 193] %1 is not
    a valid Win32 application`` for every fixture before this fix, on both
    the pre-existing incremental_load case and the other 4 templates)."""
    bin_dir = _FRONTEND_DIR / "node_modules" / ".bin"
    candidates = (bin_dir / f"{name}.cmd", bin_dir / name) if os.name == "nt" else (bin_dir / name, bin_dir / f"{name}.cmd")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


@lru_cache(maxsize=None)
def _run_frontend_generator(fixture: Path) -> list[dict[str, Any]]:
    tsx = _node_bin("tsx")
    if tsx is None:
        pytest.skip(
            "tsx not found in frontend/node_modules/.bin — run `npm install` in frontend/ to enable the "
            "generator parity test."
        )
    proc = subprocess.run(
        [tsx, str(_HARNESS), str(fixture)],
        cwd=_FRONTEND_DIR,
        capture_output=True,
        text=True,
        # Force UTF-8 decoding of the child's stdout explicitly: `text=True`
        # alone uses `locale.getpreferredencoding()`, which on Windows is
        # typically cp1252, not UTF-8. tsx/Node write UTF-8; without this, a
        # non-ASCII character (e.g. the em dash in some activity descriptions)
        # gets mis-decoded into 2-3 mojibake characters, which then look like
        # a genuine generator content mismatch (found 2026-09-13).
        encoding="utf-8",
        timeout=60,
    )
    if proc.returncode != 0:
        pytest.fail(f"frontend generator harness failed for {fixture.name} (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


@lru_cache(maxsize=None)
def _run_worker_generator(fixture: Path) -> list[dict[str, Any]]:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    draft = SpecDraft.from_dict(payload)
    artifacts = build_generated_artifacts(draft)
    return sorted(
        (
            {
                "fileName": a.file_name,
                "fileType": a.file_type,
                "artifactType": a.artifact_type,
                "layer": a.layer,
                "content": a.content,
            }
            for a in artifacts
        ),
        key=lambda a: a["fileName"],
    )


@pytest.mark.parametrize("template_id, fixture", sorted(_FIXTURES.items()))
def test_generators_produce_identical_file_sets(template_id: str, fixture: Path):
    frontend_artifacts = _run_frontend_generator(fixture)
    worker_artifacts = _run_worker_generator(fixture)
    # Compare as sets, not positional lists: each side sorts with its own
    # runtime's default string comparison (JS `localeCompare` vs Python's
    # codepoint `sorted()`), which disagree on mixed-case names like
    # "adf_control_table.sql" vs "DS_...json" — a real ordering difference,
    # but not one that means anything (neither generator itself sorts its
    # output; this list order is a test-harness artifact of each side's
    # own comparator, not a product guarantee). Found running this test for
    # the first time on Windows, 2026-09-13 — it silently never ran before.
    frontend_names = {a["fileName"] for a in frontend_artifacts}
    worker_names = {a["fileName"] for a in worker_artifacts}
    assert frontend_names == worker_names, (
        f"[{template_id}] The frontend (reference) and worker (port) generators produced different artifact "
        f"file sets for the same SpecDraft.\nonly in frontend: {sorted(frontend_names - worker_names)}\n"
        f"only in worker:   {sorted(worker_names - frontend_names)}"
    )


def _same_content(a: str, b: str) -> bool:
    """True if two artifact ``content`` strings represent the same thing.

    Most artifacts are pretty-printed JSON built independently by two separate
    codebases (JS object literals vs. Python dict construction); nothing
    guarantees they insert keys in the same order for equivalent content, and
    ADF itself is indifferent to JSON key order. Comparing raw strings would
    flag that harmless ordering as a "content" mismatch and bury a real one
    under it, so parse-and-compare when both sides are valid JSON (dict `==`
    is order-independent) and fall back to a raw string compare for anything
    that isn't (e.g. ``adf_control_table.sql``).
    """
    if a == b:
        return True
    try:
        return json.loads(a) == json.loads(b)
    except (TypeError, ValueError):
        return False


@pytest.mark.parametrize("template_id, fixture", sorted(_FIXTURES.items()))
def test_generators_produce_identical_content(template_id: str, fixture: Path):
    frontend_artifacts = _run_frontend_generator(fixture)
    worker_artifacts = _run_worker_generator(fixture)
    frontend_by_name = {a["fileName"]: a for a in frontend_artifacts}
    worker_by_name = {a["fileName"]: a for a in worker_artifacts}
    assert frontend_by_name.keys() == worker_by_name.keys(), (
        f"[{template_id}] file sets differ — see test_generators_produce_identical_file_sets for detail."
    )
    mismatches = []
    for name, f in frontend_by_name.items():
        w = worker_by_name[name]
        non_content_fields = ("fileType", "artifactType", "layer")
        if any(f[k] != w[k] for k in non_content_fields) or not _same_content(f["content"], w["content"]):
            mismatches.append(name)
    assert not mismatches, (
        f"[{template_id}] Content differs between the frontend reference generator and the worker port for: "
        f"{mismatches}. Run `npm run parity:export -- {fixture}` in frontend/ and compare against the worker "
        "port directly."
    )
