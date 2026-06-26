"""Мост Cursor SDK streaming → Odysseus SSE (native async)."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from src.cursor_sdk.async_registry_manager import AsyncSessionRegistryManager
from src.cursor_sdk.async_runtime import (
    get_async_client,
    retry_after_bridge_transport_error,
)
from src.cursor_sdk.auth import CursorSDKAuthError, extract_api_key_from_headers, resolve_api_key
from src.cursor_sdk.backend import CursorSDKError, CursorSDKRunError
from src.cursor_sdk.prompt import messages_to_prompt
from src.cursor_sdk.provider import normalize_cursor_sdk_model
from src.cursor_sdk.tool_runtime import CursorSdkToolRuntime, bind_agent_runtime

logger = logging.getLogger(__name__)


def _resolve_api_key(headers: dict | None, explicit: str | None = None) -> str:
    endpoint_key = explicit or extract_api_key_from_headers(headers)
    return resolve_api_key(endpoint_key)


async def _multiplex_run(
    run: Any,
    tool_runtime: CursorSdkToolRuntime | None,
) -> AsyncIterator[str]:
    """Мультиплексирует текст агента и tool events из custom_tools."""
    queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()

    async def _pump_text() -> None:
        try:
            async for text in run.iter_text():
                payload = text if isinstance(text, str) else str(text or "")
                if payload:
                    await queue.put(("text", payload))
        finally:
            await queue.put(("done", None))

    text_task = asyncio.create_task(_pump_text())
    try:
        while True:
            if tool_runtime is not None:
                while not tool_runtime.events.empty():
                    evt = tool_runtime.events.get_nowait()
                    yield f"data: {json.dumps(evt)}\n\n"

            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                continue

            if kind == "done":
                break
            if payload:
                yield f'data: {json.dumps({"delta": payload})}\n\n'
    finally:
        await text_task

    if tool_runtime is not None:
        while not tool_runtime.events.empty():
            evt = tool_runtime.events.get_nowait()
            yield f"data: {json.dumps(evt)}\n\n"


async def _stream_ephemeral(
    *,
    prompt: str,
    api_key: str,
    model: str,
    cwd: str,
    tool_runtime: CursorSdkToolRuntime | None = None,
) -> AsyncIterator[str]:
    from cursor_sdk import LocalAgentOptions
    from cursor_sdk.asyncio import AsyncAgent
    from cursor_sdk.errors import CursorAgentError

    from src.cursor_sdk.custom_tools import build_custom_tools
    from src.cursor_sdk.tool_runtime import RuntimeHolder

    runtime_holder = RuntimeHolder()
    client = await get_async_client(cwd)
    agent = await AsyncAgent.create(
        {"mode": "agent"},
        client=client,
        api_key=api_key,
        model=model,
        local=LocalAgentOptions(
            cwd=cwd,
            setting_sources=[],
            custom_tools=build_custom_tools(runtime_holder=runtime_holder),
        ),
    )
    agent._odysseus_runtime_holder = runtime_holder
    result = None
    try:
        bind_agent_runtime(agent, tool_runtime)
        run = await agent.send(prompt, {"mode": "agent"})
        async for chunk in _multiplex_run(run, tool_runtime):
            yield chunk
        result = await run.wait()
    except CursorAgentError as exc:
        raise CursorSDKError(f"Cursor SDK startup failed: {exc}") from exc
    finally:
        bind_agent_runtime(agent, None)
        await agent.close()

    if result is not None and result.status == "error":
        raise CursorSDKRunError(f"Cursor SDK run failed: {getattr(result, 'id', 'unknown')}")


async def _stream_chat(
    *,
    prompt: str,
    api_key: str,
    model: str,
    cwd: str,
    session_id: str,
    tool_runtime: CursorSdkToolRuntime | None = None,
) -> AsyncIterator[str]:
    from cursor_sdk.errors import CursorAgentError

    registry = await AsyncSessionRegistryManager.get_registry(
        api_key=api_key,
        model=model,
        cwd=cwd,
    )
    client = await get_async_client(cwd)
    agent = await registry.get_agent("chat", session_id, client=client)
    result = None
    bind_agent_runtime(agent, tool_runtime)
    try:
        run = await agent.send(prompt, {"mode": "agent"})
    except CursorAgentError as exc:
        bind_agent_runtime(agent, None)
        raise CursorSDKError(f"Cursor SDK startup failed: {exc}") from exc

    registry.set_active_run("chat", session_id, run)
    logger.info(
        "Cursor SDK stream: session=%s agent_id=%s run_id=%s",
        session_id,
        getattr(agent, "agent_id", "?"),
        getattr(run, "run_id", getattr(run, "id", "?")),
    )

    try:
        async for chunk in _multiplex_run(run, tool_runtime):
            yield chunk
        result = await run.wait()
    finally:
        bind_agent_runtime(agent, None)
        registry.clear_active_run("chat", session_id)

    if result is not None and result.status == "error":
        raise CursorSDKRunError(f"Cursor SDK run failed: {getattr(result, 'id', 'unknown')}")


async def stream_cursor_sdk(
    *,
    messages: list[dict],
    model: str,
    cwd: str,
    headers: dict | None = None,
    session_id: str | None = None,
    timeout: int = 300,
    tool_runtime: CursorSdkToolRuntime | None = None,
) -> AsyncIterator[str]:
    """Стримит ответ Cursor SDK в формате SSE Odysseus."""
    model = normalize_cursor_sdk_model(model)
    prompt = messages_to_prompt(messages, text_only=False)
    chat_session_id = (session_id or "").strip()

    try:
        api_key = _resolve_api_key(headers)
    except CursorSDKAuthError as exc:
        yield f'event: error\ndata: {json.dumps({"error": str(exc), "status": 503})}\n\n'
        return

    stream_fn = _stream_chat if chat_session_id else _stream_ephemeral
    kwargs = {
        "prompt": prompt,
        "api_key": api_key,
        "model": model,
        "cwd": cwd,
        "tool_runtime": tool_runtime,
    }
    if chat_session_id:
        kwargs["session_id"] = chat_session_id

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1, int(timeout or 300))

    for attempt in range(2):
        try:
            async for text in stream_fn(**kwargs):
                if loop.time() > deadline:
                    yield f'event: error\ndata: {json.dumps({"error": "Read timeout", "status": 504})}\n\n'
                    return
                yield text
            yield "data: [DONE]\n\n"
            return
        except CursorSDKRunError as exc:
            yield f'event: error\ndata: {json.dumps({"error": str(exc), "status": 502})}\n\n'
            return
        except CursorSDKError as exc:
            yield f'event: error\ndata: {json.dumps({"error": str(exc), "status": 503})}\n\n'
            return
        except Exception as exc:
            action = await retry_after_bridge_transport_error(cwd, exc)
            if attempt == 0 and action != "no":
                if action == "relaunch" and chat_session_id:
                    await AsyncSessionRegistryManager.release_chat(chat_session_id)
                continue
            logger.error("Cursor SDK stream failed: %s", exc, exc_info=True)
            yield f'event: error\ndata: {json.dumps({"error": str(exc), "status": 502})}\n\n'
            return


def release_chat_session(session_id: str) -> None:
    """Освобождает Cursor SDK chat agent (sync entrypoint)."""
    if not session_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(AsyncSessionRegistryManager.release_chat(session_id))
        return
    loop.create_task(AsyncSessionRegistryManager.release_chat(session_id))
