from __future__ import annotations


AI_ADMIN_ROLE = "AI Admin"


AI_ADMIN_PERMISSION_PROFILES = {
    "AI Agent": "full",
    "AI Agent Data Source": "full",
    "AI Agent Text Block": "full",
    "AI Chat Log": "full",
    "AI Chat Settings": "full",
    "AI Data Source Policy": "full",
    "AI Learned Expression": "full",
    "AI Role": "full",
    "Account": "full",
    "Address": "full",
    "Company": "full",
    "Contact": "full",
    "Cost Center": "full",
    "Currency": "full",
    "Customer": "full",
    "Customer Group": "full",
    "Delivery Note": "full",
    "Department": "full",
    "Designation": "full",
    "Employee": "full",
    "Fiscal Year": "full",
    "GL Entry": "read",
    "Item": "full",
    "Item Group": "full",
    "Journal Entry": "full",
    "Lead": "full",
    "Material Request": "full",
    "Mode of Payment": "full",
    "Opportunity": "full",
    "Quotation": "full",
    "Payment Entry": "full",
    "Price List": "full",
    "Purchase Order": "full",
    "Purchase Receipt": "full",
    "Report": "full",
    "Dashboard": "full",
    "Dashboard Chart": "full",
    "Dashboard Chart Source": "full",
    "Prepared Report": "read",
    "Workspace": "read",
    "Role": "full",
    "Sales Invoice": "full",
    "Sales Order": "full",
    "Sales Partner": "full",
    "Sales Person": "full",
    "Stock Entry": "full",
    "Stock Ledger Entry": "read",
    "Stock Reconciliation": "full",
    "Supplier": "full",
    "Supplier Group": "full",
    "Task": "full",
    "Territory": "full",
    "UOM": "full",
    "User": "full",
    "User Permission": "full",
    "Warehouse": "full",
    "Workflow": "full",
    "Workflow Action Master": "full",
    "Workflow State": "full",
    "Workflow Transition": "full",
    # Purchase & Sales additions
    "Purchase Invoice": "full",
    "Item Price": "full",
    "Pricing Rule": "full",
    "Landed Cost Voucher": "full",
    # HRMS
    "Leave Application": "full",
    "Leave Allocation": "full",
    "Leave Type": "read",
    "Attendance": "read",
    "Salary Slip": "read",
    "Payroll Entry": "read",
    "Salary Structure": "read",
    "Salary Structure Assignment": "read",
    "Expense Claim": "full",
    "Holiday List": "read",
    "Employee Grade": "read",
    "Job Opening": "full",
    "Job Applicant": "full",
    "Appraisal": "full",
    # Project & Asset
    "Project": "full",
    "Asset": "full",
    "Asset Movement": "read",
    "Budget": "read",
    "Payment Ledger Entry": "read",
}


DEFAULT_AGENT_INSTRUCTION_BLOCK = (
    "You are a smart, friendly, and professional ERP assistant. "
    "You work on behalf of the company and help users get the most from ERPNext. "
    "Always reply in the same language the user writes in — Arabic when they write in Arabic, English when they write in English, "
    "and so on for any other language detected. \n"
    "Be warm, clear, and helpful. Avoid overly formal or robotic phrasing. \n"
    "You have direct access to the live ERPNext database. "
    "Use DocType records, workflows, and permissions to answer live-data questions (transactions, balances, statuses, counts, amounts). "
    "For product guidance or how-to questions, draw on Frappe/ERPNext/HRMS platform knowledge "
    "(reference: https://frappe.io/erpnext/). \n"
    "Pay attention to how the user phrases questions and what topics come up repeatedly — "
    "use this to personalise responses and anticipate follow-up needs. \n"
    "Map common ERP synonyms, plural forms, and small spelling mistakes to the correct DocTypes. "
    "Prefer one best-matching DocType per question. "
    "Ask one short clarifying question only when context is genuinely insufficient. "
    "Never invent transactions, balances, records, or permissions."
)

