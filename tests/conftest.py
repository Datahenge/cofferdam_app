"""Shared fixtures for cofferdam_app tests (BR-TEST-004).

Frappe is not installed in the test environment. A minimal stub is injected
into sys.modules before any cofferdam_app module is imported so that
``import frappe`` in app source does not fail.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Frappe stub — injected at module load time, before any cofferdam_app import
# ---------------------------------------------------------------------------


class FrappeThrowError(Exception):
    """Raised by the frappe.throw() stub to simulate Frappe's ValidationError."""


def _frappe_throw(msg: str, **kw: object) -> None:
    raise FrappeThrowError(str(msg))


_frappe_stub = MagicMock(name="frappe")
_frappe_stub.local = MagicMock()
_frappe_stub.local.site = "test.localhost"
_frappe_stub.throw.side_effect = _frappe_throw

sys.modules["frappe"] = _frappe_stub


# ---------------------------------------------------------------------------
# Autouse: reset shared state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    """Clear the policy cache and reset the frappe stub before each test."""
    _frappe_stub.local.site = "test.localhost"
    _frappe_stub.throw.side_effect = _frappe_throw

    from cofferdam_app.policy import reload_policy
    reload_policy()

    yield

    from cofferdam_app.policy import reload_policy as _rl
    _rl()
