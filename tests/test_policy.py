"""Tests for cofferdam_app.policy — loading, caching, and eviction.

BR-TEST-004: runs without a Frappe bench.
"""

from __future__ import annotations

import pytest
from cofferdam import loads_policy
from cofferdam.errors import CofferdamError, PolicyFileNotFoundError
from cofferdam_app.policy import get_policy, policy_path, reload_policy

_STAGING_TOML = 'environment = "staging"\n'


# ---------------------------------------------------------------------------
# policy_path
# ---------------------------------------------------------------------------


def test_policy_path_format() -> None:
    assert policy_path("mysite.localhost") == "sites/mysite.localhost/environment_policy.toml"


# ---------------------------------------------------------------------------
# get_policy — success path and caching (Q6 / ADR-0010)
# ---------------------------------------------------------------------------


def test_get_policy_returns_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_policy() loads and returns a Policy on first call."""
    expected = loads_policy(_STAGING_TOML)
    monkeypatch.setattr("cofferdam_app.policy.load_policy", lambda _path: expected)

    result = get_policy("staging.localhost")

    assert result is expected


def test_get_policy_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_policy() does not reload on subsequent calls for the same site (Q6 / ADR-0010)."""
    call_count = 0

    def _counting_load(_path: str) -> object:
        nonlocal call_count
        call_count += 1
        return loads_policy(_STAGING_TOML)

    monkeypatch.setattr("cofferdam_app.policy.load_policy", _counting_load)

    get_policy("staging.localhost")
    get_policy("staging.localhost")

    assert call_count == 1


def test_get_policy_different_sites_load_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each site has its own cache entry."""
    loaded: list[str] = []

    def _track_load(path: str) -> object:
        loaded.append(path)
        return loads_policy(_STAGING_TOML)

    monkeypatch.setattr("cofferdam_app.policy.load_policy", _track_load)

    get_policy("site-a.localhost")
    get_policy("site-b.localhost")

    assert len(loaded) == 2


# ---------------------------------------------------------------------------
# get_policy — fail-closed paths (ADR-0005)
# ---------------------------------------------------------------------------


def test_get_policy_file_not_found_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing policy file: get_policy() returns None so caller can fail closed (ADR-0005)."""

    def _raise(_path: str) -> object:
        raise PolicyFileNotFoundError("sites/missing/environment_policy.toml")

    monkeypatch.setattr("cofferdam_app.policy.load_policy", _raise)

    assert get_policy("missing.localhost") is None


def test_get_policy_cofferdam_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed or invalid policy: get_policy() returns None so caller can fail closed."""

    def _raise(_path: str) -> object:
        raise CofferdamError("bad TOML")

    monkeypatch.setattr("cofferdam_app.policy.load_policy", _raise)

    assert get_policy("broken.localhost") is None


# ---------------------------------------------------------------------------
# reload_policy — cache eviction (Q6 / ADR-0010)
# ---------------------------------------------------------------------------


def test_reload_policy_single_site_forces_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """reload_policy(site) evicts one site; next call reloads from disk."""
    call_count = 0

    def _counting_load(_path: str) -> object:
        nonlocal call_count
        call_count += 1
        return loads_policy(_STAGING_TOML)

    monkeypatch.setattr("cofferdam_app.policy.load_policy", _counting_load)

    get_policy("staging.localhost")
    reload_policy("staging.localhost")
    get_policy("staging.localhost")

    assert call_count == 2


def test_reload_policy_none_evicts_all_sites(monkeypatch: pytest.MonkeyPatch) -> None:
    """reload_policy() with no argument evicts every cached site."""
    loaded: list[str] = []

    def _track_load(path: str) -> object:
        loaded.append(path)
        return loads_policy(_STAGING_TOML)

    monkeypatch.setattr("cofferdam_app.policy.load_policy", _track_load)

    get_policy("site-a.localhost")
    get_policy("site-b.localhost")
    reload_policy()
    get_policy("site-a.localhost")
    get_policy("site-b.localhost")

    assert len(loaded) == 4


def test_reload_policy_other_site_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """reload_policy(site) does not evict other sites."""
    call_count: dict[str, int] = {}

    def _counting_load(path: str) -> object:
        call_count[path] = call_count.get(path, 0) + 1
        return loads_policy(_STAGING_TOML)

    monkeypatch.setattr("cofferdam_app.policy.load_policy", _counting_load)

    get_policy("site-a.localhost")
    get_policy("site-b.localhost")
    reload_policy("site-a.localhost")
    get_policy("site-a.localhost")
    get_policy("site-b.localhost")

    a_path = policy_path("site-a.localhost")
    b_path = policy_path("site-b.localhost")
    assert call_count[a_path] == 2
    assert call_count[b_path] == 1
