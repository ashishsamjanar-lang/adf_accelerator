"""At-rest Git token encryption unit tests (AGENTS.md §23.12 gap #17)."""
from __future__ import annotations

import pytest

from app.crypto import decrypt_secret, encrypt_secret


def test_encrypt_then_decrypt_roundtrips() -> None:
    plaintext = "ghp_super_secret_value"
    ciphertext = encrypt_secret(plaintext)

    assert ciphertext != plaintext
    assert decrypt_secret(ciphertext) == plaintext


def test_decrypt_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        decrypt_secret("not-a-fernet-token")


def test_decrypt_rejects_a_token_from_a_different_key() -> None:
    from cryptography.fernet import Fernet

    foreign_ciphertext = Fernet(Fernet.generate_key()).encrypt(b"ghp_x").decode("ascii")
    with pytest.raises(ValueError):
        decrypt_secret(foreign_ciphertext)
