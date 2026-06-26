"""Runtime-контекст для Cursor SDK custom tool callbacks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.tool_policy import ToolPolicy


@dataclass(slots=True)
class CursorSdkToolRuntime:
    """Контекст одного agent-turn для Odysseus tools через Cursor SDK."""

    session_id: str | None = None
    owner: str | None = None
    workspace: str | None = None
    disabled_tools: frozenset[str] = frozenset()
    relevant_tools: frozenset[str] | None = None
    tool_policy: ToolPolicy | None = None
    events: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    tools_executed: set[str] = field(default_factory=set)
    round_num: int = 1
    tool_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class RuntimeHolder:
    """Ссылка на активный runtime для callback-потока Cursor SDK bridge."""

    runtime: CursorSdkToolRuntime | None = None


def bind_agent_runtime(agent: Any, runtime: CursorSdkToolRuntime | None) -> None:
    """Привязывает runtime к agent до завершения Send/run."""
    holder = getattr(agent, "_odysseus_runtime_holder", None)
    if isinstance(holder, RuntimeHolder):
        holder.runtime = runtime


def resolve_runtime(holder: RuntimeHolder | None) -> CursorSdkToolRuntime | None:
    """Возвращает runtime из holder, если он задан."""
    if holder is None:
        return None
    return holder.runtime


def record_persisted_tool_event(
    runtime: CursorSdkToolRuntime,
    *,
    tool_name: str,
    command: str,
    output_text: str,
    result: dict[str, Any],
) -> None:
    """Добавляет tool_event в том же формате, что agent_loop для reload UI."""
    from src.tool_utils import _truncate

    tool_event: dict[str, Any] = {
        "round": runtime.round_num,
        "tool": tool_name,
        "command": command,
        "output": _truncate(output_text),
        "exit_code": result.get("exit_code"),
    }
    if result.get("image_url"):
        for key in ("image_url", "image_prompt", "image_model", "image_size", "image_quality"):
            if result.get(key):
                tool_event[key] = result[key]
    if result.get("doc_id"):
        tool_event["doc_id"] = result["doc_id"]
        tool_event["doc_title"] = result.get("title", "")
    if result.get("diff"):
        tool_event["diff"] = result["diff"]
    runtime.tool_events.append(tool_event)


def is_tool_allowed(name: str, runtime: CursorSdkToolRuntime) -> bool:
    """Проверяет, доступен ли tool в текущем turn."""
    if name in runtime.disabled_tools:
        return False
    if runtime.tool_policy and runtime.tool_policy.blocks(name):
        return False
    # Cursor SDK регистрирует все custom_tools на agent заранее. Per-turn RAG
    # (relevant_tools) не должен блокировать execute — иначе модель видит tool,
    # вызывает его и получает «недоступен в этом turn» (ui_control и др.).
    from src.cursor_sdk.custom_tools import CURSOR_SDK_TOOL_NAMES

    if name in CURSOR_SDK_TOOL_NAMES:
        return True
    if runtime.relevant_tools is not None and name not in runtime.relevant_tools:
        return False
    return True
