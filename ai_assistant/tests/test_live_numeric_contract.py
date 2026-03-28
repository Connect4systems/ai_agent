from ai_assistant.ai_agent_core import AIAgentCore
from ai_assistant.live_data_validation import LiveDataValidator


class DummyValidator:
    def __init__(
        self,
        payload_top=None,
        payload_outstanding=None,
        payload_top_suppliers=None,
        payload_supplier_outstanding=None,
        payload_payable_summary=None,
    ):
        class _DA:
            def __init__(self, top_payload, out_payload, top_suppliers_payload, supplier_out_payload, payable_summary_payload):
                self._top = top_payload or {}
                self._out = out_payload or {}
                self._top_suppliers = top_suppliers_payload or {}
                self._supplier_out = supplier_out_payload or {}
                self._payable_summary = payable_summary_payload or {}

            def get_top_customers_live(self, **kwargs):
                return self._top

            def get_customer_outstanding_live(self, **kwargs):
                return self._out

            def get_top_suppliers_live(self, **kwargs):
                return self._top_suppliers

            def get_supplier_outstanding_live(self, **kwargs):
                return self._supplier_out

            def get_accounts_payable_summary_live(self, **kwargs):
                return self._payable_summary or self._supplier_out

            def resolve_supplier_candidates_live(self, **kwargs):
                supplier_text = (kwargs.get("supplier_text") or "").strip()
                if not supplier_text:
                    return {
                        "success": True,
                        "value": 0,
                        "data": [],
                        "source": ["Supplier"],
                        "filters": {"supplier_text": supplier_text},
                        "computation": "resolver mock",
                        "as_of": "2026-01-01T00:00:00",
                        "error": None,
                    }

                return {
                    "success": True,
                    "value": 1,
                    "data": [{"name": supplier_text, "supplier_name": supplier_text, "disabled": 0}],
                    "source": ["Supplier"],
                    "filters": {"supplier_text": supplier_text},
                    "computation": "resolver mock",
                    "as_of": "2026-01-01T00:00:00",
                    "error": None,
                }

        self.data_access = _DA(
            payload_top,
            payload_outstanding,
            payload_top_suppliers,
            payload_supplier_outstanding,
            payload_payable_summary,
        )

    def validate_numeric_response_payload(self, payload):
        required = ["success", "source", "filters", "computation", "as_of"]
        for k in required:
            if k not in payload:
                return False, f"missing {k}"
        if not isinstance(payload.get("source"), list) or not payload.get("source"):
            return False, "invalid source"
        if not isinstance(payload.get("filters"), dict):
            return False, "invalid filters"
        if not payload.get("computation"):
            return False, "missing computation"
        if not payload.get("as_of"):
            return False, "missing as_of"
        return True, None

    def validate_and_refresh(self, key, *args, **kwargs):
        return []


def _base_payload(success=True):
    return {
        "success": success,
        "value": 0,
        "data": [],
        "source": ["Sales Invoice"],
        "filters": {"docstatus": 1},
        "computation": "SUM(grand_total)",
        "as_of": "2026-01-01T00:00:00",
        "error": None,
    }


def test_top_customers_response_schema():
    payload = _base_payload(True)
    payload["data"] = [
        {"customer": "A", "total_sales": 120000, "currency": "EGP"},
        {"customer": "B", "total_sales": 90000, "currency": "EGP"},
    ]

    agent = AIAgentCore()
    agent.validator = DummyValidator(payload_top=payload, payload_outstanding=_base_payload(True))

    out = agent.handle_query("top customers")
    assert "Top Customers" in out
    assert "1️⃣ A" in out
    assert "Answer:" in out
    assert "Basis:" not in out
    assert "Computation:" not in out
    assert "As of:" not in out


def test_arabic_top_customers_trigger():
    payload = _base_payload(True)
    payload["data"] = [{"customer": "شركة النور", "total_sales": 120000, "currency": "EGP"}]

    agent = AIAgentCore()
    agent.validator = DummyValidator(payload_top=payload, payload_outstanding=_base_payload(True))

    out = agent.handle_query("أفضل العملاء")
    assert "أفضل العملاء" in out


def test_outstanding_failure_message():
    fail_payload = _base_payload(False)
    fail_payload["error"] = "db unavailable"

    agent = AIAgentCore()
    agent.validator = DummyValidator(payload_top=_base_payload(True), payload_outstanding=fail_payload)

    out = agent.handle_query("customer balance for ABC")
    assert "I cannot return an accurate live value right now because live database access/query failed." in out
    assert "db unavailable" in out


