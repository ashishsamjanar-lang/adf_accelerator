"""Decrypt half of the per-push Git token encryption (AGENTS.md §23.12 gap #17).

The API encrypts with the matching ``encrypt_secret`` in
``backend/app/crypto.py``; this only tests the worker's half in isolation
(same key, same Fernet mechanics) since the two services don't share an
import graph.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.crypto import decrypt_secret


def test_decrypt_secret_reverses_a_token_encrypted_with_the_same_key() -> None:
    plaintext = "ghp_some_token_value"
    ciphertext = Fernet(settings.git_token_fernet_key.encode("utf-8")).encrypt(plaintext.encode("utf-8")).decode("ascii")

    assert ciphertext != plaintext
    assert decrypt_secret(ciphertext) == plaintext


def test_decrypt_secret_rejects_a_token_from_a_different_key() -> None:
    foreign_key = Fernet.generate_key()
    ciphertext = Fernet(foreign_key).encrypt(b"ghp_some_token_value").decode("ascii")

    with pytest.raises(ValueError):
        decrypt_secret(ciphertext)


def test_decrypt_secret_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        decrypt_secret("not-a-fernet-token")
