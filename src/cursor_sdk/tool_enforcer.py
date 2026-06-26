"""Принудительное использование Odysseus tools при Cursor SDK provider."""

import json
import re
from collections.abc import Iterable, Sequence

from src.action_intents import ToolIntent, classify_tool_intent
from src.agent_tools import ToolBlock
from src.tool_policy import ToolPolicy

CURSOR_SDK_TOOL_DIRECTIVE = (
    "\n\n---\n"
    "CURSOR SDK AGENT MODE: Odysseus tools подключены как native custom tools. "
    "Для factual, weather, news, calendar, inbox и других live-data запросов "
    "вызывай matching tool (web_search, manage_calendar, list_emails и т.д.) "
    "до финального ответа. Не выдумывай факты и не пиши, что «уже проверил», "
    "если tool не был вызван или его результат не в контексте."
)

_LIVE_FACTUAL_RE = re.compile(
    r"\b(?:"
    r"latest|current(?:ly)?|today|tomorrow|yesterday|right now|breaking|"
    r"news|weather|forecast|temperature|price of|stock|score|who (?:is|won)|"
    r"release date|this week|this month|as of|202[4-9]"
    r")\b",
    re.I,
)
_LIVE_FACTUAL_RU_RE = re.compile(
    r"\b(?:погод|новост|курс|цена|сегодня|завтра|сейчас|прогноз)\b",
    re.I,
)
_EXPLICIT_URL_RE = re.compile(
    r"https?://[^\s<>\"']+|www\.[^\s<>\"']+",
    re.I,
)
_CALENDAR_LOOKUP_HINTS = ("lookup", "availability", "agenda", "question")
_CALENDAR_ACTION_HINTS = ("action request", "imperative", "put item")
_EMAIL_LIST_HINTS = ("check", "unread", "inbox")


def inject_cursor_sdk_tool_directive(messages: list[dict]) -> list[dict]:
    """Добавляет директиву про fenced tools в system prompt."""
    if not messages:
        return [{"role": "system", "content": CURSOR_SDK_TOOL_DIRECTIVE.strip()}]
    out = [dict(m) for m in messages]
    if out[0].get("role") == "system":
        out[0]["content"] = (out[0].get("content") or "") + CURSOR_SDK_TOOL_DIRECTIVE
    else:
        out.insert(0, {"role": "system", "content": CURSOR_SDK_TOOL_DIRECTIVE.strip()})
    return out


def _tool_available(
    name: str,
    *,
    relevant_tools: set[str] | None,
    disabled_tools: set[str] | None,
    tool_policy: ToolPolicy | None,
) -> bool:
    """Проверяет, можно ли вызвать tool в этом turn."""
    if disabled_tools and name in disabled_tools:
        return False
    if tool_policy and tool_policy.blocks(name):
        return False
    if relevant_tools is not None and name not in relevant_tools:
        return False
    return True


def _is_calendar_lookup(intent: ToolIntent) -> bool:
    """True, если запрос про чтение календаря, а не создание события."""
    if intent.category != "calendar":
        return False
    reason = intent.reason.lower()
    if any(h in reason for h in _CALENDAR_ACTION_HINTS):
        return False
    return any(h in reason for h in _CALENDAR_LOOKUP_HINTS)


def _is_email_list_intent(intent: ToolIntent) -> bool:
    """True для проверки inbox/unread, не для compose/reply."""
    if intent.category != "email":
        return False
    reason = intent.reason.lower()
    if "composition" in reason or "contact request" in reason:
        return False
    if "reply" in reason and "check" not in reason:
        return False
    return any(h in reason for h in _EMAIL_LIST_HINTS) or "email action" not in reason


def _needs_web_search(
    user_message: str,
    intent: ToolIntent,
    *,
    forced_tools: set[str] | None,
) -> bool:
    """Нужен ли обязательный web_search для этого сообщения."""
    if forced_tools and "web_search" in forced_tools:
        return True
    if intent.category == "web":
        return True
    text = user_message.strip()
    if not text:
        return False
    if _LIVE_FACTUAL_RE.search(text) or _LIVE_FACTUAL_RU_RE.search(text):
        return True
    lowered = text.lower()
    if "?" in text and any(
        lowered.startswith(prefix)
        for prefix in ("what", "who", "when", "where", "how much", "how many", "как", "какая", "какой", "сколько")
    ):
        return True
    return False