# Plain-text form of the default instruction shown in the AI Agent form.
DEFAULT_AGENT_INSTRUCTION_TEXT = (
    "You are a smart, friendly, and professional ERP assistant working on behalf of the company.\n"
    "Always reply in the same language the user writes in — Arabic, English, Chinese, or any other detected language.\n"
    "Address the user by their first name with the honorific Mr. (e.g. Mr. Ahmad) when greeting or when it feels natural.\n"
    "Be warm, clear, and concise. Avoid robotic or overly formal phrasing.\n\n"
    "You have direct access to the live ERPNext database for this company.\n"
    "Use DocType records, workflows, and permissions to answer live-data questions (transactions, balances, statuses, counts, amounts).\n"
    "For product guidance or how-to questions, draw on Frappe / ERPNext / HRMS platform knowledge and the official documentation at https://frappe.io/erpnext/.\n\n"
    "Pay attention to how the user phrases questions and what topics come up repeatedly — "
    "use this to personalise answers and anticipate follow-up needs.\n"
    "Map common ERP synonyms, plural forms, and small spelling mistakes to the correct DocTypes.\n"
    "Prefer one best-matching DocType per question.\n"
    "Ask one short clarifying question only when the context is genuinely insufficient.\n"
    "Never invent transactions, balances, records, or permissions."
)


DEFAULT_ANSWER_MODE = "summary"


ANSWER_MODES = [
    {
        "key": "guide",
        "label": "Guide",
        "description": "Read DocType/workflow context and provide concise step-by-step guidance.",
    },
    {
        "key": "summary",
        "label": "Summary",
        "description": "Short answer with key result plus related helpful topics.",
    },
    {
        "key": "general",
        "label": "General",
        "description": "General platform guidance from Frappe/ERPNext/HRMS knowledge.",
    },
]


ANSWER_MODE_ALIASES = {
    "1": "guide",
    "2": "summary",
    "3": "general",
    "4": "summary",
    "workflow": "guide",
    "action": "guide",
    "actions": "guide",
    "approval": "guide",
    "process": "guide",
    "guide": "guide",
    "instruction": "guide",
    "instructions": "guide",
    "steps": "guide",
    "step by step": "guide",
    "detail": "guide",
    "details": "guide",
    "records": "guide",
    "record": "guide",
    "list": "guide",
    "brief": "summary",
    "kpi": "summary",
    "overview": "summary",
    "short": "summary",
    "summary": "summary",
    "general": "general",
    "docs": "general",
    "documentation": "general",
    "internet": "general",
    "web": "general",
}


ANSWER_MODE_PROMPTS = {
    "guide": (
        "Answer mode: Guide. Prioritize system database context (DocTypes, records, workflows, permissions) and provide a concise step-by-step plan. "
        "Keep steps practical and action-oriented, and clearly state missing inputs when needed."
    ),
    "summary": (
        "Answer mode: Summary. Lead with a short direct answer and include only the most important KPI, status, count, or amount. "
        "Then add one compact line with related topics that may help next."
    ),
    "general": (
        "Answer mode: General. Provide product-level guidance for Frappe/ERPNext/HRMS and installed app behavior. "
        "Do not claim live internet browsing. If live database facts are required, ask to switch to Guide or Summary and specify the target DocType."
    ),
}


DEFAULT_ANSWER_MODE_TEXT_BLOCK = (
    "Reply style options:\n"
    "- Guide: step-by-step actions from DocType/workflow context.\n"
    "- Summary: short answer with key result and related helpful topics.\n"
    "- General: product guidance for Frappe/ERPNext/HRMS behavior."
)


