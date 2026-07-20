"""Tests for cofferdam_app.mail — Email Queue before_insert interception.

BR-EMAIL-001..008, BR-EMAIL-DECORATE-001..006.
BR-TEST-004: runs without a Frappe bench.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from cofferdam import loads_policy
from cofferdam_app.mail import before_insert_email_queue

# ---------------------------------------------------------------------------
# Inline policies
# ---------------------------------------------------------------------------

_STAGING_DENY = """
environment = "staging"
[mail]
mode = "deny"
"""

_STAGING_SINK = """
environment = "staging"
[mail]
mode = "sink"
sink = "dev@internal.test"
"""

_STAGING_ALLOW_INTERNAL = """
environment = "staging"
[mail]
mode = "allow_internal"
allow_domains = ["corp.example.com"]
"""

_STAGING_DECORATE_OFF = """
environment = "staging"
[mail]
mode = "sink"
sink = "dev@internal.test"
decorate = false
"""

_PRODUCTION = """
environment = "production"
[mail]
mode = "deny"
"""


def _policy(toml: str) -> Any:
    return loads_policy(toml)


# ---------------------------------------------------------------------------
# Doc helper
# ---------------------------------------------------------------------------


class _EmailDoc:
    """Minimal stand-in for a Frappe Email Queue document."""

    def __init__(
        self, recipients: list[str], subject: str = "Subject", message: str = "Body"
    ) -> None:
        self._rows = [SimpleNamespace(recipient=addr) for addr in recipients]
        self.subject = subject
        self.message = message

    def get(self, key: str, default: Any = None) -> Any:
        return self._rows if key == "recipients" else default

    @property
    def recipient_list(self) -> list[str]:
        return [row.recipient for row in self._rows]


# ---------------------------------------------------------------------------
# Fail-closed: missing policy (ADR-0005)
# ---------------------------------------------------------------------------


def test_no_policy_throws(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing policy file → frappe.throw() called; email blocked (ADR-0005)."""
    monkeypatch.setattr("cofferdam_app.mail.get_policy", lambda _site: None)
    doc = _EmailDoc(["user@example.com"])
    with pytest.raises(Exception, match="cofferdam:"):
        before_insert_email_queue(doc)


# ---------------------------------------------------------------------------
# Production pass-through (BR-EMAIL-DECORATE-001)
# ---------------------------------------------------------------------------


def test_production_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production environment: hook is a no-op — no throw, no decoration."""
    monkeypatch.setattr("cofferdam_app.mail.get_policy", lambda _site: _policy(_PRODUCTION))
    doc = _EmailDoc(["anyone@external.com"], subject="Invoice", message="Hello.")
    before_insert_email_queue(doc)  # must not raise
    assert doc.subject == "Invoice"
    assert doc.message == "Hello."


# ---------------------------------------------------------------------------
# Deny mode (BR-EMAIL-003)
# ---------------------------------------------------------------------------


def test_deny_mode_blocks_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-EMAIL-003: deny mode → frappe.throw() called for any recipient."""
    monkeypatch.setattr("cofferdam_app.mail.get_policy", lambda _site: _policy(_STAGING_DENY))
    doc = _EmailDoc(["customer@external.com"])
    with pytest.raises(Exception, match="cofferdam:"):
        before_insert_email_queue(doc)


# ---------------------------------------------------------------------------
# Sink mode (BR-EMAIL-005)
# ---------------------------------------------------------------------------


def test_sink_mode_redirects_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-EMAIL-005: sink mode rewrites the recipient address to the sink."""
    monkeypatch.setattr("cofferdam_app.mail.get_policy", lambda _site: _policy(_STAGING_SINK))
    doc = _EmailDoc(["customer@external.com"])
    before_insert_email_queue(doc)
    assert doc.recipient_list == ["dev@internal.test"]


def test_sink_mode_redirects_all_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-EMAIL-005: every recipient is redirected to the sink address."""
    monkeypatch.setattr("cofferdam_app.mail.get_policy", lambda _site: _policy(_STAGING_SINK))
    doc = _EmailDoc(["a@external.com", "b@external.com"])
    before_insert_email_queue(doc)
    assert doc.recipient_list == ["dev@internal.test", "dev@internal.test"]


# ---------------------------------------------------------------------------
# Allow-internal mode (BR-EMAIL-004, BR-EMAIL-007)
# ---------------------------------------------------------------------------


def test_allow_internal_permits_allowed_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-EMAIL-004: recipient in allow_domains passes without a throw."""
    monkeypatch.setattr(
        "cofferdam_app.mail.get_policy", lambda _site: _policy(_STAGING_ALLOW_INTERNAL)
    )
    doc = _EmailDoc(["staff@corp.example.com"])
    before_insert_email_queue(doc)  # must not raise


def test_allow_internal_blocks_external_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-EMAIL-003: recipient outside allow_domains is blocked."""
    monkeypatch.setattr(
        "cofferdam_app.mail.get_policy", lambda _site: _policy(_STAGING_ALLOW_INTERNAL)
    )
    doc = _EmailDoc(["customer@external.com"])
    with pytest.raises(Exception, match="cofferdam:"):
        before_insert_email_queue(doc)


# ---------------------------------------------------------------------------
# Decoration (BR-EMAIL-DECORATE-001..006)
# ---------------------------------------------------------------------------


def test_subject_decorated_in_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-EMAIL-DECORATE-003: subject gets environment prefix in non-production."""
    monkeypatch.setattr("cofferdam_app.mail.get_policy", lambda _site: _policy(_STAGING_SINK))
    doc = _EmailDoc(["a@x.com"], subject="Weekly Report", message="Hello.")
    before_insert_email_queue(doc)
    assert doc.subject == "STAGING - Weekly Report"


def test_body_decorated_in_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-EMAIL-DECORATE-006: plain-text body gets environment notice prepended."""
    monkeypatch.setattr("cofferdam_app.mail.get_policy", lambda _site: _policy(_STAGING_SINK))
    doc = _EmailDoc(["a@x.com"], subject="Hi", message="Original body.")
    before_insert_email_queue(doc)
    assert "[STAGING]" in doc.message
    assert "Original body." in doc.message


def test_html_body_decorated_in_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-EMAIL-DECORATE-005: HTML body gets banner div injected."""
    monkeypatch.setattr("cofferdam_app.mail.get_policy", lambda _site: _policy(_STAGING_SINK))
    html = "<html><body><p>Hello</p></body></html>"
    doc = _EmailDoc(["a@x.com"], subject="Hi", message=html)
    before_insert_email_queue(doc)
    assert "<div" in doc.message
    assert "STAGING" in doc.message


def test_no_decoration_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-EMAIL-DECORATE-002: decorate=false suppresses all decoration."""
    monkeypatch.setattr(
        "cofferdam_app.mail.get_policy", lambda _site: _policy(_STAGING_DECORATE_OFF)
    )
    doc = _EmailDoc(["a@x.com"], subject="Report", message="Hello.")
    before_insert_email_queue(doc)
    assert doc.subject == "Report"
    assert doc.message == "Hello."


def test_production_receives_no_decoration(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-EMAIL-DECORATE-001: production pass-through leaves subject and body unchanged."""
    monkeypatch.setattr("cofferdam_app.mail.get_policy", lambda _site: _policy(_PRODUCTION))
    doc = _EmailDoc(["a@x.com"], subject="Invoice", message="<html><body>Hi</body></html>")
    before_insert_email_queue(doc)
    assert doc.subject == "Invoice"
    assert "<div" not in doc.message
