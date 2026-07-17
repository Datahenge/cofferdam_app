"""Shared policy loading and per-process caching for cofferdam_app.

Policy files live at sites/{site}/environment_policy.toml (BR-API-001).
Caching is per-process, keyed by site name. Explicit eviction only — no
mtime checking in v1 (Q6 / ADR-0010). Call reload_policy() to force a
fresh load after a policy file change.
"""

from __future__ import annotations

import logging

from cofferdam import Policy, load_policy
from cofferdam.errors import CofferdamError, PolicyFileNotFoundError

_policy_cache: dict[str, Policy] = {}
_log = logging.getLogger("cofferdam_app")


def policy_path(site: str) -> str:
    return f"sites/{site}/environment_policy.toml"


def get_policy(site: str) -> Policy | None:
    """Return the cached policy for *site*, loading it on first call.

    Returns None on any load failure; callers must fail closed (ADR-0005).
    """
    if site in _policy_cache:
        return _policy_cache[site]
    path = policy_path(site)
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
