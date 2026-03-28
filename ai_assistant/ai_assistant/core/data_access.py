"""
data_access.py

This module provides connectors and utilities to access ERP DocTypes, users, roles, permissions, workflows, reports, ledgers, inventory, projects, leads, customers, suppliers, and related documents.
"""

import frappe



class ERPDataAccess:
    def __init__(self, db_connection=None):
        self.db = db_connection

    def get_supplier_by_name(self, supplier_name):
        # Try exact match on supplier_name
        suppliers = frappe.get_all("Supplier", filters={"supplier_name": supplier_name}, fields=["name", "supplier_name", "supplier_group", "country"])
        if suppliers:
            return suppliers[0]
        # Try exact match on name
        suppliers = frappe.get_all("Supplier", filters={"name": supplier_name}, fields=["name", "supplier_name", "supplier_group", "country"])
        if suppliers:
            return suppliers[0]
        # Try case-insensitive partial match on supplier_name
        suppliers = frappe.get_all("Supplier", filters=[["supplier_name", "like", f"%{supplier_name}%"]], fields=["name", "supplier_name", "supplier_group", "country"])
        if suppliers:
            return suppliers[0]
        # Try case-insensitive partial match on name
        suppliers = frappe.get_all("Supplier", filters=[["name", "like", f"%{supplier_name}%"]], fields=["name", "supplier_name", "supplier_group", "country"])
        if suppliers:
            return suppliers[0]
        return None

    def get_supplier_balance(self, supplier_name):
        # Find the supplier
        supplier = self.get_supplier_by_name(supplier_name)
        if not supplier:
            return None
        # Sum outstanding_amount from submitted Purchase Invoices for this supplier
        invoices = frappe.get_all(
            "Purchase Invoice",
            filters={"supplier": supplier["name"], "docstatus": 1},
            fields=["outstanding_amount"]
        )
        total = sum(inv["outstanding_amount"] for inv in invoices)
        return total

    def get_customer_by_name(self, customer_name):
        # Try exact match on customer_name
        customers = frappe.get_all("Customer", filters={"customer_name": customer_name}, fields=["name", "customer_name", "customer_group", "territory", "customer_type"])
        if customers:
            return customers[0]
        # Try exact match on name
        customers = frappe.get_all("Customer", filters={"name": customer_name}, fields=["name", "customer_name", "customer_group", "territory", "customer_type"])
        if customers:
            return customers[0]
        # Try case-insensitive partial match on customer_name
        customers = frappe.get_all("Customer", filters=[["customer_name", "like", f"%{customer_name}%"]], fields=["name", "customer_name", "customer_group", "territory", "customer_type"])
        if customers:
            return customers[0]
        # Try case-insensitive partial match on name
        customers = frappe.get_all("Customer", filters=[["name", "like", f"%{customer_name}%"]], fields=["name", "customer_name", "customer_group", "territory", "customer_type"])
        if customers:
            return customers[0]
        return None

    def get_customer_balance(self, customer_name):
        # Find the customer
        customer = self.get_customer_by_name(customer_name)
        if not customer:
            return None
        # Sum outstanding_amount from submitted Sales Invoices for this customer
        invoices = frappe.get_all(
            "Sales Invoice",
            filters={"customer": customer["name"], "docstatus": 1},
            fields=["outstanding_amount"]
        )
        total = sum(inv["outstanding_amount"] for inv in invoices)
        return total

    def get_doctypes(self):
        return frappe.get_all("DocType", fields=["name", "module", "issingle", "istable", "custom", "modified"])

    def get_users(self):
        return frappe.get_all("User", fields=["name", "full_name", "email", "enabled", "last_login", "role_profile_name"])

    def get_roles(self):
        return frappe.get_all("Role", fields=["name", "role_name", "disabled", "desk_access"])

    def get_permissions(self):
        return frappe.get_all("User Permission", fields=["name", "user", "allow", "for_value", "apply_to_all_doctypes", "creation"])

    def get_workflows(self):
        return frappe.get_all("Workflow", fields=["name", "document_type", "is_active", "workflow_state_field", "modified"])

    def get_reports(self):
        return frappe.get_all("Report", fields=["name", "ref_doctype", "report_type", "is_standard", "disabled"])

    def get_ledgers(self):
        return frappe.get_all("GL Entry", fields=["name", "posting_date", "account", "debit", "credit", "voucher_type", "voucher_no", "company"])

    def get_inventory(self):
        return frappe.get_all("Bin", fields=["name", "item_code", "warehouse", "actual_qty", "projected_qty", "reserved_qty"])

    def get_projects(self):
        return frappe.get_all("Project", fields=["name", "project_name", "status", "expected_start_date", "expected_end_date"])

    def get_leads(self):
        return frappe.get_all("Lead", fields=["name", "lead_name", "status", "email_id", "company_name", "source"])

    def get_customers(self):
        return frappe.get_all("Customer", fields=["name", "customer_name", "customer_group", "territory", "customer_type", "creation"])

    def get_suppliers(self):
        return frappe.get_all("Supplier", fields=["name", "supplier_name", "supplier_group", "country", "creation"])

    def get_related_documents(self):
        # Example: fetch Communication records as related documents
        return frappe.get_all("Communication", fields=["name", "subject", "reference_doctype", "reference_name", "sent_or_received", "creation"])

    def get_purchase_orders(self):
        return frappe.get_all("Purchase Order", fields=["name", "supplier", "transaction_date", "grand_total", "total", "status"])

    def get_employees(self):
        return frappe.get_all("Employee", fields=["name", "employee_name", "status", "department", "designation", "date_of_joining", "company"])

    def get_stock_entries(self):
        return frappe.get_all("Stock Entry", fields=["name", "stock_entry_type", "posting_date", "total_incoming_value", "total_outgoing_value", "company", "status"])

    def get_opportunities(self):
        return frappe.get_all("Opportunity", fields=["name", "customer_name", "status", "transaction_date", "opportunity_amount"])

    def get_quotations(self):
        return frappe.get_all("Quotation", fields=["name", "customer_name", "status", "transaction_date", "grand_total"])

    def get_sales_orders(self):
        return frappe.get_all("Sales Order", fields=["name", "customer_name", "status", "transaction_date", "grand_total"])

    def get_delivery_notes(self):
        return frappe.get_all("Delivery Note", fields=["name", "customer_name", "status", "posting_date", "grand_total"])

    def get_sales_invoices(self):
        return frappe.get_all("Sales Invoice", fields=["name", "customer_name", "status", "posting_date", "grand_total"])

    def get_payment_entries(self):
        return frappe.get_all("Payment Entry", fields=["name", "party_type", "party", "payment_type", "paid_amount", "posting_date", "status"])

# Usage example (to be replaced with actual DB/API connection):
# db_conn = ...
# erp_access = ERPDataAccess(db_conn)
# doctypes = erp_access.get_doctypes()
