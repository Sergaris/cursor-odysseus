# src/research_handler.py
"""Handler for research service integration with expandable UI support.

Uses the IterResearch-style DeepResearcher (LLM-in-the-loop) as the primary
engine, falling back to the legacy ResearchOrchestrator or basic web search
if needed.

Includes a task registry so research survives page refreshes and can be cancelled.
"""
import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional, Dict

from src.research_utils import strip_thinking, is_low_quality
from src.constants import DEEP_RESEARCH_DIR

logger = logging.getLogger(__name__)

RESEARCH_DATA_DIR = Path(DEEP_RESEARCH_DIR)
_RESEARCH_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, n))


def _resolve_research_timeout(value, *, default: int = 0) -> int | None:
    """Map settings/API timeout to seconds, or None when unlimited (0 or less)."""
    if value is None:
        value = default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return None if n <= 0 else n


def _resolve_research_max_time(value, *, default: int = 0) -> int:
    """Loop budget inside DeepResearcher; 0 means unlimited."""
    if value is None:
        value = default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def _format_probe_failure(model: str, exc: Exception) -> str:
    """Turn a failed research model probe into a user-facing message."""
    detail = getattr(exc, "detail", None)
    status = getattr(exc, "status_code", None)
    err = str(detail if detail is not None else exc).strip()

    if status in {401, 403} or "401" in err or "API key" in err or "Unauthorized" in err:
        return f"Model '{model}' requires an API key. Check your endpoint configuration."

    if isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError":
        return (
            f"Model '{model}' probe timed out — the model may be slow to respond. "
            "Try again or pick a specific model."
        )

    if status and err:
        return f"Model '{model}' probe failed: {err}"

    if err:
        return f"Cannot reach model '{model}' — {err}"

    return f"Cannot reach model '{model}' — check that the endpoint is running and accessible."


def _is_cursor_research_brain() -> bool:
    from src.settings import get_setting
    return get_setting("research_brain_provider", "http") == "cursor_sdk"


def _cursor_research_workspace() -> str:
    from src.cursor_sdk.provider import resolve_cursor_sdk_cwd
    return resolve_cursor_sdk_cwd()


def _cursor_research_model() -> str:
    from src.settings import get_setting
    return (get_setting("research_brain_model", "composer-2.5") or "composer-2.5").strip()


def _build_cursor_research_backend(session_id: str = ""):
    """Создаёт Cursor SDK backend для Deep Research."""
    from src.cursor_sdk.auth import resolve_api_key
    from src.cursor_sdk.async_session_registry import AsyncSessionRegistry
    from src.cursor_sdk.backend import CursorSDKBackend
    from src.settings import get_setting

    api_key = resolve_api_key((get_setting("cursor_api_key", "") or "").strip() or None)
    model = _cursor_research_model()
    cwd = _cursor_research_workspace()
    registry = AsyncSessionRegistry(api_key=api_key, model=model, cwd=cwd)
    backend = CursorSDKBackend(
        model=model,
        cwd=cwd,
        api_key=api_key,
        registry=registry,
        scope="research",
        scope_id=session_id or "research",
    )
    return backend, registry


def _build_cursor_ephemeral_backend():
    """One-shot Cursor SDK backend для synthesize/plan."""
    from src.cursor_sdk.backend import CursorSDKBackend
    from src.settings import get_setting

    return CursorSDKBackend(
        model=_cursor_research_model(),
        cwd=_cursor_research_workspace(),
        api_key=(get_setting("cursor_api_key", "") or "").strip() or None,
        scope="ephemeral",
    )


def _resolve_research_extract_llm(owner: str | None = None) -> tuple[str | None, str | None, dict | None]:
    """HTTP endpoint/model для per-URL extract в hybrid cursor mode."""
    from src.endpoint_resolver import resolve_endpoint
    from src.settings import get_setting

    if not (get_setting("research_extract_endpoint_id", "") or "").strip():
        return None, None, None

    try:
        url, model, headers = resolve_endpoint("research_extract", owner=owner)
    except Exception:
        return None, None, None

    if not url or not model:
        return None, None, None
    return url, model, headers or {}


def _research_json_path(session_id: str) -> Optional[Path]:
    if not isinstance(session_id, str) or not _RESEARCH_SESSION_ID_RE.fullmatch(session_id):
        return None
    root = RESEARCH_DATA_DIR.resolve()
    path = (RESEARCH_DATA_DIR / f"{session_id}.json").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


