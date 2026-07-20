app_name = "cofferdam_app"
app_title = "Cofferdam"
app_publisher = "Datahenge LLC"
app_description = "Routes Frappe outbound calls through the cofferdam policy engine"
app_email = "brian@datahenge.com"
app_license = "Apache-2.0"

# Desk integration (Workspace + Workspace Sidebar + Desktop Icon tile) ships as
# committed JSON files: cofferdam_app/workspace/cofferdam/, workspace_sidebar/,
# and desktop_icon/. bench migrate syncs all three — no after_install hook or
# create_desktop_icons_from_workspace() call is needed. See docs and the
# project's Lessons_Learned_Frappe_Desk notes.

# BR-EMAIL-001..008, BR-EMAIL-DECORATE-001..006
# BR-DECISION-003..010 (webhooks)
doc_events = {
    "Email Queue": {
        "before_insert": "cofferdam_app.mail.before_insert_email_queue",
    },
    "Webhook Request Log": {
        "before_insert": "cofferdam_app.webhooks.before_insert_webhook_request_log",
    },
}
