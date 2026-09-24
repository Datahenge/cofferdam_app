// Cofferdam Status — read-only operational dashboard.
// All data is assembled server-side (cofferdam_status.py::get_data); this file
// only renders and wires the action buttons. See docs/status-page-plan.md.

frappe.pages["cofferdam-status"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Cofferdam Status"),
		single_column: true,
	});
	wrapper.cofferdam_status = new CofferdamStatus(page);
};

frappe.pages["cofferdam-status"].on_page_show = function (wrapper) {
	// Refresh on revisit so the view is never stale after a policy edit.
	if (wrapper.cofferdam_status) {
		wrapper.cofferdam_status.refresh();
	}
};

const METHOD = "cofferdam_app.cofferdam_app.page.cofferdam_status.cofferdam_status";

class CofferdamStatus {
	constructor(page) {
		this.page = page;
		this._inject_styles();
		this._build_layout();
		this._build_buttons();
		// The initial data load is driven by on_page_show, which Frappe fires
		// on first display as well as on every revisit. Refreshing here too
		// would double-fire load-time messages, so we intentionally do not.
	}

	_inject_styles() {
		if (document.getElementById("cds-styles")) return;
		const css = `
			.cds-banner { padding: 14px 18px; border-radius: 6px; color: #fff;
				margin-bottom: 14px; font-size: 15px; }
			.cds-banner .cds-env { font-size: 20px; font-weight: 700; letter-spacing: .04em; }
			.cds-banner .cds-sub { opacity: .92; margin-top: 3px; }
			.cds-green { background: #1f9d55; } .cds-amber { background: #c98a00; }
			.cds-blue { background: #2b6cb0; } .cds-red { background: #c53030; }
			.cds-section { margin-bottom: 22px; }
			.cds-section h4 { margin-bottom: 8px; }
			.cds-messages { border: 1px solid var(--border-color, #d1d8dd); border-radius: 6px;
				background: var(--fg-color, #fff); margin-bottom: 16px; }
			.cds-messages-head { display: flex; justify-content: space-between; align-items: center;
				padding: 6px 10px; border-bottom: 1px solid var(--border-color, #d1d8dd);
				font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
			.cds-messages-body { max-height: 190px; overflow-y: auto; padding: 4px 0; }
			.cds-msg { padding: 3px 12px; font-family: var(--font-stack-monospace, monospace);
				font-size: 12px; border-left: 3px solid transparent; }
			.cds-msg .cds-ts { color: var(--text-muted, #8d99a6); margin-right: 8px; }
			.cds-info { border-left-color: #2b6cb0; }
			.cds-warn { border-left-color: #c98a00; background: rgba(201,138,0,.06); }
			.cds-error { border-left-color: #c53030; background: rgba(197,48,48,.06); }
			.cds-muted { color: var(--text-muted, #8d99a6); }
			.cds-yes { color: #1f9d55; font-weight: 600; }
			.cds-no { color: #c53030; font-weight: 600; }
		`;
		const style = document.createElement("style");
		style.id = "cds-styles";
		style.textContent = css;
		document.head.appendChild(style);
	}

	_build_layout() {
		this.messages = [];
		const $main = $(this.page.main);
		$main.empty();

		this.$messages_body = $('<div class="cds-messages-body"></div>');
		const $messages = $(`
			<div class="cds-messages">
				<div class="cds-messages-head">
					<span>${__("Messages")}</span>
				</div>
			</div>`);
		const $clear = $(`<button class="btn btn-xs btn-default">${__("Clear")}</button>`);
		$clear.on("click", () => this._clear_messages());
		$messages.find(".cds-messages-head").append($clear);
		$messages.append(this.$messages_body);
		$main.append($messages);

		this.$banner = $('<div class="cds-banner cds-blue"></div>');
		$main.append(this.$banner);

		this.$body = $('<div class="cds-body"></div>');
		$main.append(this.$body);
	}

