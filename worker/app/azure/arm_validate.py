"""AGENTS.md §29.1 layer 6 — optional live schema check.

Wraps a pipeline's generated ADF artifacts in an ARM template (factory + child
resources, ``dependsOn`` derived from their ``referenceName``s) and POSTs it to
``Microsoft.Resources/deployments/{name}/validate``. Validate-mode is ARM's
preflight: nothing is created, changed, or published. Findings are ``warning``
severity by default (§29.1: opt-in, ``warn`` by default) and never change the
pipeline's lifecycle state.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from .auth import AzureError, get_token, http_json

ARM_SCOPE = "https://management.azure.com/.default"
ARM_API_VERSION = "2021-04-01"
ADF_API_VERSION = "2018-06-01"
DEFAULT_SANDBOX_FACTORY = "adfacc-live-check"

# artifact_type → ARM child resource segment. `factory` (globalParameters), the
# parameters file, control-table SQL and manifest.json aren't ARM resources.
_RESOURCE_SEGMENT = {
    "credential": "credentials",
    "linked_service": "linkedservices",
    "dataset": "datasets",
    "pipeline": "pipelines",
    "trigger": "triggers",
}
# ADF reference `type` → ARM child resource segment, for dependsOn.
_REFERENCE_SEGMENT = {
    "CredentialReference": "credentials",
    "LinkedServiceReference": "linkedservices",
    "DatasetReference": "datasets",
    "PipelineReference": "pipelines",
}

REQUIRED_CONFIG = ("subscriptionId", "resourceGroup", "location")


def _references(node: Any, found: set[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        ref_type, ref_name = node.get("type"), node.get("referenceName")
        if isinstance(ref_type, str) and isinstance(ref_name, str) and ref_type in _REFERENCE_SEGMENT:
            found.add((_REFERENCE_SEGMENT[ref_type], ref_name))
        for value in node.values():
            _references(value, found)
    elif isinstance(node, list):
        for value in node:
            _references(value, found)


def build_arm_template(artifacts: list[tuple[str, str]], factory_name: str, location: str) -> dict[str, Any]:
    """``artifacts`` is ``[(artifact_type, json content)]``. Non-ARM types are skipped."""
    factory_id = f"[resourceId('Microsoft.DataFactory/factories', '{factory_name}')]"
    parsed: list[tuple[str, str, dict[str, Any]]] = []
    for artifact_type, content in artifacts:
        segment = _RESOURCE_SEGMENT.get(artifact_type)
        if segment is None:
            continue
        try:
            doc = json.loads(content)
        except ValueError:
            continue  # layer 1 already reports unparseable JSON
        if isinstance(doc, dict) and doc.get("name"):
            parsed.append((segment, str(doc["name"]), doc))
    present = {(segment, name) for segment, name, _ in parsed}
    resources: list[dict[str, Any]] = [
        {
            "type": "Microsoft.DataFactory/factories",
            "apiVersion": ADF_API_VERSION,
            "name": factory_name,
            "location": location,
            "properties": {},
        }
    ]
    for segment, name, doc in parsed:
        refs: set[tuple[str, str]] = set()
        _references(doc.get("properties"), refs)
        depends = [factory_id] + [
            f"[resourceId('Microsoft.DataFactory/factories/{ref_segment}', '{factory_name}', '{ref_name}')]"
            for ref_segment, ref_name in sorted(refs)
            if (ref_segment, ref_name) in present and (ref_segment, ref_name) != (segment, name)
        ]
        resources.append(
            {
                "type": f"Microsoft.DataFactory/factories/{segment}",
                "apiVersion": ADF_API_VERSION,
                "name": f"{factory_name}/{name}",
                "properties": doc.get("properties") or {},
                "dependsOn": depends,
            }
        )
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "resources": resources,
    }


def _flatten_errors(error: Any, out: list[dict[str, Any]]) -> None:
    if not isinstance(error, dict):
        return
    details = error.get("details") or []
    if details:
        for detail in details:
            _flatten_errors(detail, out)
    else:
        out.append(
            {
                "severity": "warning",
                "layer": "live",
                "ruleId": f"live.arm.{error.get('code') or 'ValidationError'}",
                "message": str(error.get("message") or "ARM validation failed."),
                "target": error.get("target"),
            }
        )


def missing_config(config: dict[str, Any]) -> list[str]:
    return [key for key in REQUIRED_CONFIG if not str(config.get(key) or "").strip()]


def run_live_check(config: dict[str, Any], artifacts: list[tuple[str, str]], *, deployment_name: str) -> dict[str, Any]:
    missing = missing_config(config)
    if missing:
        raise AzureError(f"Live check is missing required settings: {', '.join(missing)}.")
    factory = (config.get("factoryName") or "").strip() or DEFAULT_SANDBOX_FACTORY
    template = build_arm_template(artifacts, factory, config["location"].strip())
    if len(template["resources"]) == 1:
        raise AzureError("The pipeline has no ARM-deployable artifacts to check — generate it first.")
    url = (
        f"https://management.azure.com/subscriptions/{quote(config['subscriptionId'].strip())}"
        f"/resourcegroups/{quote(config['resourceGroup'].strip())}"
        f"/providers/Microsoft.Resources/deployments/{quote(deployment_name)}/validate?api-version={ARM_API_VERSION}"
    )
    token = get_token(config, ARM_SCOPE)
    status, body = http_json("POST", url, token, {"properties": {"mode": "Incremental", "template": template}})
    issues: list[dict[str, Any]] = []
    if status >= 400:
        _flatten_errors((body or {}).get("error") or {"message": f"ARM returned HTTP {status}."}, issues)
        if status in (401, 403):
            raise AzureError(issues[0]["message"] if issues else f"ARM denied the request (HTTP {status}).")
    return {
        "passed": not issues,
        "issues": issues,
        "resourceCount": len(template["resources"]) - 1,
        "sandbox": {"resourceGroup": config["resourceGroup"].strip(), "factoryName": factory},
    }
