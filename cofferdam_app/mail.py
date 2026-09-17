"""frappe.sendmail interception via Email Queue before_insert hook.

Routes every outbound email through the cofferdam policy engine before it is
committed to the Email Queue (BR-EMAIL-001..008, BR-EMAIL-DECORATE-001..006).

Fails closed: missing policy file → email blocked (ADR-0005).
Production environment → passes through without modification.
"""

from __future__ import annotations

import logging
from email import message_from_string
from email import policy as email_policy
from typing import Any

import frappe
from cofferdam.mail import (
    check_recipient,
    decorate_body,
    decorate_subject,
    should_decorate,
)
from cofferdam.models import Environment

from cofferdam_app.policy import get_policy, site_policy_path

_log = logging.getLogger("cofferdam_app")


def before_insert_email_queue(doc: Any, method: Any = None) -> None:
    """Intercept Email Queue before insert and apply cofferdam policy.

    Wired via hooks.py doc_events. Frappe passes the Email Queue document and
    the hook method name; both may be Any since frappe is untyped.

    BR-EMAIL-001..008, BR-EMAIL-DECORATE-001..006.
    """
    site: str = frappe.local.site
    policy = get_policy(site)

    if policy is None:
        frappe.throw(
            f"cofferdam: No policy file found. "
            f"Create {site_policy_path(site)} to configure outbound email for this environment."
        )
        return  # frappe.throw() always raises; satisfies mypy's narrowing

    # Production: pass through without any modification (BR-EMAIL-DECORATE-001).
    if policy.environment is Environment.PRODUCTION:
        return

    # Evaluate each recipient. Denied → abort insert; sink → redirect in place.
    for row in doc.get("recipients") or []:
        addr: str = row.recipient or ""
        decision = check_recipient(policy, recipient=addr)

        if not decision.allowed:
            frappe.throw(
                f"cofferdam: Email to {addr!r} blocked "
                f"(env={policy.environment.value}, reason={decision.reason_code}). "
                "Review environment_policy.toml to permit this recipient."
            )
            return  # unreachable; satisfies mypy

        if decision.redirect_to:
            _log.info(
                "cofferdam: redirecting %r → %r (site=%s, reason=%s)",
                addr,
                decision.redirect_to,
                site,
                decision.reason_code,
            )
            row.recipient = decision.redirect_to

    # Apply subject/body decoration (BR-EMAIL-DECORATE-001..006).
    #
    # At the Email Queue layer there is no `subject` field and `message` is the
    # fully-assembled MIME document (subject is a header, body lives in one or
    # more parts). So decorate inside the MIME instead of treating it as plain
    # strings. Two constraints from Frappe:
    #   * bodies are quoted-printable utf-8 (email_body.py), and its placeholder
    #     tokens (<!--email_open_check-->, <!--unsubscribe_url-->, ...) are
    #     substituted post-insert via a raw str.replace on this MIME. decorate_body
    #     only *prepends* a banner, so token-bearing lines stay byte-stable; we
    #     re-encode touched parts as QP utf-8 to keep those tokens literal.
    if should_decorate(policy):
        raw: str = doc.message or ""
        if raw:
            msg = message_from_string(raw, policy=email_policy.SMTP)
            env: str = policy.environment.value

            subject: str = msg["Subject"] or ""
            del msg["Subject"]
            msg["Subject"] = decorate_subject(subject, environment=env)

            for part in msg.walk():
                if part.is_multipart():
                    continue
                if part.get_content_type() not in ("text/plain", "text/html"):
                    continue
                if (part.get_content_disposition() or "").lower() == "attachment":
                    continue
                body: str = part.get_content()
                decorated = decorate_body(body, environment=env)
                if decorated != body:
                    part.set_content(
                        decorated,
                        subtype=part.get_content_subtype(),
                        charset="utf-8",
                        cte="quoted-printable",
                    )

            doc.message = msg.as_string()