	_build_buttons() {
		this.page.set_primary_action(__("Refresh"), () => this.refresh(), "refresh");
		this.page.add_inner_button(__("Reload Policy"), () => this._reload_policy());
		this.page.add_inner_button(__("Validate Config"), () => this._validate_config());
		this.page.add_inner_button(__("Dry-run Email Check"), () => this._dry_run_email(), __("Dry-run Checks"));
		this.page.add_inner_button(__("Dry-run Webhook Check"), () => this._dry_run_webhook(), __("Dry-run Checks"));
		this.page.add_inner_button(__("Check"), () => this._metadata_check(), __("Metadata Check-Fix"));
		this.page.add_inner_button(__("Fix"), () => this._metadata_fix(), __("Metadata Check-Fix"));
	}

	// ---- Message pane -------------------------------------------------------

	log(severity, text, show_alert = true) {
		const ts = frappe.datetime.now_time();
		this.messages.unshift({ severity, text, ts });
		this.messages = this.messages.slice(0, 50);
		this._render_messages();
		if (!show_alert) return;
		// Mirror to a transient toast so the action feels responsive.
		const indicator = severity === "error" ? "red" : severity === "warn" ? "orange" : "green";
		frappe.show_alert({ message: text, indicator });
	}

	_render_messages() {
		this.$messages_body.empty();
		if (!this.messages.length) {
			this.$messages_body.append(`<div class="cds-msg cds-muted">${__("No messages.")}</div>`);
			return;
		}
		for (const m of this.messages) {
			const $m = $(`<div class="cds-msg cds-${m.severity}"></div>`);
			$m.append($('<span class="cds-ts"></span>').text(m.ts));
			$m.append(document.createTextNode(m.text));
			this.$messages_body.append($m);
		}
	}

	_clear_messages() {
		this.messages = [];
		this._render_messages();
	}

	// ---- Data load ----------------------------------------------------------

	refresh() {
		frappe.call({
			method: `${METHOD}.get_data`,
			freeze: true,
			freeze_message: __("Loading Cofferdam status…"),
			callback: (r) => {
				if (r.message) this.render(r.message);
			},
		});
	}

	render(d) {
		this._render_banner(d);
		this.$body.empty();
		this.$body.append(this._section_status(d));
		this.$body.append(this._section_mail(d));
		this.$body.append(this._section_effects(d));
		this.$body.append(this._section_integrations(d));
		this.$body.append(this._section_credentials(d));
		this.$body.append(this._section_coverage(d));

		// Surface load-time findings in the message pane.
		if (!d.library_installed) {
			this.log("error", __("cofferdam library is NOT installed — interception is inert."));
		}
		(d.errors || []).forEach((e) => this.log("error", e));
	}

	_render_banner(d) {
		let cls = "cds-blue";
		let env = (d.environment || "").toUpperCase();
		let headline;
		if (!d.library_installed) {
			cls = "cds-red";
			headline = __("cofferdam NOT INSTALLED");
		} else if (!d.policy_file_exists || !d.environment) {
			cls = "cds-red";
			headline = __("POLICY MISSING / ERROR");
		} else if (env === "PRODUCTION") {
			cls = "cds-green";
			headline = env;
		} else if (env === "STAGING") {
			cls = "cds-amber";
			headline = env;
		} else {
			cls = "cds-blue";
			headline = env; // DEV / TEST
		}
		this.$banner.attr("class", `cds-banner ${cls}`);
		this.$banner.empty();
		this.$banner.append($('<div class="cds-env"></div>').text(headline));
		const sub = `${__("Site")}: ${d.site}   •   ${__("Policy")}: ${d.policy_file_path || "—"}`;
		this.$banner.append($('<div class="cds-sub"></div>').text(sub));
	}

	// ---- Sections -----------------------------------------------------------

	_section(title, $inner) {
		const $s = $('<div class="cds-section"></div>');
		$s.append($("<h4></h4>").text(title));
		$s.append($inner);
		return $s;
	}

	_kv_table(rows) {
		const $t = $('<table class="table table-bordered"></table>');
		const $b = $("<tbody></tbody>");
		for (const [k, v] of rows) {
			const $tr = $("<tr></tr>");
			$tr.append($("<td></td>").css("width", "260px").text(k));
			$tr.append($("<td></td>").html(v));
			$b.append($tr);
		}
		$t.append($b);
		return $t;
	}

