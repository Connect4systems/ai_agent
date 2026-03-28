from __future__ import annotations

import frappe
from frappe.permissions import add_permission, update_permission_property

from ai_assistant.assistant_config import (
    AI_ADMIN_PERMISSION_PROFILES,
    AI_ADMIN_ROLE,
    DEFAULT_AGENT_INSTRUCTION_TEXT,
)
from ai_assistant.default_learning_blocks import DEFAULT_AGENT_LEARNING_TEXT_BLOCKS


FULL_ACCESS_RIGHTS = (
    "select",
    "read",
    "write",
    "create",
    "delete",
    "print",
    "email",
    "report",
    "export",
    "share",
)
READ_ONLY_RIGHTS = ("select", "read", "print", "email", "report", "export")


def _ensure_role() -> bool:
    if frappe.db.exists("Role", AI_ADMIN_ROLE):
        return False

    frappe.get_doc({"doctype": "Role", "role_name": AI_ADMIN_ROLE}).insert(ignore_permissions=True)
    return True


def _get_rights_for_profile(meta, profile: str) -> tuple[str, ...]:
    if profile == "read":
        return READ_ONLY_RIGHTS

    rights = list(FULL_ACCESS_RIGHTS)
    if getattr(meta, "is_submittable", 0):
        rights.extend(["submit", "cancel", "amend"])
    if getattr(meta, "allow_import", 0):
        rights.append("import")
    return tuple(rights)


def _ensure_doctype_permissions(doctype: str, profile: str) -> bool:
    if not frappe.db.exists("DocType", doctype):
        return False

    meta = frappe.get_meta(doctype)
    if getattr(meta, "istable", 0):
        return False

    add_permission(doctype, AI_ADMIN_ROLE, 0, ptype="read")

    updated = False
    for right in _get_rights_for_profile(meta, profile):
        update_permission_property(
            doctype,
            AI_ADMIN_ROLE,
            0,
            right,
            1,
            validate=False,
        )
        updated = True

    try:
        from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype

        validate_permissions_for_doctype(doctype)
    except Exception:
        pass

    return updated


def ensure_ai_admin_role_permissions() -> None:
    changed = _ensure_role()

    for doctype, profile in AI_ADMIN_PERMISSION_PROFILES.items():
        try:
            changed = _ensure_doctype_permissions(doctype, profile) or changed
        except Exception:
            frappe.log_error(
                title="AI Admin Permission Provisioning Error",
                message=f"DocType={doctype}\n\n" + frappe.get_traceback(),
            )

    if changed:
        clear_cache = getattr(frappe, "clear_cache", None)
        if callable(clear_cache):
            clear_cache()

        commit = getattr(getattr(frappe, "db", None), "commit", None)
        if callable(commit):
            commit()


