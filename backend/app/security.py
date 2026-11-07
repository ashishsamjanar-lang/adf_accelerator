"""Password hashing + session/CSRF token helpers for ``AUTH_MODE=password``
(AGENTS.md §27.2 extension, §27.3).

Uses stdlib ``hashlib.scrypt`` rather than adding a dependency (``passlib``/
``bcrypt`` aren't installed anywhere in this project; ``cryptography`` is, but
that's used for at-rest Git-token encryption — see ``app/crypto.py`` — a
different problem from password hashing). scrypt is memory-hard, salted per
password, and has been in the standard library since Python 3.6.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

# scrypt cost parameters — N (CPU/memory cost, must be a power of 2), r
# (block size), p (parallelization). These are the library's own recommended
# interactive defaults (RFC 7914 §2), sized for a login request, not a batch job.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_LENGTH = 32

# Alphabet for generated temporary passwords: unambiguous, no lookalike chars
# (no 0/O, 1/l/I), long enough (16 chars) to be a real secret even though it's
# only ever meant to be used once before a forced change.
_TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
_TEMP_PASSWORD_LENGTH = 16


# Login usernames (AUTH_MODE=password). Lowercase, 2-64 chars, never containing
# "@" — that is what lets one login field take either a username or an email.
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


def normalize_username(value: str) -> str:
    return value.strip().lower()


def username_from_email(email: str) -> str:
    """Visibly-derived fallback when an admin (or the bootstrap CLI) supplies no
    username: the email's local part, lowercased, with disallowed characters
    replaced by ``.``. May still fail ``USERNAME_RE`` (e.g. a 1-char local part)
    — callers validate the result like any typed username."""
    local = normalize_username(email.split("@", 1)[0])
    return re.sub(r"[^a-z0-9._-]", ".", local).strip("._-")[:64]


def hash_password(password: str) -> str:
    """Return an opaque, storable hash. Never logged, never returned by any API."""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_KEY_LENGTH
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time verify. ``stored=None`` (mock/SSO-only account, or an
    account never given a password) always fails rather than raising."""
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(hash_hex)),
        )
        return hmac.compare_digest(derived, bytes.fromhex(hash_hex))
    except (ValueError, TypeError):
        return False


def generate_temp_password() -> str:
    """A one-time password an admin hands to a new/reset user out of band
    (this app has no email sender). Returned once by the API, never again —
    only its hash is ever persisted."""
    return "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(_TEMP_PASSWORD_LENGTH))


def new_token(nbytes: int = 32) -> str:
    """Opaque random token — used for both session ids and CSRF tokens."""
    return secrets.token_urlsafe(nbytes)


# --- Session/CSRF cookies (§27.3) ------------------------------------------
# The session cookie is httpOnly (never readable by frontend JS — the cookie
# itself is the only session artifact, per §27.3's "no token in localStorage"
# rule); the CSRF cookie is deliberately NOT httpOnly so `lib/api/client.ts`
# can read it and echo it back as a header (double-submit CSRF pattern).
SESSION_COOKIE_NAME = "adf_session"
CSRF_COOKIE_NAME = "adf_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
# Methods that mutate state — CSRF-checked in `password` mode only.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
