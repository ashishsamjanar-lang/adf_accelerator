"""Secret-redaction logging filter unit tests (AGENTS.md §30.2)."""
from __future__ import annotations

import logging

from app.logging_utils import (
    REDACTED,
    SecretRedactionFilter,
    install_secret_redaction,
    redact_mapping,
    redact_text,
)


def test_redact_mapping_scrubs_secret_shaped_keys_recursively() -> None:
    data = {
        "gitToken": "ghp_super_secret",
        "nested": {"password": "hunter2", "clientSecret": "abc123"},
        "list": [{"apiKey": "sk-live-xyz"}, {"safe": "value"}],
        "safe": "value",
        "count": 3,
    }

    redacted = redact_mapping(data)

    assert redacted["gitToken"] == REDACTED
    assert redacted["nested"]["password"] == REDACTED
    assert redacted["nested"]["clientSecret"] == REDACTED
    assert redacted["list"][0]["apiKey"] == REDACTED
    assert redacted["list"][1]["safe"] == "value"
    assert redacted["safe"] == "value"
    assert redacted["count"] == 3


def test_redact_text_scrubs_inline_key_value_pairs() -> None:
    text = 'pushing with gitToken=ghp_abc123 and password: "hunter2" to origin'

    redacted = redact_text(text)

    assert "ghp_abc123" not in redacted
    assert "hunter2" not in redacted
    assert REDACTED in redacted
    assert "origin" in redacted  # unrelated text is preserved


def test_redact_text_is_a_noop_when_nothing_secret_shaped_is_present() -> None:
    text = "pushed 3 artifacts to branch feature/foo"
    assert redact_text(text) == text


def test_filter_redacts_a_percent_style_log_record() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="git push failed for token=%s",
        args=("ghp_super_secret",),
        exc_info=None,
    )

    SecretRedactionFilter().filter(record)

    assert "ghp_super_secret" not in record.getMessage()
    assert REDACTED in record.getMessage()


def test_install_secret_redaction_is_idempotent_and_catches_child_loggers() -> None:
    root = logging.getLogger(f"test-root-{id(object())}")
    root.handlers.clear()
    handler = logging.StreamHandler()
    root.addHandler(handler)

    install_secret_redaction(root)
    install_secret_redaction(root)  # must not attach a second copy

    assert sum(isinstance(f, SecretRedactionFilter) for f in handler.filters) == 1

    child = root.getChild("child")
    record = child.makeRecord(child.name, logging.INFO, __file__, 1, "password=%s", ("hunter2",), None)
    for f in handler.filters:
        f.filter(record)

    assert "hunter2" not in record.getMessage()
