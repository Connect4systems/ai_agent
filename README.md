# AI Assistant for ERPNext v15

A Frappe application that adds an **AI chat assistant** to your ERPNext v15 installation. The assistant can:

- **Read your database** — queries permitted DocTypes for the logged-in user to include live record data in its answers.
- **Understand workflows** — includes active Workflow definitions as context so it can answer process questions.
- **Respect user permissions** — reads User Permissions and role assignments to answer "what can I access?" style questions.
- **Provision an AI Admin role** — creates an `AI Admin` role with broad ERP access for core selling, buying, stock, CRM, accounting, HR, workflow, and user-management DocTypes.
- **Handle human-language ERP questions** — maps common business terms, plural forms, and frequent spelling mistakes like `delievery note`, `purchase received`, `warhouse`, and `ciontact` to the correct DocTypes.
- **Use Agent-based control** — `AI Agent` replaces standalone policy rows and centralizes behavior, allowlisted DocTypes, and role-based widget access.
- **Let users choose answer style** — users can select `1 Summary`, `2 Detailed Records`, or `3 Workflow and Action` before sending a question.
- **Persistent chat log** — every conversation is saved as an `AI Chat Log` entry.
- **Multi-provider** — supports OpenAI, Azure OpenAI, and Ollama (local/self-hosted).

## Installation

```bash
# From your Frappe bench directory
bench get-app https://github.com/Connect4systems/ai ai_assistant
bench --site <your-site> install-app ai_assistant
bench --site <your-site> migrate
bench build --app ai_assistant
bench restart
```

## Configuration

1. Log in as **System Manager**.
2. Open **AI Chat Settings** (search in the Awesome Bar).
3. Fill in:
   | Field | Description |
   |---|---|
   | AI Provider | `OpenAI`, `Azure OpenAI`, or `Ollama (Local)` |
   | API Key | Your OpenAI/Azure API key. For Ollama enter the base URL (default `http://localhost:11434`). For Azure use `<endpoint>\|\|<api_key>`. |
   | Model Name | e.g. `gpt-4o-mini`, `gpt-4o`, `llama3` |
   | Max Tokens | Maximum reply length (default 1024) |
   | Temperature | Creativity (0 = deterministic, 1 = creative; default 0.3) |
   | Max DB Rows | Maximum rows fetched per DocType query (default 20) |
   | Max DocTypes per Question | Maximum number of DocTypes the query broker will read in one question (default 3) |
   | Default Agent | Optional global default AI Agent used when user roles match its AI Role table |
   | Require Data Source Policy | If enabled, AI reads are restricted to allowlisted DocTypes configured in the active AI Agent data sources |
   | Include Workflow Information | Inject active workflow states into context |
   | Include User Permissions | Inject user permission entries into context |
   | Default Answer Mode | Default reply style used in the widget when users have not chosen 1, 2, or 3 |
   | Answer Mode Text Block | Admin-editable text shown in the widget for the 1, 2, 3 choices |
   | Agent Instruction Block | Extra agent instructions appended to the prompt for natural-language ERP questions |
   | System Prompt | Custom assistant persona/instructions (optional) |

4. Open **AI Agent** and configure:
   - `AI Roles` table to define which roles can access the widget.
   - `Data Sources` table to define allowed DocTypes, fields, and row caps.
   - agent-specific answer mode text block, instructions, and prompt overrides.
5. For users who should have broad AI-assisted ERP access, assign the `AI Admin` role from the User form.

### API Endpoints

- `ai_assistant.api.chat.send_message` — AI response endpoint with permission-aware query broker.
- `ai_assistant.api.chat.get_chat_history` — Returns current user's chat history.
- `ai_assistant.api.chat.get_accessible_doctypes` — Returns readable DocTypes/modules for current user across ERPNext and other installed apps.
- `ai_assistant.api.chat.get_chat_preferences` — Returns safe widget preferences such as answer modes and text blocks.

## Usage

Once installed and configured, a **floating chat button** (🤖) appears in the bottom-right corner of every ERPNext page.

- Click it to open the chat panel.
- Pick **1**, **2**, or **3** to choose the answer style you want.
- Type your question in normal business language and press **Enter** (or click **Send**).
- The assistant answers using live data from your ERPNext instance, respecting the logged-in user's permissions.

## Architecture

```
ai_assistant/
├── setup.py
├── requirements.txt           # openai, requests
├── ai_assistant/
│   ├── hooks.py               # Frappe app hooks (CSS/JS injection)
│   ├── modules.txt
│   ├── api/
│   │   └── chat.py            # send_message, get_chat_history, get_accessible_doctypes, get_chat_preferences
│   ├── ai_assistant/
│   │   └── doctype/
│   │       ├── ai_chat_settings/   # Single DocType for configuration
│   │       ├── ai_agent/          # Agent-level behavior + role access
│   │       ├── ai_role/           # Child table for allowed widget roles
│   │       ├── ai_agent_data_source/ # Child table for per-agent data source allowlist
│   │       └── ai_chat_log/        # Log of every chat interaction
│   ├── public/
│   │   ├── css/ai_chat.css    # Chat widget styles
│   │   └── js/ai_chat.js      # Chat widget (floating button + panel)
│   ├── setup/
│   │   └── install.py         # AI Admin role provisioning hooks
│   └── tests/
│       └── test_chat_api.py   # Unit tests (run without a Frappe env)
```

## Running Tests

Tests use Python's `unittest` with a minimal Frappe stub — no Frappe/ERPNext installation required:

```bash
python -m unittest ai_assistant.tests.test_chat_api -v
```

## License

MIT