DOCTYPE_LANGUAGE_ALIASES = {
    "Account": (
        "account",
        "accounts",
        "bank account",
        "bank accounts",
        "cash account",
        "cash accounts",
        "bank balance",
        "cash balance",
        "cash in bank",
        "chart of accounts",
        "chart accounts",
        "ledger account",
        "coa",
        "حساب",
        "الحساب",
        "الحسابات",
        "حساب بنكي",
        "حسابات بنكية",
        "الحساب البنكي",
        "رصيد البنك",
        "رصيد الحساب البنكي",
        "شجرة الحسابات",
        "دليل الحسابات",
    ),
    "Address": ("address", "addresses", "location address", "location addresses", "عنوان", "العنوان", "العناوين"),
    "Company": ("company", "companies", "organization", "organisations", "organisation", "شركة", "الشركة", "الشركات"),
    "Contact": ("contact", "contacts", "ciontact", "ciontacts", "جهة اتصال", "جهات الاتصال"),
    "Customer": ("customer", "customers", "client", "clients", "عميل", "العميل", "العملاء"),
    "Customer Group": ("customer group", "customer groups", "مجموعة العملاء"),
    "Delivery Note": (
        "delivery note",
        "delivery notes",
        "delievery note",
        "delievery notes",
        "shipment note",
        "shipment notes",
        "إذن تسليم",
        "اذن تسليم",
        "إيصال تسليم",
        "تسليم",
    ),
    "Employee": ("employee", "employees", "staff member", "staff members", "staff", "موظف", "الموظف", "الموظفين"),
    "GL Entry": (
        "general ledger",
        "general ledgers",
        "general ledger entry",
        "general ledger entries",
        "gl",
        "gl entry",
        "gl entries",
        "ledger",
        "ledgers",
        "leadger",
        "leadgers",
        "trial balance",
        "income statement",
        "profit and loss",
        "p&l",
        "pnl",
        "balance sheet",
        "bank ledger",
        "cash ledger",
        "bank balance",
        "cash balance",
        "cash in bank",
        "دفتر الأستاذ",
        "دفتر الاستاذ",
        "قيود الأستاذ",
        "قيود الاستاذ",
        "دفتر الأستاذ العام للبنك",
        "رصيد البنك",
        "رصيد الحساب البنكي",
        "الارباح والخسائر",
        "الأرباح والخسائر",
    ),
    "Item": ("item", "items", "product", "products", "sku", "skus", "صنف", "الأصناف", "الاصناف", "منتج", "المنتجات"),
    "Item Group": ("item group", "item groups", "product group", "product groups", "category", "categories", "مجموعة أصناف"),
    "Journal Entry": ("journal entry", "journal entries", "journal", "journals", "قيد يومية", "قيود اليومية"),
    "Lead": ("lead", "leads", "prospect", "prospects", "عميل محتمل", "عملاء محتملون"),
    "Opportunity": ("opportunity", "opportunities", "deal", "deals", "فرصة", "الفرص"),
    "Purchase Order": ("purchase order", "purchase orders", "purchase", "purchases", "أمر شراء", "امر شراء", "أوامر شراء", "مشتريات", "المشتريات", "الشراء", "اجمالي المشتريات", "إجمالي المشتريات"),
    "Purchase Receipt": (
        "purchase receipt",
        "purchase receipts",
        "purchase received",
        "goods receipt",
        "goods receipts",
        "grn",
        "grns",
        "استلام شراء",
        "استلامات شراء",
    ),
    "Report": (
        "report",
        "reports",
        "query report",
        "custom report",
        "financial report",
        "financial reports",
        "ledger report",
        "ledger reports",
        "account report",
        "account reports",
        "trial balance report",
        "profit and loss report",
        "تقرير",
        "تقارير",
        "تقرير مخصص",
        "تقرير مالي",
        "تقارير مالية",
        "تقرير دفتر الأستاذ",
        "تقرير الحسابات",
    ),
    "Dashboard": (
        "dashboard",
        "dashboards",
        "kpi dashboard",
        "analytics dashboard",
        "لوحة",
        "لوحة معلومات",
        "لوحات",
    ),
    "Quotation": (
        "quotation",
        "quotations",
        "quote",
        "quotes",
        "sales quotation",
        "price quotation",
        "عرض سعر",
        "عروض سعر",
        "عرض اسعار",
        "عرض أسعار",
        "عروض اسعار",
        "عروض الأسعار",
    ),
    "Sales Invoice": ("sales invoice", "sales invoices", "customer invoice", "customer invoices", "فاتورة مبيعات", "فواتير مبيعات"),
    "Sales Order": ("sales order", "sales orders", "أمر بيع", "امر بيع", "أوامر بيع"),
    "Sales Partner": ("sales partner", "sales partners", "partner", "partners", "شريك مبيعات"),
    "Sales Person": ("sales person", "salesperson", "sales rep", "مندوب مبيعات", "مندوبي المبيعات"),
    "Stock Entry": ("stock entry", "stock entries", "stock transfer", "stock transfers", "قيد مخزني", "تحويل مخزني"),
    "Stock Ledger Entry": (
        "stock ledger",
        "stock ledger entry",
        "stock ledger entries",
        "inventory ledger",
        "inventory ledgers",
        "دفتر المخزون",
    ),
    "Stock Reconciliation": (
        "stock reconciliation",
        "inventory reconciliation",
        "physical count",
        "stock count",
        "جرد المخزون",
        "مطابقة المخزون",
        "الجرد",
    ),
    "Supplier": ("supplier", "suppliers", "vendor", "vendors", "مورد", "المورد", "الموردين"),
    "Supplier Group": ("supplier group", "supplier groups", "vendor group", "vendor groups", "مجموعة الموردين"),
    "Territory": ("territory", "territories", "region", "regions", "منطقة", "المنطقة", "المناطق"),
    "User": ("user", "users", "system user", "system users", "مستخدم", "المستخدم", "المستخدمين"),
    "User Permission": ("user permission", "user permissions", "permission rule", "permission rules", "صلاحية", "صلاحيات"),
    "Warehouse": ("warehouse", "warehouses", "warhouse", "warhouses", "store", "stores", "مخزن", "المخزن", "المخازن"),
    "Workflow": ("workflow", "workflows", "approval workflow", "approval workflows", "سير العمل", "اعتماد"),
    # Purchase Invoice
    "Purchase Invoice": ("purchase invoice", "purchase invoices", "vendor invoice", "vendor invoices", "supplier invoice", "فاتورة شراء", "فواتير شراء", "فاتورة مورد"),
    # Pricing
    "Item Price": ("item price", "item prices", "price list item", "selling price", "buying price", "سعر الصنف", "قائمة الأسعار"),
    "Pricing Rule": ("pricing rule", "pricing rules", "discount rule", "price rule", "قاعدة التسعير", "قواعد الأسعار"),
    # HRMS
    "Leave Application": ("leave application", "leave request", "time off request", "vacation request", "طلب إجازة", "طلب اجازة", "إجازة"),
    "Leave Allocation": ("leave allocation", "leave balance", "leave entitlement", "allocated leaves", "رصيد الإجازات", "تخصيص الإجازات"),
    "Leave Type": ("leave type", "leave types", "type of leave", "نوع الإجازة", "أنواع الإجازات"),
    "Attendance": ("attendance", "present", "absent", "checkin", "check in", "حضور", "الحضور", "غياب", "سجل الحضور"),
    "Salary Slip": ("salary slip", "payslip", "pay slip", "salary", "pay stub", "راتب", "كشف راتب", "قسيمة راتب", "مسير الراتب"),
    "Payroll Entry": ("payroll", "payroll entry", "pay run", "monthly payroll", "مسير الرواتب", "الرواتب الشهرية", "الرواتب"),
    "Salary Structure": ("salary structure", "pay structure", "grade structure", "هيكل الراتب", "هيكل الرواتب"),
    "Salary Structure Assignment": ("salary structure assignment", "assigned salary structure", "راتب الموظف", "هيكل راتب الموظف"),
    "Expense Claim": ("expense claim", "expense report", "expenses reimbursement", "مطالبة مصروفات", "مصروفات موظف", "مصروف"),
    "Holiday List": ("holiday list", "public holidays", "official holidays", "قائمة الإجازات", "الإجازات الرسمية", "العطل الرسمية"),
    "Employee Grade": ("employee grade", "employee grades", "grade", "pay grade", "درجة وظيفية", "الدرجات الوظيفية"),
    "Job Opening": ("job opening", "job vacancy", "vacancy", "open position", "وظيفة شاغرة", "فرصة عمل", "شواغر"),
    "Job Applicant": ("job applicant", "job application", "applicant", "candidate", "متقدم وظيفة", "مرشح وظيفة"),
    "Appraisal": ("appraisal", "performance review", "employee evaluation", "تقييم الموظف", "تقييم الأداء"),
    # Project & Task
    "Project": ("project", "projects", "مشروع", "المشروع", "المشاريع"),
    "Task": ("task", "tasks", "to do", "todo", "مهمة", "المهمة", "المهام"),
    # Asset
    "Asset": ("asset", "assets", "fixed asset", "fixed assets", "أصل", "أصل ثابت", "الأصول الثابتة"),
}