"""Frappe webhook delivery interception via Webhook Request Log before_insert hook.

Routes every outbound webhook through the cofferdam policy engine before the
delivery is committed to the Webhook Request Log (BR-DECISION-003..010).

Fails closed: missing policy or unknown integration → delivery blocked (ADR-0005).
Production environment → passes through without modification.

Convention: the policy file must contain an [integrations.frappe_webhooks] section
with kind = "webhook" to permit any webhook delivery in non-production environments.
Example:

    [integrations.frappe_webhooks]
    kind = "webhook"
    enabled = true
    allowed_hosts = ["hooks.example.com"]
    allowed_methods = ["POST"]
    allowed_operations = ["deliver"]

Note: the Frappe doctype intercepted here is "Webhook Request Log", which is the
established name in Frappe v15. Verify the doctype name in v16 if webhook
interception does not appear to fire.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import frappe

from cofferdam.models import Environment

from cofferdam_app.policy import get_policy, site_policy_path

_log = logging.getLogger("cofferdam_app")

_INTEGRATION = "frappe_webhooks"
_KIND = "webhook"
_OPERATION = "deliver"
_METHOD = "POST"


def before_insert_webhook_request_log(doc: Any, method: Any = None) -> None:  # noqa: ANN401
    """Intercept Webhook Request Log before insert and apply cofferdam policy.

    Wired via hooks.py doc_events. Uses the standard decision engine
    (BR-DECISION-003..010) with integration name "frappe_webhooks".
    """
    site: str = frappe.local.site
    policy = get_policy(site)

    if policy is None:
        frappe.throw(
            f"cofferdam: No policy file found. "
            f"Create {site_policy_path(site)} to configure webhook delivery for this environment."
        )
        return  # frappe.throw() always raises; satisfies mypy's narrowing

    # Production: pass through without modification.
    if policy.environment is Environment.PRODUCTION:
        return

    # Extract hostname from the delivery URL for host-level policy check.
    url: str = doc.url or ""
    parsed = urlparse(url)
    host: str = parsed.hostname or ""

    decision = policy.decide(
        integration=_INTEGRATION,
        kind=_KIND,
        operation=_OPERATION,
        method=_METHOD,
        host=host,
    )

    if not decision.allowed:
        _log.warning(
            "cofferdam: webhook to %r blocked (env=%s, integration=%s, reason=%s)",
            url,
            policy.environment.value,
            _INTEGRATION,
            decision.reason_code,
        )
        frappe.throw(
            f"cofferdam: Webhook delivery to {url!r} blocked "
            f"(env={policy.environment.value}, reason={decision.reason_code}). "
            "Add [integrations.frappe_webhooks] with the target host to "
            "environment_policy.toml to permit delivery."
        )
