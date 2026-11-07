"""Azure control-plane calls the worker makes on a project's behalf.

Two opt-in, per-project integrations (``app.project_integrations``):

* :mod:`.arm_validate` — AGENTS.md §29.1 layer 6: submit generated artifacts as
  an ARM ``validate``-mode deployment against a sandbox resource group. Never
  ``deploy``/apply — no resource is created or changed (§20 stays intact).
* :mod:`.purview` — push registry lineage into Microsoft Purview (Atlas API).

Both authenticate through :func:`.auth.get_token`: a service principal whose
client secret is a Key Vault *reference* (§1/§4 rule 6), or the worker's own
workload/managed identity when no secret reference is configured.
"""
from __future__ import annotations

from .auth import AzureError, get_token, http_json

__all__ = ["AzureError", "get_token", "http_json"]
