"""
ai_agent_core.py

This module implements the core logic for the AI ERP assistant.
It processes user queries, applies business logic, interacts with the knowledge library and live data validation, and generates responses.
"""

import logging

from .knowledge_library import KnowledgeLibrary
from .live_data_validation import LiveDataValidator

logger = logging.getLogger(__name__)


class AIAgentCore:
    def __init__(self, db_connection=None):
        self.knowledge = KnowledgeLibrary()
        self.validator = LiveDataValidator(db_connection, self.knowledge)

    def _format_live_numeric_response(self, answer_value, payload):
        links = (
            "\n\nRelated Reports:\n"
            "- Accounts Payable Summary: /app/query-report/Accounts%20Payable%20Summary\n"
            "- General Ledger: /app/query-report/General%20Ledger"
        )
        return f"Answer: {answer_value}{links}"

    def _extract_outstanding_value(self, row):
        for key in ("outstanding_amount", "outstanding", "balance", "party_balance"):
            try:
                return float(row.get(key) or 0)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _live_query_failure_message(self, error=None):
        base = "I cannot return an accurate live value right now because live database access/query failed."
        if error:
            return f"{base} Reason: {error}"
        return base

    def _extract_top_n(self, user_query, default=5):
        import re
        match = re.search(r"\btop\s+(\d+)\b", user_query or "", re.IGNORECASE)
        if match:
            try:
                n = int(match.group(1))
                return max(n, 1)
            except Exception:
                return default
        return default

    def handle_query(self, user_query, user_context=None):
        intent, entities = self.parse_query(user_query)
        lowered = (user_query or "").lower()
        normalized_query = lowered.replace("balanace", "balance")

        top_customers_keywords = (
            "top customers", "best customers", "largest customers", "most valuable customers",
            "أفضل العملاء", "اكبر العملاء"
        )
        if any(k in (user_query or "") for k in top_customers_keywords) or any(k in normalized_query for k in top_customers_keywords):
            limit = self._extract_top_n(user_query, default=5)
            payload = self.validator.data_access.get_top_customers_live(limit=limit)
            ok, err = self.validator.validate_numeric_response_payload(payload)
            if not ok or not payload.get("success"):
                reason = payload.get("error") or err
                return self._live_query_failure_message(reason)

            rows = payload.get("data") or []
            currency = rows[0].get("currency") if rows else "EGP"
            lines = [f"{i+1}️⃣ {r.get('customer')} — {float(r.get('total_sales') or 0):,.2f} {r.get('currency') or currency}" for i, r in enumerate(rows)]
            answer_header = "Top Customers"
            if any(k in (user_query or "") for k in ("أفضل العملاء", "اكبر العملاء")):
                answer_header = "أفضل العملاء"

            return (
                f"{answer_header}\n\n" +
                "\n".join(lines) +
                "\n\n" +
                self._format_live_numeric_response(
                    f"{len(rows)} customers ranked",
                    payload,
                )
            )

        top_suppliers_keywords = (
            "top suppliers", "best suppliers", "largest suppliers", "most valuable suppliers",
            "أفضل الموردين", "اكبر الموردين"
        )
        top_supplier_outstanding_keywords = (
            "top suppliers by outstanding",
            "suppliers with outstanding",
            "top payable suppliers",
            "highest payable suppliers",
            "الموردين اللي ليهم فلوس",
            "الموردين الذين لهم رصيد مستحق",
            "أعلى الموردين مديونية",
            "اعلى الموردين مديونية",
            "اكبر موردين مديونية",
        )

        outstanding_intent = any(k in normalized_query for k in top_supplier_outstanding_keywords)
        generic_top_supplier_intent = (
            any(k in (user_query or "") for k in top_suppliers_keywords)
            or any(k in normalized_query for k in top_suppliers_keywords)
        )

        if outstanding_intent:
            limit = self._extract_top_n(user_query, default=5)
            payload = self.validator.data_access.get_accounts_payable_summary_live(supplier_name=None)
            ok, err = self.validator.validate_numeric_response_payload(payload)
            if not ok or not payload.get("success"):
                payload = self.validator.data_access.get_supplier_outstanding_live(supplier_name=None)
                ok, err = self.validator.validate_numeric_response_payload(payload)
                if not ok or not payload.get("success"):
                    return self._live_query_failure_message(payload.get("error") or err)

            rows = payload.get("data") or []
            ranked = []
            for r in rows:
                supplier_name = str(r.get("supplier") or r.get("party") or "").strip()
                if not supplier_name:
                    continue
                outstanding_value = self._extract_outstanding_value(r)
                if outstanding_value <= 0:
                    continue
                ranked.append(
                    {
                        "supplier": supplier_name,
                        "outstanding_amount": outstanding_value,
                        "currency": r.get("currency") or "EGP",
                    }
                )

            ranked.sort(key=lambda x: (-float(x.get("outstanding_amount") or 0), str(x.get("supplier") or "").lower()))
            limited = ranked[: max(int(limit or 5), 1)]

            if not limited:
                if any(ch in (user_query or "") for ch in "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"):
                    return "لا يوجد موردون لديهم رصيد مستحق حالياً."
                return "No suppliers with outstanding payable amounts were found."

            is_ar = any(ch in (user_query or "") for ch in "ابتثجحخدذرزسشصضطظعغفقكلمنهوي")
            if is_ar:
                header = "أعلى الموردين حسب الرصيد المستحق:"
                lines = [
                    f"{i+1}️⃣ {r.get('supplier')} — {float(r.get('outstanding_amount') or 0):,.2f} {r.get('currency') or 'EGP'}"
                    for i, r in enumerate(limited)
                ]
                return header + "\n\n" + "\n".join(lines) + "\n\n" + self._format_live_numeric_response(
                    f"{len(limited)} suppliers ranked by outstanding",
                    payload,
                )

            header = "Top Suppliers by Outstanding Payable:"
            lines = [
                f"{i+1}️⃣ {r.get('supplier')} — {float(r.get('outstanding_amount') or 0):,.2f} {r.get('currency') or 'EGP'}"
                for i, r in enumerate(limited)
            ]
            return header + "\n\n" + "\n".join(lines) + "\n\n" + self._format_live_numeric_response(
                f"{len(limited)} suppliers ranked by outstanding",
                payload,
            )

        if generic_top_supplier_intent and not outstanding_intent:
            limit = self._extract_top_n(user_query, default=5)
            payload = self.validator.data_access.get_top_suppliers_live(limit=limit)
            ok, err = self.validator.validate_numeric_response_payload(payload)
            if not ok or not payload.get("success"):
                return self._live_query_failure_message(payload.get("error") or err)

            rows = payload.get("data") or []
            currency = rows[0].get("currency") if rows else "EGP"
            lines = [f"{i+1}️⃣ {r.get('supplier')} — {float(r.get('total_purchases') or 0):,.2f} {r.get('currency') or currency}" for i, r in enumerate(rows)]
            answer_header = "Top Suppliers"
            if any(k in (user_query or "") for k in ("أفضل الموردين", "اكبر الموردين")):
                answer_header = "أفضل الموردين"

            return (
                f"{answer_header}\n\n" +
                "\n".join(lines) +
                "\n\n" +
                self._format_live_numeric_response(
                    f"{len(rows)} suppliers ranked",
                    payload,
                )
            )


        supplier_balance_keywords = (
            "supplier balance",
            "payable balance",
            "total payable balance",
            "accounts payable",
            "accounts payable summary",
            "payable summary",
            "total supplier balance",
            "outstanding payable",
            "supplier/payables",
            "supplier payables",
            "outstanding payables",
            "supplier balanace",
            "payable balanace",
            "total payable balanace",
            "ملخص الذمم الدائنة",
            "ملخص الدائنين",
        )
        aggregate_supplier_balance_intent = any(
            k in normalized_query
            for k in (
                "suppliers balance",
                "suppliers balanace",
                "total supplier balance",
                "total payable balance",
                "accounts payable",
                "accounts payable summary",
                "payable summary",
                "اجمالي رصيد الموردين",
                "إجمالي رصيد الموردين",
                "رصيد الموردين",
                "ملخص الذمم الدائنة",
                "ملخص الدائنين",
            )
        )

        supplier_balance_signal = (
            any(k in normalized_query for k in supplier_balance_keywords)
            or ("balance" in normalized_query and "customer" not in normalized_query)
            or ("payable" in normalized_query)
            or ("payables" in normalized_query)
            or ("supplier" in normalized_query and "balance" in normalized_query)
            or ("supplier" in normalized_query and "outstanding" in normalized_query)
            or ("suppliers" in normalized_query and "balance" in normalized_query)
            or any(k in (user_query or "") for k in ("اجمالي رصيد الموردين", "إجمالي رصيد الموردين", "رصيد الموردين"))
        )
        if supplier_balance_signal:
            logger.info("AI_AGENT_DEBUG: matched supplier_balance branch | query=%s", user_query)
            import re

            def _norm(value):
                value = str(value or "").strip().lower()
                value = re.sub(r"[^\w\s\-]", "", value)
                value = re.sub(r"\s+", " ", value).strip()
                return value

            # Extract supplier only when query clearly targets a single supplier.
            # Do not extract for aggregate intents or for explicit plural aggregate phrasing.
            plural_aggregate_query = any(
                k in normalized_query
                for k in ("suppliers balance", "suppliers balanace", "supplier balances", "رصيد الموردين", "اجمالي رصيد الموردين", "إجمالي رصيد الموردين")
            )
            supplier_raw = None if (aggregate_supplier_balance_intent or plural_aggregate_query) else self.extract_supplier_name(user_query)
            supplier = supplier_raw

            # Resolve supplier against Supplier doctype (name/supplier_name)
            # to avoid truncated or fuzzy input mismatches.
            resolver_payload = self.validator.data_access.resolve_supplier_candidates_live(supplier_text=supplier_raw, limit=10) if supplier_raw else {"success": False}
            if supplier_raw and resolver_payload.get("success"):
                candidates = resolver_payload.get("data") or []
                if candidates:
                    import re

                    def _score(c):
                        name = (c.get("name") or "").strip()
                        sname = (c.get("supplier_name") or "").strip()
                        raw = (supplier_raw or "").strip()
                        n = re.sub(r"\s+", " ", name.lower()).strip()
                        sn = re.sub(r"\s+", " ", sname.lower()).strip()
                        r = re.sub(r"\s+", " ", raw.lower()).strip()
                        if n == r or sn == r:
                            return 100
                        if n.startswith(r) or sn.startswith(r):
                            return 80
                        if r in n or r in sn:
                            return 60
                        return 0

                    ranked = sorted(candidates, key=_score, reverse=True)
                    top = ranked[0] if ranked else None
                    top_score = _score(top) if top else 0
                    ties = [c for c in ranked if _score(c) == top_score and top_score > 0]

                    if top_score >= 80 and len(ties) == 1:
                        supplier = (top.get("name") or top.get("supplier_name") or supplier_raw)
                    elif top_score > 0 and len(ties) > 1:
                        names = ", ".join([(c.get("supplier_name") or c.get("name") or "") for c in ties[:5]])
                        return f"Multiple matching suppliers found: {names}. Please confirm the exact supplier name."

            # Prefer Accounts Payable Summary report path for supplier/payables,
            # fallback to Purchase Invoice/GL logic when report path fails.
            payload = self.validator.data_access.get_accounts_payable_summary_live(supplier_name=supplier)
            ok, err = self.validator.validate_numeric_response_payload(payload)
            if not ok or not payload.get("success"):
                payload = self.validator.data_access.get_supplier_outstanding_live(supplier_name=supplier)
                ok, err = self.validator.validate_numeric_response_payload(payload)
                if not ok or not payload.get("success"):
                    return self._live_query_failure_message(payload.get("error") or err)

            rows = payload.get("data") or []
            logger.info("AI_AGENT_DEBUG: supplier_balance payload success=%s rows=%s value=%s supplier=%s supplier_raw=%s",
                        payload.get("success") if isinstance(payload, dict) else None,
                        len(rows) if isinstance(rows, list) else None,
                        payload.get("value") if isinstance(payload, dict) else None,
                        supplier,
                        supplier_raw)
            if rows:
                logger.info("AI_AGENT_DEBUG: first_row_keys=%s first_row=%s",
                            list((rows[0] or {}).keys()) if isinstance(rows[0], dict) else None,
                            rows[0] if isinstance(rows[0], dict) else None)

            if not rows:
                all_payload = self.validator.data_access.get_accounts_payable_summary_live(supplier_name=None)
                all_ok, _all_err = self.validator.validate_numeric_response_payload(all_payload)
                if all_ok and all_payload.get("success"):
                    rows = all_payload.get("data") or rows
                    logger.info("AI_AGENT_DEBUG: fallback all_payload rows=%s", len(rows) if isinstance(rows, list) else None)

            if supplier:
                target = _norm(supplier)
                exact = [r for r in rows if _norm(r.get("supplier") or r.get("party")) == target]
                partial = [r for r in rows if target and target in _norm(r.get("supplier") or r.get("party"))]

                if len(exact) > 1 or (not exact and len(partial) > 1):
                    options = exact if len(exact) > 1 else partial
                    names = ", ".join([str(o.get("supplier") or o.get("party") or "") for o in options[:5]])
                    return f"Multiple matching suppliers found: {names}. Please confirm the exact supplier name."

                match = exact[0] if exact else (partial[0] if partial else None)
                if not match:
                    all_payload = self.validator.data_access.get_accounts_payable_summary_live(supplier_name=None)
                    all_ok, _all_err = self.validator.validate_numeric_response_payload(all_payload)
                    if all_ok and all_payload.get("success"):
                        all_rows = all_payload.get("data") or []
                        exact_all = [r for r in all_rows if _norm(r.get("supplier") or r.get("party")) == target]
                        partial_all = [r for r in all_rows if target and target in _norm(r.get("supplier") or r.get("party"))]
                        if len(exact_all) > 1 or (not exact_all and len(partial_all) > 1):
                            options = exact_all if len(exact_all) > 1 else partial_all
                            names = ", ".join([str(o.get("supplier") or o.get("party") or "") for o in options[:5]])
                            return f"Multiple matching suppliers found: {names}. Please confirm the exact supplier name."
                        match = exact_all[0] if exact_all else (partial_all[0] if partial_all else None)

                if not match:
                    return f"No matching live Accounts Payable data found for supplier {supplier}."

                value = self._extract_outstanding_value(match)
                currency = match.get("currency") or "EGP"
                return self._format_live_numeric_response(f"{value:,.2f} {currency}", payload)

            total = sum(self._extract_outstanding_value(r) for r in rows) if rows else 0.0
            if not rows and float(payload.get("value") or 0):
                total = float(payload.get("value") or 0)
            currency = rows[0].get("currency") if rows else "EGP"
            return self._format_live_numeric_response(f"{total:,.2f} {currency}", payload)

        customer_balance_keywords = (
            "customer balance",
            "receivable balance",
            "total receivable balance",
            "accounts receivable",
            "total customer balance",
            "outstanding",
        )

        if any(k in normalized_query for k in customer_balance_keywords):
            import re

            def _norm(value):
                value = str(value or "").strip().lower()
                value = re.sub(r"[^\w\s\-]", "", value)
                value = re.sub(r"\s+", " ", value).strip()
                return value

            customer = self.extract_customer_name(user_query)
            payload = self.validator.data_access.get_customer_outstanding_live(customer_name=customer)
            ok, err = self.validator.validate_numeric_response_payload(payload)
            if not ok or not payload.get("success"):
                return self._live_query_failure_message(payload.get("error") or err)

            rows = payload.get("data") or []
            if customer:
                target = _norm(customer)
                exact = [r for r in rows if _norm(r.get("customer")) == target]
                partial = [r for r in rows if target and target in _norm(r.get("customer"))]

                if len(exact) > 1 or (not exact and len(partial) > 1):
                    options = exact if len(exact) > 1 else partial
                    names = ", ".join([o.get("customer") for o in options[:5]])
                    return f"Multiple matching customers found: {names}. Please confirm the exact customer name."

                match = exact[0] if exact else (partial[0] if partial else None)
                if not match:
                    return f"No matching live submitted Sales Invoice data found for customer {customer}."

                value = float(match.get("outstanding_amount") or 0)
                currency = match.get("currency") or "EGP"
                return self._format_live_numeric_response(f"{value:,.2f} {currency}", payload)

            total = float(payload.get("value") or 0)
            currency = rows[0].get("currency") if rows else "EGP"
            return self._format_live_numeric_response(f"{total:,.2f} {currency}", payload)

        live_data = self.validator.validate_and_refresh(entities.get("category"))
        return self.generate_response(intent, entities, live_data)

    def extract_supplier_name(self, user_query):
        import re

        text = (user_query or "").strip()
        lowered = text.lower()

        if any(k in lowered for k in ("all supplier", "all suppliers", "total supplier", "all payables", "total payables", "كم علينا للموردين", "مديونية الموردين")):
            return None

        quoted = re.search(r"['\"]([^'\"]+)['\"]", text)
        if quoted:
            return quoted.group(1).strip()

        # direct pattern extractions
        patterns = [
            r"(?:supplier\s+balance|balance\s+supplier)\s+([\w\-\s]+)$",
            r"(?:supplier|vendor)\s+(?:name\s+)?([\w\-\s]+)$",
            r"([\w\-\s]+)\s+(?:supplier\s+balance|payable(?:s)?|balance)$",
            r"(?:payable(?:s)?|balance)\s+([\w\-\s]+)$",
            r"(?:رصيد|حساب)\s+المورد\s+([\w\-\s]+)$",
        ]
        stop_tokens = {
            "balance", "supplier", "suppliers", "all", "total", "payable", "payables", "outstanding",
            "name", "for", "of", "the", "a", "an", "رصيد", "حساب", "المورد", "الموردين", "كم", "علينا"
        }

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            candidate = match.group(1).strip()
            parts = [p for p in candidate.split() if p.lower() not in stop_tokens]
            candidate = " ".join(parts).strip()
            if candidate:
                return candidate

        # fallback: strip common prefix tokens then take trailing meaningful phrase
        cleaned = re.sub(
            r"\b(supplier|suppliers|vendor|name|balance|payable|payables|outstanding|for|of|the)\b",
            " ",
            lowered,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned and cleaned not in ("supplier", "suppliers", "payable", "payables", "balance"):
            return cleaned

        return None

    def extract_customer_name(self, user_query):
        import re

        text = (user_query or "").strip()
        lowered = text.lower()

        if any(k in lowered for k in ("all customer", "all customers", "total customer", "all balance", "total balance")):
            return None

        quoted = re.search(r"['\"]([^'\"]+)['\"]", text)
        if quoted:
            return quoted.group(1).strip()

        patterns = [
            r"(?:customer\s+balance|balance\s+customer)\s+([\w\-\s]+)$",
            r"(?:customer|party)\s+([\w\-\s]+)$",
        ]
        stop_tokens = {"balance", "customer", "customers", "all", "total", "outstanding"}

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            candidate = match.group(1).strip()
            parts = [p for p in candidate.split() if p.lower() not in stop_tokens]
            candidate = " ".join(parts).strip()
            if candidate:
                return candidate

        tail = re.search(r"(?:balance|outstanding)\s+([\w\-\s]+)$", text, re.IGNORECASE)
        if tail:
            candidate = tail.group(1).strip()
            if candidate.lower() not in ("all", "customer", "customers"):
                return candidate

        return None

    def parse_query(self, user_query):
        """Parse the user query to extract intent and entities (simple keyword-based)."""
        user_query = user_query.lower()
        analytics_mapping = {
            "customer_analytics": ["customer analytics", "top customers", "customer behavior", "customer summary"],
            "supplier_analytics": ["supplier analytics", "top suppliers", "supplier summary"],
            "project_analytics": ["project analytics", "project cost", "project summary"],
            "lead_analytics": ["lead analytics", "inactive leads", "lead summary"],
            "sales_analytics": ["sales analytics", "sales performance", "sales summary", "monthly sales"],
            "account_analytics": ["account analytics", "account balance", "financial summary", "profit and loss", "cash flow"],
            "employee_analytics": ["employee analytics", "employee activity", "employee summary"],
            "inventory_analytics": ["inventory analytics", "stock analytics", "inventory summary"],
            "warehouse_analytics": ["warehouse analytics", "warehouse utilization", "warehouse summary"],
            "advanced_stock_analytics": ["advanced stock analytics", "stockout risk", "warehouse utilization", "fast movers", "slow movers"]
        }
        for analytic, keywords in analytics_mapping.items():
            if any(word in user_query for word in keywords):
                return "analytics", {"analytic": analytic}
        mapping = {
            "doctypes": ["doctype", "doctypes", "document type", "document types"],
            "users": ["user", "users", "employee", "employees"],
            "roles": ["role", "roles"],
            "permissions": ["permission", "permissions"],
            "workflows": ["workflow", "workflows"],
            "reports": ["report", "reports"],
            "ledgers": ["ledger", "ledgers", "gl entry", "general ledger"],
            "inventory": ["inventory", "bin", "stock"],
            "projects": ["project", "projects"],
            "leads": ["lead", "leads"],
            "customers": ["customer", "customers"],
            "suppliers": ["supplier", "suppliers"],
            "related_documents": ["communication", "email", "note", "related document"]
        }
        for category, keywords in mapping.items():
            if any(word in user_query for word in keywords):
                return "get_info", {"category": category}
        return "get_info", {"category": "doctypes"}  # default fallback

    def check_permissions(self, user_context, intent, entities):
        """Check user permissions (to be implemented)."""
        # TODO: Implement real permission checks
        return True

    def generate_response(self, intent, entities, live_data, text_block=None):
        """Generate a data-driven response based on the query and text block guidance."""
        category = entities.get('category')
        if not live_data:
            return f"No data found for {category}."
        # If a text block is provided, use it as a template (simple replacement for now)
        if text_block:
            # Example: Replace placeholders in text block with live data values
            answer = text_block
            # You can add more advanced templating here
            if isinstance(live_data, list) and len(live_data) > 0:
                answer += f"\nعدد السجلات: {len(live_data)}"
            return answer
        # Default summaries for common categories
        if category == "users":
            users = [f"{u.get('full_name') or u.get('name')} ({u.get('email')})" for u in live_data]
            return f"Total users: {len(users)}\n" + "\n".join(users[:10])
        elif category == "doctypes":
            doctypes = [d.get('name') for d in live_data]
            return f"Total DocTypes: {len(doctypes)}\n" + ", ".join(doctypes[:10])
        elif category == "customers":
            customers = [c.get('customer_name') or c.get('name') for c in live_data]
            return f"Total customers: {len(customers)}\n" + ", ".join(customers[:10])
        elif category == "leads":
            leads = [l.get('lead_name') or l.get('name') for l in live_data]
            return f"Total leads: {len(leads)}\n" + ", ".join(leads[:10])
        # Add more categories as needed
        else:
            return f"Found {len(live_data)} records for {category}."

    def run_analytic(self, analytic):
        if analytic == "customer_analytics":
            return self.customer_analytics()
        elif analytic == "supplier_analytics":
            return self.supplier_analytics()
        elif analytic == "project_analytics":
            return self.project_analytics()
        elif analytic == "lead_analytics":
            return self.lead_analytics()
        elif analytic == "sales_analytics":
            return self.sales_analytics()
        elif analytic == "account_analytics":
            return self.account_analytics()
        elif analytic == "employee_analytics":
            return self.employee_analytics()
        elif analytic == "inventory_analytics":
            return self.inventory_analytics()
        elif analytic == "warehouse_analytics":
            return self.warehouse_analytics()
        elif analytic == "purchase_analytics":
            return self.purchase_analytics()
        elif analytic == "stock_analytics":
            return self.stock_analytics()
        elif analytic == "advanced_stock_analytics":
            return self.advanced_stock_analytics()
        else:
            return "No analytics available for this category."

    # Example analytic methods (stubs)
    def customer_analytics(self):
        customers = self.validator.validate_and_refresh("customers")
        if not customers:
            return "No customer data available."
        top_customers = customers[:5]
        return "Top customers: " + ", ".join([c.get("customer_name") or c.get("name") for c in top_customers])

    def supplier_analytics(self):
        suppliers = self.validator.validate_and_refresh("suppliers")
        if not suppliers:
            return "No supplier data available."
        return f"Total suppliers: {len(suppliers)}"

    def project_analytics(self):
        projects = self.validator.validate_and_refresh("projects")
        if not projects:
            return "No project data available."
        # Example: project cost tracking (stub)
        # You can expand this to fetch and sum costs from related ledgers or tasks
        summary = []
        for p in projects[:5]:  # Show top 5 projects
            name = p.get("project_name") or p.get("name")
            status = p.get("status")
            start = p.get("expected_start_date")
            end = p.get("expected_end_date")
            summary.append(f"{name} (Status: {status}, Start: {start}, End: {end})")
        return "Project Summary:\n" + "\n".join(summary)

    def lead_analytics(self):
        leads = self.validator.validate_and_refresh("leads")
        if not leads:
            return "No lead data available."
        inactive = [l for l in leads if l.get("status") == "Inactive"]
        return f"Inactive leads: {len(inactive)}"

    def sales_analytics(self):
        leads = self.validator.validate_and_refresh("leads")
        opportunities = self.validator.validate_and_refresh("opportunities") if hasattr(self.validator.data_access, 'get_opportunities') else []
        quotations = self.validator.validate_and_refresh("quotations") if hasattr(self.validator.data_access, 'get_quotations') else []
        sales_orders = self.validator.validate_and_refresh("sales_orders") if hasattr(self.validator.data_access, 'get_sales_orders') else []
        delivery_notes = self.validator.validate_and_refresh("delivery_notes") if hasattr(self.validator.data_access, 'get_delivery_notes') else []
        sales_invoices = self.validator.validate_and_refresh("sales_invoices") if hasattr(self.validator.data_access, 'get_sales_invoices') else []
        payments = self.validator.validate_and_refresh("payment_entries") if hasattr(self.validator.data_access, 'get_payment_entries') else []

        msg = (
            f"Sales Funnel Analytics:\n"
            f"Leads: {len(leads)}\n"
            f"Opportunities: {len(opportunities)}\n"
            f"Quotations: {len(quotations)}\n"
            f"Sales Orders: {len(sales_orders)}\n"
            f"Delivery Notes: {len(delivery_notes)}\n"
            f"Sales Invoices: {len(sales_invoices)}\n"
            f"Payments: {len(payments)}\n"
        )
        # Example: conversion rates
        if leads and opportunities:
            msg += f"Lead to Opportunity Conversion: {len(opportunities)/len(leads)*100:.1f}%\n"
        if opportunities and quotations:
            msg += f"Opportunity to Quotation Conversion: {len(quotations)/len(opportunities)*100:.1f}%\n"
        if quotations and sales_orders:
            msg += f"Quotation to Sales Order Conversion: {len(sales_orders)/len(quotations)*100:.1f}%\n"
        if sales_orders and delivery_notes:
            msg += f"Sales Order to Delivery Note Conversion: {len(delivery_notes)/len(sales_orders)*100:.1f}%\n"
        if delivery_notes and sales_invoices:
            msg += f"Delivery Note to Sales Invoice Conversion: {len(sales_invoices)/len(delivery_notes)*100:.1f}%\n"
        if sales_invoices and payments:
            msg += f"Sales Invoice to Payment Conversion: {len(payments)/len(sales_invoices)*100:.1f}%\n"
        return msg

    def account_analytics(self):
        ledgers = self.validator.validate_and_refresh("ledgers")
        if not ledgers:
            return "No financial data available."
        total_debit = sum([l.get("debit", 0) for l in ledgers])
        total_credit = sum([l.get("credit", 0) for l in ledgers])
        net_balance = total_debit - total_credit
        return (
            f"Financial Summary:\n"
            f"Total Debit: {total_debit}\n"
            f"Total Credit: {total_credit}\n"
            f"Net Balance: {net_balance}\n"
        )

    def employee_analytics(self):
        employees = self.validator.validate_and_refresh("employees") if hasattr(self.validator.data_access, 'get_employees') else []
        if not employees:
            return "No employee data available."
        active = [e for e in employees if e.get("status") == "Active"]
        summary = [f"{e.get('employee_name')} ({e.get('designation')}, {e.get('department')})" for e in active[:5]]
        return (
            f"Total Employees: {len(employees)}\n"
            f"Active Employees: {len(active)}\n"
            f"Sample: \n" + "\n".join(summary)
        )

    def inventory_analytics(self):
        inventory = self.validator.validate_and_refresh("inventory")
        total_qty = sum([i.get("actual_qty", 0) for i in inventory])
        return f"Total inventory quantity: {total_qty}"

    def warehouse_analytics(self):
        inventory = self.validator.validate_and_refresh("inventory")
        warehouses = set([i.get("warehouse") for i in inventory if i.get("warehouse")])
        return f"Warehouses in use: {len(warehouses)}"

    def purchase_analytics(self):
        # Enhanced: summarize purchase orders, total, and monthly breakdown
        purchase_orders = self.validator.validate_and_refresh("purchase_orders") if hasattr(self.validator.data_access, 'get_purchase_orders') else []
        if not purchase_orders:
            return "No purchase order data available."
        import datetime
        from collections import defaultdict
        # Monthly totals
        monthly_totals = defaultdict(float)
        for po in purchase_orders:
            date_str = po.get("transaction_date")
            total = po.get("total", 0) or po.get("grand_total", 0) or 0
            if date_str:
                try:
                    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                    month_key = dt.strftime("%Y-%m")
                except Exception:
                    month_key = "Unknown"
            else:
                month_key = "Unknown"
            monthly_totals[month_key] += total
        total_amount = sum([po.get("total", 0) or po.get("grand_total", 0) or 0 for po in purchase_orders])
        summary = [f"{po.get('name')}: {po.get('total', 0) or po.get('grand_total', 0) or 0}" for po in purchase_orders[:5]]
        monthly_lines = [f"{month}: {amount:.2f}" for month, amount in sorted(monthly_totals.items())]
        return (
            f"Total Purchase Orders: {len(purchase_orders)}\n"
            f"Total Purchase Amount: {total_amount:.2f}\n"
            f"Monthly Breakdown:\n" + "\n".join(monthly_lines) + "\n"
            f"Sample Orders: \n" + "\n".join(summary)
        )

    def stock_analytics(self):
        stock_entries = self.validator.validate_and_refresh("stock_entries") if hasattr(self.validator.data_access, 'get_stock_entries') else []
        if not stock_entries:
            return "No stock entry data available."
        total_in = sum([se.get("total_incoming_value", 0) for se in stock_entries])
        total_out = sum([se.get("total_outgoing_value", 0) for se in stock_entries])
        summary = [f"{se.get('name')} ({se.get('stock_entry_type')}, In: {se.get('total_incoming_value', 0)}, Out: {se.get('total_outgoing_value', 0)})" for se in stock_entries[:5]]
        return (
            f"Total Stock Entries: {len(stock_entries)}\n"
            f"Total Incoming Value: {total_in}\n"
            f"Total Outgoing Value: {total_out}\n"
            f"Sample: \n" + "\n".join(summary)
        )

    def advanced_stock_analytics(self):
        inventory = self.validator.validate_and_refresh("inventory")
        if not inventory:
            return "No inventory data available."
        # Stockout risk
        low_stock = [i for i in inventory if i.get("projected_qty", 0) <= 0]
        # Fast/slow movers (using actual_qty as a proxy)
        sorted_items = sorted(inventory, key=lambda x: x.get("actual_qty", 0), reverse=True)
        fast_movers = sorted_items[:5]
        slow_movers = sorted_items[-5:]
        # Warehouse utilization
        warehouse_counts = {}
        for i in inventory:
            wh = i.get("warehouse")
            if wh:
                warehouse_counts[wh] = warehouse_counts.get(wh, 0) + i.get("actual_qty", 0)
        fast_movers_text = ", ".join(
            [f"{i.get('item_code')} ({i.get('actual_qty')})" for i in fast_movers]
        )
        slow_movers_text = ", ".join(
            [f"{i.get('item_code')} ({i.get('actual_qty')})" for i in slow_movers]
        )
        msg = (
            f"Stockout Risk Items: {len(low_stock)}\n"
            f"Top Fast Movers: {fast_movers_text}\n"
            f"Top Slow Movers: {slow_movers_text}\n"
            f"Warehouse Utilization:\n"
        )
        for wh, qty in warehouse_counts.items():
            msg += f"  {wh}: {qty}\n"
        return msg
