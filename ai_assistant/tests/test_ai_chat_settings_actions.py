# Copyright (c) 2026, Connect4systems and Contributors
# See license.txt

import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock


class _Dict(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            return None

    def __setattr__(self, key, value):
        self[key] = value


class _FakeSettings(_Dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.saved = False

    def save(self, ignore_permissions=False):
        self.saved = True


class _FakeAgent(_Dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.learning_text_blocks = list(kwargs.get("learning_text_blocks", []))
        self.data_sources = list(kwargs.get("data_sources", []))
        self.saved = False

    def append(self, fieldname, value):
        row = _Dict(value)
        getattr(self, fieldname).append(row)
        return row

    def save(self, ignore_permissions=False):
        self.saved = True


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda text, *args, **kwargs: text
    frappe.get_all = MagicMock(return_value=[])
    frappe.get_doc = MagicMock()
    frappe.get_single = MagicMock()
    frappe.get_installed_apps = MagicMock(return_value=["erpnext", "ai_assistant"])
    frappe.has_permission = MagicMock(return_value=True)
    frappe.throw = MagicMock(side_effect=Exception)
    frappe.clear_cache = MagicMock()
    frappe.PermissionError = Exception
    frappe.db = MagicMock()
    frappe.db.exists = MagicMock(return_value=False)
    frappe.db.commit = MagicMock()
    frappe.utils = MagicMock()
    frappe.utils.now_datetime = MagicMock(return_value="2026-03-16 10:00:00")
    frappe.whitelist = lambda fn=None, **kwargs: (fn if fn else lambda inner: inner)

    frappe_model = types.ModuleType("frappe.model")
    frappe_model_document = types.ModuleType("frappe.model.document")

    class Document:
        pass

    frappe_model_document.Document = Document

    sys.modules["frappe"] = frappe
    sys.modules["frappe.model"] = frappe_model
    sys.modules["frappe.model.document"] = frappe_model_document
    return frappe


_frappe_stub = _install_frappe_stub()

MODULE_PATH = "ai_assistant.ai_assistant.doctype.ai_chat_settings.ai_chat_settings"
if MODULE_PATH in sys.modules:
    ai_chat_settings = importlib.reload(sys.modules[MODULE_PATH])
else:
    ai_chat_settings = importlib.import_module(MODULE_PATH)


class TestAIChatSettingsActions(unittest.TestCase):
    def setUp(self):
        _frappe_stub.get_all.reset_mock()
        _frappe_stub.get_doc.reset_mock()
        _frappe_stub.get_single.reset_mock()
        _frappe_stub.get_installed_apps.reset_mock()
        _frappe_stub.has_permission.reset_mock()
        _frappe_stub.throw.reset_mock()
        _frappe_stub.clear_cache.reset_mock()
        _frappe_stub.db.exists.reset_mock()
        _frappe_stub.db.commit.reset_mock()
        _frappe_stub.get_installed_apps.return_value = ["erpnext", "ai_assistant"]
        _frappe_stub.has_permission.return_value = True
        _frappe_stub.throw.side_effect = Exception
        _frappe_stub.db.exists.side_effect = None

    def test_upsert_system_knowledge_block_adds_new_row(self):
        agent = _FakeAgent(name="Default Agent")

        changed = ai_chat_settings._upsert_system_knowledge_block(agent, "snapshot text")

        self.assertTrue(changed)
        self.assertEqual(len(agent.learning_text_blocks), 1)
        self.assertEqual(agent.learning_text_blocks[0].title, ai_chat_settings.SYSTEM_KNOWLEDGE_BLOCK_TITLE)
        self.assertEqual(agent.learning_text_blocks[0].text_block, "snapshot text")

    def test_sync_agent_data_sources_adds_only_missing_rows(self):
        agent = _FakeAgent(
            name="Default Agent",
            data_sources=[_Dict({"doctype_name": "Customer", "allow_in_context": 1})],
        )

        added = ai_chat_settings._sync_agent_data_sources(agent, ["Customer", "Sales Order", "Workflow"])

        self.assertEqual(added, 2)
        self.assertEqual(len(agent.data_sources), 3)
        self.assertEqual(agent.data_sources[1].doctype_name, "Sales Order")
        self.assertEqual(agent.data_sources[2].doctype_name, "Workflow")

    def test_build_system_knowledge_snapshot_contains_key_sections(self):
        def _db_exists(doctype, name):
            return name in {"Workflow Transition", "DocPerm", "User", "Has Role", "User Permission"}

        def _get_all(doctype, **kwargs):
            if doctype == "DocType":
                if kwargs.get("filters") == {"istable": 0}:
                    return [
                        {"name": "Sales Order", "module": "Selling", "issingle": 0, "is_submittable": 1},
                        {"name": "Workflow", "module": "Core", "issingle": 0, "is_submittable": 0},
                    ]
                return [{"name": "Sales Order", "istable": 0}, {"name": "Workflow", "istable": 0}]
            if doctype == "Workflow":
                return [{"name": "Sales Approval", "document_type": "Sales Order", "workflow_state_field": "workflow_state"}]
            if doctype == "Workflow Document State":
                return [{"parent": "Sales Approval", "state": "Pending", "doc_status": "0"}]
            if doctype == "Workflow Transition":
                return [{"parent": "Sales Approval", "state": "Pending", "action": "Approve", "next_state": "Approved", "allowed": "Sales Manager"}]
            if doctype == "Role":
                return [{"name": "System Manager"}, {"name": "Sales Manager"}]
            if doctype == "DocPerm":
                return [{"parent": "Sales Order", "role": "Sales Manager", "permlevel": 0, "read": 1, "write": 1, "create": 1, "submit": 1, "cancel": 0, "delete": 0, "amend": 0, "report": 1, "export": 0, "share": 0, "print": 1, "email": 0, "if_owner": 0}]
            if doctype == "User":
                return [{"enabled": 1, "user_type": "System User"}, {"enabled": 0, "user_type": "Website User"}]
            if doctype == "Has Role":
                return [{"parent": "user1@example.com", "role": "Sales Manager"}, {"parent": "user2@example.com", "role": "System Manager"}]
            if doctype == "User Permission":
                return [{"allow": "Company", "apply_to_all_doctypes": 1, "applicable_for": None}]
            return []

        _frappe_stub.db.exists.side_effect = _db_exists
        _frappe_stub.get_all.side_effect = _get_all

        snapshot = ai_chat_settings._build_system_knowledge_snapshot()

        self.assertIn("Installed Apps", snapshot)
        self.assertIn("DocTypes", snapshot)
        self.assertIn("Active Workflows", snapshot)
        self.assertIn("Roles", snapshot)
        self.assertIn("Role Permission Rows", snapshot)
        self.assertIn("Users", snapshot)
        self.assertIn("User Role Links", snapshot)
        self.assertIn("User Permission Links", snapshot)

    def test_refresh_agent_system_knowledge_updates_agent_block_and_sources(self):
        settings = _FakeSettings(default_agent="Default Agent")
        agent = _FakeAgent(name="Default Agent")

        def _db_exists(doctype, name):
            if doctype == "AI Agent" and name == "Default Agent":
                return True
            return name in {"DocPerm", "User", "Has Role", "User Permission"}

        def _get_all(doctype, **kwargs):
            if doctype == "DocType":
                if kwargs.get("filters") == {"istable": 0}:
                    return [{"name": "Sales Order", "module": "Selling", "issingle": 0, "is_submittable": 1}]
                if kwargs.get("filters") == {"issingle": 0}:
                    return [{"name": "Sales Order", "istable": 0}, {"name": "Workflow", "istable": 0}]
            if doctype == "Workflow":
                return []
            if doctype == "Role":
                return [{"name": "System Manager"}]
            if doctype == "DocPerm":
                return [{"parent": "Sales Order", "role": "System Manager", "permlevel": 0, "read": 1, "write": 1, "create": 1, "submit": 0, "cancel": 0, "delete": 0, "amend": 0, "report": 1, "export": 0, "share": 0, "print": 1, "email": 0, "if_owner": 0}]
            if doctype == "User":
                return [{"enabled": 1, "user_type": "System User"}]
            if doctype == "Has Role":
                return [{"parent": "user1@example.com", "role": "System Manager"}]
            if doctype == "User Permission":
                return [{"allow": "Company", "apply_to_all_doctypes": 1, "applicable_for": None}]
            return []

        _frappe_stub.get_single.return_value = settings
        _frappe_stub.get_doc.side_effect = lambda doctype, name=None: agent if doctype == "AI Agent" else None
        _frappe_stub.get_all.side_effect = _get_all
        _frappe_stub.db.exists.side_effect = _db_exists

        result = ai_chat_settings.refresh_agent_system_knowledge()

        self.assertEqual(result["agent_name"], "Default Agent")
        self.assertEqual(result["knowledge_block_title"], ai_chat_settings.SYSTEM_KNOWLEDGE_BLOCK_TITLE)
        self.assertEqual(result["data_source_count_added"], 2)
        self.assertEqual(len(agent.learning_text_blocks), 1)
        self.assertEqual(len(agent.data_sources), 2)
        self.assertTrue(agent.saved)
        self.assertTrue(_frappe_stub.db.commit.called)


if __name__ == "__main__":
    unittest.main()