class ResearchHandler:
    """Handles research service operations with iterative deep research."""

    def __init__(self):
        self._legacy_engine = None
        self._active_tasks: Dict[str, dict] = {}
        self._initialize_legacy_engine()
        RESEARCH_DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _initialize_legacy_engine(self):
        """Initialize the legacy research engine as a fallback."""
        try:
            from research_engine import ResearchOrchestrator, Config
            config = Config(max_searches=12, max_content_per_page=15000)
            self._legacy_engine = ResearchOrchestrator(config)
            logger.info("Legacy ResearchOrchestrator initialized (fallback)")
        except ImportError:
            logger.info("Legacy research_engine.py not found — DeepResearcher only")
            self._legacy_engine = None
        except Exception as e:
            logger.warning(f"Legacy research engine init failed: {e}")
            self._legacy_engine = None

    # ------------------------------------------------------------------
    # Query synthesis & planning
    # ------------------------------------------------------------------

    async def synthesize_query(
        self, sess, latest_message: str,
        llm_endpoint: str, llm_model: str, llm_headers: dict = None,
    ) -> str:
        """Synthesize the conversation into a single focused research query.

        Reads the session history and latest message to produce a clear,
        specific research question that captures the user's full intent.
        Falls back to the latest message if synthesis fails.
        """
        # Build conversation context from history
        history = getattr(sess, 'history', [])

        # A bare affirmation ("yes", "ok", "go ahead") is the user accepting the
        # clarifying-question round, NOT a research topic — researching the word
        # "yes" is the classic failure here. When synthesis can't run or fails,
        # fall back to the earliest substantive user message (the original ask)
        # rather than the literal follow-up.
        #
        # Match on an explicit affirmation/continuation phrase only (plus the
        # empty/punctuation-only case). We deliberately do NOT use a length
        # heuristic: a short answer like "UK", "C++", or "Rust" is a real topic
        # in a clarification flow and must be left untouched.
        _AFFIRMATIONS = {
            "yes", "y", "yeah", "yep", "yup", "sure", "sure thing", "ok", "okay",
            "k", "kk", "go", "go ahead", "go for it", "do it", "please",
            "yes please", "sounds good", "continue", "proceed", "lets go",
            "let's go", "yes go ahead",
        }

        def _normalize(text: str) -> str:
            return (text or "").strip().lower().strip("!.? ")

        def _fallback() -> str:
            normalized = _normalize(latest_message)
            if normalized and normalized not in _AFFIRMATIONS:
                return latest_message  # short or long, it's a real topic
            # Affirmation, or empty/punctuation-only: use the original ask.
            for m in history:
                c = (m.content or "").strip()
                if m.role == "user" and c and _normalize(c) not in _AFFIRMATIONS:
                    return c
            return latest_message

        if len(history) <= 1:
            return _fallback()  # No conversation to synthesize

        # Take last 6 messages max for context
        recent = history[-6:]
        convo = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content[:500]}"
            for m in recent if m.content
        )
        convo += f"\nUser: {latest_message}"

        try:
            synth_messages = [{"role": "user", "content":
                "Read this conversation and write a single, specific research query that captures "
                "what the user wants to know. Include all relevant context, constraints, and preferences "
                "they mentioned. Output ONLY the research query — nothing else.\n\n"
                f"Conversation:\n{convo}"
            }]
            if _is_cursor_research_brain():
                backend = _build_cursor_ephemeral_backend()
                response = await backend.complete(
                    synth_messages,
                    temperature=0.1,
                    max_tokens=200,
                    timeout=15,
                )
            else:
                from src.llm_core import llm_call_async

                response = await llm_call_async(
                    url=llm_endpoint,
                    model=llm_model,
                    messages=synth_messages,
                    temperature=0.1,
                    max_tokens=200,
                    headers=llm_headers,
                    timeout=15,
                    max_retries=1,
                )
            query = strip_thinking(response).strip().strip('"\'')
            if query and len(query) > 5:
                return query
        except Exception as e:
            logger.warning(f"Query synthesis failed: {e}")

        return _fallback()

    async def generate_plan(
        self, query: str, llm_endpoint: str, llm_model: str, llm_headers: dict = None,
    ) -> Optional[dict]:
        """Generate a research plan for user review before starting research."""
        try:
            from src.deep_research import RESEARCH_PLAN_PROMPT, current_date_context

            prompt = current_date_context() + RESEARCH_PLAN_PROMPT.format(question=query)
            plan_messages = [{"role": "user", "content": prompt}]
            if _is_cursor_research_brain():
                backend = _build_cursor_ephemeral_backend()
                response = await backend.complete(
                    plan_messages,
                    temperature=0.3,
                    max_tokens=1024,
                    timeout=30,
                )
            else:
                from src.llm_core import llm_call_async

                response = await llm_call_async(
                    url=llm_endpoint,
                    model=llm_model,
                    messages=plan_messages,
                    temperature=0.3,
                    max_tokens=1024,
                    headers=llm_headers,
                    timeout=30,
                    max_retries=1,
                )
            response = strip_thinking(response)

            # Try to parse structured plan
            import json as _json
            parsed = None
            try:
                # Try to extract JSON from response
                _clean = response.strip()
                if _clean.startswith("```"):
                    _clean = re.sub(r'^```(?:json)?\s*', '', _clean)
                    _clean = re.sub(r'\s*```$', '', _clean)
                import re as _re
                _match = _re.search(r'\{[\s\S]*\}', _clean)
                if _match:
                    parsed = _json.loads(_match.group())
            except Exception:
                pass

            return {
                "sub_questions": parsed.get("sub_questions", []) if parsed else [],
                "key_topics": parsed.get("key_topics", []) if parsed else [],
                "success_criteria": parsed.get("success_criteria", "") if parsed else "",
                "raw": response,
            }
        except Exception as e:
            logger.warning(f"Research plan generation failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Task registry — background research with persistence
    # ------------------------------------------------------------------

    def rename_owner(self, old_owner: str, new_owner: str) -> int:
        """Move in-flight research tasks from one owner key to another."""
        old_key = str(old_owner or "").strip().lower()
        new_key = str(new_owner or "").strip().lower()
        if not old_key or not new_key:
            return 0

        changed = 0
        for entry in list(self._active_tasks.values()):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("owner", "")).strip().lower() == old_key:
                entry["owner"] = new_key
                changed += 1
        return changed

    def start_research(
        self,
        session_id: str,
        query: str,
        llm_endpoint: str,
        llm_model: str,
        max_time: int | None = None,
        hard_timeout: int = None,
        llm_headers: dict = None,
        on_complete: callable = None,
        prior_report: str = "",
        prior_findings: list = None,
        prior_urls: set = None,
        max_rounds: int = 10,
        search_provider: str = None,
        extraction_timeout: int = None,
        extraction_concurrency: int = None,
        owner: str = "",
    ) -> dict:
        """Start research as a background task. Returns task info dict.

        max_rounds is the safety cap; coverage check (after min_rounds)
        terminates the loop earlier in normal operation.
        """
        if _research_json_path(session_id) is None:
            raise ValueError("Invalid research session_id")

        # Resolve the hard wall-clock timeout from settings when the caller
        # didn't pin one. Local / edge models routinely need more than the
        # old 600s default to finish a deep-research synthesis. A setting of
        # 0 disables the cap entirely (unlimited run); any other value is
        # bounded to [60, 86400] so a misconfigured settings.json can't
        # explode into a multi-day hang.
        if hard_timeout is None:
            from src.settings import get_setting
            try:
                raw_timeout = int(get_setting("research_run_timeout_seconds", 0))
            except (TypeError, ValueError):
                raw_timeout = 0
            if raw_timeout <= 0:
                hard_timeout = None  # 0 = no wall-clock cap (asyncio.wait_for timeout=None)
            else:
                hard_timeout = _bounded_int(
                    raw_timeout,
                    default=7200,
                    minimum=60,
                    maximum=86400,
                )

        if max_time is None:
            from src.settings import get_setting
            max_time = _resolve_research_max_time(
                get_setting("research_max_time_seconds", 0),
                default=0,
            )

        # Cancel any existing research for this session
        if session_id in self._active_tasks:
            existing = self._active_tasks[session_id]
            if existing.get("status") == "running":
                self.cancel_research(session_id)

        entry = {
            "task": None,
            "researcher": None,
            "query": query,
            "status": "running",
            "progress": {},
            "result": None,
            "started_at": time.time(),
            # SECURITY: track ownership so all reads / saves can filter by user.
            "owner": owner or "",
        }
        self._active_tasks[session_id] = entry

        def on_progress(event):
            entry["progress"] = event

        _completed = False

        def _guarded_complete(*args, **kwargs):
            nonlocal _completed
            if _completed:
                return
            _completed = True
            if on_complete:
                on_complete(*args, **kwargs)

        async def _run():
            # Hard wall-clock timeout — saves partial results if an LLM call hangs
            # hard_timeout passed from start_research()
            try:
                result = await asyncio.wait_for(
                    self.call_research_service(
                        query, llm_endpoint, llm_model,
                        max_time=max_time,
                        progress_callback=on_progress,
                        _task_entry=entry,
                        llm_headers=llm_headers,
                        prior_report=prior_report,
                        prior_findings=prior_findings,
                        prior_urls=prior_urls,
                        max_rounds=max_rounds,
                        search_provider=search_provider,
                        extraction_timeout=extraction_timeout,
                        extraction_concurrency=extraction_concurrency,
                        session_id=session_id,
                    ),
                    timeout=hard_timeout,
                )
                entry["result"] = result
                entry["status"] = "done"
                self._save_result(session_id, entry)
                # Persist to DB via callback (ensures result survives even if SSE disconnected)
                try:
                    sources = entry.get("sources", [])
                    researcher = entry.get("researcher")
                    findings = self._extract_raw_findings(researcher.findings) if researcher and researcher.findings else []
                    _guarded_complete(session_id, result, sources, findings)
                except Exception as cb_err:
                    logger.error(f"on_complete callback failed: {cb_err}")
            except asyncio.TimeoutError:
                logger.error(f"Research hard timeout ({hard_timeout}s) for session {session_id}")
                entry["status"] = "error"
                entry["failure_reason"] = "run_timeout"
                researcher = entry.get("researcher")
                if researcher is not None:
                    stats = dict(researcher.get_stats())
                    stats["FailureReason"] = "run_timeout"
                    entry["stats"] = stats
                partial = await self._recover_partial_result(query, researcher)
                if partial:
                    entry["result"] = partial
                    entry["status"] = "done"
                    self._save_result(session_id, entry)
                    try:
                        sources = self._extract_sources(researcher.findings) if researcher.findings else []
                        findings = self._extract_raw_findings(researcher.findings) if researcher.findings else []
                        _guarded_complete(session_id, entry["result"], sources, findings)
                    except Exception as e:
                        logger.warning(f"on_complete callback failed in timeout branch: {e}")
                else:
                    entry["result"] = f"Research timed out after {hard_timeout}s. The model may be too slow for deep research."
                    self._save_result(session_id, entry)
                on_progress({"phase": "error", "message": f"Research timed out after {hard_timeout}s"})
            except asyncio.CancelledError:
                entry["status"] = "cancelled"
                raise
            except Exception as e:
                logger.error(f"Background research failed: {e}", exc_info=True)
                researcher = entry.get("researcher")
                partial = await self._recover_partial_result(query, researcher)
                if partial:
                    _elapsed = time.time() - entry["started_at"]
                    entry["result"] = partial
                    entry["status"] = "done"
                    self._save_result(session_id, entry)
                    try:
                        sources = self._extract_sources(researcher.findings) if researcher.findings else []
                        findings = self._extract_raw_findings(researcher.findings) if researcher.findings else []
                        _guarded_complete(session_id, entry["result"], sources, findings)
                    except Exception as cb_err:
                        logger.warning(f"on_complete callback failed in error branch: {cb_err}")
                    on_progress({"phase": "warning", "message": f"Research finished with errors — partial results saved ({_elapsed:.0f}s elapsed)"})
                else:
                    entry["result"] = str(e)
                    entry["status"] = "error"

        task = asyncio.create_task(_run())
        entry["task"] = task
        return {"session_id": session_id, "status": "running", "query": query}

    def get_status(self, session_id: str) -> Optional[dict]:
        """Get current research status for a session."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            result = {
                "status": entry["status"],
                "progress": entry["progress"],
                "query": entry["query"],
                "started_at": entry["started_at"],
            }
            # avg_duration is a historical figure over completed reports on
            # disk; get_avg_duration() globs and JSON-parses the whole research
            # dir, so compute it at most once per active stream (memoized on the
            # entry) instead of on every ~1s SSE poll. The disk branch below
            # never used it, so it no longer pays that cost at all.
            if "_avg_duration" not in entry:
                entry["_avg_duration"] = self.get_avg_duration()
            avg = entry["_avg_duration"]
            if avg is not None:
                result["avg_duration"] = round(avg, 1)
            return result
        # Check disk for completed research (skip consumed results)
        path = _research_json_path(session_id)
        if path is None:
            return None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("consumed"):
                    return None
                return {
                    "status": data.get("status", "done"),
                    "progress": {},
                    "query": data.get("query", ""),
                    "started_at": data.get("started_at", 0),
                }
            except Exception:
                pass
        return None

    def cancel_research(self, session_id: str) -> bool:
        """Cancel running research for a session."""
        if session_id not in self._active_tasks:
            return False
        entry = self._active_tasks[session_id]
        if entry["status"] != "running":
            return False
        researcher = entry.get("researcher")
        if researcher:
            researcher.cancel()
        cursor_backend = entry.get("cursor_backend")
        if cursor_backend:
            try:
                cursor_backend.close(cancel_run=True)
            except Exception as exc:
                logger.warning("Cursor SDK backend close on cancel failed: %s", exc)
            entry["cursor_backend"] = None
            entry["cursor_registry"] = None
        task = entry.get("task")
        if task and not task.done():
            task.cancel()
        entry["status"] = "cancelled"
        return True

    def get_result(self, session_id: str) -> Optional[str]:
        """Get the completed research result."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            if entry["status"] in ("done", "error", "cancelled"):
                return entry.get("result")
        # Check disk (skip consumed results)
        path = _research_json_path(session_id)
        if path is None:
            return None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("consumed"):
                    return None
                return data.get("result")
            except Exception:
                pass
        return None

    def get_sources(self, session_id: str) -> Optional[list]:
        """Get deduplicated source list from research findings."""
        # Check in-memory first
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            if entry.get("sources"):
                return entry["sources"]
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                return self._extract_sources(researcher.findings)
        # Check disk
        path = _research_json_path(session_id)
        if path is None:
            return None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("sources")
            except Exception:
                pass
        return None

    def get_raw_findings(self, session_id: str) -> Optional[list]:
        """Get raw per-source findings for display."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                return self._extract_raw_findings(researcher.findings)
        # Check disk
        path = _research_json_path(session_id)
        if path is None:
            return None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("raw_findings")
            except Exception as e:
                logger.warning(f"Failed to read raw findings for {session_id}: {e}")
        return None

    @staticmethod
    def _extract_sources(findings: list) -> list:
        """Extract deduplicated [{url, title}] from findings, filtering low-quality ones."""
        seen = set()
        sources = []
        for f in findings:
            if not isinstance(f, dict):
                continue
            url = f.get("url", "")
            title = f.get("title", "") or url
            summary = f.get("summary", "") or f.get("evidence", "")
            if url and url not in seen and not is_low_quality(summary):
                seen.add(url)
                entry = {"url": url, "title": title}
                og_img = f.get("og_image", "")
                if og_img:
                    entry["image"] = og_img
                sources.append(entry)
        return sources

    @staticmethod
    def _extract_raw_findings(findings: list) -> list:
        """Extract [{url, title, summary}] for per-source findings display, filtering junk."""
        try:
            items = []
            for f in findings:
                if not isinstance(f, dict):
                    continue
                url = f.get("url", "")
                title = f.get("title", "") or "Untitled"
                summary = f.get("summary", "")
                evidence = f.get("evidence", "")
                content = summary if summary else (evidence[:2000] if evidence else "")
                if url and content and not is_low_quality(content):
                    items.append({"url": url, "title": title, "summary": content})
            return items
        except Exception as e:
            logger.warning(f"Failed to extract raw findings: {e}")
            return []

    def get_avg_duration(self) -> Optional[float]:
        """Compute average research duration from completed results on disk."""
        durations = []
        try:
            for p in RESEARCH_DATA_DIR.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if data.get("status") == "done":
                        started = data.get("started_at", 0)
                        completed = data.get("completed_at", 0)
                        if started and completed and completed > started:
                            durations.append(completed - started)
                except Exception:
                    continue
        except Exception:
            pass
        if durations:
            return sum(durations) / len(durations)
        return None

    def clear_result(self, session_id: str):
        """Mark result as consumed so it won't be re-rendered on refresh.

        Keeps the JSON on disk so visual reports can be generated later.
        """
        self._active_tasks.pop(session_id, None)
        path = _research_json_path(session_id)
        if path is None:
            return
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["consumed"] = True
                path.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                pass

    def _save_result(self, session_id: str, entry: dict):
        """Persist completed research result to disk."""
        try:
            path = _research_json_path(session_id)
            if path is None:
                logger.error("Refusing to save research result for invalid session_id: %r", session_id)
                return
            # Extract and cache sources + raw findings
            sources = []
            raw_findings = []
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                sources = self._extract_sources(researcher.findings)
                raw_findings = self._extract_raw_findings(researcher.findings)
            entry["sources"] = sources

            data = {
                "query": entry["query"],
                "status": entry["status"],
                "result": entry["result"],
                "raw_report": entry.get("raw_report", ""),
                "sources": sources,
                "raw_findings": raw_findings,
                "stats": entry.get("stats"),
                "failure_reason": entry.get("failure_reason") or (entry.get("stats") or {}).get("FailureReason", ""),
                "scratchpad": getattr(researcher, "scratchpad", "") if researcher else "",
                "citations": getattr(researcher, "citation_map", []) if researcher else [],
                "category": "",
                "started_at": entry["started_at"],
                "completed_at": time.time(),
                # SECURITY: stamp owner so route handlers can filter by user.
                "owner": entry.get("owner", ""),
            }
            path.write_text(json.dumps(data), encoding="utf-8")
            logger.info(f"Research result saved to {path}")
            try:
                from src.event_bus import fire_event
                fire_event("research_completed", entry.get("owner") or None)
            except Exception:
                logger.debug("research_completed event dispatch failed", exc_info=True)
        except Exception as e:
            logger.error(f"Failed to save research result: {e}")

    def _get_session_json(self, session_id: str) -> Optional[dict]:
        """Load the saved research JSON for a session, if it exists."""
        path = _research_json_path(session_id)
        if path is None:
            return None
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def get_report_html(self, session_id: str) -> Optional[str]:
        """Generate the visual HTML report for a session (always fresh from JSON)."""
        json_path = _research_json_path(session_id)
        if json_path is None:
            return None
        if not json_path.exists():
            logger.warning(f"No JSON found for visual report: {json_path}")
            return None

        try:
            from src.visual_report import generate_visual_report

            data = json.loads(json_path.read_text(encoding="utf-8"))
            report_md = data.get("raw_report") or data.get("result", "")
            html_content = generate_visual_report(
                question=data.get("query", ""),
                report_markdown=report_md,
                sources=data.get("sources"),
                stats=data.get("stats"),
                session_id=session_id,
                hidden_images=data.get("hidden_images") or [],
            )
            logger.info(f"Visual report generated for {session_id}")
            return html_content
        except Exception as e:
            logger.error(f"Failed to generate visual report: {e}")
            return None

    def hide_image(self, session_id: str, image_url: str) -> bool:
        """Add image_url to the persisted hidden_images list for a research."""
        path = _research_json_path(session_id)
        if path is None:
            return False
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            hidden = data.get("hidden_images") or []
            if image_url not in hidden:
                hidden.append(image_url)
                data["hidden_images"] = hidden
                path.write_text(json.dumps(data), encoding="utf-8")
                logger.info(f"Hid image {image_url[:80]} for research {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to hide image: {e}")
            return False

    def unhide_all_images(self, session_id: str) -> bool:
        """Clear the hidden_images list for a research."""
        path = _research_json_path(session_id)
        if path is None:
            return False
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["hidden_images"] = []
            path.write_text(json.dumps(data), encoding="utf-8")
            logger.info(f"Cleared hidden_images for research {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to unhide images: {e}")
            return False

    @staticmethod
    async def _probe_endpoint(
        endpoint: str,
        model: str,
        headers: dict = None,
        *,
        chat_timeout: int = 45,
    ) -> None:
        """Verify the LLM endpoint responds to a short chat completion before research."""
        from src.llm_core import llm_call_async

        try:
            logger.info(
                "Probing %s at %s (chat_timeout=%ss, has_auth=%s)",
                model,
                endpoint,
                chat_timeout,
                bool(headers and "Authorization" in (headers or {})),
            )
            await llm_call_async(
                url=endpoint,
                model=model,
                messages=[{"role": "user", "content": "Reply with OK only."}],
                temperature=0,
                max_tokens=8,
                headers=headers,
                timeout=chat_timeout,
                max_retries=1,
            )
            logger.info("Endpoint probe OK: %s", model)
        except Exception as e:
            logger.error("Probe failed for %s: %s", model, e)
            raise RuntimeError(
                _format_probe_failure(model, e)
                + f" Increase research timeout or choose a faster model (probe budget: {chat_timeout}s)."
            ) from e

    async def call_research_service(
        self,
        query: str,
        llm_endpoint: str,
        llm_model: str,
        max_time: int | None = None,
        progress_callback=None,
        _task_entry: dict = None,
        llm_headers: dict = None,
        prior_report: str = "",
        prior_findings: list = None,
        prior_urls: set = None,
        max_rounds: int = 10,
        search_provider: str = None,
        extraction_timeout: int = None,
        extraction_concurrency: int = None,
        session_id: str = "",
    ) -> str:
        """
        Run iterative deep research using the LLM-in-the-loop DeepResearcher.

        Args:
            query: Research question
            llm_endpoint: LLM endpoint URL for chat completions
            llm_model: Model name/ID
            max_time: Maximum research time in seconds (default 5 minutes)
            _task_entry: Internal - registry entry to store researcher ref
            prior_report: Previous report to continue from.
            prior_findings: Previous findings to build on.
            prior_urls: URLs already visited (won't re-fetch).

        Returns:
            Formatted research report with expandable section and summary
        """
        is_continuation = bool(prior_report)
        logger.info(f"{'Continuing' if is_continuation else 'Starting'} IterResearch Deep Research")
        logger.info(f"Query: {query}")
        logger.info(f"LLM: {llm_endpoint} / {llm_model}")
        logger.info(f"Max time: {max_time}s")
        if is_continuation:
            logger.info(f"Prior: {len(prior_findings or [])} findings, {len(prior_urls or set())} URLs")

        from src.settings import get_setting
        if max_time is None:
            max_time = _resolve_research_max_time(
                get_setting("research_max_time_seconds", 0),
                default=0,
            )

        cursor_backend = None
        cursor_registry = None
        from src.cursor_sdk.provider import is_cursor_sdk_base

        use_cursor = is_cursor_sdk_base(llm_endpoint)
        if use_cursor:
            try:
                cursor_backend, cursor_registry = _build_cursor_research_backend(session_id)
                if _task_entry is not None:
                    _task_entry["cursor_backend"] = cursor_backend
                    _task_entry["cursor_registry"] = cursor_registry
            except Exception as e:
                logger.error("Cursor SDK backend init failed: %s", e)
                raise RuntimeError(_format_probe_failure(llm_model, e)) from e

        try:
            from src.deep_research import DeepResearcher

            _max_report_tokens = int(get_setting("research_max_tokens", 16384))
            # Deep Research never caps LLM read/inference time; slow local models
            # may run as long as they need.
            _extraction_timeout = None
            _planning_timeout = None
            _query_timeout = None
            _heavy_llm_timeout = None
            _extraction_concurrency = _bounded_int(
                extraction_concurrency if extraction_concurrency is not None else get_setting("research_extraction_concurrency", 8),
                default=8,
                minimum=1,
                maximum=16,
            )
            _queries_round1 = _bounded_int(
                get_setting("research_queries_round1", 5),
                default=5, minimum=1, maximum=12,
            )
            _queries_followup = _bounded_int(
                get_setting("research_queries_followup", 4),
                default=4, minimum=1, maximum=12,
            )
            _max_urls_per_query = _bounded_int(
                get_setting("research_max_urls_per_query", 5),
                default=5, minimum=1, maximum=20,
            )
            _effective_max_rounds = max_rounds or 10
            _min_rounds = max(2, min(_effective_max_rounds, 10) - 2)

            extract_url, extract_model, extract_headers = (None, None, None)
            if use_cursor:
                extract_url, extract_model, extract_headers = _resolve_research_extract_llm()
                if extract_url and extract_model:
                    logger.info(
                        "Hybrid research extract: %s / %s",
                        extract_url,
                        extract_model,
                    )

            try:
                _coverage_threshold = float(get_setting("research_coverage_threshold", 0.75))
            except (TypeError, ValueError):
                _coverage_threshold = 0.75
            _compression_backend = (
                get_setting("research_compression_backend", "heuristic") or "heuristic"
            ).strip().lower()
            if _compression_backend not in {"heuristic", "provence", "off"}:
                _compression_backend = "heuristic"

            researcher = DeepResearcher(
                llm_endpoint=llm_endpoint,
                llm_model=llm_model,
                llm_headers=llm_headers,
                max_rounds=_effective_max_rounds,
                min_rounds=_min_rounds,
                max_time=max_time,
                max_urls_per_query=_max_urls_per_query,
                queries_round1=_queries_round1,
                queries_followup=_queries_followup,
                max_report_tokens=_max_report_tokens,
                extraction_timeout=_extraction_timeout,
                planning_timeout=_planning_timeout,
                query_timeout=_query_timeout,
                extraction_concurrency=_extraction_concurrency,
                heavy_llm_timeout=_heavy_llm_timeout,
                progress_callback=progress_callback,
                search_provider=search_provider,
                cursor_backend=cursor_backend,
                extract_llm_endpoint=extract_url,
                extract_llm_model=extract_model,
                extract_llm_headers=extract_headers,
                session_id=session_id,
                compression_backend=_compression_backend,
                coverage_threshold=_coverage_threshold,
            )
            if _task_entry is not None:
                _task_entry["researcher"] = researcher

            start_time = time.time()
            report = await researcher.research(
                query,
                prior_report=prior_report,
                prior_findings=prior_findings,
                prior_urls=prior_urls,
            )
            elapsed = time.time() - start_time

            stats = researcher.get_stats()
            if stats.get("FailureReason") == "llm_extraction":
                logger.warning(
                    "Research finished with %s URLs but 0 extractions — LLM likely timed out",
                    stats.get("URLs", "?"),
                )
            else:
                logger.info("IterResearch completed successfully")
            for key, value in stats.items():
                logger.info(f"  {key}: {value}")

            # Store raw report and stats for visual report generation
            if _task_entry is not None:
                _task_entry["raw_report"] = strip_thinking(report)
                _task_entry["stats"] = stats

            return self._format_research_report(query, report, stats, elapsed)

        except Exception as e:
            logger.error(f"DeepResearcher failed: {e}", exc_info=True)
            return await self._fallback_research(query, llm_endpoint, llm_model, max_time, str(e))
        finally:
            if cursor_backend is not None:
                cursor_backend.close()

    async def _fallback_research(
        self, query: str, llm_endpoint: str, llm_model: str,
        max_time: int, primary_error: str,
    ) -> str:
        """Fall back to legacy engine, then to basic web search."""
        # Try legacy orchestrator
        if self._legacy_engine:
            try:
                import asyncio
                logger.info("Falling back to legacy ResearchOrchestrator...")
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, self._legacy_engine.start_research, query, max_time
                )
                stats = self._get_legacy_stats()
                elapsed = float(stats.get("Duration", "0").rstrip("s") or 0)
                return self._format_research_report(query, result, stats, elapsed)
            except Exception as e:
                logger.error(f"Legacy engine also failed: {e}")

        # Fall back to basic web search
        return self._handle_research_failure(query, primary_error)

    def _get_legacy_stats(self) -> dict:
        """Get statistics from the legacy research engine."""
        if not self._legacy_engine:
            return {}
        try:
            tracker = self._legacy_engine.progress_tracker
            return {
                "Findings": len(self._legacy_engine.findings),
                "Sources": len(self._legacy_engine.source_reports),
                "Searches": tracker.counters['searches_executed'],
                "URLs": tracker.counters['urls_processed'],
            }
        except Exception:
            return {}

    async def _recover_partial_result(self, query: str, researcher) -> str | None:
        """Build a formatted report from partial scratchpad/findings on timeout/error."""
        if not researcher:
            return None
        answer = ""
        if getattr(researcher, "scratchpad", "") or getattr(researcher, "findings", None):
            if researcher.scratchpad:
                try:
                    answer = await researcher._compose_answer(
                        query,
                        researcher.scratchpad,
                        researcher.plan_sub_questions,
                    )
                except Exception as exc:
                    logger.warning("Partial compose failed: %s", exc)
            if not answer and researcher.findings:
                answer = researcher._fallback_report(query, researcher.findings)
        if not answer:
            return None
        stats = researcher.get_stats()
        elapsed = time.time() - researcher._start_time if researcher._start_time else 0
        return self._format_research_report(query, answer, stats, elapsed)

    def _format_research_report(
        self, query: str, full_report: str, stats: dict, elapsed: float,
    ) -> str:
        """Format research report (markdown only — sources/findings handled by frontend)."""
        full_report = strip_thinking(full_report)
        summary_lines = [
            f"**Duration:** {elapsed:.1f}s",
            f"**Rounds:** {stats.get('Rounds', stats.get('Findings', '?'))}",
            f"**Queries:** {stats.get('Queries', stats.get('Searches', '?'))}",
            f"**URLs Analyzed:** {stats.get('URLs', '?')}",
        ]
        summary_text = " | ".join(summary_lines)

        formatted = f"""---

## Research Summary

{summary_text}

---

{full_report}
"""
        return formatted

    def _format_error_response(self, error_msg: str, query: str) -> str:
        """Format error response in a user-friendly way."""
        return f"""## Research Engine Unavailable

**Query:** {query}

**Error:** {error_msg}

**Please check:**
1. LLM endpoint is reachable
2. SearXNG is running at the configured instance
3. Application logs for detailed error information

**Troubleshooting:**
- Test basic search: Try the web search toggle first
- Check search config: `/api/search/config`
- Review logs for initialization errors
"""

    def _handle_research_failure(self, query: str, error: str) -> str:
        """Handle research failure with fallback to basic search."""
        try:
            logger.info("Attempting fallback to basic web search...")
            from src.search import comprehensive_web_search

            search_result = comprehensive_web_search(query)

            return f"""## Research Failed - Basic Search Fallback

**Query:** {query}

**Error:** {error}

**Note:** The deep research engine encountered an error. Here are basic search results instead:

---

### Basic Web Search Results

{search_result}

---

**To fix deep research:**
1. Check that your LLM endpoint and search provider are properly configured
2. Verify network connectivity
3. Review application logs for detailed error information

Try the web search toggle for simpler queries, or fix the research engine for comprehensive analysis.
"""

        except Exception as e2:
            logger.error(f"Fallback search also failed: {e2}", exc_info=True)
            return f"""## Complete Research Failure

**Primary Error:** {error}
**Fallback Error:** {str(e2)}

**Please check:**
1. Search provider configuration in Settings -> Search Settings
2. Network connectivity to search APIs
3. Application logs for detailed error information
4. That SearXNG is running (if using SearXNG)

**Debug Info:**
- Search config endpoint: `/api/search/config`
- Test basic search toggle with a simple query first
"""
