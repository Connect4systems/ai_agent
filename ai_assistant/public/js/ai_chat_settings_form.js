frappe.ui.form.on("AI Chat Settings", {
    refresh(frm) {
        frm.dashboard.set_headline_alert(
            __("Set provider credentials here, then manage role-based agent behavior from AI Agent."),
            "blue"
        );

        frm.add_custom_button(__("Open AI Agents"), () => {
            frappe.set_route("List", "AI Agent");
        });
        frm.add_custom_button(__("Update Knowledge"), () => {
            frappe.call({
                method: "ai_assistant.ai_assistant.doctype.ai_chat_settings.ai_chat_settings.update_knowledge_library",
                freeze: true,
                callback: function(r) {
                    if (r.message) {
                        let summary = r.message;
                        let msg = '<b>Knowledge library updated:</b><br><ul>';
                        for (const [cat, count] of Object.entries(summary)) {
                            msg += `<li><b>${cat}</b>: ${count}</li>`;
                        }
                        msg += '</ul>';
                        frappe.msgprint(msg);
                    } else {
                        frappe.msgprint(__("Knowledge update triggered."));
                    }
                },
                error: function() {
                    frappe.msgprint(__("Failed to update knowledge library."));
                }
            });
        });
    },
});
