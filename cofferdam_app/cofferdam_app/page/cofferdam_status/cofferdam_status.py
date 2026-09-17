"""Controller for the Cofferdam Status page (read-only operational dashboard).

All data assembly happens here; the JS layer only renders. Every method is
whitelisted and gated to System Manager.

Design constraint (see docs/status-page-plan.md): this module must never import
``cofferdam`` (or ``cofferdam_app.policy``, which imports it) at module scope.
``cofferdam_app`` can be installed and running while the ``cofferdam`` library is
absent — the hooks import it lazily — so the page must render *despite* a missing
library in order to report that fact. All library access goes through
``_load_library()`` inside a try/except.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import frappe

_ROLE = "System Manager"

# Interception coverage table. The two live rows correspond to the doc_events
# wired in hooks.py; the rest are planned.
_COVERAGE: list[dict[str, Any]] = [
    {"path": "frappe.sendmail() → Email Queue", "intercepted": True, "note": "before_insert hook"},
    {
        "path": "Frappe Webhook → Webhook Request Log",
        "intercepted": True,
        "note": "before_insert hook (confirmed on v16)",
    },
    {"path": "ERPNext Slack Webhook", "intercepted": False, "note": "Planned"},
    {"path": "ERPNext payment gateways", "intercepted": False, "note": "Planned"},
    {"path": "ERPNext shipping carriers", "intercepted": False, "note": "Planned"},
    {"path": "ERPNext e-commerce connectors", "intercepted": False, "note": "Planned"},
]


def _load_library() -> tuple[dict[str, Any] | None, str | None]:
    """Lazily import cofferdam and the app policy helpers.

    Returns ``(bundle, None)`` on success or ``(None, error_message)`` if the
    ``cofferdam`` library is not importable. ``cofferdam_app.policy`` imports
    ``cofferdam`` at its top, so a missing library surfaces here as ImportError.
    """
    try:
        import cofferdam

        from cofferdam_app import policy as app_policy
    except ImportError as exc:
        return None, f"cofferdam library not importable: {exc}"
    return {"cofferdam": cofferdam, "app_policy": app_policy}, None


def _app_version() -> str:
    try:
        import cofferdam_app

        return getattr(cofferdam_app, "__version__", "unknown")
    except Exception:  # pragma: no cover - the app is always importable here
        return "unknown"


def _abs_policy_path() -> str:
    """Absolute path to this site's environment_policy.toml."""
    return os.path.abspath(frappe.get_site_path("environment_policy.toml"))


@frappe.whitelist()
def get_data() -> dict[str, Any]:
    """Assemble the full dashboard payload. Never raises for a missing library."""
    frappe.only_for(_ROLE)
    site: str = frappe.local.site
    errors: list[str] = []

    lib, lib_err = _load_library()
    library_installed = lib is not None
    if lib_err:
        errors.append(lib_err)

    data: dict[str, Any] = {
        "library_installed": library_installed,
        "site": site,
        "environment": None,
        "policy_file_path": None,
        "policy_file_exists": False,
        "policy_file_mtime": None,
        "policy_loaded_in_this_worker": False,
        "library_version": None,
        "app_version": _app_version(),
        "default_decision": None,
        "mail": None,
        "integrations": [],
        "effects": [],
        "credentials": [],
        "coverage": list(_COVERAGE),
        "errors": errors,
    }

    if lib is None:
        return data

    cofferdam = lib["cofferdam"]
    app_policy = lib["app_policy"]

    data["library_version"] = getattr(cofferdam, "__version__", None)
    data["policy_file_path"] = app_policy.policy_path(site)
    data["policy_loaded_in_this_worker"] = site in app_policy._policy_cache

    abs_path = _abs_policy_path()
    if os.path.exists(abs_path):
        data["policy_file_exists"] = True
        mtime = os.stat(abs_path).st_mtime
        data["policy_file_mtime"] = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
    else:
        errors.append(f"policy file not found at {data['policy_file_path']}")
        return data

    # Load fresh from disk for display (this is not the hot path). The cache
    # status above is reported separately and truthfully.
    try:
        policy = cofferdam.load_policy(abs_path)
    except Exception as exc:
        errors.append(f"policy load failed: {exc}")
        return data

    _populate_from_policy(data, policy)
    return data


def _populate_from_policy(data: dict[str, Any], policy: Any) -> None:
    """Fill the display fields from a parsed Policy. No secrets are emitted."""
    data["environment"] = policy.environment.value
    data["default_decision"] = policy.default_decision.value

    if policy.mail is not None:
        data["mail"] = {
            "mode": policy.mail.mode,
            "sink": policy.mail.sink,
            "allow_domains": list(policy.mail.allow_domains),
            "decorate": policy.mail.decorate,
        }

    for name, integ in policy.integrations.items():
        data["integrations"].append(
            {
                "name": name,
                "kind": integ.kind.value,
                "enabled": integ.enabled,
                "credential": integ.credential,
                "allowed_hosts": list(integ.allowed_hosts),
                "allowed_methods": list(integ.allowed_methods),
                "allowed_operations": list(integ.allowed_operations),
                "allow_authorize": integ.allow_authorize,
                "allow_capture": integ.allow_capture,
            }
        )

    for kind, scopes in policy.effects.items():
        for scope, rule in scopes.items():
            data["effects"].append(
                {
                    "kind": kind,
                    "scope": scope,
                    "enabled": rule.enabled,
                    "allow_domains": list(rule.allow_domains),
                }
            )

    for name, cred in policy.credentials.items():
        secret_env = cred.secret_env
        data["credentials"].append(
            {
                "name": name,
                "profile": cred.profile,
                "secret_env": secret_env,
                "source": "env" if secret_env else "inline",
                "env_var_present": bool(os.environ.get(secret_env)) if secret_env else False,
            }
        )


