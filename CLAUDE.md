# CLAUDE.md — cofferdam_app

Guidance for working in this repo. Read this before running tests or lint so you
don't rediscover the environment layout each session.

## What this app is

A Frappe app that routes native outbound side effects (email, webhooks, and —
via `preflight_http` — custom HTTP) through the `cofferdam` policy engine so a
non-production bench cannot accidentally email real customers, hit real vendor
APIs, etc. Fail-closed: a missing/invalid policy blocks the action (ADR-0005).

- `cofferdam_app/policy.py` — policy loading, per-process caching, cwd-independent
  path resolution (`resolve_policy_path` / `site_policy_path`), and the reusable
  `preflight_http()` helper.
- `cofferdam_app/mail.py`, `cofferdam_app/webhooks.py` — `before_insert` hooks that
  intercept Email Queue and Webhook Request Log.
- The `cofferdam` **library** (the policy engine itself) is a separate package,
  installed in the bench env; its source is not in this repo.

## Environment layout

This checkout lives **inside a deployed frappe-bench**:

```
frappe-bench/
  env/                         # the virtualenv — has cofferdam + frappe installed
  apps/cofferdam_app/          # THIS repo
```

The `cofferdam` library is already importable from `../../env`. There is **no**
sibling `../cofferdam` source checkout here, so the plain `make install` / `make
test` targets (which assume one) do **not** work in this bench.

## Running tests / lint / typecheck (use these here)

The bench virtualenv is at `../../env`. Dev tools are installed there. Use the
`bench-*` Makefile targets:

```bash
make bench-install    # one-time: pins pytest/pytest-cov/mypy/ruff into the bench env
make bench-test       # PYTHONPATH=. ../../env/bin/python -m pytest
make bench-lint       # ../../env/bin/ruff check cofferdam_app tests
make bench-typecheck  # PYTHONPATH=. ../../env/bin/python -m mypy
make bench-check      # all three

make bench-test ARGS="tests/test_preflight.py -q"   # pass pytest args via ARGS
```

Tests run **without a real bench**: `tests/conftest.py` injects a `frappe`
MagicMock stub into `sys.modules` (BR-TEST-004), and code under test degrades to
the cwd-relative policy path when no real bench is present.

Do **not** put the bench `site-packages` on `PYTHONPATH` for mypy — it contains a
top-level `typing_extensions.py` that mypy refuses to scan ("shadows library
module"). Running via `../../env/bin/python -m mypy` (as the target does) avoids
this because cofferdam is already on the interpreter's path.

## Toolchain versions — pinned, and why

Pinned exactly in `pyproject.toml` `[dependency-groups].dev`:
`ruff==0.14.10`, `mypy==2.3.0`, `pytest==9.1.1`, `pytest-cov==7.1.0`.

**Ruff has no LTS** — it's pre-1.0 and its lint/isort behavior drifts between
`0.x` minors. The repo previously declared only a floor (`ruff>=0.5`), so every
install resolved to a different ruff and produced inconsistent `I001` results.
Bump these deliberately, never with a bare floor.

## Known pre-existing debt (not regressions — don't be alarmed)

- **`ruff` `I001` on ~8 committed files.** The repo's import blocks were formatted
  for a `0.5`-era ruff and trip modern ruff's isort. Fix is a one-time
  `../../env/bin/ruff check cofferdam_app tests --fix` in its own commit. Not yet
  done because it would churn in-progress files.
- **`mypy` errors in `cofferdam_app/cofferdam_app/page/cofferdam_status/…`** —
  "untyped decorator" from Frappe's untyped `@frappe.whitelist()`. Pre-existing,
  in an untracked page module.
- **`test_mail.py` failures** may appear while `mail.py`'s email-decoration
  refactor is in progress (e.g. `NameError: decorate_email`). That's WIP in the
  working tree, unrelated to the policy engine.

## Conventions

- Fail-closed everywhere: on any doubt, deny/block. Never resolve a secret before
  a policy `allow`. Never log or return a secret value (BR-LOG-002); Decision
  context carries the credential *reference name* only.
- Keep CLI/plain-library behavior (`policy_path`, cwd-relative) separate from
  Frappe-aware behavior (`resolve_policy_path`, `frappe.get_site_path`).
- Target Python 3.14, `line-length = 100`, ruff rules `E F I B UP S N RUF`.

## GitHub

`gh` may be unauthenticated in this environment (`gh auth login` or `GH_TOKEN`).
If `gh` fails, fall back to `WebFetch` for public issues/PRs.
