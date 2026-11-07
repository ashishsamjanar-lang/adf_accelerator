"""Secret-redaction primitive for logging (AGENTS.md §30.2).

The decrypted Git token (see ``app/crypto.py``, §23.12 gap #17) and any other
secret-shaped value only ever exist in memory for the duration of a request —
this filter is what keeps them from ever reaching a log line, structured or
not. Vendored in ``worker/app/logging_utils.py`` (same "build once, shared"
convention as the rest of this app's triple-vendored modules, §23.13) — keep
the two in parity. §28.1's future AI context-stripper is required to reuse
this same key-name pattern rather than becoming a second, independently
drifting secret-detection regex.
"""
from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "***REDACTED***"

_SECRET_KEY_FRAGMENT = (
    r"token|secret|password|passwd|api[_-]?key|client[_-]?secret|credential|"
    r"authorization|private[_-]?key|access[_-]?key"
)

_SECRET_KEY_PATTERN = re.compile(_SECRET_KEY_FRAGMENT, re.IGNORECASE)

# Catches an already-formatted log line containing `key=value`, `key: value`,
# or JSON-style `"key": "value"` where `key` looks secret-shaped — the case a
# structured `redact_mapping()` call can't reach because the value was
# interpolated into a plain message string before logging.
_INLINE_PATTERN = re.compile(
    r"(?P<prefix>[\"']?[A-Za-z0-9_\-]*(?:" + _SECRET_KEY_FRAGMENT + r")[A-Za-z0-9_\-]*[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"']?)(?P<value>[^\s,\"'}]+)(?P=quote)",
    re.IGNORECASE,
)


def redact_mapping(data: Any) -> Any:
    """Recursively redact secret-shaped keys in a dict/list.

    Use this before logging any structured payload — e.g. a worker
    ``job.payload``/``job.result`` — rather than relying on the inline-text
    filter alone.
    """
    if isinstance(data, dict):
        return {
            key: REDACTED if _SECRET_KEY_PATTERN.search(str(key)) else redact_mapping(value)
            for key, value in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [redact_mapping(item) for item in data]
    return data


def redact_text(text: str) -> str:
    """Best-effort redaction of secret-shaped ``key=value``/``"key": "value"`` pairs in free text."""

    def _sub(match: "re.Match[str]") -> str:
        return f"{match.group('prefix')}{match.group('quote')}{REDACTED}{match.group('quote')}"

    return _INLINE_PATTERN.sub(_sub, text)


class SecretRedactionFilter(logging.Filter):
    """Attach to a handler (not just a logger — see ``install_secret_redaction``).

    Resolves the record's final message (applying any ``%``-style args),
    redacts it, then collapses the result back onto ``record.msg`` with
    ``record.args`` cleared so the formatter never re-applies the original
    (unredacted) args on top.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - never let logging crash the app
            return True
        redacted = redact_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_secret_redaction(logger: logging.Logger | None = None) -> None:
    """Idempotently attach the redaction filter to every handler on ``logger`` (default: root).

    Filters attached to a *logger* only run for records logged directly to
    that logger, not to its children — attaching to the *handlers* instead
    (created by ``logging.basicConfig`` or equivalent) catches every record
    that propagates up to them, regardless of which module's logger emitted
    it. Call this once, right after logging is configured.
    """
    target = logger or logging.getLogger()
    for handler in target.handlers:
        if not any(isinstance(f, SecretRedactionFilter) for f in handler.filters):
            handler.addFilter(SecretRedactionFilter())
