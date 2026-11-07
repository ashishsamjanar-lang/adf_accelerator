"""Decrypt half of the per-push Git token encryption (AGENTS.md §23.12 gap #17).

The API (``backend/app/crypto.py``) encrypts a caller-supplied Git PAT before
writing it to ``Job.payload["gitToken"]``; this module reverses that using the
same ``GIT_TOKEN_FERNET_KEY``. Keep the two files' logic identical.
"""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import _DEV_ONLY_FERNET_KEY, settings

log = logging.getLogger("worker.crypto")


def _fernet() -> Fernet:
    key = settings.git_token_fernet_key
    if key == _DEV_ONLY_FERNET_KEY:
        log.warning(
            "GIT_TOKEN_FERNET_KEY is unset — using the insecure dev-only default. "
            "Set it in config/platform.env (same value as the API) before pushing real Git tokens."
        )
    return Fernet(key.encode("utf-8"))


def decrypt_secret(token: str) -> str:
    """Inverse of the API's ``encrypt_secret``. Raises ``ValueError`` on a bad/foreign/malformed token."""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Git token could not be decrypted (bad or foreign GIT_TOKEN_FERNET_KEY).") from exc
