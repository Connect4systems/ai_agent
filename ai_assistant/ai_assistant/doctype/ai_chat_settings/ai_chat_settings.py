# Copyright (c) 2024, Connect4systems and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from ai_assistant.ai_assistant.core.data_access import ERPDataAccess
from ai_assistant.ai_assistant.core.knowledge_library import KnowledgeLibrary

class AIChatSettings(Document):
    pass

@frappe.whitelist()
def update_knowledge_library():
    db_conn = None  # TODO: Replace with actual DB connection if needed
    erp_access = ERPDataAccess(db_conn)
    knowledge = KnowledgeLibrary()

    categories = [
        'doctypes', 'users', 'roles', 'permissions', 'workflows',
        'reports', 'ledgers', 'inventory', 'projects', 'leads',
        'customers', 'suppliers', 'related_documents'
    ]
    summary = {}
    for key in categories:
        fetch_method = getattr(erp_access, f'get_{key}', None)
        if fetch_method:
            records = fetch_method()
            knowledge.update(key, records)
            summary[key] = len(records)
        else:
            summary[key] = 'method not found'
    frappe.logger().info(f"Knowledge library updated with live data: {summary}")
    return summary
