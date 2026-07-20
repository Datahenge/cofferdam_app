# Plan: Cofferdam Status Page

## Goal

Add a read-only operational dashboard to `cofferdam_app` so site administrators
can see at a glance whether Cofferdam is active, what the policy says, and
verify the interception paths are working — without touching TOML files or
Python directly.

---

## Design principle: the page must survive a missing library

`cofferdam_app` can be a fully-installed, functioning Frappe app **while the
`cofferdam` Python library is not installed.** `hooks.py` does not import
`cofferdam`; only `mail.py` and `webhooks.py` do, and those are referenced by
dotted-string path in `doc_events`, so Frappe imports them lazily — only when an
Email Queue / Webhook Request Log row is inserted. The failure is therefore
invisible until the first outbound email, when the `before_insert` hook throws
on `from cofferdam.mail import ...`.

**The status page is exactly where this should be caught.** Consequences for the
controller:

- The page controller must import `cofferdam` and `cofferdam_app.policy`
  **lazily, inside `try/except ImportError`**, never at module top level. If it
  imported them at the top, a missing library would 500 the page itself — the
  page could not render the very diagnostic it exists to show.
- `library_installed: bool` is a **first-class top-level field** feeding the
  environment banner (red = library missing), not merely a Validate-button
  check. When it is false, every downstream section degrades gracefully to
  "unavailable — cofferdam not installed" rather than erroring.

---

## Frappe primitive: Page (not DocType)

Use a Frappe **Page**, not a DocType. Pages are the correct primitive for
read-only operational dashboards. A DocType is for stored data; this is all
derived at runtime from the policy file and the Frappe environment.

A Frappe Page consists of:

```
cofferdam_app/
  cofferdam_app/
    page/
      cofferdam_status/
        cofferdam_status.json      # Page manifest (name, title, roles)
        cofferdam_status.py        # Python controller: whitelisted methods
        cofferdam_status.js        # JS: frappe.pages['cofferdam-status'].onload = ...
        cofferdam_status.html      # Optional Jinja template (or build DOM in JS)
```

The page folder lives under the module directory
(`cofferdam_app/cofferdam_app/page/...`), matching the module named in
`modules.txt` (`Cofferdam App`). The JS `onload` calls
`frappe.call({ method: 'cofferdam_app.cofferdam_app.page.cofferdam_status.cofferdam_status.get_data' })`
and renders the result. All data assembly happens in Python; JS only renders.

---

## Python controller: what `get_data()` returns

`get_data()` is a `@frappe.whitelist()` function. It never imports `cofferdam`
at module scope. The returned dict — matched to the **actual**
`cofferdam.models` shapes (dicts, not lists; `Optional` mail; `secret_env`, not
`env_var`):

```python
{
  "library_installed": bool,        # cofferdam import succeeded (drives banner)
  "site": str,                      # frappe.local.site
  "environment": str | None,        # "production"|"staging"|"test"|"dev", or None
  "policy_file_path": str,          # sites/{site}/environment_policy.toml
  "policy_file_exists": bool,
  "policy_file_mtime": str | None,  # ISO-8601; informational only (no mtime cache
                                    #   invalidation exists — ADR-0010)
  "policy_loaded_in_this_worker": bool,  # site in cofferdam_app.policy._policy_cache;
                                         #   PER-PROCESS — see cache note below
  "library_version": str | None,   # cofferdam.__version__ (None if not installed)
  "app_version": str,               # cofferdam_app.__version__
  "default_decision": str,          # "allow" | "deny"

  "mail": dict | None,              # MailPolicy: {mode, sink, allow_domains[],
                                    #   decorate}; None when policy.mail is None
  "integrations": [                 # from dict[str, Integration].items()
    { "name": str, "kind": str, "enabled": bool, "credential": str | None,
      "allowed_hosts": [str], "allowed_methods": [str],
      "allowed_operations": [str], "allow_authorize": bool,
      "allow_capture": bool }
  ],
  "effects": [                      # from dict[str, dict[str, EffectRule]]
    { "kind": str, "scope": str, "enabled": bool, "allow_domains": [str] }
  ],
  "credentials": [                  # from dict[str, Credential].items() — NAMES ONLY
    { "name": str, "profile": str, "secret_env": str | None,
      "source": "env" | "inline", "env_var_present": bool }
  ],
  "coverage": [                     # interception coverage table (static + derived)
    { "path": str, "intercepted": bool, "note": str }
  ],
  "errors": [ str ],                # load/validation errors encountered
}
```

