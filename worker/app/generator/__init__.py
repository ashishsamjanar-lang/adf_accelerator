"""Agent/Worker generation layer (AGENTS.md §23.4, §23.4a).

Canonical Python port of the client-side reference generator
(``frontend/lib/adf/spec.ts`` + ``frontend/lib/adf/pipeline-codegen.ts``).
The generator is a **pure function** of ``(SpecDraft | LogicalPipelineSpec,
TemplateMetadata)`` — it never reads DB rows or request state (§16).

* :mod:`.spec`      — ``SpecDraft`` form state, the ``TEMPLATES`` registry,
                      ``build_spec`` (draft -> LogicalPipelineSpec) and
                      ``validate_draft`` (§17 client-parity rules);
* :mod:`.codegen`   — ``build_generated_artifacts`` (draft -> the 10-file ADF
                      artifact set from §23.4a);
* :mod:`.manifest`  — ``build_manifest`` (§23.9 layer-ordered ``manifest.json``).
* :mod:`.medallion` — Bronze/Silver/Gold layer naming + transformation SQL,
                      ported from ``frontend/lib/adf/medallion.ts``.

The frontend copy stays the reference until the worker reaches parity
(§23.12 gap 2).
"""
from __future__ import annotations

from .codegen import GeneratedArtifact, build_generated_artifacts
from .manifest import ARTIFACT_LAYER_ORDER, build_manifest
from .spec import (
    TEMPLATES,
    TEMPLATE_LIST,
    PipelineTemplate,
    SpecDraft,
    build_spec,
    empty_spec_draft,
    field_errors,
    template_by_id,
    validate_draft,
)

__all__ = [
    "ARTIFACT_LAYER_ORDER",
    "TEMPLATES",
    "TEMPLATE_LIST",
    "GeneratedArtifact",
    "PipelineTemplate",
    "SpecDraft",
    "build_generated_artifacts",
    "build_manifest",
    "build_spec",
    "empty_spec_draft",
    "field_errors",
    "template_by_id",
    "validate_draft",
]
