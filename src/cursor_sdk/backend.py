"""Async-обёртка над Cursor SDK для Deep Research и utility-вызовов."""

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from src.cursor_sdk.async_registry_manager import AsyncSessionRegistryManager
from src.cursor_sdk.async_runtime import get_async_client, retry_after_bridge_transport_error
from src.cursor_sdk.auth import CursorSDKAuthError, resolve_api_key
from src.cursor_sdk.prompt import messages_to_prompt
from src.cursor_sdk.provider import (
    is_cursor_sdk_routing_alias,
    normalize_cursor_sdk_model,
)

if TYPE_CHECKING:
    from src.cursor_sdk.async_session_registry import AsyncSessionRegistry

logger = logging.getLogger(__name__)


class CursorSDKError(RuntimeError):
    """Ошибка запуска Cursor SDK (auth, config, network)."""


class CursorSDKRunError(RuntimeError):
    """Run выполнился, но завершился со status=error."""


def _require_cursor_sdk() -> None:
    try:
        import cursor_sdk  # noqa: F401
    except ImportError as exc:
        raise CursorSDKError(
            "Пакет cursor-sdk не установлен. Выполните: pip install cursor-sdk"
        ) from exc


async def _collect_iter_text(run: Any) -> list[str]:
    """Собирает текст из run.iter_text() для sync и async Cursor SDK runs."""
    if not hasattr(run, "iter_text"):
        return []
    import inspect

    chunks: list[str] = []
    iterator = run.iter_text()
    if inspect.isasyncgen(iterator):
        async for chunk in iterator:
            text = chunk if isinstance(chunk, str) else str(chunk or "")
            if text:
                chunks.append(text)
        return chunks

    for chunk in iterator:
        text = chunk if isinstance(chunk, str) else str(chunk or "")
        if text:
            chunks.append(text)
    return chunks


async def _collect_messages_text(run: Any) -> list[str]:
    """Fallback: текст из assistant blocks, если iter_text пуст."""
    if not hasattr(run, "messages"):
        return []
    import inspect

    chunks: list[str] = []

    def _append_from_message(message: Any) -> None:
        if getattr(message, "type", None) != "assistant":
            return
        content = getattr(getattr(message, "message", None), "content", None) or []
        for block in content:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                if text:
                    chunks.append(text)

    messages = run.messages()
    if inspect.isasyncgen(messages):
        async for message in messages:
            _append_from_message(message)
    else:
        for message in messages:
            _append_from_message(message)
    return chunks


async def _extract_run_text(run: Any) -> str:
    chunks = await _collect_iter_text(run)
    if not chunks:
        chunks = await _collect_messages_text(run)
    return "".join(chunks).strip()


async def _complete_ephemeral(
    *,
    prompt: str,
    api_key: str,
    model: str,
    cwd: str,
) -> str:
    from cursor_sdk import LocalAgentOptions
    from cursor_sdk.asyncio import AsyncAgent
    from cursor_sdk.errors import CursorAgentError

    client = await get_async_client(cwd)
    agent = await AsyncAgent.create(
        client=client,
        api_key=api_key,
        model=model,
        local=LocalAgentOptions(cwd=cwd, setting_sources=[]),
    )
    try:
        run = await agent.send(prompt)
        logger.info(
            "Cursor SDK ephemeral send: agent_id=%s run_id=%s",
            getattr(agent, "agent_id", "?"),
            getattr(run, "run_id", getattr(run, "id", "?")),
        )
        result = await run.wait()
    except CursorAgentError as exc:
        raise CursorSDKError(f"Cursor SDK startup failed: {exc}") from exc
    finally:
        await agent.close()

    if result.status == "error":
        detail = getattr(result, "error", None) or getattr(result, "message", None) or ""
        raise CursorSDKRunError(
            f"Cursor SDK run failed: {getattr(result, 'id', 'unknown')}"
            + (f" ({detail})" if detail else "")
        )

    text = (getattr(result, "result", None) or "").strip()
    if text:
        return text
    return await _extract_run_text(run)


