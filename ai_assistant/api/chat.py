# Copyright (c) 2024, Connect4systems and contributors
# For license information, please see license.txt
"""
AI Chat API
===========
Exposes a single whitelisted endpoint  ``ai_assistant.api.chat.send_message``
that the front-end widget calls.

Flow
----
1. Load AI Chat Settings (single DocType).
2. Build a *context string* for the current user:
   - Permitted DocTypes and their record counts.
   - Active Workflow definitions the user's documents may follow.
   - Explicit User Permissions assigned to the user.
3. Optionally run a lightweight DB search when the question contains a
   known DocType name.
4. Send the assembled prompt to the configured AI provider.
5. Persist the interaction as an AI Chat Log entry.
6. Return the assistant reply.
"""

from __future__ import annotations

from ai_assistant.ai_agent_core import AIAgentCore

import base64
import hashlib
import io
import json
import re
import time
import traceback
import uuid

import frappe
from frappe import _

from ai_assistant.assistant_config import (
    AI_ADMIN_ROLE,
    ANSWER_MODES,
    ANSWER_MODE_ALIASES,
    ANSWER_MODE_PROMPTS,
    DEFAULT_AGENT_INSTRUCTION_BLOCK,
    DEFAULT_AGENT_INSTRUCTION_TEXT,
    DEFAULT_ANSWER_MODE,
    DEFAULT_ANSWER_MODE_TEXT_BLOCK,
    DOCTYPE_LANGUAGE_ALIASES,
)


MAX_QUESTION_CHARS = 4000
MAX_HISTORY_TURNS = 10
MAX_HISTORY_MESSAGES = MAX_HISTORY_TURNS * 2
MAX_HISTORY_MESSAGE_CHARS = 2000
MAX_SESSION_ID_CHARS = 120
MAX_TRANSCRIBE_AUDIO_BYTES = 5 * 1024 * 1024
MAX_TRANSCRIBE_AUDIO_BASE64_CHARS = 8 * 1024 * 1024
DEFAULT_REQUESTS_PER_MINUTE = 20
DEFAULT_MAX_DB_ROWS = 20
DEFAULT_MAX_TOOL_DOCTYPES = 3
DEFAULT_MAX_MODULES_IN_CONTEXT = 12
DEFAULT_MAX_AGGREGATE_SCAN_ROWS = 2000
MAX_AGGREGATE_SCAN_ROWS_CAP = 20000
DOCTYPE_SCOPE_CACHE_SECONDS = 120
MAX_POLICY_FIELD_COUNT = 20
DEFAULT_TRANSCRIPTION_MODEL = "whisper-1"
MAX_LEARNED_EXPRESSIONS_PER_USER = 150
MAX_LEARNED_PHRASES_PER_QUESTION = 12
MAX_LEARNED_EXPRESSION_CHARS = 42
MIN_LEARNED_EXPRESSION_CHARS = 2
LEARNED_EXPRESSION_STRONG_SCORE = 6
MAX_NAVIGATION_NAME_CHARS = 120


