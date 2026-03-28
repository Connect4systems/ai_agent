from . import __version__ as app_version

app_name = "ai_assistant"
app_title = "AI Assistant"
app_publisher = "Connect4systems"
app_description = "AI Chatbot for ERPNext v15 — reads DB, workflows, and user permissions"
app_email = "info@connect4systems.com"
app_license = "MIT"

# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------
app_include_css = ["/assets/ai_assistant/css/ai_chat.css"]
app_include_js = ["/assets/ai_assistant/js/ai_chat.js"]
doctype_js = {
    "AI Chat Settings": "public/js/ai_chat_settings_form.js",
    "AI Agent": "public/js/ai_agent_form.js",
}
after_install = "ai_assistant.setup.install.after_install"
after_migrate = "ai_assistant.setup.install.after_migrate"

# ---------------------------------------------------------------------------
# DocTypes
# ---------------------------------------------------------------------------
# Fixtures to export via bench export-fixtures
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["dt", "in", []]],
    }
]

# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
# has_permission = {}

# ---------------------------------------------------------------------------
# Document Events
# ---------------------------------------------------------------------------
# doc_events = {}

# ---------------------------------------------------------------------------
# Scheduled Tasks
# ---------------------------------------------------------------------------
# scheduler_events = {}

# ---------------------------------------------------------------------------
# Website
# ---------------------------------------------------------------------------
# website_route_rules = []
