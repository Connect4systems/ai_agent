async function ensure_default_learning_blocks(frm) {
    if (!frm || !frm.is_new()) {
        return;
    }

    const existingRows = Array.isArray(frm.doc.learning_text_blocks) ? frm.doc.learning_text_blocks : [];
    if (existingRows.length) {
        return;
    }

    if (frm.__defaultLearningBlocksLoading || frm.__defaultLearningBlocksLoaded) {
        return;
    }

    frm.__defaultLearningBlocksLoading = true;

    try {
        const response = await frappe.call({
            method: "ai_assistant.ai_assistant.doctype.ai_agent.ai_agent.get_default_learning_text_blocks",
        });

        const blocks = Array.isArray(response && response.message) ? response.message : [];
        if (!blocks.length) {
            return;
        }

        blocks.forEach((block) => {
            frm.add_child("learning_text_blocks", {
                enabled: block.enabled,
                title: block.title,
                language: block.language,
                priority: block.priority,
                text_block: block.text_block,
            });
        });

        frm.refresh_field("learning_text_blocks");
        frm.dirty();
        frm.__defaultLearningBlocksLoaded = true;
    } catch (error) {
        frappe.show_alert({
            message: __("Could not load default learning text blocks."),
            indicator: "orange",
        });
    } finally {
        frm.__defaultLearningBlocksLoading = false;
    }
}


frappe.ui.form.on("AI Agent", {
    onload(frm) {
        ensure_default_learning_blocks(frm);
    },

    refresh(frm) {
        ensure_default_learning_blocks(frm);

        frm.dashboard.set_headline_alert(
            __(
                "Configure AI Roles for widget access, then add Data Sources and Learning Text Blocks to control what the agent can read and how it responds."
            ),
            "green"
        );

        frm.add_custom_button(__("Open Chat Settings"), () => {
            frappe.set_route("Form", "AI Chat Settings", "AI Chat Settings");
        });
    },
});
