# cofferdam_app

A Frappe app that intercepts native outbound calls — email and webhooks — and
routes them through the [cofferdam](https://github.com/datahenge/cofferdam)
policy engine before any data leaves the server.

**The problem it solves:** When a Production database is restored to a Staging
or Dev environment, the restored database contains live email addresses, webhook
endpoints, and API credentials. Without a guard, the non-production environment
behaves like Production — sending customer emails, firing real webhooks, and
calling live APIs. `cofferdam_app` makes a local policy file — not the restored
database — the authority over what is permitted to leave the server.

---

## Requirements

| Requirement | Version |
|-------------|---------|
| Frappe | v16 |
| Python | 3.14.x |
| cofferdam (PyPI) | ≥ 0.1.0 (installed automatically) |

> **Frappe v15 / Python 3.10:** Not yet supported. A `version-15` branch is
> planned for a future release.

---

## Installation

```bash
bench get-app https://github.com/datahenge/cofferdam_app --branch version-16
bench --site <your-site> install-app cofferdam_app
```

`bench install-app` pulls `cofferdam` from PyPI automatically via
`requirements.txt`. No separate `pip install` is needed.

---

## Configuration

### 1. Create the policy file

The policy file must exist at:

```
sites/<site-name>/environment_policy.toml
```

This file lives on the **local filesystem** and is **not** restored from a
database backup — that separation is what makes it trustworthy as a policy
authority.

If the file is missing when an outbound call is attempted, `cofferdam_app`
**fails closed**: the call is blocked and an error is logged. Create the file
before installing the app on any site where you need outbound calls to work.

### 2. Choose a policy for your environment

**Non-production: sink all email, block all webhooks (minimal safe default)**

```toml
# sites/staging.example.com/environment_policy.toml
environment = "staging"
default_decision = "deny"

[mail]
mode = "sink"
sink = "dev@internal.example.com"   # all outbound email is redirected here
```

Without an `[integrations.frappe_webhooks]` section, all webhook delivery is
blocked. Add one when you need webhooks to fire in staging:

```toml
[integrations.frappe_webhooks]
kind = "webhook"
enabled = true
allowed_hosts = ["hooks.sandbox.example.com"]
allowed_methods = ["POST"]
allowed_operations = ["deliver"]
```

**Non-production: allow internal email only**

```toml
environment = "dev"
default_decision = "deny"

[mail]
mode = "allow_internal"
allow_domains = ["yourcompany.com"]
```

**Production: explicit pass-through**

On Production the `environment = "production"` declaration causes
`cofferdam_app` to pass all email and webhook calls through without
modification. The policy file is still required; the app will block outbound
calls on any site that lacks one.

```toml
# sites/production.example.com/environment_policy.toml
environment = "production"
default_decision = "deny"

[mail]
mode = "allow_internal"
allow_domains = ["yourcompany.com"]

# Add your real integrations here so cofferdam can audit them
[integrations.frappe_webhooks]
kind = "webhook"
enabled = true
allowed_hosts = ["hooks.production.example.com"]
allowed_methods = ["POST"]
allowed_operations = ["deliver"]
```

### 3. Email decoration

In non-production environments, outbound emails (those that are permitted or
redirected rather than blocked) have their subject and body annotated so
recipients can tell at a glance which environment sent them:

- **Subject:** `STAGING - Original Subject`
- **HTML body:** amber warning banner injected after `<body>`
- **Plain-text body:** `[STAGING] This email was sent from the staging environment…` prepended

Decoration is on by default. To disable it:

```toml
[mail]
mode = "sink"
sink = "dev@internal.example.com"
decorate = false
```

Decoration never applies to `environment = "production"` regardless of this
setting.

---

## Policy reload

The policy is loaded once per worker process and cached. To reload it after
editing `environment_policy.toml` without a full bench restart, call from a
bench console:

```python
from cofferdam_app.policy import reload_policy
reload_policy()           # evict all sites
reload_policy("mysite")   # evict one site only
```

The next outbound call on that worker will reload from disk.

---

## What this covers

| Outbound path | Intercepted? |
|---------------|:------------:|
| `frappe.sendmail()` → Email Queue | ✅ |
| Frappe Webhook DocType delivery → Webhook Request Log | ✅ |
| ERPNext Slack Webhook URL | ❌ (planned) |
| ERPNext payment gateways (Stripe, Razorpay, …) | ❌ (planned) |
| ERPNext shipping carriers (FedEx, UPS, …) | ❌ (planned) |
| ERPNext e-commerce connectors (Shopify, WooCommerce) | ❌ (planned) |

ERPNext-specific outbound paths make HTTP calls directly rather than through
Frappe's generic Webhook mechanism. The `frappe.integrations.utils` Integration
Request hook is the planned high-leverage interception point for these.

---

## Development

The test suite requires Python 3.14 and runs without a Frappe bench:

```bash
make install   # one-time: installs deps to /tmp/cofferdam_app-dev-pkgs
make test      # pytest
make lint      # ruff
make typecheck # mypy --strict
make check     # all three
```

The `cofferdam` library is expected at `../cofferdam` (sibling directory).

---

## License

Apache-2.0 © 2026 Brian Pond / Datahenge LLC
