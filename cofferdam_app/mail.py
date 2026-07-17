"""frappe.sendmail interception via Email Queue before_insert hook.

Routes every outbound email through the cofferdam policy engine before it is
committed to the Email Queue (BR-EMAIL-001..008, BR-EMAIL-DECORATE-001..006).

Fails closed: missing policy file → email blocked (ADR-0005).
Production environment → passes through without modification.
"""

from __future__ import annotations

import logging
from typing import Any

import frappe

from cofferdam import Policy, load_policy
from cofferdam.errors import CofferdamError, PolicyFileNotFoundError
from cofferdam.mail import check_recipient, decorate_email
from cofferdam.models import Environment

_policy_cache: dict[str, Policy] = {}
_log = logging.getLogger("cofferdam_app")


def _get_policy(site: str) -> Policy | None:
    """Load and cache the cofferdam policy for *site* (BR-EMAIL-001).

    Returns None on load failure; caller is responsible for fail-closed action.
    Caching is per-process; call reload_policy() to evict (Q6 / ADR-0010).
    """
    if site in _policy_cache:
        return _policy_cache[site]
    path = f"sites/{site}/environment_policy.toml"
    try:
        policy = load_policy(path)
    except PolicyFileNotFoundError:
        _log.warning("cofferdam: no policy file at %s — email will be blocked", path)
        return None
    except CofferdamError as exc:
        _log.error("cofferdam: policy load failed for %s: %s", site, exc)
        return None
    _policy_cache[site] = policy
    return policy


def reload_policy(site: str | None = None) -> None:
    """Evict the cached policy for *site* (or all sites) to force a reload on next use."""
    if site is None:
        _policy_cache.clear()
    else:
        _policy_cache.pop(site, None)


def before_insert_email_queue(doc: Any, method: Any = None) -> None:  # noqa: ANN401
    """Intercept Email Queue before insert and apply cofferdam policy.

    Wired via hooks.py doc_events. Frappe passes the Email Queue document and
    the hook method name; both may be Any since frappe is untyped.

    BR-EMAIL-001..008, BR-EMAIL-DECORATE-001..006.
    """
    site: str = frappe.local.site
    policy = _get_policy(site)

    if policy is None:
        path = f"sites/{site}/environment_policy.toml"
        frappe.throw(
            f"cofferdam: No policy file found. "
            f"Create {path} to configure outbound email for this environment."
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
    subject: str = doc.subject or ""
    message: str = doc.message or ""
    doc.subject, doc.message = decorate_email(subject, message, policy=policy)
