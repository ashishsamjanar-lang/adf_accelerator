"""At-rest encryption for the one secret that transits the metastore: a
per-push Git PAT (AGENTS.md §23.12 gap #17).

``request_git_push`` (``services.py``) forwards a caller-supplied token to the
worker via ``Job.payload["gitToken"]`` so the worker can make one provider API
call with it. The worker scrubs the payload immediately after use, but that
scrub shares a transaction with the rest of the push handler — any exception
that isn't the specific ``GitOpsError`` it already catches rolls the scrub
back too, leaving the token in ``jobs.payload`` to be reused (and exposed) on
retry. Encrypting the token before it is ever written closes that: whatever
survives a rollback is ciphertext, not a usable credential.

The worker holds the matching decrypt half (``worker/app/crypto.py``) via the
same ``GIT_TOKEN_FERNET_KEY`` env var — keep the two in sync.
"""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import _DEV_ONLY_FERNET_KEY, settings

log = logging.getLogger("app.crypto")


def _fernet() -> Fernet:
    key = settings.git_token_fernet_key
    if key == _DEV_ONLY_FERNET_KEY:
        log.warning(
            "GIT_TOKEN_FERNET_KEY is unset — using the insecure dev-only default. "
            "Set it in config/platform.env before pushing real Git tokens."
        )
    return Fernet(key.encode("utf-8"))


def encrypt_secret(value: str) -> str:
    """Encrypt ``value`` for storage. Returns an opaque token, never the input."""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Inverse of :func:`encrypt_secret`. Raises ``ValueError`` on a bad/foreign/malformed token."""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Git token could not be decrypted (bad or foreign GIT_TOKEN_FERNET_KEY).") from exc