def _explicit_url(user_message: str) -> str | None:
    """Извлекает URL из сообщения для web_fetch."""
    match = _EXPLICIT_URL_RE.search(user_message)
    if not match:
        return None
    url = match.group(0).rstrip(".,);]")
    if url.lower().startswith("www."):
        return f"https://{url}"
    return url


def plan_mandatory_tool_blocks(
    *,
    user_message: str,
    relevant_tools: set[str] | None,
    disabled_tools: set[str] | None,
    tool_policy: ToolPolicy | None,
    forced_tools: set[str] | None = None,
    executed_tools: Iterable[str] | None = None,
) -> list[ToolBlock]:
    """Планирует Odysseus tools, которые нужно выполнить принудительно.

    Args:
        user_message: Последнее сообщение пользователя.
        relevant_tools: RAG/selection набор tools для turn.
        disabled_tools: Отключённые tools.
        tool_policy: Политика доступа.
        forced_tools: Явно включённые tools (например Search toggle).
        executed_tools: Уже выполненные в этом turn — не дублировать.

    Returns:
        Список ToolBlock для server-side выполнения.
    """
    if not user_message.strip():
        return []

    done = set(executed_tools or ())
    intent = classify_tool_intent(user_message)
    blocks: list[ToolBlock] = []

    url = _explicit_url(user_message)
    if (
        url
        and "web_fetch" not in done
        and _tool_available(
            "web_fetch",
            relevant_tools=relevant_tools,
            disabled_tools=disabled_tools,
            tool_policy=tool_policy,
        )
    ):
        blocks.append(ToolBlock("web_fetch", url))
        return blocks

    if (
        "web_search" not in done
        and _needs_web_search(user_message, intent, forced_tools=forced_tools)
        and _tool_available(
            "web_search",
            relevant_tools=relevant_tools,
            disabled_tools=disabled_tools,
            tool_policy=tool_policy,
        )
    ):
        blocks.append(ToolBlock("web_search", user_message.strip()))

    if (
        "manage_calendar" not in done
        and _is_calendar_lookup(intent)
        and _tool_available(
            "manage_calendar",
            relevant_tools=relevant_tools,
            disabled_tools=disabled_tools,
            tool_policy=tool_policy,
        )
    ):
        blocks.append(
            ToolBlock("manage_calendar", json.dumps({"action": "list_events"}, ensure_ascii=False))
        )

    if (
        "list_emails" not in done
        and _is_email_list_intent(intent)
        and _tool_available(
            "list_emails",
            relevant_tools=relevant_tools,
            disabled_tools=disabled_tools,
            tool_policy=tool_policy,
        )
    ):
        unread_only = "unread" in user_message.lower()
        blocks.append(
            ToolBlock(
                "list_emails",
                json.dumps({"max_results": 20, "unread_only": unread_only}, ensure_ascii=False),
            )
        )

    if (
        "trigger_research" not in done
        and intent.category == "research"
        and _tool_available(
            "trigger_research",
            relevant_tools=relevant_tools,
            disabled_tools=disabled_tools,
            tool_policy=tool_policy,
        )
    ):
        blocks.append(ToolBlock("trigger_research", user_message.strip()))

    return blocks


def plan_postflight_tool_blocks(
    *,
    user_message: str,
    relevant_tools: set[str] | None,
    disabled_tools: set[str] | None,
    tool_policy: ToolPolicy | None,
    forced_tools: set[str] | None,
    executed_tools: Iterable[str],
) -> list[ToolBlock]:
    """Планирует postflight tools, если модель завершила round без вызова tools."""
    return plan_mandatory_tool_blocks(
        user_message=user_message,
        relevant_tools=relevant_tools,
        disabled_tools=disabled_tools,
        tool_policy=tool_policy,
        forced_tools=forced_tools,
        executed_tools=executed_tools,
    )
