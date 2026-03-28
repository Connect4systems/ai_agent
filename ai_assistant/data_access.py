"""
data_access.py

This module provides connectors and utilities to access ERP DocTypes, users, roles, permissions, workflows, reports, ledgers, inventory, projects, leads, customers, suppliers, and related documents.
"""

# Example: Data access interface (to be expanded for each DocType/module)

from datetime import datetime


class ERPDataAccess:
    def __init__(self, db_connection):
        self.db = db_connection

    def get_customer_receivable_summary(self, customer_name=None):
        """Retrieve receivable summary from ERPNext Accounts Receivable Summary report."""
        try:
            import frappe
        except Exception:
            return []

        try:
            execute = frappe.get_attr(
                "erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary.execute"
            )
        except Exception:
            return []

        try:
            company = frappe.defaults.get_global_default("company")
        except Exception:
            company = None

        filters = {
            "company": company,
            "report_date": frappe.utils.nowdate(),
            "ageing_based_on": "Due Date",
            "calculate_ageing_with": "Report Date",
            "range": "30, 60, 90, 120",
        }

        if customer_name:
            filters["party"] = customer_name

        try:
            columns, data = execute(filters)
        except Exception:
            return []

        def _idx(fieldname, default_idx=None):
            for i, col in enumerate(columns or []):
                if isinstance(col, dict) and col.get("fieldname") == fieldname:
                    return i
            return default_idx

        party_idx = _idx("party", 1)
        outstanding_idx = _idx("outstanding", 8)
        invoiced_idx = _idx("invoiced", 5)
        paid_idx = _idx("paid", 6)
        currency_idx = _idx("currency", None)

        result = []
        for row in data or []:
            if isinstance(row, dict):
                party = row.get("party")
                party_name = row.get("party_name")
                customer_name = row.get("customer_name") or row.get("customer")
                outstanding = row.get("outstanding", 0)
                invoiced = row.get("invoiced", 0)
                paid = row.get("paid", 0)
                currency = row.get("currency")
            else:
                party = row[party_idx] if party_idx is not None and len(row) > party_idx else None
                party_name = None
                customer_name = None
                outstanding = row[outstanding_idx] if outstanding_idx is not None and len(row) > outstanding_idx else 0
                invoiced = row[invoiced_idx] if invoiced_idx is not None and len(row) > invoiced_idx else 0
                paid = row[paid_idx] if paid_idx is not None and len(row) > paid_idx else 0
                currency = row[currency_idx] if currency_idx is not None and len(row) > currency_idx else None

            try:
                outstanding = float(outstanding or 0)
            except (TypeError, ValueError):
                outstanding = 0.0
            try:
                invoiced = float(invoiced or 0)
            except (TypeError, ValueError):
                invoiced = 0.0
            try:
                paid = float(paid or 0)
            except (TypeError, ValueError):
                paid = 0.0

            display_name = customer_name or party_name or party

            result.append(
                {
                    "customer": display_name,
                    "party": party,
                    "party_name": party_name,
                    "customer_name": customer_name,
                    "invoiced_amount": invoiced,
                    "paid_amount": paid,
                    "outstanding_amount": outstanding,
                    "currency": currency,
                }
            )

        return result

    def _build_live_payload(self, success, value=None, data=None, source=None, filters=None, computation=None, error=None):
        return {
            "success": bool(success),
            "value": value,
            "data": data if data is not None else [],
            "source": source or [],
            "filters": filters or {},
            "computation": computation or "",
            "as_of": datetime.utcnow().isoformat(),
            "error": error,
        }

    def get_customer_outstanding_live(self, company=None, customer_name=None, from_date=None, to_date=None):
        """Direct live outstanding from submitted Sales Invoices (docstatus=1)."""
        try:
            import frappe
        except Exception as e:
            return self._build_live_payload(
                False,
                source=["Sales Invoice"],
                filters={
                    "company": company,
                    "customer": customer_name,
                    "from_date": from_date,
                    "to_date": to_date,
                    "docstatus": 1,
                },
                computation="SUM(outstanding_amount) grouped by customer (optional)",
                error=str(e),
            )

        try:
            resolved_company = company or frappe.defaults.get_global_default("company")
        except Exception:
            resolved_company = company

        base_filters = {"docstatus": 1}
        if resolved_company:
            base_filters["company"] = resolved_company
        if customer_name:
            base_filters["customer"] = ["like", f"%{customer_name}%"]
        if from_date and to_date:
            base_filters["posting_date"] = ["between", [from_date, to_date]]
        elif from_date:
            base_filters["posting_date"] = [">=", from_date]
        elif to_date:
            base_filters["posting_date"] = ["<=", to_date]

        try:
            rows = frappe.get_all(
                "Sales Invoice",
                filters=base_filters,
                fields=["name", "customer", "outstanding_amount", "currency"],
                limit_page_length=0,
            )
        except Exception as e:
            return self._build_live_payload(
                False,
                source=["Sales Invoice"],
                filters={**base_filters},
                computation="SUM(outstanding_amount)",
                error=str(e),
            )

        if not rows:
            return self._build_live_payload(
                True,
                value=0.0,
                data=[],
                source=["Sales Invoice"],
                filters={**base_filters},
                computation="SUM(outstanding_amount)",
            )

        grouped = {}
        for row in rows:
            customer = row.get("customer")
            if not customer:
                continue
            outstanding = float(row.get("outstanding_amount") or 0)
            currency = row.get("currency") or "EGP"
            rec = grouped.setdefault(
                customer,
                {"customer": customer, "outstanding_amount": 0.0, "currency": currency},
            )
            rec["outstanding_amount"] += outstanding

        data = sorted(grouped.values(), key=lambda x: x.get("outstanding_amount", 0), reverse=True)
        total_value = sum(float(r.get("outstanding_amount") or 0) for r in data)

        return self._build_live_payload(
            True,
            value=total_value,
            data=data,
            source=["Sales Invoice"],
            filters={**base_filters},
            computation="SUM(outstanding_amount) over submitted Sales Invoice; grouped by customer",
        )

    def get_top_customers_live(self, company=None, from_date=None, to_date=None, limit=5):
        """Top customers from submitted Sales Invoices using SUM(grand_total)."""
        try:
            import frappe
        except Exception as e:
            return self._build_live_payload(
                False,
                source=["Sales Invoice"],
                filters={
                    "company": company,
                    "from_date": from_date,
                    "to_date": to_date,
                    "docstatus": 1,
                    "limit": limit,
                },
                computation="SUM(grand_total) GROUP BY customer ORDER BY total_sales DESC",
                error=str(e),
            )

        try:
            resolved_company = company or frappe.defaults.get_global_default("company")
        except Exception:
            resolved_company = company

        filters = {"docstatus": 1}
        if resolved_company:
            filters["company"] = resolved_company
        if from_date and to_date:
            filters["posting_date"] = ["between", [from_date, to_date]]
        elif from_date:
            filters["posting_date"] = [">=", from_date]
        elif to_date:
            filters["posting_date"] = ["<=", to_date]

        try:
            rows = frappe.get_all(
                "Sales Invoice",
                filters=filters,
                fields=["customer", "currency", "grand_total"],
                limit_page_length=0,
            )
        except Exception as e:
            return self._build_live_payload(
                False,
                source=["Sales Invoice"],
                filters={**filters, "limit": limit},
                computation="SUM(grand_total) GROUP BY customer ORDER BY total_sales DESC",
                error=str(e),
            )

        grouped = {}
        for row in rows or []:
            customer = row.get("customer")
            if not customer:
                continue
            amount = float(row.get("grand_total") or 0)
            currency = row.get("currency") or "EGP"
            rec = grouped.setdefault(customer, {"customer": customer, "total_sales": 0.0, "currency": currency})
            rec["total_sales"] += amount

        ranked = sorted(grouped.values(), key=lambda x: x.get("total_sales", 0), reverse=True)
        limited = ranked[: max(int(limit or 5), 1)]

        return self._build_live_payload(
            True,
            value=sum(float(r.get("total_sales") or 0) for r in limited),
            data=limited,
            source=["Sales Invoice"],
            filters={**filters, "limit": max(int(limit or 5), 1)},
            computation="SUM(grand_total) GROUP BY customer ORDER BY total_sales DESC",
        )

    def get_supplier_outstanding_live(self, company=None, supplier_name=None, from_date=None, to_date=None):
        """Direct live outstanding from submitted Purchase Invoices (docstatus=1)."""
        try:
            import frappe
        except Exception as e:
            return self._build_live_payload(
                False,
                source=["Purchase Invoice"],
                filters={
                    "company": company,
                    "supplier": supplier_name,
                    "from_date": from_date,
                    "to_date": to_date,
                    "docstatus": 1,
                },
                computation="SUM(outstanding_amount) grouped by supplier (optional)",
                error=str(e),
            )

        try:
            resolved_company = company or frappe.defaults.get_global_default("company")
        except Exception:
            resolved_company = company

        base_filters = {"docstatus": 1}
        if resolved_company:
            base_filters["company"] = resolved_company
        if supplier_name:
            base_filters["supplier"] = ["like", f"%{supplier_name}%"]
        if from_date and to_date:
            base_filters["posting_date"] = ["between", [from_date, to_date]]
        elif from_date:
            base_filters["posting_date"] = [">=", from_date]
        elif to_date:
            base_filters["posting_date"] = ["<=", to_date]

        rows = None
        get_all_error = None
        try:
            rows = frappe.get_all(
                "Purchase Invoice",
                filters=base_filters,
                fields=["name", "supplier", "outstanding_amount", "currency"],
                limit_page_length=0,
            )
        except Exception as e:
            get_all_error = str(e)

        if rows is None:
            # SQL fallback for environments where ORM field access differs
            sql_error = None
            try:
                conditions = ["docstatus = 1"]
                values = []

                if resolved_company:
                    conditions.append("company = %s")
                    values.append(resolved_company)
                if supplier_name:
                    conditions.append("supplier like %s")
                    values.append(f"%{supplier_name}%")
                if from_date:
                    conditions.append("posting_date >= %s")
                    values.append(from_date)
                if to_date:
                    conditions.append("posting_date <= %s")
                    values.append(to_date)

                where_sql = " AND ".join(conditions)
                sql = f"""
                    SELECT
                        supplier,
                        IFNULL(currency, 'EGP') AS currency,
                        SUM(IFNULL(outstanding_amount, 0)) AS outstanding_amount
                    FROM `tabPurchase Invoice`
                    WHERE {where_sql}
                    GROUP BY supplier, currency
                """
                sql_rows = frappe.db.sql(sql, tuple(values), as_dict=True) or []
                rows = [{"supplier": r.get("supplier"), "currency": r.get("currency"), "outstanding_amount": r.get("outstanding_amount")} for r in sql_rows]
            except Exception as sql_e:
                sql_error = str(sql_e)

            # Retry SQL fallback without company filter once (company permission/context mismatch)
            if rows is None and resolved_company:
                try:
                    conditions = ["docstatus = 1"]
                    values = []

                    if supplier_name:
                        conditions.append("supplier like %s")
                        values.append(f"%{supplier_name}%")
                    if from_date:
                        conditions.append("posting_date >= %s")
                        values.append(from_date)
                    if to_date:
                        conditions.append("posting_date <= %s")
                        values.append(to_date)

                    where_sql = " AND ".join(conditions)
                    sql = f"""
                        SELECT
                            supplier,
                            IFNULL(currency, 'EGP') AS currency,
                            SUM(IFNULL(outstanding_amount, 0)) AS outstanding_amount
                        FROM `tabPurchase Invoice`
                        WHERE {where_sql}
                        GROUP BY supplier, currency
                    """
                    sql_rows = frappe.db.sql(sql, tuple(values), as_dict=True) or []
                    rows = [{"supplier": r.get("supplier"), "currency": r.get("currency"), "outstanding_amount": r.get("outstanding_amount")} for r in sql_rows]
                except Exception as sql_e2:
                    sql_error = f"{sql_error}; retry_no_company_sql_error={sql_e2}" if sql_error else str(sql_e2)

            # Final fallback: Supplier balances from GL Entry aggregation
            if rows is None:
                try:
                    gl_filters = {"party_type": "Supplier"}
                    if resolved_company:
                        gl_filters["company"] = resolved_company
                    if supplier_name:
                        gl_filters["party"] = ["like", f"%{supplier_name}%"]
                    if from_date and to_date:
                        gl_filters["posting_date"] = ["between", [from_date, to_date]]
                    elif from_date:
                        gl_filters["posting_date"] = [">=", from_date]
                    elif to_date:
                        gl_filters["posting_date"] = ["<=", to_date]

                    gl_rows = frappe.get_all(
                        "GL Entry",
                        filters=gl_filters,
                        fields=["party", "account_currency", "debit", "credit"],
                        limit_page_length=0,
                    ) or []

                    grouped = {}
                    for r in gl_rows:
                        supplier_key = r.get("party")
                        if not supplier_key:
                            continue
                        debit = float(r.get("debit") or 0)
                        credit = float(r.get("credit") or 0)
                        outstanding = max(credit - debit, 0.0)
                        currency = r.get("account_currency") or "EGP"
                        rec = grouped.setdefault(
                            supplier_key,
                            {"supplier": supplier_key, "outstanding_amount": 0.0, "currency": currency},
                        )
                        rec["outstanding_amount"] += outstanding

                    rows = list(grouped.values())
                except Exception as gl_e:
                    return self._build_live_payload(
                        False,
                        source=["Purchase Invoice", "GL Entry"],
                        filters={**base_filters},
                        computation="SUM(outstanding_amount) OR GL fallback (credit-debit by supplier)",
                        error=f"get_all_error={get_all_error}; sql_error={sql_error}; gl_error={gl_e}",
                    )

        if not rows:
            return self._build_live_payload(
                True,
                value=0.0,
                data=[],
                source=["Purchase Invoice"],
                filters={**base_filters},
                computation="SUM(outstanding_amount)",
            )

        grouped = {}
        for row in rows:
            supplier = row.get("supplier")
            if not supplier:
                continue
            outstanding = float(row.get("outstanding_amount") or 0)
            currency = row.get("currency") or "EGP"
            rec = grouped.setdefault(
                supplier,
                {"supplier": supplier, "outstanding_amount": 0.0, "currency": currency},
            )
            rec["outstanding_amount"] += outstanding

        data = sorted(grouped.values(), key=lambda x: x.get("outstanding_amount", 0), reverse=True)
        total_value = sum(float(r.get("outstanding_amount") or 0) for r in data)

        return self._build_live_payload(
            True,
            value=total_value,
            data=data,
            source=["Purchase Invoice"],
            filters={**base_filters},
            computation="SUM(outstanding_amount) over submitted Purchase Invoice; grouped by supplier",
        )

    def get_top_suppliers_live(self, company=None, from_date=None, to_date=None, limit=5):
        """Top suppliers from submitted Purchase Invoices using SUM(grand_total)."""
        try:
            import frappe
        except Exception as e:
            return self._build_live_payload(
                False,
                source=["Purchase Invoice"],
                filters={
                    "company": company,
                    "from_date": from_date,
                    "to_date": to_date,
                    "docstatus": 1,
                    "limit": limit,
                },
                computation="SUM(grand_total) GROUP BY supplier ORDER BY total_purchases DESC",
                error=str(e),
            )

        try:
            resolved_company = company or frappe.defaults.get_global_default("company")
        except Exception:
            resolved_company = company

        filters = {"docstatus": 1}
        if resolved_company:
            filters["company"] = resolved_company
        if from_date and to_date:
            filters["posting_date"] = ["between", [from_date, to_date]]
        elif from_date:
            filters["posting_date"] = [">=", from_date]
        elif to_date:
            filters["posting_date"] = ["<=", to_date]

        try:
            rows = frappe.get_all(
                "Purchase Invoice",
                filters=filters,
                fields=["supplier", "currency", "grand_total"],
                limit_page_length=0,
            )
        except Exception as e:
            return self._build_live_payload(
                False,
                source=["Purchase Invoice"],
                filters={**filters, "limit": limit},
                computation="SUM(grand_total) GROUP BY supplier ORDER BY total_purchases DESC",
                error=str(e),
            )

        grouped = {}
        for row in rows or []:
            supplier = row.get("supplier")
            if not supplier:
                continue
            amount = float(row.get("grand_total") or 0)
            currency = row.get("currency") or "EGP"
            rec = grouped.setdefault(supplier, {"supplier": supplier, "total_purchases": 0.0, "currency": currency})
            rec["total_purchases"] += amount

        ranked = sorted(grouped.values(), key=lambda x: x.get("total_purchases", 0), reverse=True)
        limited = ranked[: max(int(limit or 5), 1)]

        return self._build_live_payload(
            True,
            value=sum(float(r.get("total_purchases") or 0) for r in limited),
            data=limited,
            source=["Purchase Invoice"],
            filters={**filters, "limit": max(int(limit or 5), 1)},
            computation="SUM(grand_total) GROUP BY supplier ORDER BY total_purchases DESC",
        )

    def get_accounts_payable_summary_live(
        self,
        company=None,
        report_date=None,
        ageing_based_on="Due Date",
        based_on_payment_terms=0,
        show_future_payments=0,
        show_gl_balance=0,
        range_values="30, 60, 90, 120",
        supplier_name=None,
        supplier_group=None,
        payment_terms_template=None,
        cost_center=None,
        project=None,
    ):
        """Live Accounts Payable Summary from ERPNext report execute API with standard filters."""
        try:
            import frappe
        except Exception as e:
            return self._build_live_payload(
                False,
                source=["Accounts Payable Summary Report", "Purchase Invoice"],
                filters={
                    "company": company,
                    "report_date": report_date,
                    "ageing_based_on": ageing_based_on,
                    "based_on_payment_terms": based_on_payment_terms,
                    "show_future_payments": show_future_payments,
                    "show_gl_balance": show_gl_balance,
                    "range": range_values,
                    "party_type": "Supplier",
                    "party": supplier_name,
                    "supplier_group": supplier_group,
                    "payment_terms_template": payment_terms_template,
                    "cost_center": cost_center,
                    "project": project,
                },
                computation="SUM(outstanding) from Accounts Payable Summary report rows",
                error=str(e),
            )

        try:
            execute = frappe.get_attr(
                "erpnext.accounts.report.accounts_payable_summary.accounts_payable_summary.execute"
            )
        except Exception as e:
            return self._build_live_payload(
                False,
                source=["Accounts Payable Summary Report"],
                filters={},
                computation="SUM(outstanding)",
                error=str(e),
            )

        try:
            resolved_company = company or frappe.defaults.get_global_default("company")
        except Exception:
            resolved_company = company

        resolved_report_date = report_date
        if not resolved_report_date:
            try:
                resolved_report_date = frappe.utils.nowdate()
            except Exception:
                resolved_report_date = None

        filters = {
            "company": resolved_company,
            "report_date": resolved_report_date,
            "ageing_based_on": ageing_based_on or "Due Date",
            "based_on_payment_terms": int(bool(based_on_payment_terms)),
            "show_future_payments": int(bool(show_future_payments)),
            "show_gl_balance": int(bool(show_gl_balance)),
            "range": range_values or "30, 60, 90, 120",
            "party_type": "Supplier",
        }
        if supplier_name:
            filters["party"] = supplier_name
        if supplier_group:
            filters["supplier_group"] = supplier_group
        if payment_terms_template:
            filters["payment_terms_template"] = payment_terms_template
        if cost_center:
            filters["cost_center"] = cost_center
        if project:
            filters["project"] = project

        try:
            columns, data = execute(filters)
        except Exception as e:
            return self._build_live_payload(
                False,
                source=["Accounts Payable Summary Report"],
                filters=filters,
                computation="SUM(outstanding)",
                error=str(e),
            )

        def _idx(fieldname, default_idx=None):
            for i, col in enumerate(columns or []):
                if isinstance(col, dict) and col.get("fieldname") == fieldname:
                    return i
            return default_idx

        party_idx = _idx("party", 1)
        party_name_idx = _idx("party_name", None)
        outstanding_idx = _idx("outstanding", 8)
        invoiced_idx = _idx("invoiced", 5)
        paid_idx = _idx("paid", 6)
        currency_idx = _idx("currency", None)

        rows = []
        for row in data or []:
            if isinstance(row, dict):
                supplier = row.get("party") or row.get("supplier")
                supplier_display = row.get("party_name") or row.get("supplier_name") or supplier
                outstanding = row.get("outstanding", 0)
                invoiced = row.get("invoiced", 0)
                paid = row.get("paid", 0)
                currency = row.get("currency")
            else:
                supplier = row[party_idx] if party_idx is not None and len(row) > party_idx else None
                supplier_display = (
                    row[party_name_idx]
                    if party_name_idx is not None and len(row) > party_name_idx
                    else supplier
                )
                outstanding = row[outstanding_idx] if outstanding_idx is not None and len(row) > outstanding_idx else 0
                invoiced = row[invoiced_idx] if invoiced_idx is not None and len(row) > invoiced_idx else 0
                paid = row[paid_idx] if paid_idx is not None and len(row) > paid_idx else 0
                currency = row[currency_idx] if currency_idx is not None and len(row) > currency_idx else None

            try:
                outstanding = float(outstanding or 0)
            except (TypeError, ValueError):
                outstanding = 0.0
            try:
                invoiced = float(invoiced or 0)
            except (TypeError, ValueError):
                invoiced = 0.0
            try:
                paid = float(paid or 0)
            except (TypeError, ValueError):
                paid = 0.0

            rows.append(
                {
                    "supplier": supplier_display,
                    "party": supplier,
                    "outstanding_amount": outstanding,
                    "invoiced_amount": invoiced,
                    "paid_amount": paid,
                    "currency": currency or "EGP",
                }
            )

        total_outstanding = sum(float(r.get("outstanding_amount") or 0) for r in rows)

        return self._build_live_payload(
            True,
            value=total_outstanding,
            data=rows,
            source=["Accounts Payable Summary Report"],
            filters=filters,
            computation="SUM(outstanding) over Accounts Payable Summary rows (party_type=Supplier)",
        )

    def resolve_supplier_candidates_live(self, supplier_text=None, limit=10):
        """Resolve supplier candidates from Supplier doctype by name and supplier_name."""
        try:
            import frappe
        except Exception as e:
            return self._build_live_payload(
                False,
                data=[],
                source=["Supplier"],
                filters={"supplier_text": supplier_text, "limit": limit},
                computation="Supplier resolver by exact/like match on name and supplier_name",
                error=str(e),
            )

        text = (supplier_text or "").strip()
        if not text:
            return self._build_live_payload(
                True,
                value=0,
                data=[],
                source=["Supplier"],
                filters={"supplier_text": supplier_text, "limit": limit},
                computation="No supplier text provided",
            )

        like = f"%{text}%"
        starts = f"{text}%"
        try:
            rows = frappe.get_all(
                "Supplier",
                fields=["name", "supplier_name", "disabled"],
                filters={"disabled": 0},
                or_filters=[
                    ["Supplier", "name", "=", text],
                    ["Supplier", "supplier_name", "=", text],
                    ["Supplier", "name", "like", starts],
                    ["Supplier", "supplier_name", "like", starts],
                    ["Supplier", "name", "like", like],
                    ["Supplier", "supplier_name", "like", like],
                ],
                limit_page_length=max(int(limit or 10), 1),
            ) or []
        except Exception as e:
            return self._build_live_payload(
                False,
                data=[],
                source=["Supplier"],
                filters={"supplier_text": supplier_text, "limit": limit},
                computation="Supplier resolver by exact/like match on name and supplier_name",
                error=str(e),
            )

        return self._build_live_payload(
            True,
            value=len(rows),
            data=rows,
            source=["Supplier"],
            filters={"supplier_text": supplier_text, "limit": max(int(limit or 10), 1)},
            computation="Supplier resolver by exact/like match on name and supplier_name",
        )

    def get_doctypes(self):
        """Retrieve all DocTypes from the ERP system."""
        # TODO: Implement actual data retrieval
        return []

    def get_users(self):
        """Retrieve all users."""
        # TODO: Implement actual data retrieval
        return []

    def get_roles(self):
        """Retrieve all roles."""
        # TODO: Implement actual data retrieval
        return []

    def get_permissions(self):
        """Retrieve all user permissions."""
        # TODO: Implement actual data retrieval
        return []

    def get_workflows(self):
        """Retrieve all workflows."""
        # TODO: Implement actual data retrieval
        return []

    # Add more methods for reports, ledgers, inventory, etc.

# Usage example (to be replaced with actual DB/API connection):
# db_conn = ...
# erp_access = ERPDataAccess(db_conn)
# doctypes = erp_access.get_doctypes()
