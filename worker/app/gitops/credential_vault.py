"""Real Key Vault-backed Git credential resolution (AGENTS.md §23.12 gap #14).

A ``credential_vault`` value on a Git push is a *reference* — a Key Vault
secret name — never the secret itself (§1/§4 rule 6). Previously this module
didn't exist: ``adapter._resolve_credential`` read the "secret name" straight
out of an env var, which only ever worked in local dev and silently produced
a :class:`~app.gitops.simulated.SimulatedAdapter` push everywhere else, with
no warning that anything was off.

This resolves, in order:
1. A real Azure Key Vault secret named ``credential_vault``, when
   ``KEY_VAULT_NAME`` is configured and the Azure SDK is installed.
2. An env var named ``credential_vault`` (local-dev convenience — lets a
   secret be provided without a live Key Vault).
3. The ``GIT_TOKEN`` fallback env var (no per-project credential at all).

Every fallback step, and every failure, is logged so "why did this push
simulate?" has an answer in the worker logs instead of a silent no-op.
"""
from __future__ import annotations

import logging
import os
import threading

from app.config import settings

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
_client_initialized = False
_client = None


def _vault_name() -> str:
    # Typed settings, not a raw os.environ read (IMPLEMENTATION_PLAN.md gap
    # B-5) — KEY_VAULT_NAME is a fixed, known key. `credential_vault` below
    # stays a dynamic os.environ lookup: it's a per-project secret *name*
    # chosen at runtime, not a static config key.
    return settings.key_vault_name.strip()


def _get_client():
    """Lazily build (and cache) a Key Vault ``SecretClient``, or ``None`` if
    Key Vault isn't configured / the Azure SDK isn't installed / auth fails.
    Never raises — every failure mode here should degrade to the env-var
    fallback, not crash the push.
    """
    global _client_initialized, _client
    if _client_initialized:
        return _client
    with _client_lock:
        if _client_initialized:  # re-check inside the lock
            return _client
        _client_initialized = True
        vault_name = _vault_name()
        if not vault_name or vault_name.startswith("replace-with-"):
            logger.info("KEY_VAULT_NAME is not configured; Git credentials resolve from env vars only.")
            return None
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError:
            logger.warning(
                "KEY_VAULT_NAME=%s is set but the azure-identity/azure-keyvault-secrets packages "
                "aren't installed; falling back to env-var credential resolution.",
                vault_name,
            )
            return None
        try:
            _client = SecretClient(
                vault_url=f"https://{vault_name}.vault.azure.net", credential=DefaultAzureCredential()
            )
        except Exception:  # noqa: BLE001 - any auth/SDK failure degrades, never crashes the push
            logger.exception("Failed to build a Key Vault client for '%s'; falling back to env vars.", vault_name)
            _client = None
        return _client


def resolve_credential(credential_vault: str | None) -> str | None:
    """Resolve a Git credential by reference. Returns ``None`` (never raises)
    when nothing resolves — the caller falls back to the offline
    SimulatedAdapter and that fallback is itself logged by ``get_adapter``.
    """
    if credential_vault:
        client = _get_client()
        if client is not None:
            try:
                secret = client.get_secret(credential_vault)
                return secret.value
            except Exception:  # noqa: BLE001 - Key Vault outage/missing secret/etc.
                logger.warning(
                    "Key Vault secret '%s' could not be resolved from '%s'; falling back to env var / GIT_TOKEN.",
                    credential_vault,
                    _vault_name(),
                    exc_info=True,
                )
        if os.environ.get(credential_vault):
            return os.environ[credential_vault]
    fallback = settings.git_token
    if fallback:
        logger.info("No per-project Git credential resolved for '%s'; using the GIT_TOKEN fallback.", credential_vault)
        return fallback
    return None
