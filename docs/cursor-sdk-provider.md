# Cursor SDK provider

Odysseus can use the [Cursor SDK](https://cursor.com/docs/sdk/python) as an LLM backend instead of OpenAI-compatible HTTP endpoints.

## Sentinel URL

```
cursor-sdk://local
```

Optional workspace override:

```
cursor-sdk://local?cwd=C%3A%5Cpath%5Cto%5Cworkspace
```

## Auth

API key resolution order:

1. Endpoint header `X-Cursor-Api-Key`
2. Setting `cursor_api_key`
3. Environment variable `CURSOR_API_KEY`
4. `~/.cursor/worker.env`

## Modes

| Scope | Lifecycle | Use case |
|-------|-----------|----------|
| `ephemeral` | One agent per call | Probe, utility, synthesize/plan |
| `research` | One agent per research job | Deep Research brain |
| `chat` | One agent per Odysseus session | Chat streaming |

## Deep Research

Settings → Research Model → **Brain provider: Cursor SDK**

- Plan, query generation, synthesis, final report → Composer (Cursor SDK)
- Per-URL extract → optional **hybrid** HTTP endpoint (`research_extract_endpoint_id` + `research_extract_model`)
- Search (SearXNG etc.) unchanged

## Chat

Add endpoint `cursor-sdk://local` in Admin → Models. **Agent mode** is recommended; if you stay in Chat mode, the backend auto-escalates to agent loop for Cursor SDK endpoints.

**Tools:** Odysseus tools are exposed to Composer as **Cursor SDK custom_tools** (`web_search`, `manage_calendar`, `list_emails`, …). The agent calls them natively; Odysseus executes the callback and streams `tool_start` / `tool_output` to the UI. Fenced blocks are a fallback for non-SDK endpoints only.

## API routes

- `GET /api/cursor-sdk/models` — model discovery
- `POST /api/cursor-sdk/probe` — connectivity check

## Requirements

```bash
pip install cursor-sdk
```

Listed in `requirements-optional.txt`.

## Windows notes

The SDK bridge runs via `AsyncClient.launch_bridge()`. Live tests use an isolated subprocess runner (`tests/cursor_sdk_live_runner.py`) to avoid pytest asyncio selector issues.
