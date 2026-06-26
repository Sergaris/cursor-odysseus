"""Нормализация content tool-блоков (fenced blocks и Cursor SDK JSON args)."""

import json
from typing import Any

# Поле JSON от function-calling / Cursor SDK custom_tools → plain content для handler.
_JSON_SCALAR_FIELD: dict[str, str] = {
    "bash": "command",
    "python": "code",
    "web_search": "query",
    "web_fetch": "url",
    "read_file": "path",
    "grep": "pattern",
    "glob": "pattern",
    "ls": "path",
    "get_workspace": "path",
}


def parse_tool_json_payload(content: str) -> dict[str, Any] | None:
    """Разбирает JSON-объект из content, если он там есть."""
    raw = (content or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def normalize_tool_content(tool: str, content: str) -> str:
    """Приводит content к формату, который ожидают native tool handlers.

    Cursor SDK передаёт ``json.dumps(args)``; fenced blocks — plain text.
    """
    raw = (content or "").strip()
    if not raw:
        return content or ""

    parsed = parse_tool_json_payload(raw)
    if parsed is None:
        return content

    tool_key = (tool or "").strip().lower()

    if tool_key == "web_fetch":
        url = parsed.get("url")
        if isinstance(url, str) and url.strip():
            if any(key for key in parsed if key != "url"):
                return json.dumps(parsed, ensure_ascii=False)
            return url.strip()

    if tool_key == "web_search":
        query = parsed.get("query")
        if isinstance(query, str) and query.strip():
            if any(key for key in parsed if key != "query"):
                return json.dumps(parsed, ensure_ascii=False)
            return query.strip()

    scalar_field = _JSON_SCALAR_FIELD.get(tool_key)
    if scalar_field and scalar_field in parsed:
        value = parsed[scalar_field]
        if isinstance(value, str):
            return value

    if tool_key == "write_file":
        path = parsed.get("path")
        if isinstance(path, str) and path.strip():
            body = parsed.get("content")
            if isinstance(body, str):
                return f"{path.strip()}\n{body}"
            return path.strip()

    if tool_key == "edit_file":
        path = parsed.get("path")
        if isinstance(path, str) and path.strip():
            parts = [path.strip()]
            for key in ("old_string", "new_string", "content"):
                val = parsed.get(key)
                if isinstance(val, str) and val:
                    parts.append(val)
            if len(parts) > 1:
                return "\n".join(parts)

    if tool_key == "web_search":
        query = parsed.get("query")
        if isinstance(query, str):
            return query

    if tool_key == "web_fetch":
        url = parsed.get("url")
        if isinstance(url, str):
            if any(key for key in parsed if key != "url"):
                return json.dumps(parsed, ensure_ascii=False)
            return url

    return content


def build_mcp_args_from_content(tool: str, content: str) -> dict[str, Any]:
    """Строит structured MCP args из normalized или JSON content."""
    normalized = normalize_tool_content(tool, content)
    parsed = parse_tool_json_payload(content)
    tool_key = (tool or "").strip().lower()

    if parsed is not None:
        if tool_key == "write_file" and "path" in parsed:
            return {
                "path": str(parsed.get("path", "")),
                "content": str(parsed.get("content", "")),
            }
        if tool_key == "generate_image":
            args: dict[str, Any] = {"prompt": str(parsed.get("prompt", ""))}
            for key in ("model", "size", "quality"):
                if parsed.get(key):
                    args[key] = parsed[key]
            return args
        if tool_key == "web_fetch" and "url" in parsed:
            return dict(parsed)
        if tool_key == "web_search" and "query" in parsed:
            return dict(parsed)
        scalar = _JSON_SCALAR_FIELD.get(tool_key)
        if scalar and scalar in parsed:
            return {scalar: parsed[scalar]}

    if tool_key == "bash":
        return {"command": normalized}
    if tool_key == "python":
        return {"code": normalized}
    if tool_key == "web_search":
        return {"query": normalized.split("\n", 1)[0].strip()}
    if tool_key == "web_fetch":
        line = normalized.split("\n", 1)[0].strip()
        return {"url": line}

    return {}