def test_outstanding_ambiguity_message():
    payload = _base_payload(True)
    payload["data"] = [
        {"customer": "ABC Trading", "outstanding_amount": 1000, "currency": "EGP"},
        {"customer": "ABC Supplies", "outstanding_amount": 2000, "currency": "EGP"},
    ]

    agent = AIAgentCore()
    agent.validator = DummyValidator(payload_top=_base_payload(True), payload_outstanding=payload)

    out = agent.handle_query("customer balance abc")
    assert "Multiple matching customers found" in out


def test_supplier_balance_phrase_openai_balance():
    payload = _base_payload(True)
    payload["data"] = [{"supplier": "Open AI", "outstanding_amount": 0, "currency": "EGP"}]
    payload["value"] = 0

    agent = AIAgentCore()
    agent.validator = DummyValidator(
        payload_top=_base_payload(True),
        payload_outstanding=_base_payload(True),
        payload_top_suppliers=_base_payload(True),
        payload_supplier_outstanding=payload,
    )

    out = agent.handle_query("Open AI balance")
    assert "Answer: 0.00 EGP" in out


def test_supplier_balance_phrase_balance_openai():
    payload = _base_payload(True)
    payload["data"] = [{"supplier": "Open AI", "outstanding_amount": 1500, "currency": "EGP"}]

    agent = AIAgentCore()
    agent.validator = DummyValidator(
        payload_top=_base_payload(True),
        payload_outstanding=_base_payload(True),
        payload_top_suppliers=_base_payload(True),
        payload_supplier_outstanding=payload,
    )

    out = agent.handle_query("balance Open AI")
    assert "Answer: 1,500.00 EGP" in out


def test_supplier_ambiguity_message():
    payload = _base_payload(True)
    payload["data"] = [
        {"supplier": "Open AI Cairo", "outstanding_amount": 100, "currency": "EGP"},
        {"supplier": "Open AI Dubai", "outstanding_amount": 200, "currency": "EGP"},
    ]

    agent = AIAgentCore()
    agent.validator = DummyValidator(
        payload_top=_base_payload(True),
        payload_outstanding=_base_payload(True),
        payload_top_suppliers=_base_payload(True),
        payload_supplier_outstanding=payload,
    )

    out = agent.handle_query("supplier Open AI balance")
    assert "Multiple matching suppliers found" in out


def test_top_suppliers_outstanding_arabic_sorted_top_5():
    payload = _base_payload(True)
    payload["source"] = ["Accounts Payable Summary Report"]
    payload["data"] = [
        {"supplier": "S3", "outstanding_amount": 300, "currency": "EGP"},
        {"supplier": "S1", "outstanding_amount": 100, "currency": "EGP"},
        {"supplier": "S5", "outstanding_amount": 500, "currency": "EGP"},
        {"supplier": "S2", "outstanding_amount": 200, "currency": "EGP"},
        {"supplier": "S7", "outstanding_amount": 700, "currency": "EGP"},
        {"supplier": "S6", "outstanding_amount": 600, "currency": "EGP"},
        {"supplier": "S4", "outstanding_amount": 400, "currency": "EGP"},
    ]

    agent = AIAgentCore()
    agent.validator = DummyValidator(
        payload_top=_base_payload(True),
        payload_outstanding=_base_payload(True),
        payload_top_suppliers=_base_payload(True),
        payload_supplier_outstanding=_base_payload(True),
        payload_payable_summary=payload,
    )

    out = agent.handle_query("الموردين اللي ليهم فلوس")
    assert "أعلى الموردين حسب الرصيد المستحق:" in out
    assert "1️⃣ S7" in out
    assert "2️⃣ S6" in out
    assert "3️⃣ S5" in out
    assert "4️⃣ S4" in out
    assert "5️⃣ S3" in out
    assert "S2" not in out


def test_top_suppliers_outstanding_tie_break_by_name():
    payload = _base_payload(True)
    payload["source"] = ["Accounts Payable Summary Report"]
    payload["data"] = [
        {"supplier": "beta", "outstanding_amount": 500, "currency": "EGP"},
        {"supplier": "Alpha", "outstanding_amount": 500, "currency": "EGP"},
        {"supplier": "zeta", "outstanding_amount": 400, "currency": "EGP"},
    ]

    agent = AIAgentCore()
    agent.validator = DummyValidator(
        payload_top=_base_payload(True),
        payload_outstanding=_base_payload(True),
        payload_top_suppliers=_base_payload(True),
        payload_supplier_outstanding=_base_payload(True),
        payload_payable_summary=payload,
    )

    out = agent.handle_query("top suppliers by outstanding")
    assert "Top Suppliers by Outstanding Payable:" in out
    assert out.index("1️⃣ Alpha") < out.index("2️⃣ beta")