	_section_status(d) {
		const rows = [
			[__("Library installed"), yesno(d.library_installed)],
			[__("Library version"), esc(d.library_version || __("not installed"))],
			[__("Frappe App version"), esc(d.app_version)],
			[__("Policy file path"), esc(d.policy_file_path || "—")],
			[__("Policy file present"), yesno(d.policy_file_exists)],
			[__("Last modified"), esc(d.policy_file_mtime || "—") + ` <span class="cds-muted">(${__("informational only")})</span>`],
			[
				__("Loaded in this worker"),
				yesno(d.policy_loaded_in_this_worker) +
					` <span class="cds-muted">(${__("per-process, not fleet-wide")})</span>`,
			],
			[__("Default decision"), esc(d.default_decision || "—")],
		];
		return this._section(__("Policy file & versions"), this._kv_table(rows));
	}

	_section_mail(d) {
		if (!d.mail) {
			return this._section(
				__("Mail policy"),
				$(`<div class="cds-muted">${__("No [mail] section in policy.")}</div>`)
			);
		}
		const m = d.mail;
		const rows = [
			[__("Mode"), esc(m.mode)],
			[__("Sink address"), esc(m.sink || "—")],
			[__("Allowed domains"), esc((m.allow_domains || []).join(", ") || "—")],
			[__("Decoration enabled"), yesno(m.decorate)],
		];
		return this._section(__("Mail policy"), this._kv_table(rows));
	}

	_section_effects(d) {
		const cols = [__("Kind"), __("Scope"), __("Enabled"), __("Allowed domains")];
		const rows = (d.effects || []).map((e) => [
			esc(e.kind),
			esc(e.scope),
			yesno(e.enabled),
			esc((e.allow_domains || []).join(", ") || "—"),
		]);
		return this._section(__("Effects"), this._grid(cols, rows, __("No effects rules defined.")));
	}

	_section_integrations(d) {
		const cols = [
			__("Name"), __("Kind"), __("Enabled"), __("Credential"), __("Allowed hosts"),
			__("Allowed methods"), __("Allowed operations"), __("Authorize"), __("Capture"),
		];
		const rows = (d.integrations || []).map((i) => [
			esc(i.name),
			esc(i.kind),
			yesno(i.enabled),
			esc(i.credential || "—"),
			esc((i.allowed_hosts || []).join(", ") || "—"),
			esc((i.allowed_methods || []).join(", ") || "—"),
			esc((i.allowed_operations || []).join(", ") || "—"),
			yesno(i.allow_authorize),
			yesno(i.allow_capture),
		]);
		return this._section(__("Integrations"), this._grid(cols, rows, __("No integrations defined.")));
	}

	_section_credentials(d) {
		const cols = [__("Name"), __("Profile"), __("Source"), __("Env var"), __("Env var present?")];
		const rows = (d.credentials || []).map((c) => [
			esc(c.name),
			esc(c.profile),
			esc(c.source),
			esc(c.secret_env || "—"),
			c.source === "inline"
				? `<span class="cds-muted">${__("inline secret")}</span>`
				: yesno(c.env_var_present),
		]);
		return this._section(
			__("Credentials (names only — no secret values)"),
			this._grid(cols, rows, __("No credentials defined."))
		);
	}

	_section_coverage(d) {
		const cols = [__("Outbound path"), __("Intercepted?"), __("Note")];
		const rows = (d.coverage || []).map((c) => [
			esc(c.path),
			c.intercepted ? `<span class="cds-yes">✅</span>` : `<span class="cds-no">❌</span>`,
			esc(c.note),
		]);
		return this._section(__("Interception coverage"), this._grid(cols, rows, ""));
	}

	_grid(cols, rows, empty_text) {
		const $t = $('<table class="table table-bordered"></table>');
		const $h = $("<thead></thead>");
		const $hr = $("<tr></tr>");
		cols.forEach((c) => $hr.append($("<th></th>").text(c)));
		$h.append($hr);
		$t.append($h);
		const $b = $("<tbody></tbody>");
		if (!rows.length) {
			const $tr = $("<tr></tr>");
			$tr.append(
				$("<td></td>")
					.attr("colspan", cols.length)
					.addClass("cds-muted")
					.text(empty_text || __("None."))
			);
			$b.append($tr);
		} else {
			for (const cells of rows) {
				const $tr = $("<tr></tr>");
				cells.forEach((cell) => $tr.append($("<td></td>").html(cell)));
				$b.append($tr);
			}
		}
		$t.append($b);
		return $t;
	}