**Secret hygiene:** never include `secret_value`. For each credential,
`source = "env"` when `secret_env` is set, else `"inline"`. `env_var_present =
bool(os.environ.get(secret_env))` **only when `secret_env` is set** — the name
is safe to show, the value is not. Inline-secret credentials render as
"inline secret" with no value.

---

## The per-worker cache caveat (important)

`cofferdam_app.policy._policy_cache` is a module-level dict — **one per gunicorn
worker process.** Two consequences the UI must be honest about:

1. `policy_loaded_in_this_worker` reflects only the worker that served *this*
   HTTP request. It is not a fleet-wide health signal; label it precisely
   ("loaded in the worker that served this page"), not "Cache: Warm."
2. The **Reload Policy** action evicts the cache of whichever single worker
   handles that one request. The other workers — including those running the
   email/webhook hooks — keep their stale policy. An admin who edits the TOML,
   clicks Reload, and sees success may still be on the old policy elsewhere.
   The button copy and the message-pane result must say so explicitly
   ("evicted this worker's cache; other workers reload on their next policy
   miss or restart"). Do not present it as a reassuring fleet-wide refresh.

---

## Message pane (persistent, at the top)

A persistent, append-only message pane sits below the banner and above the
sections. Rationale: Frappe's native feedback is ephemeral — `show_alert()` is a
fading toast and `msgprint()` is a modal you dismiss; both discard their content
on close. For a diagnostics page, that content is the product. The pane keeps it.

Behaviour:

- **Append-only, newest on top, each entry timestamped and severity-colored**
  (info / warn / error).
- On **page load**, every entry in `errors[]` and the `library_installed=false`
  condition are written here.
- On **button actions**, all feedback (success and failure) is written here
  instead of only into a dialog that gets dismissed.
- **Capped** at the most recent ~50 entries; includes a **Clear** button.
- **Complements, does not replace, `show_alert()`** — fire the quick green toast
  for a success flash *and* mirror the message into the pane. Toast = the
  notification; pane = the record.
- **Client-side / session-scoped only.** It is an aid to the human at the
  screen, not a persisted audit log. Real logging stays in the Python layer
  (`_log`).

---

## UI sections (rendered in JS/HTML)

### 1. Environment banner
Large coloured badge at the top:
- Library missing → **red** ("cofferdam not installed")
- Policy missing / error → **red**
- PRODUCTION → green
- STAGING → amber
- DEV / TEST → blue

Shows: site name, environment, policy file path.

### 2. Policy file & version status
| Field | Value |
|---|---|
| Library installed | Yes / **No** |
| Library version | cofferdam 0.1.0 (or "not installed") |
| Frappe App version | cofferdam_app 0.1.0 |
| Path | `sites/mysite/environment_policy.toml` |
| Present | Yes / **No** |
| Last modified | 2026-07-15 14:32 UTC (informational only) |
| Loaded in this worker | Yes / No (per-process — not fleet-wide) |
| Default decision | allow / **deny** |

### 3. Mail policy
Render the `mail` section as a small table; show "no `[mail]` section" when
`mail` is `None`:
- Mode (deny / sink / allow_internal)
- Sink address (if mode=sink)
- Allowed domains (`allow_domains`)
- Decoration enabled (`decorate`, yes/no)

### 3b. Effects
One row per `effects.<kind>.<scope>` rule:
| Kind | Scope | Enabled | Allowed Domains |
|---|---|---|---|
| email | customer | Yes | example.com |

### 4. Integrations table
One row per `integrations.*` entry:
| Name | Kind | Enabled | Credential | Allowed Hosts | Allowed Methods | Allowed Operations | Authorize | Capture |
|---|---|---|---|---|---|---|---|---|
| frappe_webhooks | webhook | Yes | — | hooks.example.com | POST | deliver | — | — |

### 5. Credentials table (names only)
| Credential name | Profile | Source | Env var | Env var present? |
|---|---|---|---|---|
| stripe_sandbox | sandbox | env | STRIPE_SANDBOX_KEY | Yes |
| sendgrid | staging | env | SENDGRID_API_KEY | **No** |
| legacy_key | dev | inline | — | (inline secret) |

### 6. Interception coverage
| Outbound path | Intercepted? | Note |
|---|---|---|
| frappe.sendmail() → Email Queue | ✅ | before_insert hook |
| Frappe Webhook → Webhook Request Log | ✅ | before_insert hook (Webhook Request Log confirmed present on v16) |
| ERPNext Slack Webhook | ❌ | Planned |
| ERPNext payment gateways | ❌ | Planned |
| ERPNext shipping carriers | ❌ | Planned |
| ERPNext e-commerce connectors | ❌ | Planned |

### 7. Action buttons

| Button | What it does |
|---|---|
| **Reload Policy** | `cofferdam_app.policy.reload_policy(site)` — evicts **this worker's** cache only. Result message must state the per-worker scope. |
| **Validate Config** | Calls `load_policy(path, strict=True)` fresh (bypassing cache). `strict=True` additionally verifies every `secret_env` is set — the CLI's own validation path. First checks `library_installed`; if false, reports that and stops. Returns parse/validation errors to the message pane. |
| **Dry-run Email Check** | Evaluates `check_recipient(policy, recipient=<address>)` for a user-supplied address. **No Email Queue row is inserted** — pure decision, zero side effect. |
| **Dry-run Webhook Check** | Evaluates `policy.decide(integration="frappe_webhooks", kind="webhook", operation="deliver", method="POST", host=<host>)` for a user-supplied URL/host. **No Webhook Request Log row is inserted.** |

**Why dry-run, not "send test":** the live hooks *insert real docs*, and in
PRODUCTION the mail hook passes through unmodified (`mail.py`) — a "send test
email" button would emit a genuine email in prod. The decision engine
(`check_recipient` / `policy.decide`) yields the same "what would cofferdam do"
answer with no outbound side effect. Render the `Decision` inline:
- Original recipient / target host
- `decision.allowed` (allowed / blocked)
- `decision.redirect_to` (sink redirect target, if any)
- `decision.reason_code`
- Decorated subject preview (email only, via `decorate_email`, on a sample)

All button results are written to the **message pane** (persistent), with a
`show_alert()` toast for the quick flash.

---

## Access control

Restrict the Page to the role that should see environment config:
- `System Manager`

`Administrator` is a *user*, not a role, and implicitly holds every role — do
not list it. Add `System Manager` to the `roles` child table in
`cofferdam_status.json`. Do **not** expose the page to regular users.

---

## Implementation order

1. Create `cofferdam_app/cofferdam_app/page/cofferdam_status/` with the four files.
2. Write `cofferdam_status.py`: `get_data()`, `reload_policy_action()`,
   `validate_config()`, `dry_run_email()`, `dry_run_webhook()` — all
   `@frappe.whitelist()`, all with **lazy guarded imports** of `cofferdam` /
   `cofferdam_app.policy`.
3. Write `cofferdam_status.json` manifest (`roles`: System Manager).
4. Write `cofferdam_status.js`: `onload`, the message pane, section renderers,
   button handlers.
5. Write `cofferdam_status.html` (minimal scaffold; most DOM built in JS).
6. `bench --site <site> migrate` to register the page.
7. Navigate to `/app/cofferdam-status` and verify each section, including the
   library-missing path (temporarily simulate) and the per-worker cache copy.

---

## Tricky Frappe Page details to remember

- The JSON manifest's `name` and the directory name use **underscores**
  (`cofferdam_status`); the URL uses **hyphens** (`/app/cofferdam-status`).
- `@frappe.whitelist()` methods are called via
  `frappe.call({ method: '<dotted.module.path>.function_name' })`.
- The page folder must sit under the module directory that matches
  `modules.txt` (`Cofferdam App` → `cofferdam_app/cofferdam_app/page/...`), or
  Frappe may not pick it up on migrate.
- `onload` receives a `wrapper` DOM element; use `$(wrapper).html(...)` or
  `frappe.render_template(...)` to populate it.
- Use `frappe.show_alert()` / `frappe.msgprint()` for transient feedback, but
  the **message pane is the primary channel** for anything worth keeping.
