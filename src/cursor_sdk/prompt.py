"""Преобразование сообщений Odysseus в prompt для Cursor SDK."""

from collections.abc import Sequence

TEXT_ONLY_SUFFIX = (
    "\n\n---\n"
    "IMPORTANT: Respond with plain text only. "
    "Do not use tools, shell commands, MCP, or code execution. "
    "Do not attempt to read or modify files."
)

_ROLE_LABELS = {
    "system": "System",
    "user": "User",
    "assistant": "Assistant",
}


def messages_to_prompt(messages: Sequence[dict], *, text_only: bool = False) -> str:
    """Сериализует OpenAI-style messages в один текстовый prompt.

    Args:
        messages: Список сообщений с role/content.
        text_only: Если True, добавляет суффикс «без tools» (Deep Research).

    Returns:
        Текст prompt, опционально с text-only суффиксом.
    """
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user").lower()
        content = message.get("content")
        if content is None:
            continue
        if isinstance(content, list):
            text_chunks = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            text = "\n".join(chunk for chunk in text_chunks if chunk)
        else:
            text = str(content)
        text = text.strip()
        if not text:
            continue
        label = _ROLE_LABELS.get(role, role.title())
        parts.append(f"{label}:\n{text}")

    body = "\n\n".join(parts).strip()
    if not body:
        body = "User:\n(empty message)"
    if text_only:
        return body + TEXT_ONLY_SUFFIX
    return body
