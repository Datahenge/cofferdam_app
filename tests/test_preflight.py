"""Tests for cofferdam_app.policy path resolution and the preflight_http helper.

Covers issue #1:
  * resolve_policy_path() is cwd-independent when a real Frappe bench is present
    and falls back to the cwd-relative path otherwise.
  * preflight_http() loads policy, fails closed, parses host, decides, and
    resolves the credential only after an allow — never leaking a secret value.

BR-TEST-004: runs without a Frappe bench (frappe is a MagicMock from conftest).
"""

from __future__ import annotations

import pytest

from cofferdam import loads_policy

from cofferdam_app.policy import (
    Decision,
    PreflightResult,
    preflight_http,
    resolve_policy_path,
    site_policy_path,
)

_POLICY_TOML = """
environment = "staging"

[credentials.windmill_test_fake]
profile = "sandbox"
secret_env = "WINDMILL_TEST_SECRET"

[integrations.windmill_fake]
enabled = true
kind = "vendor_api"
credential = "windmill_test_fake"
allowed_hosts = ["windmill.example.com"]
allowed_methods = ["POST"]
allowed_operations = ["run_wait_result"]
"""

_URL = "https://windmill.example.com/api/run"

# The value the credential's secret_env resolves to. Named neutrally so linters
# do not flag comparisons against it as a hardcoded password (S105).
_ENV_VALUE = "resolved-value-abc123"


@pytest.fixture()
def _load_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make get_policy() return the fixture policy without touching disk."""
    policy = loads_policy(_POLICY_TOML)
    monkeypatch.setattr("cofferdam_app.policy.load_policy", lambda _path: policy)


def _preflight(**overrides: object) -> PreflightResult:
    kwargs: dict[str, object] = dict(
        integration="windmill_fake",
        kind="vendor_api",
        operation="run_wait_result",
        method="POST",
        url=_URL,
        credential="windmill_test_fake",
        require_credential=True,
        site="staging.localhost",
    )
    kwargs.update(overrides)
    return preflight_http(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# resolve_policy_path — cwd independence (issue #1, part 1)
# ---------------------------------------------------------------------------


def test_resolve_policy_path_uses_frappe_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real bench-absolute path from frappe.get_site_path is preferred."""
    import frappe

    absolute = "/bench/sites/staging.localhost/environment_policy.toml"
    monkeypatch.setattr(frappe, "get_site_path", lambda *a: absolute, raising=False)

    path, source = resolve_policy_path("staging.localhost")

    assert path == absolute
    assert source == "frappe"


def test_resolve_policy_path_falls_back_to_cwd_relative() -> None:
    """When get_site_path yields no real str (no bench), use the cwd-relative path."""
    # conftest's frappe stub is a MagicMock: get_site_path() returns a MagicMock,
    # not a str, so resolution degrades to the plain-library path.
    path, source = resolve_policy_path("staging.localhost")

    assert path == "sites/staging.localhost/environment_policy.toml"
    assert source == "cwd_relative"


def test_site_policy_path_returns_just_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import frappe

    absolute = "/bench/sites/staging.localhost/environment_policy.toml"
    monkeypatch.setattr(frappe, "get_site_path", lambda *a: absolute, raising=False)

    assert site_policy_path("staging.localhost") == absolute


# ---------------------------------------------------------------------------
# preflight_http — allow path (issue #1, part 2)
# ---------------------------------------------------------------------------


def test_preflight_allows_and_returns_token(
    _load_policy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allowed call with require_credential resolves the secret."""
    monkeypatch.setenv("WINDMILL_TEST_SECRET", _ENV_VALUE)

    result = _preflight()

    assert result.decision.allowed
    assert result.token == _ENV_VALUE
    assert result.host == "windmill.example.com"


def test_preflight_unpacks_as_decision_token(
    _load_policy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The example call site `decision, token = preflight_http(...)` works."""
    monkeypatch.setenv("WINDMILL_TEST_SECRET", _ENV_VALUE)

    decision, token = _preflight()

    assert decision.allowed
    assert token == _ENV_VALUE


def test_preflight_no_token_when_not_requested(
    _load_policy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """require_credential=False allows but does not resolve the secret."""
    monkeypatch.setenv("WINDMILL_TEST_SECRET", _ENV_VALUE)

    result = _preflight(require_credential=False)

    assert result.decision.allowed
    assert result.token is None


def test_preflight_reports_policy_path_and_source(_load_policy: None) -> None:
    result = _preflight()

    assert result.policy_path == "sites/staging.localhost/environment_policy.toml"
    assert result.policy_source == "cwd_relative"


# ---------------------------------------------------------------------------
# preflight_http — fail-closed paths (ADR-0005)
# ---------------------------------------------------------------------------


def test_preflight_denies_disallowed_host(
    _load_policy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host outside the allowlist denies and never resolves the secret."""
    monkeypatch.setenv("WINDMILL_TEST_SECRET", _ENV_VALUE)

    result = _preflight(url="https://evil.example.com/api/run")

    assert not result.decision.allowed
    assert result.decision.reason_code == "host_not_allowed"
    assert result.token is None


def test_preflight_denies_when_policy_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No usable policy → deny with policy_unavailable, no secret resolution."""
    monkeypatch.setattr("cofferdam_app.policy.get_policy", lambda _site: None)
    monkeypatch.setenv("WINDMILL_TEST_SECRET", _ENV_VALUE)

    result = _preflight()

    assert not result.decision.allowed
    assert result.decision.reason_code == "policy_unavailable"
    assert result.token is None


def test_preflight_denies_unparseable_url(_load_policy: None) -> None:
    result = _preflight(url="not-a-url")

    assert not result.decision.allowed
    assert result.decision.reason_code == "invalid_url"
    assert result.host == ""


def test_preflight_denies_when_credential_required_but_missing(_load_policy: None) -> None:
    """require_credential=True with no credential named fails closed."""
    result = _preflight(credential=None)

    assert not result.decision.allowed
    assert result.decision.reason_code == "credential_required"
    assert result.token is None


def test_preflight_denies_when_secret_env_unset(
    _load_policy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allowed decision but unset env var → deny credential_unresolved, no leak."""
    monkeypatch.delenv("WINDMILL_TEST_SECRET", raising=False)

    result = _preflight()

    assert not result.decision.allowed
    assert result.decision.reason_code == "credential_unresolved"
    assert result.token is None


def test_preflight_never_puts_secret_in_decision(
    _load_policy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BR-LOG-002: the redacted decision context carries the reference name only."""
    monkeypatch.setenv("WINDMILL_TEST_SECRET", _ENV_VALUE)

    result = _preflight()
    log_dict = result.decision.as_log_dict()

    assert _ENV_VALUE not in repr(log_dict)
    assert log_dict["credential"] == "windmill_test_fake"


def test_preflight_result_is_decision_type(_load_policy: None) -> None:
    """Sanity: the decision field is a cofferdam Decision."""
    result = _preflight(url="https://evil.example.com/api/run")

    assert isinstance(result.decision, Decision)
