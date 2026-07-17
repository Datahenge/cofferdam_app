app_name = "cofferdam_app"
app_title = "Cofferdam"
app_publisher = "Datahenge LLC"
app_description = "Routes Frappe outbound calls through the cofferdam policy engine"
app_email = "brian@datahenge.com"
app_license = "Apache-2.0"

# BR-EMAIL-001..008, BR-EMAIL-DECORATE-001..006
doc_events = {
    "Email Queue": {
        "before_insert": "cofferdam_app.mail.before_insert_email_queue",
    }
}

# BR-FRAPPE-002: Webhook delivery interception — implemented in item 14.