def _normalize_block_title(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _get_default_agent_name_from_settings() -> str:
    try:
        settings = frappe.get_single("AI Chat Settings")
        name = str(getattr(settings, "default_agent", "") or "").strip()
        if name and frappe.db.exists("AI Agent", name):
            return name
    except Exception:
        pass
    return ""


def _find_existing_default_agent_name() -> str:
    for filters in ({"is_default": 1}, {"enabled": 1}):
        try:
            rows = frappe.get_all(
                "AI Agent",
                filters=filters,
                fields=["name"],
                order_by="is_default desc, modified desc",
                limit=1,
            )
        except Exception:
            rows = []

        if rows:
            return str(rows[0].get("name") or "").strip()

    if frappe.db.exists("AI Agent", "Default Agent"):
        return "Default Agent"

    return ""


def _create_default_agent():
    description = (
        "Default AI Agent provisioned by AI Assistant setup with role-aware behavior and learning text blocks."
    )
    agent = frappe.get_doc(
        {
            "doctype": "AI Agent",
            "agent_name": "Default Agent",
            "enabled": 1,
            "is_default": 1,
            "allow_widget_access": 1,
            "include_workflows": 1,
            "include_permissions": 1,
            "default_answer_mode": "summary",
            "max_db_rows": 20,
            "max_tool_doctypes": 3,
            "agent_instruction_block": DEFAULT_AGENT_INSTRUCTION_TEXT,
            "description": description,
        }
    )
    agent.insert(ignore_permissions=True)
    return agent


def _set_default_agent_in_settings(agent_name: str) -> bool:
    if not agent_name:
        return False

    try:
        settings = frappe.get_single("AI Chat Settings")
    except Exception:
        return False

    if str(getattr(settings, "default_agent", "") or "").strip() == agent_name:
        return False

    settings.default_agent = agent_name
    settings.save(ignore_permissions=True)
    return True


def _ensure_default_agent_doc():
    if not frappe.db.exists("DocType", "AI Agent"):
        return None, False

    chosen_name = _get_default_agent_name_from_settings() or _find_existing_default_agent_name()
    if chosen_name:
        try:
            return frappe.get_doc("AI Agent", chosen_name), False
        except Exception:
            pass

    return _create_default_agent(), True


def _sync_learning_text_blocks(agent_doc) -> bool:
    changed = False
    existing_rows = list(getattr(agent_doc, "learning_text_blocks", []) or [])
    row_by_title = {
        _normalize_block_title(getattr(row, "title", "")): row
        for row in existing_rows
        if _normalize_block_title(getattr(row, "title", ""))
    }

    for default_block in DEFAULT_AGENT_LEARNING_TEXT_BLOCKS:
        row_title = _normalize_block_title(default_block.get("title"))
        if not row_title:
            continue

        existing = row_by_title.get(row_title)
        if existing is None:
            agent_doc.append("learning_text_blocks", dict(default_block))
            changed = True
            continue

        for fieldname in ("enabled", "title", "language", "priority", "text_block"):
            desired = default_block.get(fieldname)
            current = getattr(existing, fieldname, None)

            if fieldname in ("enabled", "priority"):
                try:
                    current_value = int(current or 0)
                except (TypeError, ValueError):
                    current_value = 0
                try:
                    desired_value = int(desired or 0)
                except (TypeError, ValueError):
                    desired_value = 0
                if current_value != desired_value:
                    setattr(existing, fieldname, desired_value)
                    changed = True
                continue

            current_text = str(current or "")
            desired_text = str(desired or "")
            if current_text != desired_text:
                setattr(existing, fieldname, desired_text)
                changed = True

    return changed


def ensure_default_agent_learning_blocks() -> None:
    if not frappe.db.exists("DocType", "AI Agent"):
        return

    changed = False

    try:
        agent_doc, created = _ensure_default_agent_doc()
    except Exception:
        frappe.log_error(
            title="Default AI Agent Provisioning Error",
            message=frappe.get_traceback(),
        )
        return

    if not agent_doc:
        return

    changed = changed or created

    instruction_text = str(getattr(agent_doc, "agent_instruction_block", "") or "").strip()
    if not instruction_text:
        agent_doc.agent_instruction_block = DEFAULT_AGENT_INSTRUCTION_TEXT
        changed = True

    changed = _sync_learning_text_blocks(agent_doc) or changed

    try:
        changed = _set_default_agent_in_settings(str(getattr(agent_doc, "name", "") or "")) or changed
    except Exception:
        frappe.log_error(
            title="Default AI Agent Settings Sync Error",
            message=frappe.get_traceback(),
        )

    if changed:
        try:
            agent_doc.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(
                title="Default AI Agent Save Error",
                message=frappe.get_traceback(),
            )
            return

        clear_cache = getattr(frappe, "clear_cache", None)
        if callable(clear_cache):
            clear_cache()

        commit = getattr(getattr(frappe, "db", None), "commit", None)
        if callable(commit):
            commit()


def after_install() -> None:
    ensure_ai_admin_role_permissions()
    ensure_default_agent_learning_blocks()


def after_migrate() -> None:
    ensure_ai_admin_role_permissions()
    ensure_default_agent_learning_blocks()