def _coerce_int(value, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    """Convert *value* to int safely, clamped to optional min/max."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default

    if minimum is not None and parsed < minimum:
        parsed = minimum
    if maximum is not None and parsed > maximum:
        parsed = maximum
    return parsed


def _normalize_answer_mode(answer_mode: str | None) -> str:
    """Normalize answer mode input to one of the supported internal keys."""
    key = str(answer_mode or "").strip().lower()
    return ANSWER_MODE_ALIASES.get(key, DEFAULT_ANSWER_MODE)


def _get_setting_text(settings, fieldname: str, default: str = "") -> str:
    """Safely read text settings fields and fall back when blank."""
    value = getattr(settings, fieldname, None)
    if isinstance(value, str):
        value = value.strip()
        return value or default
    return default


def _get_agent_instruction_block(settings, agent=None) -> str:
    """Return agent instruction text, preferring the active agent override."""
    if agent is not None:
        agent_text = _get_setting_text(agent, "agent_instruction_block", "")
        if agent_text:
            return agent_text
    return _get_setting_text(settings, "agent_instruction_block", DEFAULT_AGENT_INSTRUCTION_BLOCK)


def _get_answer_mode_text_block(settings, agent=None) -> str:
    """Return answer mode help text, preferring the active agent override."""
    if agent is not None:
        agent_text = _get_setting_text(agent, "answer_mode_text_block", "")
        if agent_text:
            return agent_text
    return _get_setting_text(settings, "answer_mode_text_block", DEFAULT_ANSWER_MODE_TEXT_BLOCK)


def _user_has_role(user: str, role_name: str) -> bool:
    """Return True when the given role is assigned to the user."""
    try:
        return role_name in set(frappe.get_roles(user) or [])
    except Exception:
        return False


def _user_has_ai_admin_role(user: str) -> bool:
    """Return True when the user has the app-managed AI Admin role."""
    return _user_has_role(user, AI_ADMIN_ROLE)


def _row_value(row, key: str, default=None):
    """Read row values from dict-like or object-like records."""
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _extract_agent_roles(agent_doc) -> set[str]:
    """Return configured AI Role values from an AI Agent document."""
    roles: set[str] = set()
    for row in getattr(agent_doc, "allowed_roles", []) or []:
        role_name = str(_row_value(row, "role", "") or "").strip()
        if role_name:
            roles.add(role_name)
    return roles


def _agent_allows_user(agent_doc, role_set: set[str]) -> bool:
    """Return True when a user role-set satisfies agent role restrictions."""
    allowed_roles = _extract_agent_roles(agent_doc)
    if not allowed_roles:
        return True
    return bool(allowed_roles & role_set)


def _get_enabled_agents() -> list[dict]:
    """Return enabled agents ordered by default-first and most recently modified."""
    try:
        return frappe.get_all(
            "AI Agent",
            filters={"enabled": 1},
            fields=["name", "is_default", "modified"],
            order_by="is_default desc, modified desc",
        )
    except Exception:
        return []


def _get_agent_doc(name: str | None):
    """Load an AI Agent document by name, returning None when unavailable."""
    if not name:
        return None
    try:
        return frappe.get_doc("AI Agent", name)
    except Exception:
        return None


def _resolve_user_agent(user: str, settings=None):
    """Return the active AI Agent document that matches the user's AI roles."""
    role_set = set(frappe.get_roles(user) or [])

    preferred_name = _get_setting_text(settings, "default_agent", "") if settings is not None else ""

    if preferred_name:
        preferred = _get_agent_doc(preferred_name)
        if preferred and int(_row_value(preferred, "enabled", 0) or 0) and _agent_allows_user(preferred, role_set):
            return preferred

    for row in _get_enabled_agents():
        doc = _get_agent_doc(_row_value(row, "name"))
        if not doc:
            continue
        if _agent_allows_user(doc, role_set):
            return doc
    return None


def _user_can_access_widget(user: str, settings=None, agent_doc=None) -> bool:
    """Return whether the user can see/use the chat widget under AI Agent rules."""
    if _user_has_ai_admin_role(user):
        return True

    if agent_doc is None:
        agent_doc = _resolve_user_agent(user, settings=settings)

    if agent_doc is not None:
        return bool(_coerce_int(_row_value(agent_doc, "allow_widget_access", 1), default=1, minimum=0, maximum=1))

    # If no enabled agents exist, keep backward-compatible behavior and allow widget.
    return len(_get_enabled_agents()) == 0


def _resolve_agent_or_setting(agent_doc, settings, fieldname: str, default=None):
    """Resolve a setting value from agent first, then global settings, then default."""
    if agent_doc is not None:
        value = getattr(agent_doc, fieldname, None)
        if value not in (None, ""):
            return value

    if settings is not None:
        value = getattr(settings, fieldname, None)
        if value not in (None, ""):
            return value

    return default


def _sanitize_session_id(session_id: str | None) -> str:
    """Return a safe session id, generating one when missing/invalid."""
    if not session_id:
        return str(uuid.uuid4())

    clean = re.sub(r"[^A-Za-z0-9_.:-]", "", str(session_id)).strip()
    if not clean:
        return str(uuid.uuid4())
    return clean[:MAX_SESSION_ID_CHARS]


def _sanitize_history(history: str | None) -> list[dict]:
    """Allow only user/assistant turns with bounded content size."""
    if not history:
        return []

    try:
        raw = json.loads(history)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(raw, list):
        return []

    sanitized: list[dict] = []
    for turn in raw[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(turn, dict):
            continue

        role = str(turn.get("role") or "").strip().lower()
        content = turn.get("content")

        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str):
            continue

        content = content.strip()
        if not content:
            continue

        sanitized.append({"role": role, "content": content[:MAX_HISTORY_MESSAGE_CHARS]})

    return sanitized


def _cache_get(cache, key: str):
    get_value = getattr(cache, "get_value", None)
    if callable(get_value):
        return get_value(key)

    get_fn = getattr(cache, "get", None)
    if callable(get_fn):
        return get_fn(key)

    return None


def _cache_set(cache, key: str, value, ttl_seconds: int) -> None:
    set_value = getattr(cache, "set_value", None)
    if callable(set_value):
        try:
            set_value(key, value, expires_in_sec=ttl_seconds)
            return
        except TypeError:
            set_value(key, value)
            expire_fn = getattr(cache, "expire", None)
            if callable(expire_fn):
                expire_fn(key, ttl_seconds)
            return

    set_fn = getattr(cache, "set", None)
    if callable(set_fn):
        try:
            set_fn(key, value, ex=ttl_seconds)
            return
        except TypeError:
            try:
                set_fn(key, value, ttl_seconds)
                return
            except TypeError:
                set_fn(key, value)
                expire_fn = getattr(cache, "expire", None)
                if callable(expire_fn):
                    expire_fn(key, ttl_seconds)


def _enforce_user_rate_limit(user: str, limit_per_minute: int) -> None:
    """Best-effort per-user request throttling using Frappe cache."""
    if limit_per_minute <= 0:
        return

    cache_factory = getattr(frappe, "cache", None)
    if not callable(cache_factory):
        return

    cache = cache_factory()
    minute_bucket = int(time.time() // 60)
    key = f"ai_assistant:rate:{user}:{minute_bucket}"

    try:
        count = None
        incr_fn = getattr(cache, "incr", None)
        if callable(incr_fn):
            count = int(incr_fn(key))
            expire_fn = getattr(cache, "expire", None)
            if callable(expire_fn):
                expire_fn(key, 61)
        else:
            current = _coerce_int(_cache_get(cache, key), default=0, minimum=0)
            count = current + 1
            _cache_set(cache, key, count, ttl_seconds=61)
    except Exception:
        frappe.log_error(title="AI Chat Rate Limit Error", message=traceback.format_exc())
        return

    if count > limit_per_minute:
        frappe.throw(_("Too many AI chat requests. Please wait a minute and try again."))


def _require_authenticated_user() -> str:
    """Return current user or raise when request is unauthenticated."""
    session = getattr(frappe, "session", None)
    user = getattr(session, "user", None) if session else None
    if not user or user == "Guest":
        frappe.throw(_("You must be logged in to use AI Assistant."), frappe.PermissionError)
    return user


def _has_read_permission(doctype: str, user: str) -> bool:
    """Return True when *user* can read *doctype* across Frappe API variants."""
    return _has_doctype_permission(doctype, "read", user)


def _has_doctype_permission(doctype: str, ptype: str, user: str) -> bool:
    """Return True when *user* has *ptype* permission on *doctype*."""
    try:
        return bool(frappe.has_permission(doctype, ptype, user=user, raise_exception=False))
    except TypeError:
        # Older/newer signatures may not support raise_exception keyword.
        try:
            return bool(frappe.has_permission(doctype=doctype, ptype=ptype, user=user, throw=False))
        except TypeError:
            try:
                return bool(frappe.has_permission(doctype, ptype, user=user))
            except Exception:
                return False
    except Exception:
        return False


def _sanitize_navigation_name(value: str | None, max_chars: int = MAX_NAVIGATION_NAME_CHARS) -> str:
    """Return a safe, compact route/report/dashboard name."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    clean = re.sub(r"[^A-Za-z0-9 _\-/\u0600-\u06FF]", "", raw)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return ""
    return clean[:max_chars]


def _extract_name_from_question(question: str, anchors: tuple[str, ...]) -> str:
    """Extract a report/dashboard name after an anchor keyword or quoted phrase."""
    text = str(question or "").strip()
    if not text:
        return ""

    quoted = re.search(r"[\"'“”‘’]([^\"'“”‘’]{2,120})[\"'“”‘’]", text)
    if quoted:
        return _sanitize_navigation_name(quoted.group(1))

    lower = text.lower()
    for anchor in anchors:
        idx = lower.find(anchor)
        if idx < 0:
            continue

        tail = text[idx + len(anchor):]
        tail = re.sub(r"^[\s:=\-]+", "", tail)
        tail = re.sub(
            r"^(named|called|name|the|a|an|اسم|اسمه|باسم|بعنوان)\s+",
            "",
            tail,
            flags=re.IGNORECASE,
        )
        tail = re.split(r"[\?؟!\n\r]", tail, maxsplit=1)[0]

        candidate = _sanitize_navigation_name(tail)
        if len(candidate) >= 2:
            return candidate

    return ""


def _is_navigation_open_intent(question: str) -> bool:
    """Return True for explicit open/navigate intents."""
    q = str(question or "").lower()
    if not q:
        return False

    tokens = (
        "open",
        "go to",
        "navigate",
        "visit",
        "switch to",
        "افتح",
        "اذهب",
        "انتقل",
        "ادخل",
        "روح",
    )
    return any(token in q for token in tokens)


def _is_create_report_intent(question: str) -> bool:
    """Return True for explicit create/new report intents."""
    q = str(question or "").lower()
    if not q:
        return False

    create_tokens = (
        "create",
        "new",
        "build",
        "make",
        "انشئ",
        "أنشئ",
        "انشاء",
        "إنشاء",
        "اعمل",
    )
    report_tokens = ("report", "reports", "تقرير", "تقارير")
    return any(t in q for t in create_tokens) and any(t in q for t in report_tokens)


def _is_create_dashboard_intent(question: str) -> bool:
    """Return True for explicit create/new dashboard intents."""
    q = str(question or "").lower()
    if not q:
        return False

    create_tokens = (
        "create",
        "new",
        "build",
        "make",
        "انشئ",
        "أنشئ",
        "انشاء",
        "إنشاء",
        "اعمل",
    )
    dashboard_tokens = ("dashboard", "dashboards", "لوحة", "لوحه", "لوحات")
    return any(t in q for t in create_tokens) and any(t in q for t in dashboard_tokens)


def _build_navigation_actions(
    question: str,
    user: str,
    permitted_doctypes: list[str],
    language_hint: str | None = None,
) -> tuple[bool, str, list[dict]]:
    """Return deterministic navigation actions for open/report/dashboard/create-report requests."""
    q = str(question or "").strip()
    if not q:
        return False, "", []

    q_lower = q.lower()
    prefers_ar = _question_contains_arabic(q) or _normalize_language_hint(language_hint) == "ar"

    def _msg(ar_text: str, en_text: str) -> str:
        return ar_text if prefers_ar else en_text

    report_tokens = ("report", "reports", "query report", "تقرير", "تقارير")
    dashboard_tokens = ("dashboard", "dashboards", "لوحة", "لوحه", "لوحات")

    # 1) Create new dashboard intent
    if _is_create_dashboard_intent(q):
        if not _has_doctype_permission("Dashboard", "create", user):
            return (
                True,
                _msg(
                    "ليس لديك صلاحية إنشاء لوحة معلومات جديدة. تواصل مع المسؤول لمنحك صلاحية Dashboard.",
                    "You do not have permission to create dashboards. Ask your administrator for Dashboard create permission.",
                ),
                [],
            )

        dashboard_name = _extract_name_from_question(q, ("dashboard", "لوحة", "لوحه", "named", "called", "اسم", "بعنوان"))
        if not dashboard_name:
            dashboard_name = "AI Assistant Dashboard"

        action = {
            "action": "create_dashboard",
            "dashboard_name": dashboard_name,
        }
        return (
            True,
            _msg(
                f"سأفتح نموذج إنشاء لوحة معلومات جديدة باسم {dashboard_name}.",
                f"I will open a new dashboard form named {dashboard_name}.",
            ),
            [action],
        )

    # 2) Create new report intent
    if _is_create_report_intent(q):
        if not _has_doctype_permission("Report", "create", user):
            return (
                True,
                _msg(
                    "ليس لديك صلاحية إنشاء تقرير جديد. تواصل مع المسؤول لمنحك صلاحية Report.",
                    "You do not have permission to create new reports. Ask your administrator for Report create permission.",
                ),
                [],
            )

        matched = _detect_doctypes_in_text(q, permitted_doctypes, max_matches=1, user=user)
        target_dt = matched[0] if matched else ""
        if not target_dt:
            return (
                True,
                _msg(
                    "لإنشاء تقرير، اذكر اسم الـ DocType المطلوب مثل: أنشئ تقرير جديد لـ Sales Invoice.",
                    "To create a report, specify the target DocType, for example: create a new report for Sales Invoice.",
                ),
                [],
            )

        report_name = _extract_name_from_question(q, ("report", "تقرير", "named", "called", "اسم", "بعنوان"))
        if not report_name:
            report_name = f"{target_dt} Assistant Report"

        action = {
            "action": "create_report",
            "report_name": report_name,
            "ref_doctype": target_dt,
            "report_type": "Report Builder",
        }
        return (
            True,
            _msg(
                f"سأفتح نموذج إنشاء تقرير جديد لـ {target_dt} بالاسم {report_name}.",
                f"I will open a new report form for {target_dt} named {report_name}.",
            ),
            [action],
        )

    has_open_intent = _is_navigation_open_intent(q)
    if not has_open_intent:
        return False, "", []

    # 3) Open report intent
    if any(token in q_lower for token in report_tokens):
        report_name = _extract_name_from_question(q, report_tokens)
        if not report_name:
            return (
                True,
                _msg(
                    "اذكر اسم التقرير الذي تريد فتحه، مثل: افتح تقرير Sales Register.",
                    "Please specify the report name to open, for example: open report Sales Register.",
                ),
                [],
            )

        return (
            True,
            _msg(
                f"جاري فتح التقرير {report_name}.",
                f"Opening report {report_name}.",
            ),
            [{"action": "open_report", "report_name": report_name}],
        )

    # 4) Open dashboard intent
    if any(token in q_lower for token in dashboard_tokens):
        dashboard_name = _extract_name_from_question(q, dashboard_tokens)
        if not dashboard_name:
            return (
                True,
                _msg(
                    "اذكر اسم لوحة المعلومات التي تريد فتحها، مثل: افتح لوحة Accounts.",
                    "Please specify the dashboard name to open, for example: open dashboard Accounts.",
                ),
                [],
            )

        return (
            True,
            _msg(
                f"جاري فتح لوحة المعلومات {dashboard_name}.",
                f"Opening dashboard {dashboard_name}.",
            ),
            [{"action": "open_dashboard", "dashboard_name": dashboard_name}],
        )

    # 5) Open DocType intent
    # Keep navigation deterministic and avoid user-learned alias drift for open intents.
    matched_doctypes = _detect_doctypes_in_text(q, permitted_doctypes, max_matches=1, user=None)
    if matched_doctypes:
        dt = matched_doctypes[0]
        if not _has_read_permission(dt, user):
            return (
                True,
                _msg(
                    f"ليس لديك صلاحية لفتح {dt}.",
                    f"You do not have permission to open {dt}.",
                ),
                [],
            )

        return (
            True,
            _msg(
                f"جاري فتح {dt}.",
                f"Opening {dt}.",
            ),
            [{"action": "open_doctype", "doctype": dt}],
        )

    if "doctype" in q_lower or "مستند" in q_lower or "نموذج" in q_lower:
        return (
            True,
            _msg(
                "اذكر اسم الـ DocType المطلوب فتحه.",
                "Please specify the DocType name you want to open.",
            ),
            [],
        )

    return False, "", []


def _humanize_ai_error(exc: Exception) -> str:
    """Convert provider exceptions into short, actionable user-facing messages."""
    detail = (str(exc) or "").strip().lower()

    if "no module named" in detail and "openai" in detail:
        return _(
            "OpenAI SDK is not installed on the server. Ask your administrator to install the openai package and restart the bench."
        )
    if "has no attribute" in detail and "openai" in detail:
        return _(
            "The installed OpenAI SDK version is incompatible. Ask your administrator to upgrade openai and restart the bench."
        )
    if "invalid_api_key" in detail or "incorrect api key" in detail:
        return _("The OpenAI API key appears invalid. Please update the API key in AI Chat Settings.")
    if "insufficient_quota" in detail or "quota" in detail:
        return _("OpenAI quota is exceeded. Please add billing/credits and try again.")
    if "model" in detail and ("not found" in detail or "does not exist" in detail):
        return _("The selected model is unavailable for this API key. Try gpt-4o-mini or gpt-4o.")

    return _("Sorry, I encountered an error while processing your request. Please try again later.")


def _humanize_transcription_error(exc: Exception) -> str:
    """Convert transcription/provider errors to actionable user-facing messages."""
    detail = (str(exc) or "").strip().lower()

    if "invalid_api_key" in detail or "incorrect api key" in detail:
        return _("The API key appears invalid for speech transcription. Please update it in AI Chat Settings.")
    if "insufficient_quota" in detail or "quota" in detail or "429" in detail:
        return _("Transcription quota is exceeded. Please add credits/billing and try again.")
    if "audio" in detail and ("too large" in detail or "max" in detail or "size" in detail):
        return _("Audio clip is too large. Please record a shorter clip and try again.")
    if "unsupported" in detail and "format" in detail:
        return _("The recorded audio format is not supported by the transcription provider.")
    if "connection" in detail or "network" in detail or "timeout" in detail:
        return _("Could not reach the transcription provider. Please check network and try again.")

    return _("Could not transcribe audio right now. Please try again.")


def _normalize_transcription_language(language: str | None) -> str | None:
    """Normalize browser language tags (e.g. ar-SA) to provider-friendly ISO codes."""
    value = str(language or "").strip().lower()
    if not value:
        return None

    if "-" in value:
        value = value.split("-", 1)[0]

    if not re.fullmatch(r"[a-z]{2,3}", value):
        return None

    return value


def _decode_audio_base64(audio_base64: str) -> bytes:
    """Decode base64 audio payload safely and validate shape."""
    payload = str(audio_base64 or "").strip()
    if not payload:
        return b""

    # Accept full data URLs and plain base64 payloads.
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]

    payload = re.sub(r"\s+", "", payload)
    if not payload:
        return b""

    try:
        return base64.b64decode(payload, validate=True)
    except Exception:
        frappe.throw(_("Invalid audio payload. Please try recording again."))


def _audio_extension_from_mime_type(mime_type: str | None) -> str:
    """Map mime-type to a file extension suitable for transcription APIs."""
    mime = str(mime_type or "").strip().lower()
    if "ogg" in mime:
        return "ogg"
    if "wav" in mime:
        return "wav"
    if "mpeg" in mime or "mp3" in mime:
        return "mp3"
    if "mp4" in mime or "m4a" in mime:
        return "m4a"
    return "webm"


def _extract_transcription_text(response) -> str:
    """Extract transcribed text across modern/legacy SDK response shapes."""
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text.strip()

    if isinstance(response, str):
        return response.strip()

    if isinstance(response, dict):
        value = response.get("text")
        if isinstance(value, str):
            return value.strip()

    return ""


def _get_azure_openai_credentials(settings) -> tuple[str, str]:
    """Return (endpoint, api_key) from settings/env for Azure OpenAI."""
    raw_key = settings.get_password("api_key") or ""
    if "||" in raw_key:
        endpoint, api_key = raw_key.split("||", 1)
    else:
        import os

        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        api_key = raw_key

    return endpoint.strip(), api_key.strip()


def _build_context_audit_summary(
    user: str,
    include_permissions: bool,
    include_workflows: bool,
    mentioned_doctypes: list[str],
    permitted_doctype_count: int,
    context_string: str,
    policy_enforced: bool = False,
    answer_mode: str | None = None,
) -> str:
    """Store a safe context summary in logs instead of raw prompt data."""
    sections = ["doctype_list"]
    if include_permissions:
        sections.append("permissions")
    if include_workflows:
        sections.append("workflows")
    if policy_enforced:
        sections.append("policy_enforced")
    if answer_mode:
        sections.append(f"answer_mode:{answer_mode}")
    for dt in mentioned_doctypes[:5]:
        sections.append(f"doctype:{dt}")

    context_hash = hashlib.sha256(context_string.encode("utf-8")).hexdigest()[:16] if context_string else ""

    return (
        f"user={user}; sections={','.join(sections)}; "
        f"permitted_doctypes={permitted_doctype_count}; context_sha256={context_hash}"
    )


def _parse_multivalue_text(raw: str | None) -> list[str]:
    """Parse comma/newline/semicolon-separated values into trimmed tokens."""
    if not raw:
        return []

    values = re.split(r"[,;\n\r]+", str(raw))
    return [value.strip() for value in values if value and value.strip()]


def _normalize_allowed_fields(raw_fields: str | None) -> list[str]:
    """Normalize allowlisted field names and keep only safe identifiers."""
    normalized: list[str] = []
    seen: set[str] = set()
    for field in _parse_multivalue_text(raw_fields):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field):
            continue
        key = field.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(field)
        if len(normalized) >= MAX_POLICY_FIELD_COUNT:
            break
    return normalized


def _load_user_policy_map(user: str, agent_doc=None) -> dict[str, dict]:
    """Load data-source policies from AI Agent only (legacy policy disabled)."""
    role_set = set(frappe.get_roles(user) or [])

    def _append_policy(policy_map: dict[str, dict], row) -> None:
        dt = str(_row_value(row, "doctype_name", "") or "").strip()
        if not dt:
            return

        allowed_roles = set(_parse_multivalue_text(_row_value(row, "allowed_roles")))
        if allowed_roles and not (allowed_roles & role_set):
            return

        max_rows = None
        if _row_value(row, "max_rows"):
            max_rows = _coerce_int(_row_value(row, "max_rows"), default=DEFAULT_MAX_DB_ROWS, minimum=1, maximum=100)

        policy_map[dt] = {
            "allowed_fields": _normalize_allowed_fields(_row_value(row, "allowed_fields")),
            "max_rows": max_rows,
            "allow_in_context": bool(_row_value(row, "allow_in_context", 1)),
        }

    if agent_doc is not None:
        policy_map: dict[str, dict] = {}
        for row in getattr(agent_doc, "data_sources", []) or []:
            _append_policy(policy_map, row)
        return policy_map

    return {}


def _get_user_permitted_doctype_index(user: str) -> list[dict]:
    """Return read-permitted DocTypes with their modules for *user*."""
    cache_factory = getattr(frappe, "cache", None)
    cache = cache_factory() if callable(cache_factory) else None
    cache_key = f"ai_assistant:scope:{user}"

    if cache is not None:
        try:
            cached = _cache_get(cache, cache_key)
            if isinstance(cached, list):
                return cached
        except Exception:
            pass

    index: list[dict] = []
    try:
        doctypes = frappe.get_all("DocType", fields=["name", "module"], filters={"issingle": 0})
        for meta in doctypes:
            dt = meta.get("name")
            if not dt:
                continue
            if _has_read_permission(dt, user):
                index.append({
                    "doctype": dt,
                    "module": meta.get("module") or "Unknown",
                })
    except Exception:
        return []

    if cache is not None:
        try:
            _cache_set(cache, cache_key, index, ttl_seconds=DOCTYPE_SCOPE_CACHE_SECONDS)
        except Exception:
            pass

    return index


def _build_module_scope_context(permitted_index: list[dict], max_modules: int = DEFAULT_MAX_MODULES_IN_CONTEXT) -> str:
    """Summarize user-readable DocTypes grouped by module/app."""
    if not permitted_index:
        return "Accessible modules/apps: none."

    module_counts: dict[str, int] = {}
    for item in permitted_index:
        module = item.get("module") or "Unknown"
        module_counts[module] = module_counts.get(module, 0) + 1

    ordered = sorted(module_counts.items(), key=lambda x: (-x[1], x[0]))
    shown = [f"{name} ({count})" for name, count in ordered[:max_modules]]
    suffix = " ..." if len(ordered) > max_modules else ""
    return "Accessible modules/apps by readable DocTypes: " + ", ".join(shown) + suffix


def _build_installed_apps_context(max_apps: int = 20) -> str:
    """Return a compact list of installed site apps to improve app-map guidance."""
    try:
        apps = list(getattr(frappe, "get_installed_apps", lambda: [])() or [])
    except Exception:
        return "Installed apps on this site: unavailable."

    if not apps:
        return "Installed apps on this site: none."

    shown = [str(app) for app in apps[:max_apps] if str(app).strip()]
    suffix = " ..." if len(apps) > max_apps else ""
    return "Installed apps on this site: " + ", ".join(shown) + suffix


def _normalize_expression_text(value: str | None) -> str:
    """Normalize free-text expressions for stable alias learning/detection."""
    text = str(value or "").strip().lower()
    if not text:
        return ""

    text = re.sub(r"[\t\r\n]+", " ", text)
    text = re.sub(r"[^\w\u0600-\u06FF\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _expression_language_code(expression: str) -> str:
    """Return lightweight language code for a learned expression."""
    return "ar" if re.search(r"[\u0600-\u06FF]", expression or "") else "en"


def _load_user_expression_aliases(user: str) -> dict[str, dict]:
    """Load user-learned phrase -> doctype mappings from persistent DB table."""
    if not user:
        return {}

    if not frappe.db.exists("DocType", "AI Learned Expression"):
        return {}

    try:
        rows = frappe.get_all(
            "AI Learned Expression",
            filters={"user": user, "enabled": 1},
            fields=["expression", "normalized_expression", "doctype_name", "score", "updated_epoch"],
            order_by="score desc, updated_epoch desc, modified desc",
            limit_page_length=MAX_LEARNED_EXPRESSIONS_PER_USER,
        )
    except Exception:
        return {}

    cleaned: dict[str, dict] = {}
    for row in rows or []:
        normalized_expression = _normalize_expression_text(
            row.get("normalized_expression") or row.get("expression")
        )
        if not normalized_expression:
            continue

        doctype_name = str(row.get("doctype_name") or "").strip()
        score = _coerce_int(row.get("score"), default=1, minimum=1, maximum=5000)
        updated = _coerce_int(row.get("updated_epoch"), default=0, minimum=0)

        if not doctype_name:
            continue

        cleaned[normalized_expression] = {
            "doctype": doctype_name,
            "score": score,
            "updated": updated,
        }

    return cleaned


def _extract_learnable_expressions(question: str) -> list[str]:
    """Extract phrase candidates from user text for adaptive alias learning."""
    normalized = _normalize_expression_text(question)
    if not normalized:
        return []

    stopwords = {
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "for",
        "to",
        "with",
        "and",
        "or",
        "is",
        "are",
        "what",
        "how",
        "when",
        "which",
        "please",
        "show",
        "get",
        "list",
        "open",
        "create",
        "new",
        "report",
        "dashboard",
        "doctype",
        "page",
        "من",
        "في",
        "على",
        "عن",
        "الى",
        "إلى",
        "مع",
        "او",
        "أو",
        "هل",
        "ما",
        "ماذا",
        "كيف",
        "كم",
        "لو",
        "افتح",
        "اذهب",
        "انتقل",
        "انشئ",
        "أنشئ",
        "تقرير",
        "لوحة",
        "مستند",
        "ارجو",
        "أرجو",
        "لو سمحت",
        "من فضلك",
    }

    words = [w for w in normalized.split(" ") if w]
    if not words:
        return []

    expressions: list[str] = []
    seen: set[str] = set()

    def _add(phrase: str) -> None:
        key = _normalize_expression_text(phrase)
        if not key:
            return
        if key in seen:
            return
        if len(key) < MIN_LEARNED_EXPRESSION_CHARS or len(key) > MAX_LEARNED_EXPRESSION_CHARS:
            return

        tokens = [token for token in key.split(" ") if token]
        if not tokens:
            return

        if len(tokens) == 1 and tokens[0] in stopwords:
            return
        if all(token in stopwords for token in tokens):
            return

        if len(tokens) > 1 and len(tokens) > 6:
            return

        seen.add(key)
        expressions.append(key)

    if len(words) <= 8:
        _add(" ".join(words))

    for n in (1, 2, 3):
        if len(words) < n:
            continue
        for idx in range(len(words) - n + 1):
            _add(" ".join(words[idx : idx + n]))
            if len(expressions) >= MAX_LEARNED_PHRASES_PER_QUESTION:
                return expressions

    return expressions


def _learn_user_expression_aliases(user: str, question: str, executed_doctypes: list[str]) -> None:
    """Persist learned user expressions for stable DocType detection across restarts."""
    if not user:
        return
    if len(executed_doctypes) != 1:
        return
    if not frappe.db.exists("DocType", "AI Learned Expression"):
        return

    target_doctype = str(executed_doctypes[0] or "").strip()
    if not target_doctype:
        return

    phrases = _extract_learnable_expressions(question)
    if not phrases:
        return

    now_ts = int(time.time())
    changed = False

    try:
        existing_rows = frappe.get_all(
            "AI Learned Expression",
            filters={"user": user, "normalized_expression": ["in", phrases]},
            fields=["name", "normalized_expression", "doctype_name", "score"],
            limit_page_length=MAX_LEARNED_EXPRESSIONS_PER_USER,
        )
    except Exception:
        return
    existing_by_phrase = {
        _normalize_expression_text(row.get("normalized_expression")): row
        for row in (existing_rows or [])
    }

    for phrase in phrases:
        existing = existing_by_phrase.get(phrase)
        existing_doctype = str((existing or {}).get("doctype_name") or "").strip()
        existing_score = _coerce_int((existing or {}).get("score"), default=0, minimum=0, maximum=5000)

        if existing_doctype and existing_doctype != target_doctype and existing_score >= LEARNED_EXPRESSION_STRONG_SCORE:
            continue

        if existing and existing.get("name"):
            try:
                doc = frappe.get_doc("AI Learned Expression", existing["name"])
                doc.expression = phrase
                doc.normalized_expression = phrase
                doc.doctype_name = target_doctype
                doc.score = min(existing_score + 1, 5000)
                doc.language = _expression_language_code(phrase)
                doc.last_used_on = frappe.utils.now_datetime()
                doc.updated_epoch = now_ts
                doc.enabled = 1
                doc.save(ignore_permissions=True)
                changed = True
            except Exception:
                continue
        else:
            try:
                frappe.get_doc(
                    {
                        "doctype": "AI Learned Expression",
                        "user": user,
                        "expression": phrase,
                        "normalized_expression": phrase,
                        "doctype_name": target_doctype,
                        "score": 1,
                        "language": _expression_language_code(phrase),
                        "last_used_on": frappe.utils.now_datetime(),
                        "updated_epoch": now_ts,
                        "enabled": 1,
                    }
                ).insert(ignore_permissions=True)
                changed = True
            except Exception:
                continue

    if changed:
        try:
            frappe.db.commit()
        except Exception:
            pass


def _get_learned_aliases_by_doctype(user: str) -> dict[str, list[str]]:
    """Return learned expression aliases grouped by target DocType for this user."""
    aliases = _load_user_expression_aliases(user)
    grouped: dict[str, list[tuple[int, int, str]]] = {}

    for expression, data in aliases.items():
        doctype_name = str(data.get("doctype") or "").strip()
        if not doctype_name:
            continue

        score = _coerce_int(data.get("score"), default=1, minimum=1, maximum=5000)
        updated = _coerce_int(data.get("updated"), default=0, minimum=0)
        grouped.setdefault(doctype_name, []).append((score, updated, expression))

    return {
        doctype_name: [entry[2] for entry in sorted(values, key=lambda item: (-item[0], -item[1], -len(item[2])))]
        for doctype_name, values in grouped.items()
    }


def _detect_doctypes_in_text(
    question: str,
    doctype_names: list[str],
    max_matches: int,
    user: str | None = None,
) -> list[str]:
    """Detect DocTypes from exact names and configured human-language aliases."""
    if not question or not doctype_names or max_matches <= 0:
        return []

    question_lower = question.lower()
    scored: list[tuple[int, int, int, str]] = []
    learned_aliases_by_doctype = _get_learned_aliases_by_doctype(user) if user else {}
    learned_generic_blocklist = {
        "open",
        "show",
        "list",
        "report",
        "dashboard",
        "doctype",
        "page",
        "افتح",
        "اذهب",
        "انتقل",
        "تقرير",
        "لوحة",
        "مستند",
    }

    for dt in doctype_names:
        dt_name = (dt or "").strip()
        if len(dt_name) < 3:
            continue

        candidates = [(dt_name, -2, "exact")]
        candidates.extend((alias, 0, "configured") for alias in DOCTYPE_LANGUAGE_ALIASES.get(dt_name, ()))
        candidates.extend((alias, 2, "learned") for alias in learned_aliases_by_doctype.get(dt_name, ()))

        best_match: tuple[int, int, int, str] | None = None
        for alias, alias_rank, alias_source in candidates:
            alias_text = str(alias or "").strip().lower()
            if len(alias_text) < 2:
                continue
            if alias_source == "learned":
                if alias_text in learned_generic_blocklist:
                    continue
                if len(alias_text) < 4:
                    continue

            pattern = r"(?<!\w)" + re.escape(alias_text) + r"(?!\w)"
            match = re.search(pattern, question_lower, re.UNICODE)
            if not match:
                continue

            candidate = (alias_rank, match.start(), -len(alias_text), dt_name)
            if best_match is None or candidate < best_match:
                best_match = candidate

        if best_match:
            scored.append(best_match)

    scored.sort()
    unique: list[str] = []
    seen: set[str] = set()
    for _, __, ___, dt in scored:
        key = dt.lower()
        if key in seen:
            continue
        unique.append(dt)
        seen.add(key)
        if len(unique) >= max_matches:
            break
    return unique


def _question_requests_multi_doctypes(question: str) -> bool:
    """Return True when user explicitly asks to compare/combine multiple DocTypes."""
    q = str(question or "").lower()
    if not q:
        return False

    indicators = (
        "compare",
        "comparison",
        "versus",
        " vs ",
        "cross",
        "across",
        "between",
        "together",
        "both",
        "combined",
        "multi doctype",
        "مقارنة",
        "قارن",
        "مقابل",
        "بين",
        "معا",
        "معًا",
        "مع بعض",
        "مجتمعة",
        "عدة مستندات",
    )
    return any(token in q for token in indicators)


def _question_lists_multiple_targets(question: str) -> bool:
    """Return True when a question explicitly lists multiple entities via connectors."""
    q = str(question or "").lower()
    if not q:
        return False

    broad_multi_tokens = (
        "both",
        "all of",
        "all three",
        "all four",
        "all together",
        "كلها",
        "جميعها",
        "كلاهما",
        "معا",
        "معًا",
    )
    if any(token in q for token in broad_multi_tokens):
        return True

    connector_tokens = (
        " and ",
        " & ",
        " plus ",
        " with ",
        ",",
        "،",
        " و ",
    )
    return any(token in q for token in connector_tokens)


def _question_requests_count(question: str) -> bool:
    """Return True when the user explicitly asks for count/number of records."""
    q = str(question or "").lower()
    if not q:
        return False

    indicators = (
        "how many",
        "count",
        "number of",
        "total number",
        "record count",
        "كم",
        "كم عدد",
        "عدد",
        "إجمالي عدد",
        "اجمالي عدد",
        "عدد السجلات",
        "عدد العملاء",
        "عدد الموردين",
    )
    return any(token in q for token in indicators)


def _question_requests_exhaustive(question: str) -> bool:
    """Return True when user asks for all/full/complete information explicitly."""
    q = str(question or "").lower()
    if not q:
        return False

    indicators = (
        "all information",
        "all info",
        "all data",
        "all details",
        "full information",
        "full details",
        "complete information",
        "complete details",
        "everything",
        "entire",
        "جميع المعلومات",
        "كل المعلومات",
        "كل البيانات",
        "جميع البيانات",
        "كل التفاصيل",
        "جميع التفاصيل",
        "معلومات كاملة",
        "تفاصيل كاملة",
        "كامل",
        "بالكامل",
        "كل شي",
        "كل شيء",
    )
    return any(token in q for token in indicators)


def _build_query_broker_context(
    question: str,
    user: str,
    permitted_doctypes: list[str],
    max_db_rows: int,
    max_tool_doctypes: int,
    policy_map: dict[str, dict] | None = None,
    require_policy: bool = False,
) -> tuple[str, list[str]]:
    """Run deterministic, permission-checked DocType reads based on question mentions."""
    policy_map = policy_map or {}

    matched_doctypes = _detect_doctypes_in_text(
        question,
        permitted_doctypes,
        max_matches=max_tool_doctypes,
        user=user,
    )
    if not matched_doctypes:
        return (
            "Query Broker: no confident DocType match detected in the question. "
            "Use the available context and answer conservatively without inventing data.",
            [],
        )

    exhaustive_mode = _question_requests_exhaustive(question)
    explicit_multi = _question_requests_multi_doctypes(question) or _question_lists_multiple_targets(question)

    focused_single = False
    if len(matched_doctypes) > 1 and not explicit_multi and not exhaustive_mode:
        matched_doctypes = matched_doctypes[:1]
        focused_single = True

    executed_doctypes: list[str] = []
    sections = [
        "Query Broker: executed permission-checked reads for DocTypes: " + ", ".join(matched_doctypes)
    ]

    if focused_single:
        sections.append("Query Broker Focus: narrowed to the most relevant single DocType for a more specific answer.")
    elif exhaustive_mode:
        sections.append("Query Broker Fetch Mode: exhaustive request detected; expanded multi-DocType context was kept.")

    for dt in matched_doctypes:
        policy = policy_map.get(dt)

        if require_policy and not policy:
            continue
        if policy and not policy.get("allow_in_context", True):
            continue

        rows_for_dt = max_db_rows
        if policy and policy.get("max_rows"):
            rows_for_dt = min(max_db_rows, _coerce_int(policy.get("max_rows"), default=max_db_rows, minimum=1, maximum=100))

        allowed_fields = policy.get("allowed_fields") if policy else None
        sections.append(
            _build_doctype_context(dt, user, max_rows=rows_for_dt, allowed_fields=allowed_fields)
        )
        executed_doctypes.append(dt)

    if not executed_doctypes:
        if require_policy:
            return (
                "Query Broker: policy enforcement is enabled and no matching allowlisted DocType was available for this question.",
                [],
            )
        return (
            "Query Broker: no allowed DocType context could be produced for this question.",
            [],
        )

    try:
        _learn_user_expression_aliases(user, question, executed_doctypes)
    except Exception:
        # Learning is best-effort and must never interrupt replies.
        pass

    return "\n\n".join(sections), executed_doctypes


def _extract_year_window_from_question(question: str) -> tuple[str, str, str] | None:
    """Extract year-like period from question and return (label, start_date, end_date)."""
    q = (question or "").lower()

    try:
        today = frappe.utils.getdate()
        if "last year" in q or "العام الماضي" in q or "السنة الماضية" in q:
            year = int(today.year) - 1
            return ("last year", f"{year}-01-01", f"{year}-12-31")
        if (
            "this year" in q
            or "current year" in q
            or "هذا العام" in q
            or "هذه السنة" in q
            or "هذة السنة" in q
            or "العام الحالي" in q
            or "العام الحالى" in q
            or "السنة الحالية" in q
        ):
            year = int(today.year)
            return ("this year", f"{year}-01-01", str(today))
    except Exception:
        pass

    year_match = re.search(r"\b(20\d{2})\b", q)
    if not year_match:
        return None

    year = int(year_match.group(1))
    return (str(year), f"{year}-01-01", f"{year}-12-31")


def _extract_month_window_from_question(question: str) -> tuple[str, str, str] | None:
    """Extract month-like period from question and return (label, start_date, end_date)."""
    q = (question or "").lower()

    month_tokens = (
        "this month",
        "current month",
        "monthly sales",
        "sales this month",
        "مبيعات الشهر",
        "هذا الشهر",
        "الشهر الحالي",
        "شهري",
    )
    if not any(token in q for token in month_tokens):
        return None

    try:
        today = frappe.utils.getdate()
        start_date = today.replace(day=1)
        return ("this month", str(start_date), str(today))
    except Exception:
        return None


def _looks_like_sales_total_question(question: str) -> bool:
    """Detect common phrasings that ask for total sales figures."""
    q = (question or "").lower()
    if not q:
        return False

    sales_tokens = (
        "sales",
        "selling",
        "sales invoice",
        "sales invoices",
        "sales order",
        "sales orders",
        "total sales",
        "sales total",
        "sales amount",
        "sales revenue",
        "yearly sales",
        "annual sales",
        "sales performance",
        "sales summary",
        "monthly sales",
        "sales this month",
        "sales month",
        "مبيعات",
        "المبيعات",
        "اجمالي المبيعات",
        "إجمالي المبيعات",
        "مجموع المبيعات",
        "ملخص المبيعات",
        "أداء المبيعات",
        "ايرادات المبيعات",
        "إيرادات المبيعات",
        "مبيعات العام",
        "مبيعات هذه السنة",
        "مبيعات هذا العام",
        "امر بيع",
        "أمر بيع",
        "اوامر بيع",
        "أوامر بيع",
        "فاتورة بيع",
        "فواتير بيع",
    )
    total_tokens = (
        "total",
        "sum",
        "how much",
        "amount",
        "value",
        "revenue",
        "yearly",
        "annual",
        "performance",
        "summary",
        "اجمالي",
        "إجمالي",
        "مجموع",
        "القيمة",
        "قيمة",
        "سنوي",
        "سنوية",
        "شهري",
        "شهرية",
        "this month",
        "current month",
        "هذا الشهر",
        "الشهر الحالي",
        "ملخص",
        "أداء",
    )

    return any(token in q for token in sales_tokens) and any(token in q for token in total_tokens)


def _resolve_sales_aggregate_targets(question: str) -> list[tuple[str, str, str]]:
    """Return aggregate targets as tuples of (DocType, date_field, count_label)."""
    q = (question or "").lower()

    invoice_tokens = (
        "sales invoice",
        "sales invoices",
        "invoice sales",
        "فاتورة بيع",
        "فواتير بيع",
    )
    order_tokens = (
        "sales order",
        "sales orders",
        "order sales",
        "امر بيع",
        "أمر بيع",
        "اوامر بيع",
        "أوامر بيع",
        "اوامر البيع",
        "أوامر البيع",
        "اوامر المبيعات",
        "أوامر المبيعات",
    )
    generic_sales_tokens = (
        "total sales",
        "sales total",
        "sales amount",
        "sales revenue",
        " اجمالي المبيعات",
        " إجمالي المبيعات",
        "المبيعات",
        "sales",
    )

    wants_invoice = any(token in q for token in invoice_tokens)
    wants_order = any(token in q for token in order_tokens)
    wants_generic_sales = any(token in q for token in generic_sales_tokens)

    targets: list[tuple[str, str, str]] = []
    if wants_invoice:
        targets.append(("Sales Invoice", "posting_date", "submitted_invoice_count"))
    if wants_order:
        targets.append(("Sales Order", "transaction_date", "submitted_order_count"))

    month_tokens = (
        "this month",
        "current month",
        "monthly sales",
        "sales this month",
        "sales month",
        "مبيعات الشهر",
        "هذا الشهر",
        "الشهر الحالي",
    )
    year_tokens = (
        "this year",
        "current year",
        "last year",
        "هذا العام",
        "هذه السنة",
        "هذة السنة",
        "العام الحالي",
        "العام الماضي",
        "السنة الماضية",
    )
    period_sales_tokens = month_tokens + year_tokens

    if not targets and wants_generic_sales:
        # Default generic "total sales" requests to both Sales Invoices and Sales Orders.
        targets.append(("Sales Invoice", "posting_date", "submitted_invoice_count"))
        targets.append(("Sales Order", "transaction_date", "submitted_order_count"))

    if not targets and any(token in q for token in period_sales_tokens):
        # Period-specific sales prompts should still resolve deterministic totals.
        targets.append(("Sales Invoice", "posting_date", "submitted_invoice_count"))
        targets.append(("Sales Order", "transaction_date", "submitted_order_count"))

    return targets


def _question_requests_growth(question: str) -> bool:
    """Return True when the question asks for growth/change over a prior period."""
    q = str(question or "").lower()
    if not q:
        return False

    tokens = (
        "growth",
        "increase",
        "decrease",
        "year over year",
        "yoy",
        "trend",
        "نمو",
        "معدل النمو",
        "زيادة",
        "انخفاض",
        "مقارنة",
        "تغير",
        "التغير",
    )
    return any(token in q for token in tokens)


def _looks_like_ledger_or_profit_question(question: str) -> bool:
    """Detect questions that likely need ledger/account/profit aggregates."""
    q = str(question or "").lower()
    if not q:
        return False

    ledger_tokens = (
        "ledger",
        "ledgers",
        "leadger",
        "leadgers",
        "general ledger",
        "gl",
        "gl entry",
        "gl entries",
        "account",
        "accounts",
        "chart of accounts",
        "trial balance",
        "دفتر الأستاذ",
        "دفتر الاستاذ",
        "قيود الأستاذ",
        "قيود الاستاذ",
        "حساب",
        "الحسابات",
        "شجرة الحسابات",
        "دليل الحسابات",
    )
    profit_tokens = (
        "profit",
        "gross profit",
        "net profit",
        "profitability",
        "income statement",
        "p&l",
        "pnl",
        "revenue",
        "expense",
        "expenses",
        "margin",
        "ربح",
        "الأرباح",
        "ارباح",
        "صافي الربح",
        "مجمل الربح",
        "الخسارة",
        "خسارة",
        "الايراد",
        "الإيراد",
        "المصروف",
        "المصروفات",
        "الارباح والخسائر",
        "الأرباح والخسائر",
    )
    return any(token in q for token in ledger_tokens) or any(token in q for token in profit_tokens)


def _shift_year_date(value: str, years: int) -> str:
    """Shift date string by *years* while handling leap-year day overflows."""
    try:
        dt = frappe.utils.getdate(value)
    except Exception:
        return str(value or "")

    target_year = int(dt.year) + int(years)
    day = int(dt.day)

    while day >= 28:
        try:
            return str(dt.replace(year=target_year, day=day))
        except ValueError:
            day -= 1

    try:
        return str(dt.replace(year=target_year))
    except Exception:
        return str(value or "")


def _aggregate_gl_entry_totals(start_date: str, end_date: str, max_scan_rows: int) -> dict:
    """Aggregate GL Entry debit/credit totals with bounded scans for deterministic answers."""
    scanned = 0
    count = 0
    sum_debit = 0.0
    sum_credit = 0.0
    account_totals: dict[str, dict[str, float]] = {}
    batch_size = 500
    truncated = False

    filters = {
        "is_cancelled": 0,
        "posting_date": ["between", [start_date, end_date]],
    }

    while scanned < max_scan_rows:
        page_length = min(batch_size, max_scan_rows - scanned)
        if page_length <= 0:
            truncated = True
            break

        rows = frappe.get_list(
            "GL Entry",
            filters=filters,
            fields=["name", "account", "debit", "credit"],
            order_by="posting_date asc",
            start=scanned,
            page_length=page_length,
        )

        if not rows:
            break

        for row in rows:
            try:
                debit = float(row.get("debit") or 0)
            except (TypeError, ValueError):
                debit = 0.0

            try:
                credit = float(row.get("credit") or 0)
            except (TypeError, ValueError):
                credit = 0.0

            sum_debit += debit
            sum_credit += credit
            count += 1

            account_name = str(row.get("account") or "").strip()
            if account_name:
                bucket = account_totals.setdefault(account_name, {"debit": 0.0, "credit": 0.0})
                bucket["debit"] += debit
                bucket["credit"] += credit

        scanned += len(rows)
        if len(rows) < page_length:
            break

    if scanned >= max_scan_rows:
        truncated = True

    return {
        "count": count,
        "sum_debit": sum_debit,
        "sum_credit": sum_credit,
        "truncated": truncated,
        "account_totals": account_totals,
    }


def _build_account_root_type_map(account_names: list[str]) -> dict[str, str]:
    """Resolve Account root_type values for the supplied account names."""
    if not account_names:
        return {}

    mapping: dict[str, str] = {}
    chunk_size = 200

    for start in range(0, len(account_names), chunk_size):
        chunk = [name for name in account_names[start : start + chunk_size] if str(name or "").strip()]
        if not chunk:
            continue

        rows = frappe.get_all(
            "Account",
            filters={"name": ["in", chunk]},
            fields=["name", "root_type"],
            limit_page_length=len(chunk),
        )
        for row in rows or []:
            account_name = str(row.get("name") or "").strip()
            root_type = str(row.get("root_type") or "").strip()
            if account_name and root_type:
                mapping[account_name] = root_type

    return mapping


def _calculate_profit_snapshot(account_totals: dict[str, dict[str, float]], root_type_map: dict[str, str]) -> tuple[float, float, float]:
    """Return (income_total, expense_total, net_profit) from GL totals + account root types."""
    income_total = 0.0
    expense_total = 0.0

    for account_name, totals in (account_totals or {}).items():
        root_type = root_type_map.get(account_name)
        debit = float(totals.get("debit") or 0)
        credit = float(totals.get("credit") or 0)

        if root_type == "Income":
            income_total += credit - debit
        elif root_type == "Expense":
            expense_total += debit - credit

    return income_total, expense_total, income_total - expense_total


def _looks_like_bank_balance_question(question: str) -> bool:
    """Return True when the user asks for bank/cash account balances."""
    q = str(question or "").lower()
    if not q:
        return False

    direct_tokens = (
        "bank balance",
        "cash balance",
        "cash in bank",
        "bank account balance",
        "balances per bank account",
        "رصيد البنك",
        "رصيد بنكي",
        "رصيد الحساب البنكي",
        "ارصدة البنوك",
        "أرصدة البنوك",
        "رصيد النقدية",
        "رصيد الخزينة",
    )
    if any(token in q for token in direct_tokens):
        return True

    # Broader phrasing support where tokens are separated.
    bank_tokens = ("bank", "banks", "bank account", "banking", "بنك", "البنك", "بنكي", "الحساب البنكي")
    balance_tokens = ("balance", "balances", "رصيد", "ارصدة", "أرصدة")
    cash_tokens = ("cash", "cashflow", "نقدية", "الخزينة", "الصندوق")

    has_bank = any(token in q for token in bank_tokens)
    has_balance = any(token in q for token in balance_tokens)
    has_cash = any(token in q for token in cash_tokens)
    return (has_bank and has_balance) or (has_cash and has_balance)


def _load_bank_accounts(user: str) -> list[dict]:
    """Load readable non-group bank accounts for the current user scope."""
    if not _has_read_permission("Account", user):
        return []

    fields = ["name", "account_name", "company", "account_currency"]
    filters = {"account_type": "Bank", "is_group": 0}

    try:
        rows = frappe.get_all(
            "Account",
            filters=filters,
            fields=fields,
            order_by="name asc",
            limit_page_length=5000,
        )
    except Exception:
        # Some setups may not expose is_group consistently; retry with account_type only.
        try:
            rows = frappe.get_all(
                "Account",
                filters={"account_type": "Bank"},
                fields=fields,
                order_by="name asc",
                limit_page_length=5000,
            )
        except Exception:
            return []

    cleaned: list[dict] = []
    for row in rows or []:
        account_name = str(row.get("name") or "").strip()
        if not account_name:
            continue

        label = str(row.get("account_name") or account_name).strip() or account_name
        cleaned.append(
            {
                "name": account_name,
                "label": label,
                "company": str(row.get("company") or "").strip(),
                "currency": str(row.get("account_currency") or "").strip(),
            }
        )

    return cleaned


def _aggregate_bank_account_balances(bank_accounts: list[dict], max_scan_rows: int) -> dict:
    """Aggregate submitted GL Entry debit/credit totals for bank accounts."""
    account_names = [str(item.get("name") or "").strip() for item in (bank_accounts or [])]
    account_names = [name for name in account_names if name]
    if not account_names:
        return {"row_count": 0, "truncated": False, "by_account": {}}

    by_account: dict[str, dict] = {
        name: {"debit": 0.0, "credit": 0.0, "currencies": set()}
        for name in account_names
    }

    batch_size = 500

    def _scan(gl_filters: dict) -> tuple[int, bool]:
        scanned = 0
        row_count = 0
        truncated = False

        while scanned < max_scan_rows:
            page_length = min(batch_size, max_scan_rows - scanned)
            if page_length <= 0:
                truncated = True
                break

            rows = frappe.get_list(
                "GL Entry",
                filters=gl_filters,
                fields=["name", "account", "debit", "credit", "currency"],
                order_by="posting_date asc",
                start=scanned,
                page_length=page_length,
            )

            if not rows:
                break

            for row in rows:
                account_name = str(row.get("account") or "").strip()
                if account_name not in by_account:
                    continue

                try:
                    debit = float(row.get("debit") or 0)
                except (TypeError, ValueError):
                    debit = 0.0

                try:
                    credit = float(row.get("credit") or 0)
                except (TypeError, ValueError):
                    credit = 0.0

                bucket = by_account[account_name]
                bucket["debit"] += debit
                bucket["credit"] += credit

                currency = str(row.get("currency") or "").strip()
                if currency:
                    bucket["currencies"].add(currency)

            row_count += len(rows)
            scanned += len(rows)
            if len(rows) < page_length:
                break

        if scanned >= max_scan_rows:
            truncated = True

        return row_count, truncated

    # Preferred filter: submitted GL rows + non-cancelled entries.
    preferred_filters = {
        "account": ["in", account_names],
        "is_cancelled": 0,
        "docstatus": 1,
    }

    try:
        row_count, truncated = _scan(preferred_filters)
    except Exception:
        # Compatibility fallback where docstatus may not be queryable.
        fallback_filters = {
            "account": ["in", account_names],
            "is_cancelled": 0,
        }
        row_count, truncated = _scan(fallback_filters)

    return {
        "row_count": row_count,
        "truncated": truncated,
        "by_account": by_account,
    }


def _build_bank_balance_context(question: str, user: str, max_scan_rows: int) -> str:
    """Build deterministic bank-balance context from GL Entry for bank accounts."""
    if not _looks_like_bank_balance_question(question):
        return ""

    if not _has_read_permission("GL Entry", user):
        return "Bank Balance Aggregates (deterministic): no read permission for GL Entry."

    bank_accounts = _load_bank_accounts(user)
    if not bank_accounts:
        if _has_read_permission("Account", user):
            return "Bank Balance Aggregates (deterministic): no bank accounts (Account.account_type=Bank) found in current scope."
        return "Bank Balance Aggregates (deterministic): no read permission for Account, so bank account classification is unavailable."

    try:
        aggregate = _aggregate_bank_account_balances(bank_accounts, max_scan_rows)
    except Exception as exc:
        return f"Bank Balance Aggregates (deterministic): could not be calculated ({exc})."

    by_account = aggregate.get("by_account") or {}
    lines = [
        "Bank Balance Aggregates (deterministic):",
        "- Calculation: Bank Balance = SUM(debit) - SUM(credit).",
        "- Filters: GL Entry submitted postings (docstatus=1) for Account.account_type=Bank.",
    ]

    total_balance = 0.0
    all_currencies: set[str] = set()
    for account in bank_accounts:
        account_name = str(account.get("name") or "").strip()
        label = str(account.get("label") or account_name).strip() or account_name
        company = str(account.get("company") or "").strip()

        bucket = by_account.get(account_name, {"debit": 0.0, "credit": 0.0, "currencies": set()})
        sum_debit = float(bucket.get("debit") or 0)
        sum_credit = float(bucket.get("credit") or 0)
        balance = sum_debit - sum_credit
        total_balance += balance

        currencies = set(bucket.get("currencies") or set())
        configured_currency = str(account.get("currency") or "").strip()
        if configured_currency:
            currencies.add(configured_currency)

        all_currencies.update(currencies)
        if len(currencies) == 1:
            currency_note = f" currency={next(iter(currencies))}."
        elif len(currencies) > 1:
            currency_note = " currencies=mixed."
        else:
            currency_note = ""

        company_note = f", company={company}" if company else ""
        lines.append(
            f"- Bank Account: {label} ({account_name}{company_note}) -> "
            f"sum_debit={sum_debit:.2f}, sum_credit={sum_credit:.2f}, balance={balance:.2f}.{currency_note}"
        )

    if len(all_currencies) == 1:
        total_currency_note = f" currency={next(iter(all_currencies))}."
    elif len(all_currencies) > 1:
        total_currency_note = " currencies=mixed."
    else:
        total_currency_note = ""

    partial_note = " Result may be partial (scan limit reached)." if bool(aggregate.get("truncated")) else ""
    lines.append(f"- Total Bank Balance: {total_balance:.2f}.{total_currency_note}{partial_note}")

    if int(aggregate.get("row_count") or 0) <= 0:
        lines.append("- No submitted GL rows were found for these bank accounts in the current scope.")

    lines.append("- Suggested Reports: General Ledger, Trial Balance.")
    lines.append("- ERP Links: /app/query-report/General Ledger | /app/query-report/Trial Balance | /app/chart-of-accounts")
    lines.append(
        "Response rule: For bank-balance questions, calculate and present balances first, then explain the formula, "
        "and only then provide report links for verification."
    )
    lines.append("Use these bank balances as authoritative for bank/cash balance answers.")

    return "\n".join(lines)


def _build_ledger_totals_context(question: str, user: str, max_scan_rows: int) -> str:
    """Build deterministic account/ledger/profit aggregates for finance-oriented questions."""
    if not _looks_like_ledger_or_profit_question(question):
        return ""

    if not _has_read_permission("GL Entry", user):
        return "Ledger Aggregates (deterministic): no read permission for GL Entry."

    period = _extract_year_window_from_question(question)
    if period:
        label, start_date, end_date = period
    else:
        try:
            today = frappe.utils.getdate()
            year = int(today.year)
            label = "this year"
            start_date = f"{year}-01-01"
            end_date = str(today)
        except Exception:
            return ""

    if not start_date or not end_date:
        return ""

    try:
        current = _aggregate_gl_entry_totals(start_date, end_date, max_scan_rows)
    except Exception as exc:
        return f"Ledger Aggregates (deterministic): could not be calculated ({exc})."

    lines = ["Ledger Aggregates (deterministic):"]

    if int(current.get("count") or 0) <= 0:
        lines.append(f"- GL Entry Aggregate ({label}): no ledger entries found in this scope for this user.")
        return "\n".join(lines)

    sum_debit = float(current.get("sum_debit") or 0)
    sum_credit = float(current.get("sum_credit") or 0)
    net_movement = sum_credit - sum_debit
    partial_note = " Result may be partial (scan limit reached)." if bool(current.get("truncated")) else ""
    lines.append(
        f"- GL Entry Aggregate ({label}): posted_entry_count={int(current.get('count') or 0)}, "
        f"sum_debit={sum_debit:.2f}, sum_credit={sum_credit:.2f}, net_movement={net_movement:.2f}.{partial_note}"
    )

    current_profit = None
    root_type_map: dict[str, str] = {}

    account_totals = current.get("account_totals") or {}
    if account_totals and _has_read_permission("Account", user):
        try:
            root_type_map = _build_account_root_type_map(list(account_totals.keys()))
        except Exception:
            root_type_map = {}

        if root_type_map:
            income_total, expense_total, current_profit = _calculate_profit_snapshot(account_totals, root_type_map)
            lines.append(
                f"- Profit Snapshot ({label}) from GL Entry + Account root_type: "
                f"income_total={income_total:.2f}, expense_total={expense_total:.2f}, net_profit={current_profit:.2f}."
            )

    if _question_requests_growth(question) and current_profit is not None:
        prev_start = _shift_year_date(start_date, -1)
        prev_end = _shift_year_date(end_date, -1)

        try:
            previous = _aggregate_gl_entry_totals(prev_start, prev_end, max_scan_rows)
        except Exception:
            previous = None

        if previous and (previous.get("account_totals") or {}) and _has_read_permission("Account", user):
            previous_map = root_type_map
            missing_accounts = [
                account_name
                for account_name in (previous.get("account_totals") or {}).keys()
                if account_name not in previous_map
            ]
            if missing_accounts:
                try:
                    previous_map = {**previous_map, **_build_account_root_type_map(missing_accounts)}
                except Exception:
                    pass

            prev_income, prev_expense, previous_profit = _calculate_profit_snapshot(
                previous.get("account_totals") or {},
                previous_map,
            )
            growth_amount = current_profit - previous_profit

            if abs(previous_profit) > 1e-9:
                growth_percent = (growth_amount / abs(previous_profit)) * 100.0
                lines.append(
                    f"- Profit Growth vs previous comparable period ({prev_start} to {prev_end}): "
                    f"previous_net_profit={previous_profit:.2f}, growth_amount={growth_amount:.2f}, "
                    f"growth_percent={growth_percent:.2f}%."
                )
            else:
                lines.append(
                    f"- Profit Growth vs previous comparable period ({prev_start} to {prev_end}): "
                    f"previous_net_profit={previous_profit:.2f}, growth_amount={growth_amount:.2f}, "
                    "growth_percent=not available (previous period is zero)."
                )

            lines.append(
                f"- Previous Profit Snapshot ({prev_start} to {prev_end}): "
                f"income_total={prev_income:.2f}, expense_total={prev_expense:.2f}."
            )

    lines.append("Use these values as authoritative for ledger, account, and profit answers.")
    return "\n".join(lines)


def _build_sales_totals_context(question: str, user: str, max_scan_rows: int) -> str:
    """Build deterministic, permission-aware sales aggregate context blocks."""
    if not _looks_like_sales_total_question(question):
        period_probe = _extract_month_window_from_question(question) or _extract_year_window_from_question(question)
        if not period_probe:
            return ""

    targets = _resolve_sales_aggregate_targets(question)
    if not targets:
        return ""

    period = _extract_month_window_from_question(question) or _extract_year_window_from_question(question)
    if period:
        label = period[0]
        start_date = period[1]
        end_date = period[2]
    else:
        # Default to current year when no explicit period is mentioned.
        try:
            today = frappe.utils.getdate()
            year = int(today.year)
            label = "this year"
            start_date = f"{year}-01-01"
            end_date = str(today)
        except Exception:
            label = "all available submitted records"
            start_date = ""
            end_date = ""

    lines = ["Sales Aggregates (deterministic):"]

    for doctype, date_field, count_label in targets:
        if not _has_read_permission(doctype, user):
            lines.append(f"- {doctype} Aggregate: no read permission for this user.")
            continue

        filters = {"docstatus": 1}
        if start_date and end_date:
            filters[date_field] = ["between", [start_date, end_date]]

        scanned = 0
        total_grand = 0.0
        total_net = 0.0
        count = 0
        currencies: set[str] = set()
        batch_size = 500
        truncated = False

        try:
            while scanned < max_scan_rows:
                page_length = min(batch_size, max_scan_rows - scanned)
                if page_length <= 0:
                    truncated = True
                    break

                rows = frappe.get_list(
                    doctype,
                    filters=filters,
                    fields=["name", "base_grand_total", "grand_total", "base_net_total", "net_total", "currency"],
                    order_by=f"{date_field} asc",
                    start=scanned,
                    page_length=page_length,
                )

                if not rows:
                    break

                for row in rows:
                    amount = row.get("base_grand_total")
                    if amount in (None, ""):
                        amount = row.get("grand_total")
                    try:
                        total_grand += float(amount or 0)
                    except (TypeError, ValueError):
                        pass

                    net_amount = row.get("base_net_total")
                    if net_amount in (None, ""):
                        net_amount = row.get("net_total")
                    if net_amount in (None, ""):
                        net_amount = amount
                    try:
                        total_net += float(net_amount or 0)
                    except (TypeError, ValueError):
                        pass

                    count += 1
                    if row.get("currency"):
                        currencies.add(str(row.get("currency")))

                scanned += len(rows)
                if len(rows) < page_length:
                    break

            if scanned >= max_scan_rows:
                truncated = True

        except Exception as exc:
            lines.append(f"- {doctype} Aggregate: could not be calculated ({exc}).")
            continue

        if count == 0:
            lines.append(
                f"- {doctype} Aggregate ({label}): no submitted records found in this scope for this user."
            )
            continue

        currency_note = ""
        if len(currencies) == 1:
            currency_note = f" currency={next(iter(currencies))}."
        elif len(currencies) > 1:
            currency_note = " currencies=mixed."

        partial_note = " Result may be partial (scan limit reached)." if truncated else ""
        lines.append(
            f"- {doctype} Aggregate ({label}): {count_label}={count}, "
            f"sum_grand_total={total_grand:.2f}, sum_net_total={total_net:.2f}.{currency_note}{partial_note}"
        )

    if len(lines) == 1:
        return ""

    lines.append("Use these totals as authoritative for deterministic sales amount answers.")
    return "\n".join(lines)


def _looks_like_purchase_total_question(question: str) -> bool:
    """Return True when the question asks for purchase total/amount (by project or overall)."""
    q = str(question or "").lower()
    if not q:
        return False

    purchase_tokens = (
        "purchase",
        "purchases",
        "purchase order",
        "purchase orders",
        "purchase invoice",
        "purchase invoices",
        "مشتريات",
        "المشتريات",
        "أمر شراء",
        "امر شراء",
        "أوامر شراء",
        "فاتورة شراء",
        "فواتير شراء",
    )
    total_tokens = (
        "total",
        "sum",
        "amount",
        "value",
        "how much",
        "by project",
        "per project",
        "based on project",
        "grouped by project",
        "sort",
        "اجمالي",
        "إجمالي",
        "مجموع",
        "القيمة",
        "قيمة",
        "حسب المشروع",
        "لكل مشروع",
        "بناءً على المشروع",
        "ترتيب",
    )
    return any(t in q for t in purchase_tokens) and any(t in q for t in total_tokens)


def _build_purchase_by_project_context(question: str, user: str, max_scan_rows: int) -> str:
    """Return purchase totals grouped/sorted by project for purchase-by-project questions."""
    if not _looks_like_purchase_total_question(question):
        return ""

    # Determine which DocType to use: Purchase Order vs Purchase Invoice.
    q = question.lower()
    invoice_tokens = (
        "purchase invoice",
        "purchase invoices",
        "vendor invoice",
        "فاتورة شراء",
        "فواتير شراء",
    )
    doctype = "Purchase Invoice" if any(t in q for t in invoice_tokens) else "Purchase Order"

    if not _has_read_permission(doctype, user):
        return ""

    project_totals: dict[str, float] = {}
    project_counts: dict[str, int] = {}
    scanned = 0
    batch_size = 500
    truncated = False
    currencies: set[str] = set()

    try:
        while scanned < max_scan_rows:
            page_length = min(batch_size, max_scan_rows - scanned)
            if page_length <= 0:
                truncated = True
                break

            rows = frappe.get_list(
                doctype,
                filters={"docstatus": 1},
                fields=["name", "project", "base_grand_total", "grand_total", "currency"],
                order_by="modified desc",
                start=scanned,
                page_length=page_length,
            )

            if not rows:
                break

            for row in rows:
                project = str(row.get("project") or "").strip() or "(No Project)"
                amount = row.get("base_grand_total")
                if amount in (None, ""):
                    amount = row.get("grand_total")
                try:
                    amount = float(amount or 0)
                except (TypeError, ValueError):
                    amount = 0.0

                project_totals[project] = project_totals.get(project, 0.0) + amount
                project_counts[project] = project_counts.get(project, 0) + 1

                if row.get("currency"):
                    currencies.add(str(row["currency"]))

            scanned += len(rows)
            if len(rows) < page_length:
                break

        if scanned >= max_scan_rows:
            truncated = True

    except Exception as exc:
        return f"{doctype} by project: could not retrieve ({exc})."

    if not project_totals:
        return ""

    sorted_projects = sorted(project_totals.items(), key=lambda x: x[1], reverse=True)

    currency_note = ""
    if len(currencies) == 1:
        currency_note = f" (currency: {next(iter(currencies))})"
    elif len(currencies) > 1:
        currency_note = " (mixed currencies)"

    partial_note = " (scan limit reached — result may be partial)." if truncated else ""

    lines = [
        f"Deterministic {doctype} Totals by Project (authoritative — sorted by total descending){currency_note}{partial_note}:"
    ]
    for project, total in sorted_projects:
        count = project_counts.get(project, 0)
        lines.append(f"  - {project}: total={total:.2f}, orders={count}")

    lines.append(f"Grand Total across all projects: {sum(project_totals.values()):.2f}")
    lines.append("Use these figures as authoritative. Do not guess or infer different values.")
    return "\n".join(lines)


def _is_user_stats_question(question: str) -> bool:
    """Return True when the question is about system user counts / active-disabled breakdown."""
    q = str(question or "").lower()
    if not q:
        return False

    user_tokens = (
        "user",
        "users",
        "system user",
        "system users",
        "مستخدم",
        "المستخدم",
        "المستخدمين",
        "مستخدمين",
        "حسابات المستخدمين",
        "حسابات النظام",
    )
    count_tokens = (
        "how many",
        "count",
        "number of",
        "total",
        "عدد",
        "كم",
        "إجمالي",
        "اجمالي",
        "active",
        "disabled",
        "نشطين",
        "نشط",
        "معطل",
    )
    has_user = any(t in q for t in user_tokens)
    has_count = any(t in q for t in count_tokens)
    return has_user and has_count


def _is_regions_recommendation_question(question: str) -> bool:
    """Return True when user asks for important regions/areas and expects concrete area names."""
    q = str(question or "").lower()
    if not q:
        return False

    region_tokens = (
        "region",
        "regions",
        "area",
        "areas",
        "zone",
        "zones",
        "location",
        "locations",
        "منطقة",
        "مناطق",
        "المنطقة",
        "المناطق",
        "مدينة",
        "مدن",
        "محافظة",
        "محافظات",
    )
    ranking_tokens = (
        "top",
        "best",
        "important",
        "priority",
        "recommended",
        "target",
        "main",
        "key",
        "اهم",
        "أهم",
        "افضل",
        "أفضل",
        "مستهدفة",
        "ترشيح",
        "مقترحة",
    )

    return any(t in q for t in region_tokens) and any(t in q for t in ranking_tokens)


def _build_regions_recommendation_reply(question: str, language_hint: str | None = None) -> str:
    """Return a deterministic, user-friendly region list reply for Arabic/English prompts."""
    prefers_ar = _question_contains_arabic(question) or _normalize_language_hint(language_hint) == "ar"

    if prefers_ar:
        return (
            "أهم المناطق المقترحة حالياً:\n"
            "- القاهرة الكبرى (القاهرة / الجيزة / القليوبية)\n"
            "- الإسكندرية\n"
            "- البحيرة\n"
            "- الدقهلية\n"
            "- الشرقية\n"
            "- الغربية\n\n"
            "سبب الاختيار باختصار: كثافة سكانية عالية + نشاط عمراني وتجاري + طلب متزايد على خفض تكلفة الكهرباء.\n"
            "إذا رغبت، أقدر أكمل لك بخطة دخول سوق مختصرة لكل منطقة (قنوات البيع، نوع العملاء، أولوية التنفيذ)."
        )

    return (
        "Top recommended regions right now:\n"
        "- Greater Cairo (Cairo / Giza / Qalyubia)\n"
        "- Alexandria\n"
        "- Beheira\n"
        "- Dakahlia\n"
        "- Sharqia\n"
        "- Gharbia\n\n"
        "Why these: high population density + active commercial/residential demand + growing need to reduce electricity costs.\n"
        "If you want, I can provide a short go-to-market plan per region (channels, customer segments, and rollout priority)."
    )


def _is_new_project_question(question: str) -> bool:
    """Return True when user asks to start/create a new project."""
    q = str(question or "").lower().strip()
    if not q:
        return False

    project_tokens = ("project", "new project", "start project", "create project", "مشروع", "مشروع جديد", "ابدأ مشروع", "انشاء مشروع", "إنشاء مشروع")
    return any(token in q for token in project_tokens)


def _build_new_project_structured_reply(question: str, language_hint: str | None = None) -> str:
    """Return structured, actionable reply for new-project intents."""
    prefers_ar = _question_contains_arabic(question) or _normalize_language_hint(language_hint) == "ar"

    if prefers_ar:
        return (
            "لبدء مشروع جديد بشكل صحيح، هذه البيانات المطلوبة:\n"
            "- اسم المشروع\n"
            "- العميل/الجهة\n"
            "- تاريخ البداية وتاريخ النهاية المتوقع\n"
            "- الميزانية التقديرية\n"
            "- مدير المشروع والفريق\n"
            "- نطاق العمل (Scope) والنتائج المطلوبة\n\n"
            "خطوات التنفيذ داخل النظام:\n"
            "1) افتح Project وأنشئ سجل جديد.\n"
            "2) أدخل بيانات المشروع الأساسية.\n"
            "3) أضف المهام الرئيسية (Tasks) وحدد المسؤوليات والمواعيد.\n"
            "4) اربط المشروع بالعميل/المبيعات/المشتريات عند الحاجة.\n"
            "5) تابع التقدم والتكلفة الفعلية مقابل الميزانية.\n\n"
            "حالة البيانات الحالية: لا يمكنني تأكيد وجود مشروع محدد من رسالتك الحالية فقط.\n"
            "أرسل اسم المشروع أو العميل الآن لأعطيك خطة تنفيذ جاهزة مباشرة."
        )

    return (
        "To start a new project properly, provide:\n"
        "- Project name\n"
        "- Customer/party\n"
        "- Start date and expected end date\n"
        "- Estimated budget\n"
        "- Project manager and team\n"
        "- Scope and expected deliverables\n\n"
        "Execution steps in the system:\n"
        "1) Open Project and create a new record.\n"
        "2) Fill core project details.\n"
        "3) Add major tasks with owners and deadlines.\n"
        "4) Link project to sales/purchase/customer context when needed.\n"
        "5) Track progress and actual cost vs budget.\n\n"
        "Current data status: I cannot confirm a specific project from the current message alone.\n"
        "Share the project name or customer now and I will generate a ready-to-use execution plan."
    )


def _build_data_first_fallback_reply(
    question: str,
    language_hint: str | None = None,
    mentioned_doctypes: list[str] | None = None,
) -> str:
    """Return non-generic structured fallback when no deterministic path was triggered."""
    prefers_ar = _question_contains_arabic(question) or _normalize_language_hint(language_hint) == "ar"
    doctype_label = ", ".join([str(dt) for dt in (mentioned_doctypes or []) if str(dt).strip()][:3])

    if prefers_ar:
        return (
            "ملاحظتي على سؤالك:\n"
            f"- السؤال: {question.strip()}\n"
            f"- النطاق المحتمل في النظام: {doctype_label or 'غير محدد'}\n"
            "- البيانات المباشرة المتاحة من الرسالة الحالية: غير كافية لإخراج نتيجة رقمية دقيقة.\n\n"
            "لإعطائك إجابة مبنية على بيانات فعلية، أرسل أحد التالي:\n"
            "- اسم المستند (DocType) المطلوب (مثل: Project / Lead / Sales Order)\n"
            "- أو اسم السجل/العميل/الفترة الزمنية\n"
            "- أو المطلوب بالضبط (عدد، إجمالي، حالة، أعلى العناصر)\n\n"
            "بمجرد إرسال ذلك، سأرجع لك النتيجة بصيغة واضحة تحتوي البيانات المطلوبة مباشرة."
        )

    return (
        "Data-first status for your question:\n"
        f"- Question: {question.strip()}\n"
        f"- Potential scope: {doctype_label or 'not specified'}\n"
        "- Direct data from current message: insufficient for a precise numeric/result output.\n\n"
        "To return a concrete data-backed answer, send one of:\n"
        "- target DocType (e.g., Project / Lead / Sales Order)\n"
        "- record/customer name or date range\n"
        "- exact output type (count, total, status, top items)\n\n"
        "Once shared, I will return the required data in a clear structured format."
    )


def _build_user_stats_context(question: str, user: str) -> str:
    """Return authoritative active/disabled user counts, excluding system accounts."""
    if not _is_user_stats_question(question):
        return ""

    if not _has_read_permission("User", user):
        return ""

    SYSTEM_ACCOUNTS = {"Administrator", "Guest"}

    try:
        active_users = frappe.get_all(
            "User",
            filters={"enabled": 1},
            fields=["name"],
            ignore_permissions=False,
        )
        active_count = sum(1 for r in active_users if _row_value(r, "name") not in SYSTEM_ACCOUNTS)

        disabled_users = frappe.get_all(
            "User",
            filters={"enabled": 0},
            fields=["name"],
            ignore_permissions=False,
        )
        disabled_count = sum(1 for r in disabled_users if _row_value(r, "name") not in SYSTEM_ACCOUNTS)

    except Exception as exc:
        return f"User stats: could not retrieve ({exc})."

    total = active_count + disabled_count
    lines = [
        "Deterministic User Stats (authoritative — use these exact numbers, do not guess):",
        f"- Active Users (enabled=1, excluding Administrator/Guest): {active_count}",
        f"- Disabled Users (enabled=0, excluding Administrator/Guest): {disabled_count}",
        f"- Total Users: {total}",
    ]
    return "\n".join(lines)


def _build_doctype_count_context(question: str, user: str, doctypes: list[str], max_scan_rows: int) -> str:
    """Build deterministic, permission-aware record counts for matched DocTypes."""
    if not _question_requests_count(question):
        return ""
    if not doctypes:
        return ""

    lines = ["Deterministic Count Context (authoritative):"]
    for doctype in doctypes[:8]:
        dt = str(doctype or "").strip()
        if not dt:
            continue

        if not _has_read_permission(dt, user):
            lines.append(f"- {dt}: no read permission for this user.")
            continue

        scanned = 0
        count = 0
        batch_size = 500
        truncated = False

        try:
            while scanned < max_scan_rows:
                page_length = min(batch_size, max_scan_rows - scanned)
                if page_length <= 0:
                    truncated = True
                    break

                rows = frappe.get_list(
                    dt,
                    fields=["name"],
                    order_by="modified desc",
                    start=scanned,
                    page_length=page_length,
                )

                if not rows:
                    break

                count += len(rows)
                scanned += len(rows)
                if len(rows) < page_length:
                    break

            if scanned >= max_scan_rows:
                truncated = True

        except Exception as exc:
            lines.append(f"- {dt}: could not calculate count ({exc}).")
            continue

        partial_note = " Result may be partial (scan limit reached)." if truncated else ""
        lines.append(f"- {dt}: visible_record_count={count}.{partial_note}")

    if len(lines) == 1:
        return ""

    lines.append("Use these counts as authoritative. Do not infer totals from sampled record lists.")
    return "\n".join(lines)


def _build_interactive_topic_options(
    question: str,
    mentioned_doctypes: list[str] | None,
    permitted_doctypes: list[str] | None,
    language_hint: str | None = None,
) -> list[dict]:
    """Return deterministic quick-topic options for interactive front-end selects."""
    prefers_ar = _question_contains_arabic(question) or _normalize_language_hint(language_hint) == "ar"
    mentioned = [str(dt or "").strip() for dt in (mentioned_doctypes or []) if str(dt or "").strip()]
    permitted = {str(dt or "").strip() for dt in (permitted_doctypes or []) if str(dt or "").strip()}

    options: list[dict] = []
    seen: set[str] = set()

    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")

    def _add(key: str, label: str, prompt: str) -> None:
        topic_key = _slug(key)[:60]
        topic_label = str(label or "").strip()[:80]
        topic_prompt = str(prompt or "").strip()[:240]
        if not topic_key or not topic_label or not topic_prompt:
            return
        if topic_key in seen:
            return
        seen.add(topic_key)
        options.append({"key": topic_key, "label": topic_label, "prompt": topic_prompt})

    for dt in mentioned[:3]:
        key = _slug(dt) or "doctype"
        if prefers_ar:
            _add(f"{key}-summary", f"ملخص {dt}", f"اعطني ملخصا قصيرا لاحدث سجلات {dt}.")
            _add(f"{key}-count", f"عدد سجلات {dt}", f"كم عدد السجلات المرئية لي في {dt}؟")
            _add(f"open-{key}", f"افتح {dt}", f"افتح {dt}")
        else:
            _add(f"{key}-summary", f"{dt} summary", f"Give me a short summary of recent {dt} records.")
            _add(f"{key}-count", f"{dt} count", f"How many {dt} records are visible to me?")
            _add(f"open-{key}", f"Open {dt}", f"open {dt}")

    if "Sales Invoice" in permitted or "Sales Order" in permitted:
        if prefers_ar:
            _add(
                "sales-total-this-year",
                "إجمالي المبيعات هذا العام",
                "احسب إجمالي المبيعات لهذا العام مع العدد والمبلغ.",
            )
        else:
            _add(
                "sales-total-this-year",
                "Total sales this year",
                "Calculate total sales for this year with count and amount.",
            )

    if "GL Entry" in permitted or "Account" in permitted:
        if prefers_ar:
            _add(
                "bank-balance-summary",
                "ملخص أرصدة البنوك",
                "احسب أرصدة البنوك الحالية لكل حساب بنكي مع الإجمالي وطريقة الحساب.",
            )
        else:
            _add(
                "bank-balance-summary",
                "Bank balance summary",
                "Calculate current bank balances per bank account with total and formula.",
            )

    if prefers_ar:
        _add("pending-approvals", "موافقات معلقة", "اعرض موافقاتي المعلقة حسب DocType والخطوة التالية.")
        _add("access-scope", "نطاق صلاحياتي", "اعرض اهم DocTypes التي يمكنني الوصول لها واقترح الخطوة التالية.")
    else:
        _add("pending-approvals", "Pending approvals", "Show my pending approvals by DocType and next action.")
        _add("access-scope", "My access scope", "List the main DocTypes I can access and suggest next actions.")

    return options[:10]


def _extract_inline_options(reply: str) -> tuple[str, list[dict]]:
    """
    Detect a numbered pick-list inside an AI reply and return
    ``(cleaned_reply, inline_options)``.

    When 2+ contiguous numbered items are found the numbered lines are removed
    from the reply text and returned as interactive chip options.  A trailing
    "which one?" / "تقصد أي نوع منهم؟" style question is also stripped because
    the chips make the intent self-evident.
    """
    if not reply or not reply.strip():
        return reply, []

    lines = reply.split("\n")

    # Match: leading optional whitespace, a digit sequence (Western or Arabic-Indic),
    # then one of . ) - :, then the item text.
    NUMBERED_ITEM = re.compile(
        r"^\s*(?:\d+|[\u0660-\u0669\u06F0-\u06F9]+)[.)\-:]\s*(.+)\s*$"
    )

    # Trailing "which one?" detector (Arabic + English common forms).
    CLOSING_Q = re.compile(
        r"(?:تقصد|أيهم|أي منهم|أيها|اختر|اختار|ما الذي|ماذا تريد"
        r"|select one|choose one|which one|pick one|please choose|what would you like)",
        re.IGNORECASE,
    )

    item_lines: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        m = NUMBERED_ITEM.match(line)
        if m:
            text = m.group(1).strip()
            if text:
                item_lines.append((idx, text))

    if len(item_lines) < 2:
        return reply, []

    # Items must be reasonably contiguous (no gap > 2 blank lines).
    indices = [i for i, _ in item_lines]
    if len(indices) > 1:
        max_gap = max(indices[j + 1] - indices[j] for j in range(len(indices) - 1))
        if max_gap > 3:
            return reply, []

    inline_options = [{"label": text, "query": text} for _, text in item_lines]

    remove_set = set(indices)
    last_item_idx = max(indices)
    for j in range(last_item_idx + 1, min(last_item_idx + 5, len(lines))):
        stripped = lines[j].strip()
        if stripped and CLOSING_Q.search(stripped):
            remove_set.add(j)

    cleaned_lines = [line for i, line in enumerate(lines) if i not in remove_set]
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()

    cleaned_reply = "\n".join(cleaned_lines).strip()
    return cleaned_reply, inline_options


# ---------------------------------------------------------------------------
# Helpers: context builders
# ---------------------------------------------------------------------------

def _get_user_permitted_doctypes(user: str) -> list[str]:
    """Return a list of DocTypes the *user* has at least Read permission on."""
    return [item["doctype"] for item in _get_user_permitted_doctype_index(user)]


def _build_permission_context(user: str) -> str:
    """Return a human-readable summary of User Permissions for *user*."""
    lines = []
    try:
        perms = frappe.get_all(
            "User Permission",
            filters={"user": user},
            fields=["allow", "for_value", "apply_to_all_doctypes", "applicable_for"],
        )
        if perms:
            lines.append("User Permissions:")
            for p in perms:
                scope = "all doctypes" if p.apply_to_all_doctypes else (p.applicable_for or "all doctypes")
                lines.append(f"  - {p.allow}: {p.for_value}  (scope: {scope})")
        else:
            lines.append("User Permissions: none explicitly set.")
    except Exception:
        lines.append("User Permissions: could not be retrieved.")
    return "\n".join(lines)


def _build_workflow_context(user: str | None = None, max_rows: int = 10) -> str:
    """Return a summary of active Workflows, filtered by readable DocTypes when user is provided."""
    lines = []
    try:
        workflows = frappe.get_all(
            "Workflow",
            filters={"is_active": 1},
            fields=["name", "document_type", "workflow_state_field"],
        )
        if not workflows:
            return "Workflows: none active."
        lines.append("Active Workflows:")
        shown = 0
        for wf in workflows[:max_rows]:
            wf_doctype = wf.get("document_type")
            if user and wf_doctype and not _has_read_permission(wf_doctype, user):
                continue

            states = frappe.get_all(
                "Workflow Document State",
                filters={"parent": wf["name"]},
                fields=["state", "doc_status"],
            )
            state_names = [s["state"] for s in states]
            lines.append(
                f"  - {wf['name']} (DocType: {wf['document_type']}) "
                f"states: {', '.join(state_names)}"
            )
            shown += 1
            if shown >= max_rows:
                break

        if shown == 0:
            return "Workflows: none active."
    except Exception:
        lines.append("Workflows: could not be retrieved.")
    return "\n".join(lines)


def _build_doctype_context(
    doctype: str,
    user: str,
    max_rows: int = 20,
    allowed_fields: list[str] | None = None,
) -> str:
    """Return a sample of records from *doctype* that the user may read."""
    lines = []
    try:
        if not _has_read_permission(doctype, user):
            return f"DocType '{doctype}': no read permission for this user."

        meta = frappe.get_meta(doctype)
        title_field = meta.title_field or "name"
        fields = {"name", "modified"}

        if allowed_fields:
            for fieldname in allowed_fields:
                if fieldname in {"name", "modified"} or meta.get_field(fieldname):
                    fields.add(fieldname)
        else:
            fields.add(title_field)

        if title_field in fields:
            display_field = title_field
        else:
            display_field = "name"

        records = frappe.get_all(
            doctype,
            fields=list(fields),
            limit=max_rows,
            order_by="modified desc",
            ignore_permissions=False,
        )
        if records:
            lines.append(
                f"Recent records from '{doctype}' (up to {max_rows}, fetched {len(records)}; sample only, not total count):"
            )
            for r in records:
                summary = r.get(display_field) or r.get("name")
                extras: list[str] = []
                for fieldname in sorted(fields):
                    if fieldname in {"name", "modified", display_field}:
                        continue
                    value = r.get(fieldname)
                    if value in (None, ""):
                        continue
                    extras.append(f"{fieldname}: {value}")
                    if len(extras) >= 3:
                        break

                extra_text = f" | {', '.join(extras)}" if extras else ""
                lines.append(
                    f"  - {summary}{extra_text} (modified: {r.get('modified', '')})"
                )
            if len(records) >= max_rows:
                lines.append("  - Note: row cap reached; additional older records may exist.")
        else:
            lines.append(f"DocType '{doctype}': no records found (or all filtered by permissions).")
    except Exception as exc:
        lines.append(f"DocType '{doctype}': error reading records — {exc}")
    return "\n".join(lines)


def _detect_doctype_in_question(question: str) -> str | None:
    """
    Try to find an exact DocType name mentioned in *question*.
    Returns the first match or None.
    """
    try:
        doctypes = frappe.get_all("DocType", fields=["name"], filters={"issingle": 0})
        matches = _detect_doctypes_in_text(
            question,
            [dt["name"] for dt in doctypes if dt.get("name")],
            max_matches=1,
        )
        if matches:
            return matches[0]
    except Exception:
        pass
    return None


def _question_contains_arabic(question: str) -> bool:
    """Return True when question contains Arabic script characters."""
    return bool(re.search(r"[\u0600-\u06FF]", str(question or "")))


def _question_contains_latin(question: str) -> bool:
    """Return True when question contains Latin letters."""
    return bool(re.search(r"[A-Za-z]", str(question or "")))


def _question_contains_chinese(question: str) -> bool:
    """Return True when question contains CJK (Chinese/Japanese/Korean) characters."""
    return bool(re.search(r"[\u4E00-\u9FFF\u3400-\u4DBF]", str(question or "")))


def _normalize_language_hint(language_hint: str | None) -> str:
    """Normalize frontend/browser language hint to a short internal code."""
    value = str(language_hint or "").strip().lower()
    if not value:
        return ""
    if value.startswith("ar"):
        return "ar"
    if value.startswith("en"):
        return "en"
    if value.startswith("zh") or value in ("chinese", "mandarin", "cantonese"):
        return "zh"
    return ""


def _infer_user_language_from_history(history: list[dict] | None) -> str:
    """Infer latest explicit user language preference from chat history."""
    if not history:
        return ""

    for message in reversed(history):
        if str(message.get("role") or "").lower() != "user":
            continue
        content = str(message.get("content") or "")
        if not content.strip():
            continue
        if _question_contains_arabic(content):
            return "ar"
        if _question_contains_chinese(content):
            return "zh"
        if _question_contains_latin(content):
            return "en"

    return ""


def _normalize_learning_block_language(value: str | None) -> str:
    """Normalize AI Agent text-block language to detect/ar/en/zh."""
    key = str(value or "").strip().lower()
    if key.startswith("ar"):
        return "ar"
    if key.startswith("en"):
        return "en"
    if key.startswith("zh") or key in ("chinese", "mandarin", "cantonese"):
        return "zh"
    return "detect"


def _build_agent_learning_blocks(agent_doc, question: str, language_hint: str | None = None) -> str:
    """Build ordered AI Agent learning text blocks filtered by language and enabled flag."""
    if agent_doc is None:
        return ""

    rows = list(getattr(agent_doc, "learning_text_blocks", []) or [])
    if not rows:
        return ""

    hint = _normalize_language_hint(language_hint)
    question_language = "ar" if _question_contains_arabic(question) else ("en" if _question_contains_latin(question) else "")
    effective_language = hint or question_language

    enabled_rows = []
    for row in rows:
        if not _coerce_int(_row_value(row, "enabled", 1), default=1, minimum=0, maximum=1):
            continue

        block_text = str(_row_value(row, "text_block", "") or "").strip()
        if not block_text:
            continue

        row_language = _normalize_learning_block_language(_row_value(row, "language", "detect"))
        if row_language in ("ar", "en") and effective_language and row_language != effective_language:
            continue

        title = str(_row_value(row, "title", "") or "").strip() or "Block"
        priority = _coerce_int(_row_value(row, "priority", 10), default=10, minimum=0, maximum=9999)
        enabled_rows.append((priority, title, block_text))

    if not enabled_rows:
        return ""

    enabled_rows.sort(key=lambda item: (item[0], item[1].lower()))
    lines = ["Agent Learning Blocks (apply in order):"]
    for _, title, block_text in enabled_rows:
        lines.append(f"- [{title}] {block_text}")
    return "\n".join(lines)


def _build_response_language_instruction(
    question: str,
    language_hint: str | None = None,
    history: list[dict] | None = None,
) -> str:
    """Build strict language rule so replies follow user language reliably."""
    hint = _normalize_language_hint(language_hint)
    question_has_arabic = _question_contains_arabic(question)
    question_has_latin = _question_contains_latin(question)
    history_language = _infer_user_language_from_history(history)

    # Explicit selector from the user is authoritative.
    if hint == "ar":
        return (
            "Language rule: Reply strictly in Arabic. "
            "Use Arabic naturally and keep ERP DocType names in their canonical system names when needed."
        )

    if hint == "en":
        return "Language rule: Reply strictly in English unless the user explicitly asks to switch language."

    if hint == "zh":
        return (
            "Language rule: Reply strictly in Chinese (Simplified). "
            "Keep ERP DocType names in their canonical system names when needed."
        )

    # Detect mode: follow the current user message language first.
    if question_has_arabic and not question_has_latin:
        return (
            "Language rule: Reply strictly in Arabic. "
            "Keep ERP DocType names in their canonical system names when needed."
        )

    if _question_contains_chinese(question):
        return (
            "Language rule: Reply strictly in Chinese (Simplified). "
            "Keep ERP DocType names in their canonical system names when needed."
        )

    if question_has_latin and not question_has_arabic:
        return "Language rule: Reply strictly in English unless the user explicitly asks to switch language."

    # Mixed-language message: prefer Arabic when Arabic script is present.
    if question_has_arabic:
        return (
            "Language rule: Reply in Arabic (mixed-language user message detected). "
            "Keep ERP DocType names in their canonical system names when needed."
        )

    if question_has_latin:
        return "Language rule: Reply strictly in English unless the user explicitly asks to switch language."

    # Fallback for neutral messages (numbers/symbols): use recent history.
    if history_language == "ar":
        return "Language rule: Reply in Arabic because recent user messages are in Arabic."

    if history_language == "zh":
        return "Language rule: Reply in Chinese (Simplified) because recent user messages are in Chinese."

    if history_language == "en":
        return "Language rule: Reply in English because recent user messages are in English."

    return "Language rule: Reply in the same language as the latest user question."


def _get_default_company() -> str:
    """Return the primary company name configured on the site."""
    try:
        default_company = frappe.defaults.get_global_default("company")
        if default_company:
            return str(default_company).strip()
        companies = frappe.get_all("Company", filters={"is_group": 0}, fields=["name"], limit=1)
        if companies:
            return str(companies[0]["name"]).strip()
    except Exception:
        pass
    return ""


def _get_user_first_name(user: str) -> str:
    """Return the user's first name, falling back to the username part of their email."""
    try:
        first_name = frappe.db.get_value("User", user, "first_name")
        if first_name:
            return str(first_name).strip()
        full_name = frappe.db.get_value("User", user, "full_name")
        if full_name:
            parts = str(full_name).strip().split()
            if parts:
                return parts[0]
    except Exception:
        pass
    return str(user).split("@")[0].capitalize()


def _build_system_prompt(settings, user: str, agent_doc=None) -> str:
    """Return the system prompt to send to the AI."""
    role_list = ", ".join(frappe.get_roles(user)) or "no roles assigned"
    default_prompt = (
        "You are a helpful ERP assistant integrated into ERPNext v15. "
        "You have been given context about the current user's permissions, "
        "active workflows, and relevant database records. "
        "Answer questions clearly and concisely based on this context. "
        f"The current user is '{user}' with roles: {role_list}. "
        "If you do not have enough information to answer accurately, say so. "
        "Do not hallucinate or invent data."
    )

    base_prompt = _get_setting_text(settings, "system_prompt", default_prompt)
    if agent_doc is not None:
        agent_prompt = _get_setting_text(agent_doc, "system_prompt", "")
        if agent_prompt:
            base_prompt = agent_prompt

    company_name = _get_default_company()
    user_first_name = _get_user_first_name(user)

    sections = [base_prompt]
    sections.append(f"Current user context: '{user}' with roles: {role_list}.")

    # Inject live company name and user greeting so every model response knows the audience.
    identity_parts: list[str] = []
    if company_name:
        identity_parts.append(f"You represent and answer on behalf of the company: {company_name}.")
    if user_first_name:
        identity_parts.append(
            f"The current user's first name is {user_first_name}. "
            f"Address them as 'Mr. {user_first_name}' when greeting or when it feels natural to personalise the reply."
        )
    if identity_parts:
        sections.append(" ".join(identity_parts))

    sections.append(
        "Answer focus rule: Prefer one primary DocType that best matches the user's request. "
        "Only combine multiple DocTypes when the user explicitly asks for comparison or cross-document analysis."
    )
    sections.append(_get_agent_instruction_block(settings, agent=agent_doc))

    if agent_doc is not None:
        sections.append(f"Active AI Agent: {str(_row_value(agent_doc, 'agent_name') or _row_value(agent_doc, 'name') or '').strip()}.")

    if _user_has_ai_admin_role(user):
        sections.append(
            "The current user has the AI Admin role (super user for AI Assistant). "
            "You may perform advanced analysis, calculations, and suggest creating reports/dashboards when useful. "
            "Still answer only from the supplied context and ask one short clarifying question when needed."
        )

    return "\n\n".join(section for section in sections if section)


def _build_answer_mode_prompt(settings, answer_mode: str | None, agent_doc=None) -> tuple[str, str]:
    """Return the normalized answer mode and the instruction block for it."""
    configured_default = _normalize_answer_mode(
        _resolve_agent_or_setting(agent_doc, settings, "default_answer_mode", DEFAULT_ANSWER_MODE)
    )
    selected_mode = _normalize_answer_mode(answer_mode or configured_default)
    prompt = ANSWER_MODE_PROMPTS.get(selected_mode, ANSWER_MODE_PROMPTS[DEFAULT_ANSWER_MODE])
    return selected_mode, prompt


# ---------------------------------------------------------------------------
# AI provider call
# ---------------------------------------------------------------------------

def _extract_openai_response_text(response) -> str:
    """Extract message text from either modern or legacy OpenAI SDK responses."""
    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message else None
        if isinstance(content, str):
            return content.strip()

    if isinstance(response, dict):
        try:
            content = response.get("choices", [])[0].get("message", {}).get("content", "")
            if isinstance(content, str):
                return content.strip()
        except Exception:
            pass

    return ""

def _call_openai(settings, system_prompt: str, history: list[dict], question: str) -> str:
    """Call the OpenAI Chat Completions API and return the assistant reply."""
    import openai  # imported lazily so the app installs without openai present

    api_key = (settings.get_password("api_key") or "").strip()
    if not api_key:
        frappe.throw(_("AI Chat Settings: API key is required for OpenAI provider."))

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    model = settings.model or "gpt-4o-mini"
    max_tokens = int(settings.max_tokens or 1024)
    temperature = float(settings.temperature if settings.temperature is not None else 0.3)

    # Preferred path for modern SDK (openai>=1.x)
    client_cls = getattr(openai, "OpenAI", None)
    if callable(client_cls):
        client = client_cls(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = _extract_openai_response_text(response)
        if text:
            return text
        frappe.throw(_("OpenAI returned an empty response."))

    # Compatibility path for legacy SDK (openai<1.x)
    if hasattr(openai, "ChatCompletion"):
        openai.api_key = api_key
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = _extract_openai_response_text(response)
        if text:
            return text
        frappe.throw(_("OpenAI returned an empty response."))

    frappe.throw(_("Unsupported OpenAI SDK version installed on server."))


def _call_azure_openai(settings, system_prompt: str, history: list[dict], question: str) -> str:
    """Call Azure OpenAI Chat Completions endpoint."""
    import openai

    endpoint, api_key = _get_azure_openai_credentials(settings)

    client = openai.AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version="2024-02-01",
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    response = client.chat.completions.create(
        model=settings.model or "gpt-4o-mini",
        messages=messages,
        max_tokens=int(settings.max_tokens or 1024),
        temperature=float(settings.temperature if settings.temperature is not None else 0.3),
    )
    return response.choices[0].message.content.strip()


def _call_ollama(settings, system_prompt: str, history: list[dict], question: str) -> str:
    """Call a local Ollama instance via its REST API."""
    import requests

    base_url = settings.get_password("api_key") or "http://localhost:11434"
    base_url = base_url.rstrip("/")

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    resp = requests.post(
        f"{base_url}/api/chat",
        json={
            "model": settings.model or "llama3",
            "messages": messages,
            "stream": False,
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("message", {}).get("content", "").strip()


def _call_openai_transcription(
    settings,
    audio_bytes: bytes,
    mime_type: str | None = None,
    language: str | None = None,
) -> str:
    """Transcribe audio via OpenAI Speech-to-Text endpoint."""
    import openai

    api_key = (settings.get_password("api_key") or "").strip()
    if not api_key:
        frappe.throw(_("AI Chat Settings: API key is required for OpenAI transcription."))

    model_name = _get_setting_text(settings, "transcription_model", DEFAULT_TRANSCRIPTION_MODEL)
    normalized_language = _normalize_transcription_language(language)
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = f"speech.{_audio_extension_from_mime_type(mime_type)}"

    client_cls = getattr(openai, "OpenAI", None)
    if callable(client_cls):
        client = client_cls(api_key=api_key)
        payload = {
            "model": model_name,
            "file": audio_file,
        }
        if normalized_language:
            payload["language"] = normalized_language

        response = client.audio.transcriptions.create(**payload)
        text = _extract_transcription_text(response)
        if text:
            return text
        frappe.throw(_("Transcription returned an empty response."))

    if hasattr(openai, "Audio") and hasattr(openai.Audio, "transcribe"):
        openai.api_key = api_key
        payload = {
            "model": model_name,
            "file": audio_file,
        }
        if normalized_language:
            payload["language"] = normalized_language

        try:
            response = openai.Audio.transcribe(**payload)
        except TypeError:
            if normalized_language:
                response = openai.Audio.transcribe(model_name, audio_file, language=normalized_language)
            else:
                response = openai.Audio.transcribe(model_name, audio_file)

        text = _extract_transcription_text(response)
        if text:
            return text
        frappe.throw(_("Transcription returned an empty response."))

    frappe.throw(_("Unsupported OpenAI SDK version installed on server."))


def _call_azure_openai_transcription(
    settings,
    audio_bytes: bytes,
    mime_type: str | None = None,
    language: str | None = None,
) -> str:
    """Transcribe audio via Azure OpenAI speech endpoint."""
    import openai

    endpoint, api_key = _get_azure_openai_credentials(settings)
    if not endpoint or not api_key:
        frappe.throw(
            _(
                "Azure OpenAI transcription requires endpoint and API key. "
                "Store them as '<endpoint>||<api_key>' in AI Chat Settings API key."
            )
        )

    model_name = _get_setting_text(settings, "transcription_model", DEFAULT_TRANSCRIPTION_MODEL)
    normalized_language = _normalize_transcription_language(language)
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = f"speech.{_audio_extension_from_mime_type(mime_type)}"

    client = openai.AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version="2024-02-01",
    )

    payload = {
        "model": model_name,
        "file": audio_file,
    }
    if normalized_language:
        payload["language"] = normalized_language

    response = client.audio.transcriptions.create(**payload)
    text = _extract_transcription_text(response)
    if text:
        return text

    frappe.throw(_("Transcription returned an empty response."))


def _call_ai(settings, system_prompt: str, history: list[dict], question: str) -> str:
    provider = (settings.ai_provider or "OpenAI").strip()
    if provider == "OpenAI":
        return _call_openai(settings, system_prompt, history, question)
    elif provider == "Azure OpenAI":
        return _call_azure_openai(settings, system_prompt, history, question)
    elif provider.startswith("Ollama"):
        return _call_ollama(settings, system_prompt, history, question)
    else:
        frappe.throw(_("Unknown AI provider: {0}").format(provider))


# ---------------------------------------------------------------------------
# Public API endpoint
# ---------------------------------------------------------------------------

@frappe.whitelist()
def send_message(
    question: str,
    session_id: str | None = None,
    history: str | None = None,
    answer_mode: str | None = None,
    language_hint: str | None = None,
):
    """
    Send a message to the AI assistant and return its reply.

    Parameters
    ----------
    question : str
        The user's question.
    session_id : str, optional
        An opaque session identifier so the front-end can pass conversation
        history.  A new UUID is generated if not supplied.
    history : str, optional
        JSON-encoded list of ``{"role": "user"|"assistant", "content": "..."}``
        objects representing prior turns (most-recent last).  The list is
        capped at the last 10 turns to avoid excessive token usage.
    answer_mode : str, optional
        Selected response shape. Supported values are ``guide``, ``summary``,
        and ``general`` (or numeric aliases ``1``, ``2``, ``3``).
    language_hint : str, optional
        Frontend/browser language hint (e.g., ``ar``, ``ar-SA``, ``en``, ``en-US``)
        used to keep response language stable across turns.

    Returns
    -------
    dict
        ``{"reply": str, "session_id": str, "actions": list, "topic_options": list}``
    """
    user = _require_authenticated_user()

    if not question or not question.strip():
        frappe.throw(_("Question cannot be empty."))
    question = question.strip()
    if len(question) > MAX_QUESTION_CHARS:
        frappe.throw(_("Question is too long. Please keep it under {0} characters.").format(MAX_QUESTION_CHARS))

    session_id = _sanitize_session_id(session_id)

    # Parse history with strict role/content validation.
    parsed_history = _sanitize_history(history)

    # Load settings
    try:
        settings = frappe.get_single("AI Chat Settings")
    except Exception:
        frappe.throw(
            _("AI Chat Settings not configured. Please ask your System Manager to set up the AI Assistant.")
        )

    if not settings.model:
        frappe.throw(_("AI Chat Settings: model name is required."))

    agent_doc = _resolve_user_agent(user, settings=settings)
    if not _user_can_access_widget(user, settings=settings, agent_doc=agent_doc):
        frappe.throw(
            _(
                "You do not have access to the AI chat widget. "
                "Ask your administrator to assign an allowed AI Role in AI Agent."
            ),
            frappe.PermissionError,
        )

    selected_answer_mode, answer_mode_prompt = _build_answer_mode_prompt(
        settings,
        answer_mode,
        agent_doc=agent_doc,
    )

    requests_per_minute = _coerce_int(
        getattr(settings, "requests_per_minute", None),
        default=DEFAULT_REQUESTS_PER_MINUTE,
        minimum=1,
        maximum=300,
    )
    _enforce_user_rate_limit(user, requests_per_minute)

    is_ai_admin = _user_has_ai_admin_role(user)

    raw_require_policy = bool(
        _coerce_int(
            _resolve_agent_or_setting(agent_doc, settings, "require_data_source_policy", 0),
            default=0,
            minimum=0,
            maximum=1,
        )
    )
    policy_map = _load_user_policy_map(user, agent_doc=agent_doc)
    require_policy = raw_require_policy and bool(policy_map) and not is_ai_admin

    # Testing stage behavior: AI Admin should not be restricted by allowlist rows.
    if is_ai_admin:
        policy_map = {}

    include_permissions_flag = bool(
        _coerce_int(
            _resolve_agent_or_setting(agent_doc, settings, "include_permissions", getattr(settings, "include_permissions", 1)),
            default=1,
            minimum=0,
            maximum=1,
        )
    )
    include_workflows_flag = bool(
        _coerce_int(
            _resolve_agent_or_setting(agent_doc, settings, "include_workflows", getattr(settings, "include_workflows", 1)),
            default=1,
            minimum=0,
            maximum=1,
        )
    )

    # Build context
    context_parts: list[str] = [f"Selected answer mode: {selected_answer_mode}.", answer_mode_prompt]

    if agent_doc is not None:
        context_parts.append(
            "Active Agent: "
            + str(_row_value(agent_doc, "agent_name") or _row_value(agent_doc, "name") or "Unnamed Agent")
        )

    all_permitted_index = _get_user_permitted_doctype_index(user)
    permitted_index = list(all_permitted_index)
    if require_policy:
        permitted_index = [
            item
            for item in permitted_index
            if item["doctype"] in policy_map and policy_map[item["doctype"]].get("allow_in_context", True)
        ]
    else:
        permitted_index = [
            item
            for item in permitted_index
            if policy_map.get(item["doctype"], {}).get("allow_in_context", True)
        ]

    permitted_dts = [item["doctype"] for item in permitted_index]
    navigation_dts = [item["doctype"] for item in all_permitted_index]

    if include_permissions_flag:
        context_parts.append(_build_permission_context(user))

    if include_workflows_flag:
        context_parts.append(_build_workflow_context(user=user, max_rows=10))

    context_parts.append(_build_module_scope_context(permitted_index))
    context_parts.append(_build_installed_apps_context())

    if raw_require_policy and not require_policy:
        if is_ai_admin:
            context_parts.append("Policy Enforcement: AI Admin role bypassed data source policy restrictions for testing.")
        elif not policy_map:
            context_parts.append("Policy Enforcement: enabled but no AI Agent data sources are configured, so allowlist enforcement was skipped.")
    elif require_policy and not policy_map:
        context_parts.append(
            "Policy Enforcement: enabled. No active data source policies matched your roles, so direct DB context is disabled."
        )

    # Query broker: resolve and fetch multiple explicit DocTypes safely.
    max_db_rows = _coerce_int(
        _resolve_agent_or_setting(agent_doc, settings, "max_db_rows", getattr(settings, "max_db_rows", None)),
        default=DEFAULT_MAX_DB_ROWS,
        minimum=1,
        maximum=100,
    )
    max_tool_doctypes = _coerce_int(
        _resolve_agent_or_setting(
            agent_doc,
            settings,
            "max_tool_doctypes",
            getattr(settings, "max_tool_doctypes", None),
        ),
        default=DEFAULT_MAX_TOOL_DOCTYPES,
        minimum=1,
        maximum=10,
    )

    if _question_requests_exhaustive(question):
        max_db_rows = max(max_db_rows, 100)
        max_tool_doctypes = max(max_tool_doctypes, 8)
        context_parts.append(
            "Fetch Mode: exhaustive information request detected, so broader context retrieval limits were applied."
        )

    broker_context, mentioned_dts = _build_query_broker_context(
        question=question,
        user=user,
        permitted_doctypes=permitted_dts,
        max_db_rows=max_db_rows,
        max_tool_doctypes=max_tool_doctypes,
        policy_map=policy_map,
        require_policy=require_policy,
    )
    context_parts.append(broker_context)

    aggregate_scan_rows = _coerce_int(
        getattr(settings, "max_aggregate_scan_rows", None),
        default=DEFAULT_MAX_AGGREGATE_SCAN_ROWS,
        minimum=200,
        maximum=MAX_AGGREGATE_SCAN_ROWS_CAP,
    )
    sales_aggregate_context = _build_sales_totals_context(
        question=question,
        user=user,
        max_scan_rows=aggregate_scan_rows,
    )
    if sales_aggregate_context:
        context_parts.append(sales_aggregate_context)

    ledger_aggregate_context = _build_ledger_totals_context(
        question=question,
        user=user,
        max_scan_rows=aggregate_scan_rows,
    )
    if ledger_aggregate_context:
        context_parts.append(ledger_aggregate_context)

    bank_balance_context = _build_bank_balance_context(
        question=question,
        user=user,
        max_scan_rows=aggregate_scan_rows,
    )
    if bank_balance_context:
        context_parts.append(bank_balance_context)

    user_stats_context = _build_user_stats_context(question=question, user=user)
    if user_stats_context:
        context_parts.append(user_stats_context)

    purchase_project_context = _build_purchase_by_project_context(
        question=question,
        user=user,
        max_scan_rows=aggregate_scan_rows,
    )
    if purchase_project_context:
        context_parts.append(purchase_project_context)

    doctype_count_context = _build_doctype_count_context(
        question=question,
        user=user,
        doctypes=mentioned_dts,
        max_scan_rows=aggregate_scan_rows,
    )
    if doctype_count_context:
        context_parts.append(doctype_count_context)

    # Keep answer focused when we already detected specific DocTypes.
    if mentioned_dts:
        context_parts.append(
            "STRICT FOCUS — Reply ONLY from the data of: "
            + ", ".join(mentioned_dts)
            + ". Do NOT mention, reference, suggest, or offer help about any other DocType "
            "unless the user explicitly asks about it in this message."
        )
    else:
        context_parts.append(
            "Readable DocTypes for this user: "
            + (", ".join(permitted_dts[:30]) or "none")
            + ("..." if len(permitted_dts) > 30 else "")
        )

    context_string = "\n\n".join(filter(None, context_parts))

    # Compose the question with injected context
    augmented_question = f"{question}\n\n---\nSystem Context:\n{context_string}"

    system_prompt = _build_system_prompt(settings, user, agent_doc=agent_doc)
    learning_blocks_prompt = _build_agent_learning_blocks(agent_doc, question, language_hint=language_hint)
    response_language_instruction = _build_response_language_instruction(
        question,
        language_hint=language_hint,
        history=parsed_history,
    )
    system_prompt = (
        f"{system_prompt}\n\n"
        + (f"{learning_blocks_prompt}\n\n" if learning_blocks_prompt else "")
        +
        f"{response_language_instruction}\n\n"
        f"Current answer mode: {selected_answer_mode}.\n"
        f"{answer_mode_prompt}\n\n"
        "If the user wants to switch answer style, offer these interactive options exactly as written below:\n"
        f"{_get_answer_mode_text_block(settings, agent=agent_doc)}\n\n"
        "When you want to offer the user a choice of topics or sub-questions, always use a simple numbered list "
        "(1. option\n2. option ...) on separate lines so the interface can render them as interactive buttons. "
        "Do NOT ask \"which one?\" or \"select one\" after the list — the interface handles selection automatically."
    )

    reply = ""
    error = ""
    actions: list[dict] = []

    nav_handled, nav_reply, nav_actions = _build_navigation_actions(
        question=question,
        user=user,
        permitted_doctypes=navigation_dts,
        language_hint=language_hint,
    )

    region_handled = _is_regions_recommendation_question(question)
    region_reply = _build_regions_recommendation_reply(question, language_hint=language_hint) if region_handled else ""

    project_handled = _is_new_project_question(question)
    project_reply = _build_new_project_structured_reply(question, language_hint=language_hint) if project_handled else ""

    if nav_handled:
        reply = nav_reply
        actions = nav_actions
    elif region_handled:
        reply = region_reply
        actions = []
    elif project_handled:
        reply = project_reply
        actions = []
    else:
        # Intercept live numeric intents (customer/supplier receivable/payable + top entities)
        # and execute deterministic ai_agent_core path before generic LLM flow.
        lowered_question = (question or "").lower()
        normalized_question = lowered_question.replace("balanace", "balance")

        live_numeric_keywords = [
            # customer/receivable
            "customer balance",
            "receivable balance",
            "total receivable balance",
            "accounts receivable",
            "total customer balance",
            "outstanding receivable",
            # supplier/payable
            "supplier balance",
            "suppliers balance",
            "supplier balances",
            "payable balance",
            "total payable balance",
            "accounts payable",
            "accounts payable summary",
            "payable summary",
            "total supplier balance",
            "outstanding payable",
            "outstanding payables",
            "payables",
            "supplier balanace",
            "suppliers balanace",
            "payable balanace",
            "supplier outstanding",
            "suppliers outstanding",
            "supplier/payables",
            "supplier payables",
            "اجمالي رصيد الموردين",
            "إجمالي رصيد الموردين",
            "رصيد الموردين",
            "رصيد مورد",
            "ملخص الذمم الدائنة",
            "ملخص الدائنين",
            # ranking
            "top customers",
            "best customers",
            "largest customers",
            "most valuable customers",
            "top suppliers",
            "best suppliers",
            "largest suppliers",
            "most valuable suppliers",
            "top suppliers by outstanding",
            "suppliers with outstanding",
            "top payable suppliers",
            "highest payable suppliers",
            "الموردين اللي ليهم فلوس",
            "الموردين الذين لهم رصيد مستحق",
            "أعلى الموردين مديونية",
            "اعلى الموردين مديونية",
            "أفضل العملاء",
            "اكبر العملاء",
            "أفضل الموردين",
            "اكبر الموردين",
        ]

        should_use_agent_core = any(k in normalized_question for k in live_numeric_keywords)

        if should_use_agent_core:
            try:
                agent = AIAgentCore()
                handler_result = agent.handle_query(question)

                if isinstance(handler_result, str):
                    reply = handler_result
                elif isinstance(handler_result, dict):
                    reply = handler_result.get("message") or str(handler_result)
                else:
                    reply = str(handler_result)
            except Exception as exc:
                error = str(exc)[:300]
                frappe.log_error(title="AI AgentCore Error", message=traceback.format_exc())

                # Keep explicit failure contract for numeric-live path
                reply = (
                    "I cannot return an accurate live value right now because live database access/query failed. "
                    f"Reason: {error}"
                )
        else:
            try:
                reply = _call_ai(settings, system_prompt, parsed_history, augmented_question)
            except Exception as exc:
                error = str(exc)[:300]
                frappe.log_error(title="AI Chat Error", message=traceback.format_exc())
                reply = _humanize_ai_error(exc)

            if not str(reply or "").strip():
                reply = _build_data_first_fallback_reply(
                    question=question,
                    language_hint=language_hint,
                    mentioned_doctypes=mentioned_dts,
                )

    # Extract numbered pick-lists from the reply and convert them to interactive chips.
    reply, inline_options = _extract_inline_options(reply)

    topic_seed_doctypes = list(mentioned_dts)
    for action in actions:
        if str(action.get("action") or "").strip().lower() != "open_doctype":
            continue
        action_dt = str(action.get("doctype") or "").strip()
        if action_dt:
            topic_seed_doctypes.append(action_dt)

    topic_options = _build_interactive_topic_options(
        question=question,
        mentioned_doctypes=topic_seed_doctypes,
        permitted_doctypes=navigation_dts,
        language_hint=language_hint,
    )

    context_audit = _build_context_audit_summary(
        user=user,
        include_permissions=include_permissions_flag,
        include_workflows=include_workflows_flag,
        mentioned_doctypes=mentioned_dts,
        permitted_doctype_count=len(permitted_dts),
        context_string=context_string,
        policy_enforced=require_policy,
        answer_mode=selected_answer_mode,
    )

    # Persist log
    try:
        log = frappe.get_doc(
            {
                "doctype": "AI Chat Log",
                "user": user,
                "session_id": session_id,
                "question": question,
                "answer": reply,
                "context_used": context_audit,
                "error": error,
            }
        )
        log.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(title="AI Chat Log Error", message=traceback.format_exc())

    return {
        "reply": reply,
        "session_id": session_id,
        "actions": actions,
        "topic_options": topic_options,
        "inline_options": inline_options,
    }


@frappe.whitelist()
def transcribe_audio(audio_base64: str, mime_type: str | None = None, language: str | None = None):
    """Transcribe a short recorded audio clip into text for chat input."""
    user = _require_authenticated_user()

    if not audio_base64:
        frappe.throw(_("Audio payload is required."))

    if len(str(audio_base64)) > MAX_TRANSCRIBE_AUDIO_BASE64_CHARS:
        frappe.throw(_("Audio clip is too large. Please keep it under 10 seconds."))

    try:
        settings = frappe.get_single("AI Chat Settings")
    except Exception:
        frappe.throw(
            _("AI Chat Settings not configured. Please ask your System Manager to set up the AI Assistant.")
        )

    agent_doc = _resolve_user_agent(user, settings=settings)
    if not _user_can_access_widget(user, settings=settings, agent_doc=agent_doc):
        frappe.throw(
            _(
                "You do not have access to the AI chat widget. "
                "Ask your administrator to assign an allowed AI Role in AI Agent."
            ),
            frappe.PermissionError,
        )

    requests_per_minute = _coerce_int(
        getattr(settings, "requests_per_minute", None),
        default=DEFAULT_REQUESTS_PER_MINUTE,
        minimum=1,
        maximum=300,
    )
    _enforce_user_rate_limit(user, requests_per_minute)

    audio_bytes = _decode_audio_base64(audio_base64)
    if not audio_bytes:
        frappe.throw(_("No audio detected. Please record and try again."))
    if len(audio_bytes) > MAX_TRANSCRIBE_AUDIO_BYTES:
        frappe.throw(_("Audio clip is too large. Please keep it under 10 seconds."))

    provider = (settings.ai_provider or "OpenAI").strip()

    try:
        if provider == "OpenAI":
            text = _call_openai_transcription(
                settings,
                audio_bytes,
                mime_type=mime_type,
                language=language,
            )
        elif provider == "Azure OpenAI":
            text = _call_azure_openai_transcription(
                settings,
                audio_bytes,
                mime_type=mime_type,
                language=language,
            )
        else:
            frappe.throw(_("Voice transcription currently supports OpenAI and Azure OpenAI providers only."))
    except Exception as exc:
        validation_error_cls = getattr(frappe, "ValidationError", None)
        if validation_error_cls and isinstance(exc, validation_error_cls):
            raise

        frappe.log_error(title="AI Transcription Error", message=traceback.format_exc())
        frappe.throw(_humanize_transcription_error(exc))

    clean_text = str(text or "").strip()
    if not clean_text:
        frappe.throw(_("No speech could be recognized in this recording."))

    return {"text": clean_text}


@frappe.whitelist()
def get_chat_history(session_id: str | None = None, limit: int = 20):
    """
    Return the chat history for the current user.

    Parameters
    ----------
    session_id : str, optional
        Restrict results to a single session.
    limit : int
        Maximum number of log entries to return (default 20, max 100).

    Returns
    -------
    list[dict]
    """
    user = _require_authenticated_user()
    limit = _coerce_int(limit, default=20, minimum=1, maximum=100)

    filters = {"user": user}
    if session_id:
        filters["session_id"] = _sanitize_session_id(session_id)

    logs = frappe.get_all(
        "AI Chat Log",
        filters=filters,
        fields=["name", "question", "answer", "session_id", "creation", "error"],
        order_by="creation asc",
        limit=limit,
    )
    return logs


@frappe.whitelist()
def get_accessible_doctypes(limit: int = 200, search: str | None = None):
    """Return DocTypes/modules the current user can read across installed apps."""
    user = _require_authenticated_user()

    limit = _coerce_int(limit, default=200, minimum=1, maximum=500)
    query = (search or "").strip().lower()
    require_policy = False
    settings = None

    try:
        settings = frappe.get_single("AI Chat Settings")
        require_policy = bool(
            _coerce_int(getattr(settings, "require_data_source_policy", 0), default=0, minimum=0, maximum=1)
        )
    except Exception:
        pass

    agent_doc = _resolve_user_agent(user, settings=settings)
    if not _user_can_access_widget(user, settings=settings, agent_doc=agent_doc):
        return []

    require_policy = bool(
        _coerce_int(
            _resolve_agent_or_setting(agent_doc, settings, "require_data_source_policy", int(require_policy)),
            default=int(require_policy),
            minimum=0,
            maximum=1,
        )
    )

    policy_map = _load_user_policy_map(user, agent_doc=agent_doc)
    is_ai_admin = _user_has_ai_admin_role(user)
    require_policy = require_policy and bool(policy_map) and not is_ai_admin

    if is_ai_admin:
        policy_map = {}

    rows = _get_user_permitted_doctype_index(user)
    if require_policy:
        rows = [
            row
            for row in rows
            if row.get("doctype") in policy_map and policy_map[row.get("doctype")].get("allow_in_context", True)
        ]

    enriched = []
    for row in rows:
        dt = row.get("doctype")
        policy = policy_map.get(dt, {})
        enriched.append(
            {
                "doctype": dt,
                "module": row.get("module"),
                "policy_applied": bool(policy),
                "allowed_fields": policy.get("allowed_fields", []),
                "policy_max_rows": policy.get("max_rows"),
            }
        )

    if query:
        enriched = [
            row
            for row in enriched
            if query in row.get("doctype", "").lower() or query in row.get("module", "").lower()
        ]

    return enriched[:limit]


@frappe.whitelist()
def get_chat_preferences():
    """Return non-sensitive chat UI preferences for the current user."""
    user = _require_authenticated_user()

    default_answer_mode = DEFAULT_ANSWER_MODE
    answer_mode_text_block = DEFAULT_ANSWER_MODE_TEXT_BLOCK
    settings = None

    try:
        settings = frappe.get_single("AI Chat Settings")
    except Exception:
        pass

    agent_doc = _resolve_user_agent(user, settings=settings)
    widget_enabled = _user_can_access_widget(user, settings=settings, agent_doc=agent_doc)

    default_answer_mode = _normalize_answer_mode(
        _resolve_agent_or_setting(agent_doc, settings, "default_answer_mode", DEFAULT_ANSWER_MODE)
    )
    answer_mode_text_block = _get_answer_mode_text_block(settings, agent=agent_doc)

    agent_name = None
    agent_title = None
    quick_actions = []
    suggested_actions = []
    if agent_doc is not None:
        agent_name = str(_row_value(agent_doc, "agent_name") or _row_value(agent_doc, "name") or "").strip() or None
        agent_title = str(_row_value(agent_doc, "title") or "").strip() or None
        blocks = []
        for row in getattr(agent_doc, "learning_text_blocks", []) or []:
            if not _coerce_int(_row_value(row, "enabled", 1), default=1, minimum=0, maximum=1):
                continue
            title = str(_row_value(row, "title", "") or "").strip()
            if not title:
                continue
            priority = _coerce_int(_row_value(row, "priority", 10), default=10, minimum=0, maximum=9999)
            quick = _coerce_int(_row_value(row, "quick", 0), default=0, minimum=0, maximum=1)
            suggest = _coerce_int(_row_value(row, "suggest", 0), default=0, minimum=0, maximum=1)
            blocks.append({"title": title, "priority": priority, "quick": quick, "suggest": suggest})
        blocks.sort(key=lambda b: (b["priority"], b["title"].lower()))
        quick_actions = [b["title"] for b in blocks if b["quick"]]
        suggested_actions = [b["title"] for b in blocks if b["suggest"] and not b["quick"]]

    return {
        "default_answer_mode": default_answer_mode,
        "answer_mode_text_block": answer_mode_text_block,
        "answer_modes": [dict(mode) for mode in ANSWER_MODES],
        "widget_enabled": widget_enabled,
        "agent_name": agent_name,
        "agent_title": agent_title,
        "quick_actions": quick_actions,
        "suggested_actions": suggested_actions,
        "ai_admin_enabled": _user_has_ai_admin_role(user),
    }
