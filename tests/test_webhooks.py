"""Tests for cofferdam_app.webhooks — Webhook Request Log before_insert interception.

BR-DECISION-003..010.
BR-TEST-004: runs without a Frappe bench.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from cofferdam import loads_policy
from cofferdam_app.webhooks import before_insert_webhook_request_log

# ---------------------------------------------------------------------------
# Inline policies
# ---------------------------------------------------------------------------

_STAGING_NO_WEBHOOKS = 'environment = "staging"\n'

_STAGING_WEBHOOKS_ALLOWED = """
environment = "staging"
[integrations.frappe_webhooks]
kind = "webhook"
enabled = true
allowed_hosts = ["hooks.example.com"]
allowed_methods = ["POST"]
allowed_operations = ["deliver"]
"""

_STAGING_WEBHOOKS_WRONG_HOST = """
environment = "staging"
[integrations.frappe_webhooks]
kind = "webhook"
enabled = true
allowed_hosts = ["hooks.example.com"]
allowed_methods = ["POST"]
allowed_operations = ["deliver"]
"""

_STAGING_WEBHOOKS_DISABLED = """
environment = "staging"
[integrations.frappe_webhooks]
kind = "webhook"
enabled = false
allowed_hosts = ["hooks.example.com"]
allowed_methods = ["POST"]
allowed_operations = ["deliver"]
"""

_PRODUCTION = 'environment = "production"\n'


def _policy(toml: str) -> Any:
    return loads_policy(toml)


def _webhook_doc(url: str) -> SimpleNamespace:
    return SimpleNamespace(url=url)


# ---------------------------------------------------------------------------
# Fail-closed: missing policy (ADR-0005)
# ---------------------------------------------------------------------------


def test_no_policy_throws(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing policy file → frappe.throw() called; delivery blocked (ADR-0005)."""
    monkeypatch.setattr("cofferdam_app.webhooks.get_policy", lambda _site: None)
    doc = _webhook_doc("https://hooks.example.com/endpoint")
    with pytest.raises(Exception, match="cofferdam:"):
        before_insert_webhook_request_log(doc)


# ---------------------------------------------------------------------------
# Production pass-through
# ---------------------------------------------------------------------------


def test_production_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production environment: hook is a no-op — delivery proceeds unblocked."""
    monkeypatch.setattr(
        "cofferdam_app.webhooks.get_policy", lambda _site: _policy(_PRODUCTION)
    )
    doc = _webhook_doc("https://hooks.example.com/endpoint")
    before_insert_webhook_request_log(doc)  # must not raise


# ---------------------------------------------------------------------------
# Unknown integration — fail closed (BR-DECISION-003)
# ---------------------------------------------------------------------------


def test_unknown_integration_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-DECISION-003: no [integrations.frappe_webhooks] section → delivery blocked."""
    monkeypatch.setattr(
        "cofferdam_app.webhooks.get_policy", lambda _site: _policy(_STAGING_NO_WEBHOOKS)
    )
    doc = _webhook_doc("https://hooks.example.com/endpoint")
    with pytest.raises(Exception, match="cofferdam:"):
        before_insert_webhook_request_log(doc)


# ---------------------------------------------------------------------------
# Disabled integration (BR-DECISION-004)
# ---------------------------------------------------------------------------


def test_disabled_integration_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-DECISION-004: frappe_webhooks integration present but disabled → blocked."""
    monkeypatch.setattr(
        "cofferdam_app.webhooks.get_policy",
        lambda _site: _policy(_STAGING_WEBHOOKS_DISABLED),
    )
    doc = _webhook_doc("https://hooks.example.com/endpoint")
    with pytest.raises(Exception, match="cofferdam:"):
        before_insert_webhook_request_log(doc)


# ---------------------------------------------------------------------------
# Host checks (BR-DECISION-010)
# ---------------------------------------------------------------------------


def test_allowed_host_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-DECISION-010: host in allowed_hosts → delivery proceeds."""
    monkeypatch.setattr(
        "cofferdam_app.webhooks.get_policy",
        lambda _site: _policy(_STAGING_WEBHOOKS_ALLOWED),
    )
    doc = _webhook_doc("https://hooks.example.com/api/notify")
    before_insert_webhook_request_log(doc)  # must not raise


def test_disallowed_host_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-DECISION-010: host not in allowed_hosts → delivery blocked."""
    monkeypatch.setattr(
        "cofferdam_app.webhooks.get_policy",
        lambda _site: _policy(_STAGING_WEBHOOKS_WRONG_HOST),
    )
    doc = _webhook_doc("https://production-endpoint.example.com/hook")
    with pytest.raises(Exception, match="cofferdam:"):
        before_insert_webhook_request_log(doc)


def test_empty_url_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty URL yields no hostname; host check fails closed (BR-DECISION-010)."""
    monkeypatch.setattr(
        "cofferdam_app.webhooks.get_policy",
        lambda _site: _policy(_STAGING_WEBHOOKS_ALLOWED),
    )
    doc = _webhook_doc("")
    with pytest.raises(Exception, match="cofferdam:"):
        before_insert_webhook_request_log(doc)
