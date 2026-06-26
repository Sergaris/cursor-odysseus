"""Subprocess live test runner for Cursor SDK (real API).

Isolated from pytest asyncio to avoid Windows bridge selector issues.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _run_all() -> dict[str, str]:
    from src import llm_core
    from src.cursor_sdk.auth import resolve_api_key
    from src.cursor_sdk.backend import CursorSDKBackend
    from src.cursor_sdk.provider import CURSOR_SDK_BASE_URL, build_cursor_sdk_base_url
    from src.cursor_sdk.stream_bridge import stream_cursor_sdk
    from cursor_sdk.asyncio import AsyncClient, AsyncCursor

    results: dict[str, str] = {}
    api_key = resolve_api_key(None)
    cwd = str(ROOT)
    timeout = 120

    try:
        client = await AsyncClient.launch_bridge(workspace=cwd)
        models = await AsyncCursor.models.list(client=client, api_key=api_key)
        ids = [getattr(m, "id", None) or str(m) for m in models]
        if not ids:
            results["models_list"] = "FAIL: empty model list"
        else:
            results["models_list"] = f"OK: {len(ids)} models"
        await client.aclose()
    except Exception as exc:
        results["models_list"] = f"FAIL: {exc}"

    backend = CursorSDKBackend(
        model="composer-2.5",
        cwd=cwd,
        api_key=api_key,
        scope="ephemeral",
    )
    try:
        await backend.probe(timeout=timeout)
        results["probe"] = "OK"
    except Exception as exc:
        results["probe"] = f"FAIL: {exc}"

    try:
        text = await backend.complete(
            [{"role": "user", "content": "Reply with exactly the word: pong"}],
            timeout=timeout,
        )
        results["complete"] = "OK" if "pong" in text.lower() else f"FAIL: unexpected {text[:40]!r}"
    except Exception as exc:
        results["complete"] = f"FAIL: {exc}"

    deltas: list[str] = []
    try:
        async for chunk in stream_cursor_sdk(
            messages=[{"role": "user", "content": "Say hi in one word."}],
            model="composer-2.5",
            cwd=cwd,
            headers={"X-Cursor-Api-Key": api_key},
            session_id="live-subprocess-stream",
            timeout=timeout,
        ):
            if "event: error" in chunk:
                results["stream_bridge"] = f"FAIL: {chunk[:200]}"
                break
            for line in chunk.split("\n"):
                line = line.strip()
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    payload = json.loads(line[6:])
                    if payload.get("delta"):
                        deltas.append(payload["delta"])
                if line == "data: [DONE]":
                    results["stream_bridge"] = "OK" if deltas else "FAIL: empty stream"
        if "stream_bridge" not in results:
            results["stream_bridge"] = "OK" if deltas else "FAIL: no DONE"
    except Exception as exc:
        results["stream_bridge"] = f"FAIL: {exc}"

    try:
        url = build_cursor_sdk_base_url(cwd)
        text = await llm_core.llm_call_async(
            url,
            "composer-2.5",
            [{"role": "user", "content": "Reply with exactly: ok"}],
            headers={"X-Cursor-Api-Key": api_key},
            timeout=timeout,
        )
        results["llm_call_async"] = "OK" if "ok" in text.lower() else f"FAIL: {text[:40]!r}"
    except Exception as exc:
        results["llm_call_async"] = f"FAIL: {exc}"

    stream_deltas: list[str] = []
    try:
        url = build_cursor_sdk_base_url(cwd)
        async for chunk in llm_core.stream_llm(
            url,
            "composer-2.5",
            [{"role": "user", "content": "Say yes."}],
            headers={"X-Cursor-Api-Key": api_key},
            session_id="live-subprocess-llm-stream",
            timeout=timeout,
        ):
            if "event: error" in chunk:
                results["stream_llm"] = f"FAIL: {chunk[:200]}"
                break
            for line in chunk.split("\n"):
                line = line.strip()
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    payload = json.loads(line[6:])
                    if payload.get("delta"):
                        stream_deltas.append(payload["delta"])
        if "stream_llm" not in results:
            results["stream_llm"] = "OK" if stream_deltas else "FAIL: empty"
    except Exception as exc:
        results["stream_llm"] = f"FAIL: {exc}"

    if llm_core._detect_provider(CURSOR_SDK_BASE_URL) != "cursor-sdk":
        results["detect_provider"] = "FAIL"
    else:
        results["detect_provider"] = "OK"

    from src.cursor_sdk.async_runtime import shutdown_async_clients

    await shutdown_async_clients()
    return results


def main() -> int:
    results = asyncio.run(_run_all())
    print(json.dumps(results, ensure_ascii=False, indent=2))
    failed = [name for name, status in results.items() if not status.startswith("OK")]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
