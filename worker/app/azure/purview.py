"""Registry lineage → Microsoft Purview (Atlas v2 API on the Data Map).

Input is the ``registry-lineage.json`` document the Onboarding page builds
(``buildRegistryLineage``: ``{jobs: [{job, medallionLayer, inputs, outputs}]}``
with OpenLineage ``namespace``/``name`` datasets). Two custom Atlas types —
``adf_accelerator_dataset`` (a ``DataSet``) and ``adf_accelerator_process``
(a ``Process``) — are created on first push; entities are upserted by
``qualifiedName`` so re-pushing is idempotent.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .auth import AzureError, get_token, http_json

PURVIEW_SCOPE = "https://purview.azure.net/.default"
DATASET_TYPE = "adf_accelerator_dataset"
PROCESS_TYPE = "adf_accelerator_process"
BATCH_SIZE = 100
MAX_JOBS = 5000

TYPEDEFS = {
    "entityDefs": [
        {"name": DATASET_TYPE, "superTypes": ["DataSet"], "serviceType": "ADF Accelerator", "attributeDefs": []},
        {
            "name": PROCESS_TYPE,
            "superTypes": ["Process"],
            "serviceType": "ADF Accelerator",
            "attributeDefs": [
                {"name": "medallionLayer", "typeName": "string", "isOptional": True, "cardinality": "SINGLE"}
            ],
        },
    ]
}


def dataset_qualified_name(dataset: dict[str, Any]) -> str:
    return f"{dataset.get('namespace') or 'unknown://'}/{dataset.get('name') or ''}"


def build_entities(lineage: dict[str, Any], project_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    jobs = lineage.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise AzureError("The lineage document has no jobs to push.")
    if len(jobs) > MAX_JOBS:
        raise AzureError(f"The lineage document has {len(jobs)} jobs; the limit per push is {MAX_JOBS}.")
    datasets: dict[str, dict[str, Any]] = {}
    processes: list[dict[str, Any]] = []

    def ref(dataset: dict[str, Any]) -> dict[str, Any]:
        qn = dataset_qualified_name(dataset)
        datasets.setdefault(
            qn,
            {"typeName": DATASET_TYPE, "attributes": {"qualifiedName": qn, "name": dataset.get("name") or qn}},
        )
        return {"typeName": DATASET_TYPE, "uniqueAttributes": {"qualifiedName": qn}}

    for job in jobs:
        if not isinstance(job, dict):
            continue
        name = str((job.get("job") or {}).get("name") or "").strip()
        if not name:
            continue
        processes.append(
            {
                "typeName": PROCESS_TYPE,
                "attributes": {
                    "qualifiedName": f"adf-accelerator://{project_id}/{name}",
                    "name": name,
                    "medallionLayer": job.get("medallionLayer"),
                    "inputs": [ref(d) for d in job.get("inputs") or [] if isinstance(d, dict)],
                    "outputs": [ref(d) for d in job.get("outputs") or [] if isinstance(d, dict)],
                },
            }
        )
    if not processes:
        raise AzureError("The lineage document has no named jobs to push.")
    return list(datasets.values()), processes


def push_lineage(config: dict[str, Any], lineage: dict[str, Any], *, project_id: str) -> dict[str, Any]:
    account = (config.get("accountName") or "").strip()
    if not account:
        raise AzureError("Purview push is missing required setting: accountName.")
    datasets, processes = build_entities(lineage, project_id)
    base = f"https://{quote(account)}.purview.azure.com/datamap/api/atlas/v2"
    token = get_token(config, PURVIEW_SCOPE)

    status, body = http_json("POST", f"{base}/types/typedefs", token, TYPEDEFS)
    if status >= 400 and status != 409:  # 409 = the types already exist
        raise AzureError(f"Purview rejected the type definitions (HTTP {status}): {_message(body)}")

    created = updated = 0
    # Datasets first, so each process's uniqueAttributes references resolve.
    for batch_entities in (datasets, processes):
        for start in range(0, len(batch_entities), BATCH_SIZE):
            chunk = batch_entities[start : start + BATCH_SIZE]
            status, body = http_json("POST", f"{base}/entity/bulk", token, {"entities": chunk})
            if status >= 400:
                raise AzureError(f"Purview rejected an entity batch (HTTP {status}): {_message(body)}")
            mutated = (body or {}).get("mutatedEntities") or {}
            created += len(mutated.get("CREATE") or [])
            updated += len(mutated.get("UPDATE") or []) + len(mutated.get("PARTIAL_UPDATE") or [])
    return {
        "account": account,
        "datasets": len(datasets),
        "processes": len(processes),
        "created": created,
        "updated": updated,
    }


def _message(body: Any) -> str:
    if isinstance(body, dict):
        return str(body.get("errorMessage") or (body.get("error") or {}).get("message") or body)[:300]
    return str(body)[:300]