	// ---- Actions ------------------------------------------------------------

	_reload_policy() {
		frappe.call({
			method: `${METHOD}.reload_policy_action`,
			callback: (r) => {
				const m = r.message || {};
				this.log(m.ok ? "warn" : "error", m.message || __("Reload failed."));
			},
		});
	}

	_validate_config() {
		frappe.call({
			method: `${METHOD}.validate_config`,
			callback: (r) => {
				const m = r.message || {};
				this.log(m.ok ? "info" : "error", m.message || __("Validation failed."));
				(m.problems || []).forEach((p) => this.log("error", `  • ${p}`));
			},
		});
	}

	_dry_run_email() {
		frappe.prompt(
			[
				{ fieldname: "recipient", fieldtype: "Data", label: __("Recipient address"), reqd: 1 },
				{
					fieldtype: "HTML",
					options: `<p class="text-muted">${__(
						"No email will be sent. This only checks how Cofferdam rules would handle the recipient."
					)}</p>`,
				},
			],
			(v) => {
				frappe.call({
					method: `${METHOD}.dry_run_email`,
					args: { recipient: v.recipient },
					callback: (r) => {
						const m = r.message || {};
						const result = [m.message || __("No result.")];
						if (m.ok && m.decorated_subject) {
							result.push(`${__("Decorated subject")}: ${m.decorated_subject}`);
						}
						this.log(this._decision_severity(m), result.join(" "), false);
						this._show_result_output(__("Dry-run Email Check"), result.join("\n"), m.ok);
					},
				});
			},
			__("Dry-run Email Check"),
			__("Evaluate")
		);
	}

	_dry_run_webhook() {
		frappe.prompt(
			[{ fieldname: "url", fieldtype: "Data", label: __("Webhook URL"), reqd: 1 }],
			(v) => {
				frappe.call({
					method: `${METHOD}.dry_run_webhook`,
					args: { url: v.url },
					callback: (r) => {
						const m = r.message || {};
						const result = m.message || __("No result.");
						this.log(this._decision_severity(m), result, false);
						this._show_result_output(__("Dry-run Webhook Check"), result, m.ok);
					},
				});
			},
			__("Dry-run Webhook Check"),
			__("Evaluate")
		);
	}

	_metadata_check() {
		this._run_metadata_action("metadata_check", __("Metadata Check"), "info");
	}

	_metadata_fix() {
		frappe.confirm(
			__("Fix will permanently delete only dangling Accounting Dimensions, Custom Fields, and Property Setters found by the audit. Continue?"),
			() => this._run_metadata_action("metadata_fix", __("Metadata Fix"), "warn"),
			() => {}
		);
	}

	_run_metadata_action(action, title, success_severity) {
		frappe.call({
			method: `${METHOD}.${action}`,
			freeze: true,
			freeze_message: action === "metadata_fix" ? __("Repairing metadata…") : __("Checking metadata…"),
			callback: (r) => {
				const m = r.message || {};
				this.log(m.ok ? success_severity : "error", m.message || __("No result."));
				this._show_result_output(title, m.stdout || m.message || __("No output."), m.ok);
				if (m.ok && action === "metadata_fix") this.refresh();
			},
		});
	}

	_show_result_output(title, output, ok) {
		frappe.msgprint({
			title,
			indicator: ok ? "green" : "red",
			message: `<pre style="max-height: 420px; overflow: auto; white-space: pre-wrap; margin: 0;">${esc(output)}</pre>`,
		});
	}

	_decision_severity(m) {
		if (!m.ok) return "error";
		if (m.allowed === false) return "warn";
		return "info";
	}
}

function esc(v) {
	return frappe.utils.escape_html(v == null ? "" : String(v));
}

function yesno(v) {
	return v
		? `<span class="cds-yes">${__("Yes")}</span>`
		: `<span class="cds-no">${__("No")}</span>`;
}
