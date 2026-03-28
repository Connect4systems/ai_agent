from __future__ import annotations

import re

import frappe


def _parse_values(raw: str | None) -> list[str]:
    if not raw:
        return []
    values = re.split(r"[,;\n\r]+", str(raw))
    return [value.strip() for value in values if value and value.strip()]


def execute() -> None:
    if not frappe.db.exists("DocType", "AI Agent"):
        return

    if frappe.get_all("AI Agent", filters={"enabled": 1}, fields=["name"], limit=1):
        return

    try:
        old_rows = frappe.get_all(
            "AI Data Source Policy",
            filters={"enabled": 1},
            fields=["doctype_name", "allowed_fields", "allowed_roles", "max_rows", "allow_in_context"],
            order_by="modified desc",
        )
    except Exception:
        return

    if not old_rows:
        return

    agent = frappe.get_doc(
        {
            "doctype": "AI Agent",
            "agent_name": "Default Agent",
            "enabled": 1,
            "is_default": 1,
            "allow_widget_access": 1,
            "require_data_source_policy": 1,
            "include_workflows": 1,
            "include_permissions": 1,
            "default_answer_mode": "summary",
            "max_db_rows": 20,
            "max_tool_doctypes": 3,
            "description": "Auto-migrated from legacy AI Data Source Policy rows.",
        }
    )

    merged_roles: set[str] = set()
    for row in old_rows:
        dt = row.get("doctype_name")
        if not dt:
            continue

        row_roles = _parse_values(row.get("allowed_roles"))
        merged_roles.update(row_roles)

        agent.append(
            "data_sources",
            {
                "doctype_name": dt,
                "allowed_fields": row.get("allowed_fields"),
                "allowed_roles": "\n".join(row_roles),
                "max_rows": row.get("max_rows"),
                "allow_in_context": row.get("allow_in_context", 1),
            },
        )

    for role in sorted(merged_roles):
        agent.append("allowed_roles", {"role": role})

    agent.insert(ignore_permissions=True)
    frappe.db.commit()
