# Copyright (c) 2024, Connect4systems and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe

from frappe.model.document import Document

from ai_assistant.default_learning_blocks import DEFAULT_AGENT_LEARNING_TEXT_BLOCKS


def _copy_default_learning_blocks() -> list[dict]:
    return [dict(row) for row in DEFAULT_AGENT_LEARNING_TEXT_BLOCKS]


class AIAgent(Document):
    def validate(self):
        if self.is_new():
            self._ensure_default_learning_text_blocks()

    def before_insert(self):
        self._ensure_default_learning_text_blocks()

    def _ensure_default_learning_text_blocks(self) -> None:
        if list(getattr(self, "learning_text_blocks", []) or []):
            return

        for row in _copy_default_learning_blocks():
            self.append("learning_text_blocks", row)


@frappe.whitelist()
def get_default_learning_text_blocks() -> list[dict]:
    return _copy_default_learning_blocks()
