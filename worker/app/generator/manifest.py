"""``manifest.json`` emitter (AGENTS.md §23.4a gap 6, §23.9).

``{ project, version, artifacts: [{ name, type, layer }] }`` ordered by the
Layer 0–5 apply order so a consumer can push/apply deterministically. This
product only *pushes* to Git in this order — it does not apply to an
environment.
"""
from __future__ import annotations

from typing import Any

from .codegen import GeneratedArtifact

# Layer 0–5 apply order (§23.9). "infrastructure" (Layer 0) is never emitted by
# the generator but is listed for completeness.
ARTIFACT_LAYER_ORDER: tuple[str, ...] = (
    "infrastructure",
    "connection",
    "data",
    "pipeline",
    "runtime",
    "metadata",
)

_LAYER_INDEX = {layer: i for i, layer in enumerate(ARTIFACT_LAYER_ORDER)}


def order_artifacts(artifacts: list[GeneratedArtifact]) -> list[GeneratedArtifact]:
    """Stable sort by §23.9 layer, preserving intra-layer generation order."""
    return sorted(artifacts, key=lambda a: _LAYER_INDEX.get(a.layer, len(ARTIFACT_LAYER_ORDER)))


def build_manifest(project: str, version: str, artifacts: list[GeneratedArtifact]) -> dict[str, Any]:
    ordered = order_artifacts(artifacts)
    return {
        "project": project,
        "version": version,
        "artifacts": [
            {"name": a.file_name, "type": a.artifact_type, "layer": a.layer} for a in ordered
        ],
    }