def test_top_suppliers_outstanding_no_data_message_arabic():
    payload = _base_payload(True)
    payload["source"] = ["Accounts Payable Summary Report"]
    payload["data"] = [
        {"supplier": "S1", "outstanding_amount": 0, "currency": "EGP"},
    ]

    agent = AIAgentCore()
    agent.validator = DummyValidator(
        payload_top=_base_payload(True),
        payload_outstanding=_base_payload(True),
        payload_top_suppliers=_base_payload(True),
        payload_supplier_outstanding=_base_payload(True),
        payload_payable_summary=payload,
    )

    out = agent.handle_query("الموردين الذين لهم رصيد مستحق")
    assert out == "لا يوجد موردون لديهم رصيد مستحق حالياً."


def test_suppliers_balanace_phrase_returns_numeric_answer():
    payload = _base_payload(True)
    payload["source"] = ["Accounts Payable Summary Report"]
    payload["data"] = [
        {"supplier": "Open AI", "outstanding": 1250, "currency": "EGP"},
        {"supplier": "Contabo", "outstanding": 2750, "currency": "EGP"},
    ]

    agent = AIAgentCore()
    agent.validator = DummyValidator(
        payload_top=_base_payload(True),
        payload_outstanding=_base_payload(True),
        payload_top_suppliers=_base_payload(True),
        payload_supplier_outstanding=_base_payload(True),
        payload_payable_summary=payload,
    )

    out = agent.handle_query("Suppliers Balanace")
    assert "Answer: 4,000.00 EGP" in out


def test_suppliers_balance_contabo_returns_numeric_answer():
    payload = _base_payload(True)
    payload["source"] = ["Accounts Payable Summary Report"]
    payload["data"] = [
        {"supplier": "Contabo", "outstanding": 3333.33, "currency": "EGP"},
    ]

    agent = AIAgentCore()
    agent.validator = DummyValidator(
        payload_top=_base_payload(True),
        payload_outstanding=_base_payload(True),
        payload_top_suppliers=_base_payload(True),
        payload_supplier_outstanding=_base_payload(True),
        payload_payable_summary=payload,
    )

    out = agent.handle_query("Suppliers Balance Contabo")
    assert "Answer: 3,333.33 EGP" in out


def test_arabic_total_supplier_balance_returns_numeric_answer():
    payload = _base_payload(True)
    payload["source"] = ["Accounts Payable Summary Report"]
    payload["data"] = [
        {"supplier": "S1", "balance": 100, "currency": "EGP"},
        {"supplier": "S2", "balance": 200, "currency": "EGP"},
    ]

    agent = AIAgentCore()
    agent.validator = DummyValidator(
        payload_top=_base_payload(True),
        payload_outstanding=_base_payload(True),
        payload_top_suppliers=_base_payload(True),
        payload_supplier_outstanding=_base_payload(True),
        payload_payable_summary=payload,
    )

    out = agent.handle_query("اجمالي رصيد الموردين")
    assert "Answer: 300.00 EGP" in out


def test_supplier_not_found_format_is_deterministic():
    payload = _base_payload(True)
    payload["source"] = ["Accounts Payable Summary Report"]
    payload["data"] = []

    agent = AIAgentCore()
    agent.validator = DummyValidator(
        payload_top=_base_payload(True),
        payload_outstanding=_base_payload(True),
        payload_top_suppliers=_base_payload(True),
        payload_supplier_outstanding=_base_payload(True),
        payload_payable_summary=payload,
    )

    out = agent.handle_query("Supplier balance TEST SUPPLIER")
    assert (
        out == "No matching live Accounts Payable data found for supplier TEST SUPPLIER."
        or "No matching live Accounts Payable data found for supplier" in out
    )


def test_payload_validator_helper():
    v = LiveDataValidator(None, None)
    ok, err = v.validate_numeric_response_payload(_base_payload(True))
    assert ok is True
    assert err is None

    bad = {"success": True}
    ok, err = v.validate_numeric_response_payload(bad)
    assert ok is False
    assert "Missing required payload keys" in err