@frappe.whitelist()
def reload_policy_action() -> dict[str, Any]:
    """Evict *this worker's* cached policy. Per-process only — not fleet-wide."""
    frappe.only_for(_ROLE)
    lib, lib_err = _load_library()
    if lib is None:
        return {"ok": False, "message": lib_err}

    app_policy = lib["app_policy"]
    site: str = frappe.local.site
    was_loaded = site in app_policy._policy_cache
    app_policy.reload_policy(site)
    return {
        "ok": True,
        "message": (
            f"Evicted this worker's cached policy for {site} "
            f"(was {'loaded' if was_loaded else 'not loaded'}). "
            "Other worker processes reload on their next policy miss or restart."
        ),
    }


@frappe.whitelist()
def validate_config() -> dict[str, Any]:
    """Load the policy fresh with strict checks (verifies each secret_env is set)."""
    frappe.only_for(_ROLE)
    lib, lib_err = _load_library()
    if lib is None:
        return {"ok": False, "message": lib_err, "problems": []}

    cofferdam = lib["cofferdam"]
    app_policy = lib["app_policy"]
    site: str = frappe.local.site
    display_path = app_policy.policy_path(site)
    abs_path = _abs_policy_path()

    if not os.path.exists(abs_path):
        return {"ok": False, "message": f"No policy file at {display_path}.", "problems": []}

    try:
        cofferdam.load_policy(abs_path, strict=True)
    except Exception as exc:
        problems = list(getattr(exc, "problems", None) or [])
        return {"ok": False, "message": f"Validation failed: {exc}", "problems": problems}

    return {"ok": True, "message": f"Policy at {display_path} is valid (strict).", "problems": []}


@frappe.whitelist()
def dry_run_email(recipient: str) -> dict[str, Any]:
    """Evaluate what cofferdam would do with an email to *recipient*.

    Pure decision — no Email Queue row is inserted. Mirrors the hook in
    cofferdam_app.mail, including the PRODUCTION pass-through short-circuit.
    """
    frappe.only_for(_ROLE)
    recipient = (recipient or "").strip()
    if not recipient:
        return {"ok": False, "message": "Provide a recipient address."}

    lib, lib_err = _load_library()
    if lib is None:
        return {"ok": False, "message": lib_err}

    app_policy = lib["app_policy"]
    site: str = frappe.local.site
    policy = app_policy.get_policy(site)
    if policy is None:
        return {
            "ok": False,
            "message": "No policy loaded (missing or invalid policy file). "
            "Email would be blocked (fail-closed).",
        }

    from cofferdam.mail import check_recipient, decorate_email
    from cofferdam.models import Environment

    if policy.environment is Environment.PRODUCTION:
        return {
            "ok": True,
            "recipient": recipient,
            "environment": "production",
            "allowed": True,
            "redirect_to": None,
            "reason_code": "production_passthrough",
            "decorated_subject": None,
            "message": (
                f"PRODUCTION: email to {recipient} passes through "
                "unmodified (no interception)."
            ),
        }

    decision = check_recipient(policy, recipient=recipient)
    decorated_subject, _ = decorate_email("Test subject", "Test body", policy=policy)
    if not decision.allowed:
        msg = (
            f"BLOCKED: {recipient} (env={policy.environment.value}, "
            f"reason={decision.reason_code})."
        )
    elif decision.redirect_to:
        msg = f"REDIRECTED: {recipient} → {decision.redirect_to} (env={policy.environment.value})."
    else:
        msg = f"ALLOWED: {recipient} (env={policy.environment.value})."

    return {
        "ok": True,
        "recipient": recipient,
        "environment": policy.environment.value,
        "allowed": decision.allowed,
        "redirect_to": decision.redirect_to,
        "reason_code": decision.reason_code,
        "decorated_subject": decorated_subject,
        "message": msg,
    }


@frappe.whitelist()
def dry_run_webhook(url: str) -> dict[str, Any]:
    """Evaluate what cofferdam would do with a webhook delivery to *url*.

    Pure decision — no Webhook Request Log row is inserted. Mirrors the hook in
    cofferdam_app.webhooks, including the PRODUCTION pass-through short-circuit.
    """
    frappe.only_for(_ROLE)
    url = (url or "").strip()
    if not url:
        return {"ok": False, "message": "Provide a webhook URL."}

    lib, lib_err = _load_library()
    if lib is None:
        return {"ok": False, "message": lib_err}

    app_policy = lib["app_policy"]
    site: str = frappe.local.site
    policy = app_policy.get_policy(site)
    if policy is None:
        return {
            "ok": False,
            "message": "No policy loaded (missing or invalid policy file). "
            "Webhook would be blocked (fail-closed).",
        }

    from urllib.parse import urlparse

    from cofferdam.models import Environment

    if policy.environment is Environment.PRODUCTION:
        return {
            "ok": True,
            "url": url,
            "environment": "production",
            "allowed": True,
            "reason_code": "production_passthrough",
            "message": f"PRODUCTION: webhook to {url} passes through unmodified (no interception).",
        }

    host = urlparse(url).hostname or ""
    # Constants mirror cofferdam_app.webhooks.
    decision = policy.decide(
        integration="frappe_webhooks",
        kind="webhook",
        operation="deliver",
        method="POST",
        host=host,
    )
    verdict = "ALLOWED" if decision.allowed else "BLOCKED"
    return {
        "ok": True,
        "url": url,
        "host": host,
        "environment": policy.environment.value,
        "allowed": decision.allowed,
        "reason_code": decision.reason_code,
        "message": (
            f"{verdict}: {url} (host={host or '?'}, "
            f"env={policy.environment.value}, reason={decision.reason_code})."
        ),
    }
