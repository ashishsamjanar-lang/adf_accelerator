"""Token acquisition + a tiny JSON-over-HTTPS helper (stdlib ``urllib``, like
the Git adapters). Tests monkeypatch :func:`get_token` / :func:`http_json`."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from app.gitops import credential_vault

logger = logging.getLogger(__name__)


class AzureError(RuntimeError):
    """An Azure call failed in a way worth surfacing to the user."""


def resolve_secret(reference: str) -> str | None:
    """Resolve a Key Vault secret *reference* (name) — Key Vault first, then an
    env var of the same name (local dev). Unlike Git credentials there is no
    global fallback: a service-principal secret must be the one referenced."""
    client = credential_vault._get_client()  # noqa: SLF001 - shared, cached Key Vault client
    if client is not None:
        try:
            return client.get_secret(reference).value
        except Exception:  # noqa: BLE001 - missing secret / outage degrades to the env var
            logger.warning("Key Vault secret '%s' could not be resolved; trying env var.", reference, exc_info=True)
    return os.environ.get(reference) or None


def get_token(config: dict[str, Any], scope: str) -> str:
    """Bearer token for ``scope``. ``config`` carries ``tenantId``/``clientId``/
    ``clientSecretRef``; with no secret reference, the worker's own identity
    (``DefaultAzureCredential`` — workload/managed identity, env credentials)
    is used instead."""
    try:
        from azure.identity import ClientSecretCredential, DefaultAzureCredential
    except ImportError as exc:  # pragma: no cover - installed by default (requirements.txt)
        raise AzureError("azure-identity is not installed in the worker image.") from exc
    secret_ref = (config.get("clientSecretRef") or "").strip()
    try:
        if secret_ref:
            tenant, client_id = (config.get("tenantId") or "").strip(), (config.get("clientId") or "").strip()
            if not tenant or not client_id:
                raise AzureError("tenantId and clientId are required when a client secret reference is set.")
            secret = resolve_secret(secret_ref)
            if not secret:
                raise AzureError(f"Secret reference '{secret_ref}' did not resolve (Key Vault or env var).")
            credential = ClientSecretCredential(tenant, client_id, secret)
        else:
            credential = DefaultAzureCredential()
        return credential.get_token(scope).token
    except AzureError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any auth failure as a domain error
        raise AzureError(f"Azure authentication failed: {exc}") from exc


def http_json(method: str, url: str, token: str, body: Any = None) -> tuple[int, Any]:
    """Returns ``(status, parsed body)`` for 2xx *and* 4xx — callers decide what a
    4xx means (ARM validate reports template errors as 400). 5xx/unreachable raise."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "adf-accelerator-worker")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed Azure hosts
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        raw = exc.read().decode("utf-8", "replace")
        if exc.code >= 500:
            raise AzureError(f"{method} {url} failed ({exc.code}): {raw[:300]}") from exc
        try:
            return exc.code, json.loads(raw or "null")
        except ValueError:
            return exc.code, {"error": {"message": raw[:300]}}
    except urllib.error.URLError as exc:  # pragma: no cover
        raise AzureError(f"{url} is unreachable: {exc.reason}") from exc
