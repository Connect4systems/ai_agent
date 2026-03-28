# AI Widget Role Visibility TODO

- [x] Audit frontend widget init flow in `ai_assistant/public/js/ai_chat.js`.
- [x] Enforce role-based widget visibility using backend `get_chat_preferences` (`widget_enabled`) before rendering widget.
- [x] Ensure blocked users remain blocked across page navigation/re-init attempts.
- [x] Keep existing widget behavior unchanged for authorized users.
- [x] Add/extend tests for `get_chat_preferences` role-based `widget_enabled` behavior (if current test suite supports it).
- [x] Run targeted tests and summarize final behavior.