async def _complete_durable(
    *,
    prompt: str,
    api_key: str,
    model: str,
    cwd: str,
    registry: "AsyncSessionRegistry",
    scope: str,
    scope_id: str,
) -> str:
    from cursor_sdk.errors import CursorAgentError

    client = await get_async_client(cwd)
    agent = await registry.get_agent(scope, scope_id, client=client)
    try:
        run = await agent.send(prompt)
    except CursorAgentError as exc:
        raise CursorSDKError(f"Cursor SDK startup failed: {exc}") from exc

    registry.set_active_run(scope, scope_id, run)
    logger.info(
        "Cursor SDK send: scope=%s scope_id=%s agent_id=%s run_id=%s",
        scope,
        scope_id,
        getattr(agent, "agent_id", "?"),
        getattr(run, "run_id", getattr(run, "id", "?")),
    )

    try:
        result = await run.wait()
    finally:
        registry.clear_active_run(scope, scope_id)
    if result.status == "error":
        detail = getattr(result, "error", None) or getattr(result, "message", None) or ""
        raise CursorSDKRunError(
            f"Cursor SDK run failed: {getattr(result, 'id', 'unknown')}"
            + (f" ({detail})" if detail else "")
        )

    text = await _extract_run_text(run)
    if text:
        return text
    fallback = getattr(result, "result", None)
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return ""


class CursorSDKBackend:
    """Backend для LLM-вызовов через Cursor SDK."""

    def __init__(
        self,
        *,
        model: str,
        cwd: str,
        api_key: str | None = None,
        registry: "AsyncSessionRegistry | None" = None,
        scope: str = "ephemeral",
        scope_id: str = "",
    ) -> None:
        _require_cursor_sdk()
        self.model = normalize_cursor_sdk_model(model)
        self.cwd = cwd
        self._api_key = resolve_api_key(api_key)
        self._registry = registry
        self.scope = scope
        self.scope_id = scope_id or uuid.uuid4().hex[:12]

    async def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.3,  # noqa: ARG002
        max_tokens: int = 4096,  # noqa: ARG002
        timeout: int = 60,
    ) -> str:
        """Выполняет completion через Cursor SDK."""
        prompt = messages_to_prompt(messages, text_only=True)

        try:
            if self.scope == "ephemeral" or self._registry is None:
                async def _run() -> str:
                    return await _complete_ephemeral(
                        prompt=prompt,
                        api_key=self._api_key,
                        model=self.model,
                        cwd=self.cwd,
                    )
            else:
                async def _run() -> str:
                    return await _complete_durable(
                        prompt=prompt,
                        api_key=self._api_key,
                        model=self.model,
                        cwd=self.cwd,
                        registry=self._registry,
                        scope=self.scope,
                        scope_id=self.scope_id,
                    )

            for attempt in range(2):
                try:
                    return await asyncio.wait_for(_run(), timeout=timeout)
                except Exception as exc:
                    action = await retry_after_bridge_transport_error(self.cwd, exc)
                    if attempt == 0 and action != "no":
                        continue
                    raise
        except CursorSDKAuthError as exc:
            raise CursorSDKError(str(exc)) from exc

    async def probe(self, timeout: int = 15) -> None:
        """Проверяет доступность Cursor SDK без тяжёлого research/chat agent."""
        if is_cursor_sdk_routing_alias(self.model):
            await asyncio.wait_for(get_async_client(self.cwd), timeout=timeout)
            return

        prompt = "Reply with exactly: ok"

        async def _run() -> str:
            return await _complete_ephemeral(
                prompt=prompt,
                api_key=self._api_key,
                model=self.model,
                cwd=self.cwd,
            )

        for attempt in range(2):
            try:
                await asyncio.wait_for(_run(), timeout=timeout)
                return
            except Exception as exc:
                action = await retry_after_bridge_transport_error(self.cwd, exc)
                if attempt == 0 and action != "no":
                    continue
                raise

    def close(self, *, cancel_run: bool = False) -> None:
        """Освобождает durable agent (sync wrapper для research cleanup)."""
        if self._registry is None:
            return
        try:
            asyncio.get_running_loop()
            has_loop = True
        except RuntimeError:
            has_loop = False

        async def _cleanup() -> None:
            if cancel_run:
                await self._registry.cancel_active(self.scope, self.scope_id)
            await self._registry.release(self.scope, self.scope_id)

        if has_loop:
            asyncio.create_task(_cleanup())
        else:
            asyncio.run(_cleanup())
