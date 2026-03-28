# Copyright (c) 2024, Connect4systems and contributors
# For license information, please see license.txt

from __future__ import annotations

import re
import time

import frappe
from frappe.model.document import Document


MAX_EXPRESSION_LENGTH = 42
MIN_EXPRESSION_LENGTH = 2


def _normalize_expression(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[\t\r\n]+", " ", text)
    text = re.sub(r"[^\w\u0600-\u06FF\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _language_from_expression(expression: str) -> str:
    return "ar" if re.search(r"[\u0600-\u06FF]", expression or "") else "en"


class AILearnedExpression(Document):
    def validate(self):
        normalized = _normalize_expression(self.expression or self.normalized_expression)
        if not normalized:
            frappe.throw("Expression is required.")

        if len(normalized) < MIN_EXPRESSION_LENGTH or len(normalized) > MAX_EXPRESSION_LENGTH:
            frappe.throw("Expression length is out of allowed range.")

        self.expression = normalized
        self.normalized_expression = normalized
        self.language = (self.language or _language_from_expression(normalized)).strip() or "en"
        if not self.updated_epoch:
            self.updated_epoch = int(time.time())
        if not self.last_used_on:
            self.last_used_on = frappe.utils.now_datetime()
