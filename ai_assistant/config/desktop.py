from __future__ import unicode_literals
from frappe import _


def get_data():
    return [
        {
            "label": _("Settings"),
            "items": [
                {
                    "type": "doctype",
                    "name": "AI Chat Settings",
                    "description": _("Configure AI provider, model, and API key."),
                },
                {
                    "type": "doctype",
                    "name": "AI Agent",
                    "description": _("Create agents, assign AI roles for widget access, and configure per-agent data sources."),
                },
            ],
        },
        {
            "label": _("Logs"),
            "items": [
                {
                    "type": "doctype",
                    "name": "AI Chat Log",
                    "description": _("History of all chatbot interactions."),
                },
            ],
        },
    ]
