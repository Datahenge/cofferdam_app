"""Shared policy loading, path resolution, and preflight helpers for cofferdam_app.

Policy files live at ``sites/{site}/environment_policy.toml`` (BR-API-001). Two
concerns are kept deliberately separate (issue #1):

* **CLI / plain-library** callers get a cwd-relative path from :func:`policy_path`.
* **Frappe app** callers get a bench-absolute path resolved via
  ``frappe.get_site_path``, so the policy loads correctly regardless of the
  process's current working directory. Frappe workers, the test runner, and
  bench commands do not always run from the bench root; a cwd-relative path
  produced false fail-closed denials there.

Caching is per-process, keyed by site name. Explicit eviction only — no mtime
checking in v1 (Q6 / ADR-0010). Call :func:`reload_policy` after a policy change.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cofferdam import Policy, load_policy
from cofferdam.decisions import Decision
from cofferdam.errors import CofferdamError, PolicyFileNotFoundError
from cofferdam.http import host_from_url

if TYPE_CHECKING:  # pragma: no cover - typing only
    from types import ModuleType

_POLICY_FILENAME = "environment_policy.toml"

_policy_cache: dict[str, Policy] = {}
_log = logging.getLogger("cofferdam_app")


# ---------------------------------------------------------------------------
# Path resolution (issue #1, part 1)
# ---------------------------------------------------------------------------


def policy_path(site: str) -> str:
    """Return the cwd-relative policy path for *site* (CLI / plain-library use).

    This is intentionally independent of Frappe. Frappe app code should prefer
    :func:`resolve_policy_path`, which returns a bench-absolute path.
    """
    return f"sites/{site}/{_POLICY_FILENAME}"


def _get_frappe() -> ModuleType | None:
    """Return the ``frappe`` module if importable, else None."""
    try:
        import frappe
    except ModuleNotFoundError:
        return None
    module: ModuleType = frappe
    return module


def _frappe_site_path() -> str | None:
    """Resolve the absolute policy path via ``frappe.get_site_path``.

    Returns None when Frappe is not importable, exposes no ``get_site_path``, or
    does not hand back a real ``str`` (e.g. a test double). The ``str`` check is
    what lets this degrade cleanly to the cwd-relative fallback outside a real
    bench without special-casing the test environment.
    """
    frappe = _get_frappe()
    if frappe is None:
        return None
    getter = getattr(frappe, "get_site_path", None)
    if getter is None:
        return None
    try:
        path = getter(_POLICY_FILENAME)
    except Exception as exc:  # any failure means "no bench-absolute path"
        _log.debug("cofferdam: frappe.get_site_path unavailable (%s); using cwd-relative path", exc)
        return None
    return path if isinstance(path, str) else None


def resolve_policy_path(site: str) -> tuple[str, str]:
    """Resolve the policy path for *site* and report where it came from.

    Returns ``(path, source)`` where ``source`` is ``"frappe"`` for a
    bench-absolute path (cwd-independent) or ``"cwd_relative"`` for the
    plain-library fallback. Callers may record ``source`` in a ledger for
    diagnostics.
    """
    frappe_path = _frappe_site_path()
    if frappe_path is not None:
        return frappe_path, "frappe"
    return policy_path(site), "cwd_relative"


def site_policy_path(site: str) -> str:
    """Return just the resolved policy path (Frappe-aware) for *site*.

    Convenience for user-facing messages that should name the real file location.
    """
    return resolve_policy_path(site)[0]


def _current_site() -> str:
    """Return the bound Frappe site name, or raise if unavailable."""
    frappe = _get_frappe()
    site = getattr(getattr(frappe, "local", None), "site", None)
    if isinstance(site, str) and site:
        return site
    raise CofferdamError(
        "no site supplied and no bound Frappe site is available "
        "(pass site=... when calling outside a Frappe request)"
    )


# ---------------------------------------------------------------------------
# Policy loading + caching
# ---------------------------------------------------------------------------


def get_policy(site: str) -> Policy | None:
    """Return the cached policy for *site*, loading it on first call.

    The policy file is resolved via :func:`resolve_policy_path` so the load does
    not depend on the process's current working directory (issue #1). Returns
    None on any load failure; callers must fail closed (ADR-0005).
    """
    if site in _policy_cache:
        return _policy_cache[site]
    path, _source = resolve_policy_path(site)
    try:
        p = load_policy(path)
    except PolicyFileNotFoundError:
        _log.warning("cofferdam: no policy file at %s — action will be blocked", path)
        return None
    except CofferdamError as exc:
        _log.error("cofferdam: policy load failed for %s: %s", path, exc)
        return None
    _policy_cache[site] = p
    return p


def reload_policy(site: str | None = None) -> None:
    """Evict the cached policy for *site* (or all sites) to force a reload on next use."""
    if site is None:
        _policy_cache.clear()
    else:
        _policy_cache.pop(site, None)


# ---------------------------------------------------------------------------
# Reusable HTTP preflight (issue #1, part 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightResult:
    """Outcome of :func:`preflight_http`.

    Unpacks as ``decision, token`` for the common call site::

        decision, token = preflight_http(...)

    while still exposing the parsed ``host`` and the policy file's
    ``policy_path`` / ``policy_source`` for ledger records and diagnostics.

    ``token`` is the resolved secret. It is present only when a credential was
    *requested* (``require_credential=True``) **and** the decision allowed the
    call. It is never logged, and no field here echoes a secret value
    (BR-LOG-002).
    """

    decision: Decision
    token: str | None
    host: str
    policy_path: str
    policy_source: str

    def __iter__(self) -> Iterator[object]:
        # Support: decision, token = preflight_http(...)
        yield self.decision
        yield self.token


def _deny(
    reason_code: str,
    *,
    environment: str | None,
    integration: str,
    kind: str | None,
    operation: str | None,
    method: str | None,
    host: str | None,
    credential: str | None,
    detail: str = "",
) -> Decision:
    """Build a fail-closed (denied) Decision with redacted, log-safe context."""
    return Decision(
        allowed=False,
        reason_code=reason_code,
        environment=environment,
        integration=integration,
        kind=kind,
        operation=operation,
        method=method,
        host=host,
        credential=credential,  # reference name only, never the secret
        detail=detail,
    )


def preflight_http(
    *,
    integration: str,
    operation: str,
    method: str,
    url: str,
    kind: str | None = None,
    credential: str | None = None,
    require_credential: bool = False,
    site: str | None = None,
) -> PreflightResult:
    """Run the full cofferdam preflight for one outbound HTTP side effect.

    This owns the pattern that Frappe callers previously hand-rolled: load the
    site policy (fail closed if missing/invalid), parse the host from ``url``,
    evaluate the policy, and resolve the credential **only after** an allow. It
    is fail-closed throughout (ADR-0005) and never logs or embeds a secret value
    (BR-LOG-002).

    :param require_credential: when True, the allowed call also resolves
        ``credential`` to its secret; the resolved value is returned as
        ``token``. When False, ``token`` is always None even if ``credential``
        participates in the decision.
    :param site: site name; defaults to the bound Frappe site.
    :returns: a :class:`PreflightResult`. Unpack as ``decision, token``; inspect
        ``decision.allowed`` before acting.
    """
    if site is None:
        site = _current_site()

    path, source = resolve_policy_path(site)

    def _result(decision: Decision, token: str | None, host: str) -> PreflightResult:
        return PreflightResult(decision, token, host, path, source)

    # Parse the host up front (BR-HOST-002). A URL we cannot parse denies.
    try:
        host = host_from_url(url)
    except CofferdamError:
        return _result(
            _deny(
                "invalid_url",
                environment=None,
                integration=integration,
                kind=kind,
                operation=operation,
                method=method,
                host=None,
                credential=credential,
                detail="could not parse a hostname from the request URL",
            ),
            None,
            "",
        )

    policy = get_policy(site)
    if policy is None:
        return _result(
            _deny(
                "policy_unavailable",
                environment=None,
                integration=integration,
                kind=kind,
                operation=operation,
                method=method,
                host=host,
                credential=credential,
                detail=f"no usable policy at {path}",
            ),
            None,
            host,
        )

    decision = policy.decide(
        integration=integration,
        kind=kind,
        operation=operation,
        method=method,
        host=host,
        credential=credential,
    )
    if not decision.allowed:
        return _result(decision, None, host)

    # Allowed. Resolve the credential only now, and only if requested.
    if not require_credential:
        return _result(decision, None, host)

    if credential is None:
        return _result(
            _deny(
                "credential_required",
                environment=policy.environment.value,
                integration=integration,
                kind=kind,
                operation=operation,
                method=method,
                host=host,
                credential=None,
                detail="require_credential=True but no credential was named",
            ),
            None,
            host,
        )

    try:
        token = policy.resolve_secret(credential)
    except CofferdamError as exc:
        # Resolution failure (undefined credential, unset env var, …). The
        # cofferdam credential layer never puts a secret value in the message,
        # so str(exc) is safe to surface.
        return _result(
            _deny(
                "credential_unresolved",
                environment=policy.environment.value,
                integration=integration,
                kind=kind,
                operation=operation,
                method=method,
                host=host,
                credential=credential,
                detail=str(exc),
            ),
            None,
            host,
        )

    return _result(decision, token, host)
