"""Odysseus tools как Cursor SDK custom_tools (агент вызывает сам)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from src.agent_tools import ToolBlock
from src.cursor_sdk.tool_runtime import (
    RuntimeHolder,
    is_tool_allowed,
    record_persisted_tool_event,
    resolve_runtime,
)
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

logger = logging.getLogger(__name__)

# Набор tools, которые отдаём Composer через Cursor SDK.
CURSOR_SDK_TOOL_NAMES: frozenset[str] = frozenset({
    "web_search",
    "web_fetch",
    "bash",
    "python",
    "read_file",
    "write_file",
    "edit_file",
    "grep",
    "glob",
    "list_dir",
    "manage_calendar",
    "manage_notes",
    "manage_tasks",
    "manage_memory",
    "list_emails",
    "read_email",
    "list_email_accounts",
    "send_email",
    "reply_to_email",
    "bulk_email",
    "create_document",
    "update_document",
    "ui_control",
    "trigger_research",
    "manage_research",
    "manage_skills",
    "ask_user",
})

_SCHEMA_BY_NAME: dict[str, dict[str, Any]] = {
    schema["function"]["name"]: schema["function"]
    for schema in FUNCTION_TOOL_SCHEMAS
    if schema.get("function", {}).get("name")
}


def build_custom_tools(
    names: frozenset[str] | None = None,
    *,
    runtime_holder: RuntimeHolder | None = None,
) -> dict[str, Any]:
    """Строит mapping name → CustomTool для LocalAgentOptions."""
    from cursor_sdk.types import CustomTool

    allowed = names or CURSOR_SDK_TOOL_NAMES
    tools: dict[str, CustomTool] = {}
    for tool_name in sorted(allowed):
        fn = _SCHEMA_BY_NAME.get(tool_name)
        if fn is None:
            continue
        tools[tool_name] = CustomTool(
            description=fn.get("description"),
            input_schema=fn.get("parameters"),
            execute=_make_execute_handler(tool_name, runtime_holder),
        )
    return tools


def _format_tool_output(result: dict[str, Any]) -> str:
    """Форматирует результат Odysseus tool для Cursor SDK."""
    if result.get("error"):
        return str(result["error"])
    for key in ("output", "stdout", "results", "response", "content"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if result.get("success") is True:
        return f"OK: {result.get('path', 'done')}"
    return json.dumps(result, ensure_ascii=False, default=str)


def _command_display(tool_name: str, args: Mapping[str, Any]) -> str:
    if not args:
        return tool_name
    if tool_name == "web_search" and args.get("query"):
        return str(args["query"])[:200]
    if tool_name == "web_fetch" and args.get("url"):
        return str(args["url"])[:200]
    raw = json.dumps(dict(args), ensure_ascii=False)
    return raw[:200] if len(raw) > 200 else raw


def _make_execute_handler(tool_name: str, runtime_holder: RuntimeHolder | None):
    async def execute(
        args: Mapping[str, Any],
        _ctx: Any,
    ) -> dict[str, Any]:
        runtime = resolve_runtime(runtime_holder)
        if runtime is None:
            text = "Odysseus tool runtime не активен."
            return {"content": [{"type": "text", "text": text}], "isError": True}

        if not is_tool_allowed(tool_name, runtime):
            text = f"Tool {tool_name!r} недоступен в этом turn."
            return {"content": [{"type": "text", "text": text}], "isError": True}

        cmd = _command_display(tool_name, args)
        content = json.dumps(dict(args), ensure_ascii=False) if args else ""
        block = ToolBlock(tool_name, content)

        await runtime.events.put(
            {
                "type": "tool_start",
                "tool": tool_name,
                "command": cmd,
                "round": runtime.round_num,
            }
        )

        from src.tool_execution import execute_tool_block

        try:
            _desc, result = await execute_tool_block(
                block,
                session_id=runtime.session_id,
                disabled_tools=set(runtime.disabled_tools),
                owner=runtime.owner,
                workspace=runtime.workspace,
                tool_policy=runtime.tool_policy,
            )
        except Exception as exc:
            logger.exception("Cursor SDK custom tool %s failed", tool_name)
            err_text = f"{type(exc).__name__}: {exc}"
            err_result = {"error": err_text, "exit_code": 1}
            await runtime.events.put(
                {
                    "type": "tool_output",
                    "tool": tool_name,
                    "command": cmd,
                    "output": err_text,
                    "exit_code": 1,
                }
            )
            record_persisted_tool_event(
                runtime,
                tool_name=tool_name,
                command=cmd,
                output_text=err_text,
                result=err_result,
            )
            return {"content": [{"type": "text", "text": err_text}], "isError": True}

        output_text = _format_tool_output(result)
        tool_output: dict[str, Any] = {
            "type": "tool_output",
            "tool": tool_name,
            "command": cmd,
            "output": output_text[:10000],
            "exit_code": result.get("exit_code", 0),
        }
        if tool_name == "web_search" and output_text:
            marker = "<!-- SOURCES:"
            idx = output_text.find(marker)
            if idx >= 0:
                end = output_text.find(" -->", idx)
                if end >= 0:
                    try:
                        sources = json.loads(output_text[idx + len(marker):end])
                        await runtime.events.put({"type": "web_sources", "data": sources})
                    except json.JSONDecodeError:
                        pass
        await runtime.events.put(tool_output)
        runtime.tools_executed.add(tool_name)
        record_persisted_tool_event(
            runtime,
            tool_name=tool_name,
            command=cmd,
            output_text=output_text,
            result=result,
        )

        is_error = bool(result.get("error")) or result.get("exit_code", 0) not in (0, None)
        return {
            "content": [{"type": "text", "text": output_text}],
            "isError": is_error,
        }

    return execute
