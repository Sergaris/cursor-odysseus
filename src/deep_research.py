# src/deep_research.py
"""
IterResearch-style deep research engine.

Implements an iterative Think→Search→Extract→Scratchpad→Compose loop where the
LLM drives every decision: what to search, what's relevant, what's missing, and
when to stop.  Inspired by Alibaba's IterResearch approach.
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from src.research_utils import strip_thinking, is_low_quality, normalize_url
from src.scratchpad_schema import validate_scratchpad

from src.goal_based_extractor import EXTRACTOR_SYSTEM
from src.prompt_security import untrusted_context_message

logger = logging.getLogger(__name__)


def current_date_context() -> str:
    """Preamble that grounds query-generation/planning LLMs in the real current
    date. Without it the model falls back to its training-cutoff year and emits
    queries like "best Python tutorials 2025" when the year is actually 2026.
    System TZ-local so it matches what the user sees. Portable strftime only."""
    now = datetime.now().astimezone()
    return (
        f"Today's date is {now.strftime('%B %d, %Y')} ({now.strftime('%Y-%m-%d')}). "
        f"When a search query needs a year or refers to 'latest'/'current'/"
        f"'this year', use {now.strftime('%Y')} or relative wording — never a "
        f"year inferred from training data.\n\n"
    )

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
RESEARCH_PLAN_PROMPT = """\
You are a research strategist. Before searching, analyze this question and create a research plan.

**Question:** {question}

Break this question down:
1. What are the key sub-topics that need to be covered for a comprehensive answer?
2. What specific data points, facts, or perspectives should we look for?
3. What would a complete, high-quality answer include?

Return a JSON object with:
- "sub_questions": Array of 3-6 specific sub-questions to investigate
- "key_topics": Array of key topics/angles to cover
- "success_criteria": One sentence describing what a complete answer looks like

Example:
{{
  "sub_questions": ["What is the cost of living in X?", "How is the healthcare system?"],
  "key_topics": ["economy", "healthcare", "safety", "culture"],
  "success_criteria": "A balanced comparison covering cost, quality of life, and practical considerations."
}}
"""

QUERY_GEN_PROMPT = """\
You are a research assistant planning web searches.

**Original question:** {question}

**Research plan:**
{research_plan}

**Current scratchpad (coverage notes):**
{scratchpad}

**Gaps still to fill:**
{gaps}

**Round:** {round_num}

Generate {num_queries} focused search queries that will help answer the question.
{round_instruction}

Prioritize PRIMARY documentation over forums, blogs, and secondary roundups.
Use search operators when they improve depth:
- Official docs first: site:<vendor-docs-host>, paths like /docs/ or /api/, docs.* / developer.* hosts
- site:github.com for source/code/issues (supporting, not instead of vendor docs)
- site:arxiv.org / filetype:pdf only for scientific claims — NOT for product SDK/API how-tos
- after:YYYY-MM-DD (or year) for recent material
- quoted "exact phrases" for rare API symbols

For product / SDK / API / library questions:
- At least ONE query MUST target official docs (site: on the vendor docs host, or an explicit docs path)
- Do NOT spend the majority of slots on arxiv/github/pdf when the answer lives in vendor docs
- Forums and cookbooks are secondary corroboration only

Prefer diverse strategies across the set, but keep strategy:"web" dominant for product docs.
Use strategy:"academic"/"github"/"pdf" only when that corpus is actually needed.

CRITICAL: Reply with ONLY a JSON array — no prose, no refusal, no markdown fences.
Preferred form (objects with strategy tags):
[
  {{"query": "LibraryName Client.create official docs site:docs.example.com", "strategy": "web"}},
  {{"query": "LibraryName API reference documentation", "strategy": "web"}},
  {{"query": "LibraryName site:github.com", "strategy": "github"}},
  {{"query": "topic after:2025-01-01", "strategy": "news"}},
  {{"query": "scientific claim site:arxiv.org", "strategy": "academic"}}
]
Also accepted: ["query one", "query two"] (strategy defaults to "web").
"""

SCRATCHPAD_UPDATE_PROMPT = """\
You are maintaining a structured research scratchpad (NOT a user-facing article).

**Question:** {question}

**Research plan sub-questions:**
{sub_questions}

**Current scratchpad:**
{scratchpad}

**New findings this round (summary + url only):**
{new_findings}

Update the scratchpad. For EACH sub-question include:
- status: covered | partial | missing
- key_facts: bullet list with source URLs inline
- gaps: what still needs searching (empty if covered)
- conflicts: disagreements between sources (empty if none)

Output valid JSON:
{{
  "sub_topics": [
    {{"question": "...", "status": "partial", "key_facts": ["..."], "gaps": ["..."], "conflicts": ["..."]}}
  ],
  "insights": ["2-4 concise bullets for UI — key discoveries this round"]
}}

Do NOT write a flowing essay. Be factual and compact.
"""

COVERAGE_STOP_PROMPT = """\
Decide if research coverage is sufficient to write the final answer.

**Question:** {question}
**Sub-questions:** {sub_questions}
**Scratchpad:** {scratchpad}
**Round:** {round_num} of {max_rounds}

Stop (YES) when:
- Every sub-question is covered or partial with evidence from at least 2 independent sources
- Remaining gaps are unlikely to be filled by more searching

Continue (NO) when obvious gaps remain and rounds budget allows.

Reply: YES or NO — one sentence reason.
"""

FINAL_ANSWER_PROMPT = """\
Write the final research answer for the user.

**Question:** {question}
**Research scratchpad (all evidence gathered):**
{scratchpad}

**Source index for citations:**
{citation_index}

Requirements:
- Lead with a direct answer to the question
- Include facts, numbers, nuances that justify the depth of research
- Choose format yourself (prose, bullets, table, sections) — no fixed template
- No filler, no "in this article", no repetition
- Cite inline immediately after claims: [^1], [^2] (no space before bracket)
- Up to 3 citation numbers per sentence
- Do NOT add a References section — sources are shown separately in the UI
- If data is incomplete for a sub-topic, state it briefly; do not invent
- Length: as much as needed for completeness — no minimum or maximum word count
"""

# ---------------------------------------------------------------------------
# DeepResearcher
# ---------------------------------------------------------------------------
class DeepResearcher:
    """
    Iterative research engine following the IterResearch pattern.

    Each round: LLM generates queries → SearXNG search → LLM extracts from
    top pages → LLM updates scratchpad → LLM decides continue/stop → compose answer.
    """

    def __init__(
        self,
        llm_endpoint: str,
        llm_model: str,
        llm_headers: dict | None = None,
        max_rounds: int = 10,
        max_time: int = 300,
        max_urls_per_query: int = 5,
        queries_round1: int = 5,
        queries_followup: int = 4,
        max_content_chars: int = 15000,
        max_report_tokens: int = 8192,
        scratchpad_max_tokens: int = 4096,
        extraction_timeout: int | None = 0,
        planning_timeout: int | None = 0,
        query_timeout: int | None = 0,
        extraction_concurrency: int = 3,
        heavy_llm_timeout: int | None = 0,
        min_rounds: int = 2,
        max_empty_rounds: int = 2,
        progress_callback: Callable | None = None,
        search_provider: str | None = None,
        cursor_backend=None,
        extract_llm_endpoint: str | None = None,
        extract_llm_model: str | None = None,
        extract_llm_headers: dict | None = None,
        evidence_store=None,
        session_id: str = "",
        compression_backend: str = "heuristic",
        coverage_threshold: float = 0.75,
        # Backward compat alias — deprecated, use max_urls_per_query
        max_urls_per_round: int | None = None,
    ):
        self.llm_endpoint = llm_endpoint
        self.llm_model = llm_model
        self.llm_headers = llm_headers
        self._cursor_backend = cursor_backend
        self.extract_llm_endpoint = (extract_llm_endpoint or "").strip() or None
        self.extract_llm_model = (extract_llm_model or "").strip() or None
        self.extract_llm_headers = extract_llm_headers
        self.search_provider_override = search_provider
        self.max_rounds = max_rounds
        self.max_time = max_time
        if max_urls_per_round is not None:
            self.max_urls_per_query = max_urls_per_round
        else:
            self.max_urls_per_query = max_urls_per_query
        self.queries_round1 = queries_round1
        self.queries_followup = queries_followup
        self.max_content_chars = max_content_chars
        self.max_report_tokens = max_report_tokens
        self.scratchpad_max_tokens = scratchpad_max_tokens
        self.extraction_timeout = self._normalize_llm_timeout(extraction_timeout)
        self.planning_timeout = self._normalize_llm_timeout(planning_timeout)
        self.query_timeout = self._normalize_llm_timeout(query_timeout)
        self.extraction_concurrency = max(1, int(extraction_concurrency or 3))
        self.heavy_llm_timeout = self._normalize_llm_timeout(heavy_llm_timeout)
        self.min_rounds = min_rounds
        self.max_empty_rounds = max_empty_rounds
        self._progress = progress_callback
        self._cancelled = False
        self._start_time: float = 0
        self._step_counter: int = 0
        self._reading_step: int | None = None
        self._reading_pages: list[dict[str, str]] = []
        self.queries_used: set[str] = set()
        self.urls_fetched: set[str] = set()
        self.analyzed_urls: list[dict[str, str]] = []
        self.round_count: int = 0
        self.providers_used: list[str] = []
        self.findings: list[dict] = []
        self.scratchpad: str = ""
        self.plan_sub_questions: list[str] = []
        self.citation_map: list[dict] = []
        self.research_plan: str = ""
        self.extract_ok: int = 0
        self.extract_failed: int = 0
        self.extract_timeout: int = 0
        self.pages_fetched: int = 0
        self.evidence_store = evidence_store
        self.session_id = (session_id or "").strip()
        self.compression_backend = (compression_backend or "heuristic").strip().lower()
        try:
            self.coverage_threshold = float(coverage_threshold)
        except (TypeError, ValueError):
            self.coverage_threshold = 0.75
        self._pending_outbound: list[dict] = []
        self._query_strategies: dict[str, str] = {}

    @staticmethod
    def _normalize_llm_timeout(value: int | None) -> int | None:
        """Return seconds cap, or None when research should not cap LLM reads."""
        if value is None:
            return None
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        return None if n <= 0 else n

    @staticmethod
    def _is_extraction_timeout_error(exc: Exception) -> bool:
        """True when an extraction LLM call likely failed on timeout."""
        text = str(exc).lower()
        if "timeout" in text or "timed out" in text:
            return True
        detail = getattr(exc, "detail", None)
        if detail is not None and ("timeout" in str(detail).lower() or "timed out" in str(detail).lower()):
            return True
        try:
            import httpx

            if isinstance(exc, httpx.TimeoutException):
                return True
        except ImportError:
            pass
        return False

    def cancel(self):
        """Request cooperative cancellation of the research loop."""
        self._cancelled = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def research(
        self,
        question: str,
        prior_report: str = "",
        prior_findings: list[dict] | None = None,
        prior_urls: set[str] | None = None,
    ) -> str:
        """Run iterative research and return the final answer.

        Args:
            question: The research question.
            prior_report: Previous scratchpad to continue from (continuation API).
            prior_findings: Previous findings to build on.
            prior_urls: URLs already visited (won't be re-fetched).
        """
        self._start_time = time.time()
        findings: list[dict] = list(prior_findings) if prior_findings else []
        scratchpad = prior_report or ""
        if not scratchpad and self.session_id:
            scratchpad = self._load_latest_scratchpad() or ""

        if self.evidence_store is None:
            try:
                from src.evidence_store import EvidenceStore
                self.evidence_store = EvidenceStore()
            except Exception as exc:
                logger.info("EvidenceStore unavailable: %s", exc)
                self.evidence_store = None

        self._emit(phase="planning")
        self.research_plan = await self._create_plan(question)
        logger.info(f"Research plan: {self.research_plan[:200]}")

        if prior_urls:
            for url in prior_urls:
                key = normalize_url(url) or url
                self.urls_fetched.add(key)
        self.findings = findings
        consecutive_empty_rounds = 0

        for round_num in range(1, self.max_rounds + 1):
            self.round_count = round_num
            if self._cancelled:
                logger.info(f"Research cancelled after {round_num - 1} rounds")
                break
            if self._time_exceeded():
                logger.info(f"Time limit reached after {round_num - 1} rounds")
                break

            logger.info(f"=== Research Round {round_num} ===")
            gaps = self._extract_gaps_from_scratchpad(scratchpad)
            queries = await self._generate_queries(question, scratchpad, round_num, gaps)
            if not queries:
                logger.warning(f"Round {round_num}: no queries generated, stopping")
                break

            self._emit(
                phase="searching",
                step_description=f"Searching round {round_num}",
                round=round_num,
                queries=queries,
                query_preview=queries[0] if queries else "",
                total_sources=len(self.urls_fetched),
                total_findings=len(findings),
            )

            urls_before = len(self.urls_fetched)
            round_findings = await self._search_and_extract(queries, question)
            sources_found = len(self.urls_fetched) - urls_before

            if round_findings:
                findings.extend(round_findings)
                consecutive_empty_rounds = 0
                logger.info(f"Round {round_num}: extracted {len(round_findings)} findings")
            else:
                consecutive_empty_rounds += 1
                logger.info(
                    f"Round {round_num}: no new findings "
                    f"({consecutive_empty_rounds} consecutive empty)"
                )
                if consecutive_empty_rounds >= self.max_empty_rounds:
                    logger.warning(
                        f"Search appears to be down — "
                        f"{self.max_empty_rounds} consecutive rounds with no results"
                    )
                    err_detail = getattr(self, '_last_search_error', 'unknown error')
                    self._emit(phase="error", message=f"Search engine unavailable: {err_detail}")
                    if not findings:
                        return (
                            f"**Search unavailable** — Web search failed after "
                            f"{round_num} rounds. Error: {err_detail}\n\n"
                            "Please check your search provider settings and ensure the service is running."
                        )
                    break

            if findings:
                scratchpad = await self._update_scratchpad(
                    question, self.plan_sub_questions, scratchpad, round_findings,
                )
                self.scratchpad = scratchpad
                self._persist_scratchpad(round_num, scratchpad)
                insights = self._extract_insights_for_ui(scratchpad)
                sources_preview = self._sources_preview_from_round(round_findings, limit=5)
                self._emit(
                    phase="analyzing",
                    step_description="Updating research notes",
                    round=round_num,
                    insights=insights,
                    sources_found=sources_found,
                    sources_preview=sources_preview,
                    sources_more=max(0, len(self.urls_fetched) - len(sources_preview)),
                    total_sources=len(self.urls_fetched),
                    total_findings=len(findings),
                )

            if round_num >= self.min_rounds:
                if await self._coverage_complete(question, scratchpad, self.plan_sub_questions, round_num):
                    logger.info(f"Coverage complete after round {round_num}")
                    break

        self.scratchpad = scratchpad
        self._emit(
            phase="writing",
            step_description="Composing final answer",
            total_sources=len(self.urls_fetched),
            total_findings=len(findings),
        )

        if not scratchpad and not findings:
            return "No information could be gathered for this question."

        final = await self._compose_answer(question, scratchpad, self.plan_sub_questions)
        if not final and findings:
            logger.warning(
                "Compose produced no answer; returning %d gathered finding(s) as fallback",
                len(findings),
            )
            return self._fallback_report(question, findings)

        elapsed = time.time() - self._start_time
        logger.info(
            f"Research complete: {self.round_count} rounds, "
            f"{len(findings)} findings, {len(self.urls_fetched)} URLs, "
            f"{elapsed:.1f}s"
        )
        return final or self._fallback_report(question, findings)

    # ------------------------------------------------------------------
    # LLM helper
    # ------------------------------------------------------------------
    async def _llm(self, messages: list[dict], temperature: float = 0.3,
                   max_tokens: int = 4096, timeout: int | None = None) -> str:
        """Call the LLM asynchronously and strip thinking tags."""
        if self._cursor_backend is not None:
            response = await self._cursor_backend.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            return strip_thinking(response)

        from src.llm_core import llm_call_async
        response = await llm_call_async(
            url=self.llm_endpoint,
            model=self.llm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            headers=self.llm_headers,
            timeout=timeout,
        )
        return strip_thinking(response)

    async def _llm_extract(self, messages: list[dict], temperature: float = 0.2,
                           max_tokens: int = 2048, timeout: int | None = None) -> str:
        """Per-URL extraction LLM — HTTP endpoint when hybrid mode is configured."""
        if not self.extract_llm_endpoint or not self.extract_llm_model:
            return await self._llm(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )

        from src.llm_core import llm_call_async
        response = await llm_call_async(
            url=self.extract_llm_endpoint,
            model=self.extract_llm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            headers=self.extract_llm_headers,
            timeout=timeout,
        )
        return strip_thinking(response)

    # ------------------------------------------------------------------
    # PLAN: create research strategy
    # ------------------------------------------------------------------
    async def _create_plan(self, question: str) -> str:
        """LLM analyzes the question and creates a research plan."""
        prompt = current_date_context() + RESEARCH_PLAN_PROMPT.format(question=question)
        try:
            response = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1024,
                timeout=getattr(self, "planning_timeout", None),
            )
            # Try to parse as JSON for structured plan
            parsed = self._parse_json_object(response)
            if parsed:
                if parsed.get("sub_questions"):
                    self.plan_sub_questions = [
                        str(q) for q in parsed["sub_questions"] if q
                    ]
                parts = []
                if parsed.get("sub_questions"):
                    parts.append("Sub-questions: " + "; ".join(parsed["sub_questions"]))
                if parsed.get("key_topics"):
                    parts.append("Key topics: " + ", ".join(parsed["key_topics"]))
                if parsed.get("success_criteria"):
                    parts.append("Success: " + parsed["success_criteria"])
                return "\n".join(parts) if parts else response
            return response
        except Exception as e:
            logger.warning(f"Research planning failed: {e}")
            self._emit(phase="warning", message="Planning step failed, proceeding with direct search")
            return ""

    def _fallback_search_queries(
        self,
        question: str,
        *,
        round_num: int,
        gaps: list[str] | None,
        num_queries: int,
    ) -> list[str]:
        """Запасные поисковые запросы, если LLM не вернул разбираемый JSON."""
        candidates: list[str] = []

        for sq in self.plan_sub_questions:
            text = str(sq).strip()
            if text:
                candidates.append(text)

        for gap in gaps or []:
            text = str(gap).strip()
            if text:
                candidates.append(text)

        lines = [ln.strip() for ln in question.splitlines() if ln.strip()]
        if lines:
            candidates.append(lines[0][:240])
        condensed = " ".join(question.split())
        if condensed:
            candidates.append(condensed[:240])

        if round_num > 1 and gaps:
            for gap in gaps[:2]:
                text = str(gap).strip()
                if text:
                    candidates.append(f"{text} {condensed[:120]}".strip()[:240])

        out: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            item = candidate.strip()
            if not item or item in self.queries_used or item in seen:
                continue
            seen.add(item)
            out.append(item)
            if len(out) >= num_queries:
                break
        return out

    # ------------------------------------------------------------------
    # THINK: generate search queries
    # ------------------------------------------------------------------
    async def _generate_queries(
        self,
        question: str,
        scratchpad: str,
        round_num: int,
        gaps: list[str] | None = None,
    ) -> list[str]:
        if round_num == 1:
            num_queries = self.queries_round1
            round_instruction = (
                "This is the first round — generate broad, diverse queries "
                "that explore the key facets of the question."
            )
        else:
            num_queries = self.queries_followup
            round_instruction = (
                "We already have partial findings. Generate targeted follow-up "
                "queries to fill gaps, verify claims, or explore specific aspects "
                "that the scratchpad doesn't yet cover well."
            )

        gaps_text = "\n".join(f"- {g}" for g in (gaps or [])) or "(none identified)"

        prompt = current_date_context() + QUERY_GEN_PROMPT.format(
            question=question,
            research_plan=self.research_plan or "(No plan — search broadly.)",
            scratchpad=scratchpad or "(No findings yet.)",
            gaps=gaps_text,
            round_num=round_num,
            num_queries=num_queries,
            round_instruction=round_instruction,
        )

        try:
            response = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=4096,
                timeout=getattr(self, "query_timeout", None),
            )
            parsed_items = self._parse_query_items(response)
            queries = [item["query"] for item in parsed_items if item.get("query")]
            strategies = getattr(self, "_query_strategies", None)
            if strategies is None:
                self._query_strategies = {}
                strategies = self._query_strategies
            for item in parsed_items:
                q = item.get("query", "")
                if q:
                    strategies[q] = item.get("strategy", "web")
            if not queries:
                logger.warning(
                    "Round %s: LLM returned no parseable queries (preview: %r)",
                    round_num,
                    (response or "")[:160],
                )
                queries = self._fallback_search_queries(
                    question,
                    round_num=round_num,
                    gaps=gaps,
                    num_queries=num_queries,
                )
                if queries:
                    self._emit(
                        phase="warning",
                        message="Модель не вернула JSON-запросы — используем запасной набор",
                    )
            new_queries = [q for q in queries if q not in self.queries_used]
            self.queries_used.update(new_queries)
            logger.info(f"Round {round_num} queries: {new_queries}")
            return new_queries
        except Exception as e:
            logger.error(f"Query generation failed: {e}")
            self._emit(phase="warning", message=f"Query generation failed: {e}")
            fallback = self._fallback_search_queries(
                question,
                round_num=round_num,
                gaps=gaps,
                num_queries=num_queries,
            )
            new_queries = [q for q in fallback if q not in self.queries_used]
            self.queries_used.update(new_queries)
            if new_queries:
                logger.info(f"Round {round_num} fallback queries: {new_queries}")
            return new_queries

    # ------------------------------------------------------------------
    # SEARCH + EXTRACT
    # ------------------------------------------------------------------
    async def _search_and_extract(self, queries: list[str],
                                  question: str) -> list[dict]:
        """Search each query and extract relevant info from top results."""
        all_findings: list[dict] = []

        # Search all queries in parallel
        search_tasks = [self._search(q) for q in queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # Collect URLs to fetch from all search results, then rank the pool
        # before fetch so budget goes to the most relevant pages.
        urls_to_fetch = []
        round_url_cap = self.max_urls_per_query * len(queries)
        for result in search_results:
            if isinstance(result, Exception):
                logger.warning(f"Search error: {result}")
                continue
            if not result:
                continue
            for r in result:
                url = r.get("url", "")
                key = normalize_url(url) or url
                if url and key not in self.urls_fetched:
                    urls_to_fetch.append(r)

        if urls_to_fetch:
            try:
                from services.search.ranking import rank_search_results
                urls_to_fetch = rank_search_results(question, urls_to_fetch)
            except Exception as exc:
                logger.warning("Research ranking failed, using provider order: %s", exc)
            urls_to_fetch = self._prioritize_official_docs(urls_to_fetch, round_url_cap)

        selected: list[dict] = []
        for r in urls_to_fetch:
            if len(selected) >= round_url_cap:
                break
            url = r.get("url", "")
            key = normalize_url(url) or url
            if not url or key in self.urls_fetched:
                continue
            self.urls_fetched.add(key)
            self.analyzed_urls.append({
                "url": url,
                "title": r.get("title", "") or url,
            })
            selected.append(r)

        # Optional outbound 1-hop follow-ups queued from previous extracts.
        for pending in list(self._pending_outbound):
            if len(selected) >= round_url_cap:
                break
            url = pending.get("url", "")
            key = normalize_url(url) or url
            if not url or key in self.urls_fetched:
                continue
            self.urls_fetched.add(key)
            self.analyzed_urls.append({
                "url": url,
                "title": pending.get("title", "") or url,
            })
            selected.append(pending)
        self._pending_outbound = []

        if self._cancelled or self._time_exceeded():
            return all_findings

        # One timeline step for the whole reading batch — not one empty
        # "reading" row per URL (that flooded the UI with noise).
        if selected:
            preview = self._sources_preview_from_results(selected, limit=8)
            self._reading_pages = list(preview)
            self._emit(
                phase="reading",
                step_description=f"Reading {len(selected)} pages",
                sources_preview=preview,
                sources_more=max(0, len(selected) - len(preview)),
                total_sources=len(self.urls_fetched),
                bump_step=True,
            )
            self._reading_step = self._step_counter

        # Fetch and extract URLs with backpressure. Local model servers often
        # serialize requests behind one GPU; flooding them makes every request
        # slower and can trip the extraction timeout.
        semaphore = asyncio.Semaphore(self.extraction_concurrency)

        async def _bounded_extract(result: dict) -> dict | None:
            async with semaphore:
                return await self._fetch_and_extract(result["url"], question, result.get("title", ""))

        extract_tasks = [_bounded_extract(r) for r in selected]
        results_gathered = await asyncio.gather(*extract_tasks, return_exceptions=True)

        for result in results_gathered:
            if isinstance(result, Exception):
                logger.warning(f"Extraction error: {result}")
                continue
            if result:
                all_findings.append(result)

        return all_findings

    async def _search(self, query: str) -> list[dict]:
        """Run a search query using the configured research search provider."""
        try:
            strategies = getattr(self, "_query_strategies", None) or {}
            strategy = strategies.get(query, "web")
            specialty_hits: list[dict] = []
            if strategy in {"academic", "github", "pdf"}:
                try:
                    from services.search.academic import search_academic
                    specialty_hits = await asyncio.to_thread(
                        search_academic, query, strategy, 10,
                    ) or []
                    if specialty_hits and strategy not in self.providers_used:
                        self.providers_used.append(strategy)
                except Exception as exc:
                    logger.warning("Academic search (%s) failed: %s", strategy, exc)
                    specialty_hits = []

            # Product/SDK/docs questions must still hit the open web. Specialty
            # corpora (arxiv/github/pdf) alone routinely miss vendor docs like
            # cursor.com/docs while surfacing forums and secondary roundups.
            run_web = (
                strategy in {"web", "news"}
                or not specialty_hits
                or self._query_needs_web_docs(query, strategy)
            )
            if not run_web:
                return specialty_hits

            from src.search.providers import _get_search_settings
            from src.search.core import _call_provider, _build_provider_chain

            settings = _get_search_settings()
            provider = (self.search_provider_override or "").strip()
            if not provider:
                provider = (settings.get("research_search_provider") or "").strip()
            if not provider:
                provider = settings.get("search_provider", "searxng")

            if provider == "disabled":
                logger.info("Search is disabled for research")
                return specialty_hits

            # Try primary provider, then fallbacks
            chain = _build_provider_chain(provider)
            raised = False
            web_hits: list[dict] = []
            for prov in chain:
                try:
                    results = await asyncio.to_thread(_call_provider, prov, query, 10)
                    if results:
                        try:
                            from services.search.ranking import rank_search_results
                            results = rank_search_results(query, results)
                        except Exception as rank_exc:
                            logger.debug("Per-query ranking skipped: %s", rank_exc)
                        logger.info(f"Research search: {prov} returned {len(results)} results")
                        if prov not in self.providers_used:
                            self.providers_used.append(prov)
                        web_hits = results
                        break
                except Exception as e:
                    raised = True
                    logger.warning(f"Research search: {prov} failed: {e}")
                    self._last_search_error = f"{prov}: {e}"
            if not web_hits and not specialty_hits:
                if not raised:
                    self._last_search_error = (
                        f"no results from search provider(s): "
                        f"{', '.join(chain) if chain else provider}"
                    )
                return []

            merged = list(specialty_hits) + list(web_hits)
            # Dedup by normalized URL while preserving specialty-first order.
            seen: set[str] = set()
            deduped: list[dict] = []
            for item in merged:
                url = (item.get("url") or "").strip()
                key = normalize_url(url) or url
                if not key or key in seen:
                    continue
                seen.add(key)
                deduped.append(item)
            try:
                from services.search.ranking import rank_search_results
                return rank_search_results(query, deduped)
            except Exception:
                return deduped
        except Exception as e:
            logger.error(f"Search failed for '{query}': {e}")
            self._last_search_error = str(e)
            return []

    async def _fetch_and_extract(self, url: str, question: str,
                                 title: str) -> dict | None:
        """Fetch a URL's content and use LLM to extract relevant info."""
        display = title or url
        # Live phase update only — reuses the batch "Reading N pages" step.
        pages = list(getattr(self, "_reading_pages", []) or [])
        try:
            domain = urlparse(url).netloc or url
        except Exception:
            domain = url
        if not any((p.get("url") == url) for p in pages):
            pages.append({"domain": domain, "url": url, "title": display})
            self._reading_pages = pages
        preview = pages[:12]
        self._emit(
            phase="reading",
            step_description=f"Reading {len(pages)} pages",
            url=url,
            title=display,
            sources_preview=preview,
            sources_more=max(0, len(pages) - len(preview)),
            total_sources=len(self.urls_fetched),
            bump_step=False,
            step=getattr(self, "_reading_step", None),
        )
        try:
            from src.search import fetch_webpage_content
            page = await asyncio.to_thread(fetch_webpage_content, url, 10)
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return None

        if not page.get("success") or not page.get("content"):
            return None

        self.pages_fetched += 1
        content = page["content"]
        result = await self._extract_page_content(
            url,
            question,
            title,
            page,
            content,
            allow_retry=True,
        )
        if result is not None:
            self.extract_ok += 1
            self._index_finding_evidence(url, content, result)
            self._maybe_queue_outbound_links(url, question, content, result)
        return result

    async def _extract_page_content(
        self,
        url: str,
        question: str,
        title: str,
        page: dict,
        content: str,
        *,
        allow_retry: bool,
    ) -> dict | None:
        """Run LLM extraction on fetched page text."""
        max_chars = self.max_content_chars
        prepared = content
        if self.compression_backend != "off":
            try:
                from services.search.compression import compress_content
                prepared = compress_content(
                    content,
                    question,
                    backend=self.compression_backend,
                    max_chars=max_chars,
                )
            except Exception as exc:
                logger.warning("Compression failed for %s: %s", url, exc)
                prepared = content

        if len(prepared) > max_chars:
            truncated = prepared[:max_chars]
            last_para = truncated.rfind("\n\n")
            if last_para > max_chars * 0.8:
                prepared = truncated[:last_para]
            else:
                prepared = truncated

        try:
            response = await self._llm_extract(
                [
                    {"role": "user", "content": EXTRACTOR_SYSTEM.format(goal=question)},
                    untrusted_context_message("webpage", prepared),
                ],
                temperature=0.2,
                max_tokens=2048,
                timeout=self.extraction_timeout,
            )
            parsed = self._parse_json_object(response)
            if parsed:
                parsed["url"] = url
                parsed["title"] = title or page.get("title", "")
                parsed["og_image"] = page.get("og_image", "")
                if is_low_quality(parsed.get("summary", "")):
                    logger.info(f"Skipping low-quality extraction from {url}")
                    return None
                return parsed
            return {
                "url": url,
                "title": title or page.get("title", ""),
                "og_image": page.get("og_image", ""),
                "rational": "LLM extraction (raw)",
                "evidence": response[:3000],
                "summary": response[:500],
            }
        except Exception as e:
            if self._is_extraction_timeout_error(e):
                self.extract_timeout += 1
                if allow_retry and len(prepared) > 8000:
                    logger.info(
                        "Retrying extraction for %s with reduced content after timeout",
                        url,
                    )
                    retry = await self._extract_page_content(
                        url,
                        question,
                        title,
                        page,
                        prepared[:8000],
                        allow_retry=False,
                    )
                    if retry is not None:
                        return retry
            self.extract_failed += 1
            logger.warning(f"LLM extraction failed for {url}: {e}")
            return None

    # ------------------------------------------------------------------
    # SCRATCHPAD + COMPOSE
    # ------------------------------------------------------------------
    async def _update_scratchpad(
        self,
        question: str,
        sub_questions: list[str],
        scratchpad: str,
        round_findings: list[dict],
    ) -> str:
        """LLM updates structured scratchpad from new round findings."""
        new_findings_text = self._format_findings_for_scratchpad(round_findings)
        sub_q_text = "\n".join(f"- {q}" for q in sub_questions) or "(from research plan)"

        prompt = SCRATCHPAD_UPDATE_PROMPT.format(
            question=question,
            sub_questions=sub_q_text,
            scratchpad=scratchpad or "(empty — first round)",
            new_findings=new_findings_text,
        )

        try:
            # Default 180s when attribute missing (bare __new__ in tests / legacy).
            # Explicit None means unlimited (research_handler sets this for local LLMs).
            heavy_timeout = getattr(self, "heavy_llm_timeout", 180)
            response = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=getattr(self, "scratchpad_max_tokens", 4096),
                timeout=heavy_timeout,
            )
            parsed = self._parse_json_object(response)
            if parsed:
                validated = validate_scratchpad(parsed)
                if validated is not None:
                    return validated.model_dump_json(indent=2)
                logger.info("Scratchpad failed schema validation; keeping raw JSON")
                return json.dumps(parsed, ensure_ascii=False, indent=2)
            return response.strip() or scratchpad
        except Exception as e:
            logger.error(f"Scratchpad update failed: {e}")
            self._emit(phase="warning", message="Scratchpad update failed, keeping previous notes")
            return scratchpad

    async def _coverage_complete(
        self,
        question: str,
        scratchpad: str,
        sub_questions: list[str],
        round_num: int,
    ) -> bool:
        """Decide if research coverage is sufficient to compose the final answer."""
        if self._scratchpad_coverage_ok(scratchpad):
            return True

        if (
            self.evidence_store is not None
            and self.coverage_threshold > 0
            and sub_questions
        ):
            try:
                score = self.evidence_store.coverage_score(sub_questions)
                logger.info(
                    "Embedding coverage score (round %s): %.3f (threshold=%.3f)",
                    round_num,
                    score,
                    self.coverage_threshold,
                )
                if score >= self.coverage_threshold:
                    return True
            except Exception as exc:
                logger.debug("Embedding coverage check failed: %s", exc)

        sub_q_text = "\n".join(f"- {q}" for q in sub_questions) or "(none)"
        prompt = COVERAGE_STOP_PROMPT.format(
            question=question,
            sub_questions=sub_q_text,
            scratchpad=scratchpad or "(empty)",
            round_num=round_num,
            max_rounds=self.max_rounds,
        )

        try:
            response = await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=128,
            )
            clean = strip_thinking(response).strip()
            answer = re.sub(r'^[\s*_`"\'>#\-]+', '', clean).upper()
            should_stop = answer.startswith("YES")
            logger.info(f"Coverage decision (round {round_num}): {clean[:120]}")
            return should_stop
        except Exception as e:
            logger.warning(f"Coverage decision failed: {e}")
            return False

    async def _compose_answer(
        self,
        question: str,
        scratchpad: str,
        sub_questions: list[str],
    ) -> str:
        """LLM writes the final user-facing answer from scratchpad + citations."""
        citation_index, self.citation_map = self._build_citation_index()

        prompt = FINAL_ANSWER_PROMPT.format(
            question=question,
            scratchpad=scratchpad or "(no structured notes)",
            citation_index=citation_index,
        )

        try:
            return await self._llm(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=getattr(self, "max_report_tokens", 8192),
                timeout=getattr(self, "heavy_llm_timeout", 180),
            )
        except Exception as e:
            logger.error(f"Final answer composition failed: {e}")
            return ""

    def _build_citation_index(self) -> tuple[str, list[dict]]:
        """Build [^N] citation index from unique URLs in findings."""
        seen: set[str] = set()
        citation_map: list[dict] = []
        lines: list[str] = []

        for finding in self.findings:
            if not isinstance(finding, dict):
                continue
            url = (finding.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            cid = len(citation_map) + 1
            title = finding.get("title", "") or url
            citation_map.append({"id": cid, "url": url, "title": title})
            lines.append(f"[^{cid}] {url} — {title}")

        return ("\n".join(lines) if lines else "(no sources)"), citation_map

    def _extract_insights_for_ui(self, scratchpad: str) -> list[str]:
        """Extract insights bullets from scratchpad JSON for progress UI."""
        parsed = self._parse_json_object(scratchpad)
        if not parsed:
            return []
        insights = parsed.get("insights") or []
        return [str(i) for i in insights if i][:4]

    def _extract_gaps_from_scratchpad(self, scratchpad: str) -> list[str]:
        """Collect gap strings from scratchpad sub_topics."""
        parsed = self._parse_json_object(scratchpad)
        if not parsed:
            return []
        gaps: list[str] = []
        for topic in parsed.get("sub_topics") or []:
            if not isinstance(topic, dict):
                continue
            for gap in topic.get("gaps") or []:
                if gap:
                    gaps.append(str(gap))
        return gaps

    def _scratchpad_coverage_ok(self, scratchpad: str) -> bool:
        """Heuristic: all sub_topics covered or partial with ≥2 URLs in key_facts."""
        parsed = self._parse_json_object(scratchpad)
        if not parsed:
            return False
        sub_topics = parsed.get("sub_topics") or []
        if not sub_topics:
            return False

        url_pattern = re.compile(r"https?://[^\s\]\)\"']+")

        for topic in sub_topics:
            if not isinstance(topic, dict):
                return False
            status = (topic.get("status") or "").lower()
            if status == "missing":
                return False
            if status == "partial":
                facts = topic.get("key_facts") or []
                urls = set()
                for fact in facts:
                    urls.update(url_pattern.findall(str(fact)))
                if len(urls) < 2:
                    return False
        return True

    @staticmethod
    def _sources_preview_from_round(
        round_findings: list[dict],
        limit: int = 5,
    ) -> list[dict[str, str]]:
        """Build domain preview list for progress events."""
        return DeepResearcher._sources_preview_from_results(round_findings, limit=limit)

    @staticmethod
    def _sources_preview_from_results(
        results: list[dict],
        limit: int = 5,
    ) -> list[dict[str, str]]:
        """Build domain/title preview list from search or finding dicts."""
        preview: list[dict[str, str]] = []
        seen_domains: set[str] = set()
        for f in results:
            url = (f.get("url") or "").strip()
            if not url:
                continue
            try:
                domain = urlparse(url).netloc or url
            except Exception:
                domain = url
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            item: dict[str, str] = {"domain": domain, "url": url}
            title = (f.get("title") or "").strip()
            if title:
                item["title"] = title
            preview.append(item)
            if len(preview) >= limit:
                break
        return preview

    def _format_findings_for_scratchpad(self, findings: list[dict]) -> str:
        """Format findings as summary + url only (no full evidence)."""
        parts = []
        for i, f in enumerate(findings, 1):
            url = f.get("url", "unknown")
            title = f.get("title", "")
            summary = f.get("summary", "") or (f.get("evidence", "")[:500] if f.get("evidence") else "")
            parts.append(f"**{i}** [{title}]({url})\n{summary}")
        return "\n\n".join(parts) if parts else "(no new findings)"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _emit(self, **kwargs):
        """Send a progress event via the callback, if one is registered.

        Args:
            bump_step: When False, reuse the current step id (live phase
                updates for the same milestone). Default True creates a new
                timeline step. Pass ``step=`` to pin a specific step id.
        """
        bump_step = bool(kwargs.pop("bump_step", True))
        pinned = kwargs.pop("step", None) if "step" in kwargs else None
        if pinned is not None:
            step = int(pinned)
            self._step_counter = max(getattr(self, "_step_counter", 0), step)
        elif bump_step:
            step = getattr(self, "_step_counter", 0) + 1
            self._step_counter = step
        else:
            step = getattr(self, "_step_counter", 0) or 1
        kwargs["step"] = step
        # Backward compat: queries as count if list passed
        if isinstance(kwargs.get("queries"), list):
            qlist = kwargs["queries"]
            kwargs["queries"] = qlist
            kwargs.setdefault("query_preview", qlist[0] if qlist else "")
        progress = getattr(self, "_progress", None)
        if progress:
            try:
                progress(kwargs)
            except Exception:
                pass

    def _time_exceeded(self) -> bool:
        if self.max_time <= 0:
            return False
        return (time.time() - self._start_time) > self.max_time

    # _strip_think_tags removed — use research_utils.strip_thinking()

    @staticmethod
    def _strip_code_block(text: str) -> str:
        """Strip markdown code-block fences (```json ... ```) if present."""
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
        return text.strip()

    def _parse_json_array(self, text: str) -> list[str]:
        """Extract a JSON array of strings from LLM output."""
        items = self._parse_query_items(text)
        return [item["query"] for item in items if item.get("query")]

    def _parse_query_items(self, text: str) -> list[dict[str, str]]:
        """Parse query list as strings or {query, strategy} objects."""
        text = self._strip_code_block(text)
        allowed = {"web", "academic", "github", "news", "pdf"}

        def _normalize_item(item: object) -> dict[str, str] | None:
            if isinstance(item, str):
                query = item.strip()
                if not query:
                    return None
                return {"query": query, "strategy": self._infer_strategy(query)}
            if isinstance(item, dict):
                query = str(item.get("query") or item.get("q") or "").strip()
                if not query:
                    return None
                strategy = str(item.get("strategy") or item.get("type") or "").strip().lower()
                if strategy not in allowed:
                    strategy = self._infer_strategy(query)
                return {"query": query, "strategy": strategy}
            return None

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                out = []
                for item in parsed:
                    normalized = _normalize_item(item)
                    if normalized:
                        out.append(normalized)
                if out:
                    return out
        except json.JSONDecodeError:
            pass

        # Handle truncated arrays — e.g. '["query one", "query two", "query thr'
        # Repair from the LAST array start so an echoed example array earlier
        # in the reply is not harvested into the real query set.
        last_start = text.rfind('[')
        truncated = last_start != -1 and ']' not in text[last_start:]
        if truncated:
            complete_items = re.findall(r'"([^"]*)"', text[last_start:])
            if complete_items:
                logger.info(f"Repaired truncated JSON array: recovered {len(complete_items)} items")
                return [
                    {"query": item, "strategy": self._infer_strategy(item)}
                    for item in complete_items
                    if item.strip()
                ]

        # Greedy match to capture the full outermost array
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, list):
                    out = []
                    for item in parsed:
                        normalized = _normalize_item(item)
                        if normalized:
                            out.append(normalized)
                    if out:
                        return out
            except json.JSONDecodeError:
                pass

        # Multiple complete arrays in one reply (e.g. the model echoes the
        # prompt's Example: [...] before the real array). The greedy match
        # above spans them all and fails to parse, so scan non-greedily and
        # keep the LAST parseable array, which is the model's actual answer.
        last_parsed = None
        for m in re.finditer(r'\[[\s\S]*?\]', text):
            try:
                parsed = json.loads(m.group())
                if isinstance(parsed, list):
                    last_parsed = parsed
            except json.JSONDecodeError:
                continue
        if last_parsed is not None:
            out = []
            for item in last_parsed:
                normalized = _normalize_item(item)
                if normalized:
                    out.append(normalized)
            if out:
                return out

        # Last resort: harvest quoted strings from the first array start
        arr_start = text.find('[')
        if arr_start != -1:
            fragment = text[arr_start:]
            # Find the last complete quoted string
            complete_items = re.findall(r'"([^"]*)"', fragment)
            if complete_items:
                logger.info(f"Repaired truncated JSON array: recovered {len(complete_items)} items")
                return [
                    {"query": item, "strategy": self._infer_strategy(item)}
                    for item in complete_items
                    if item.strip()
                ]

        logger.warning(f"Could not parse JSON array from: {text[:200]}")
        return []

    @staticmethod
    def _infer_strategy(query: str) -> str:
        """Infer search strategy from query operators when tag is missing."""
        q = (query or "").lower()
        if "site:arxiv.org" in q or "site:semanticscholar.org" in q:
            return "academic"
        if "site:github.com" in q:
            return "github"
        if "filetype:pdf" in q:
            return "pdf"
        if "after:" in q or "before:" in q:
            return "news"
        return "web"

    @staticmethod
    def _query_needs_web_docs(query: str, strategy: str) -> bool:
        """True when specialty search alone is likely to miss vendor docs."""
        q = (query or "").lower()
        if strategy in {"web", "news"}:
            return True
        markers = (
            "sdk", "api", "documentation", "docs", "readme",
            "/docs", "typescript sdk", "python sdk", "rest api",
            "official docs", "api reference",
        )
        return any(m in q for m in markers)

    @staticmethod
    def _is_official_docs_url(url: str) -> bool:
        """Heuristic: vendor documentation paths, not forums/blogs."""
        u = (url or "").lower()
        if not u:
            return False
        if any(bad in u for bad in (
            "forum.", "/forums/", "reddit.com", "stackoverflow.com",
            "medium.com", "dev.to", "blogspot.",
        )):
            return False
        return any(tok in u for tok in (
            "/docs/", "/documentation/", "/api/", "/reference/",
            "docs.", "developer.", "developers.",
        ))

    @classmethod
    def _prioritize_official_docs(
        cls,
        ranked: list[dict],
        cap: int,
        *,
        min_docs: int = 2,
    ) -> list[dict]:
        """Keep ranking order but reserve slots for official docs URLs."""
        if not ranked or cap <= 0:
            return ranked
        docs = [r for r in ranked if cls._is_official_docs_url(r.get("url", ""))]
        if not docs:
            return ranked
        reserve = min(min_docs, len(docs), cap)
        chosen: list[dict] = []
        seen: set[int] = set()
        for r in docs[:reserve]:
            chosen.append(r)
            seen.add(id(r))
        for r in ranked:
            if len(chosen) >= cap:
                break
            if id(r) in seen:
                continue
            chosen.append(r)
            seen.add(id(r))
        # Append any leftovers so callers that ignore cap still see full pool.
        for r in ranked:
            if id(r) in seen:
                continue
            chosen.append(r)
        return chosen

    def _session_state_dir(self) -> Path | None:
        """Directory for per-round scratchpad serde, if session_id is set."""
        if not self.session_id:
            return None
        from src.constants import DEEP_RESEARCH_DIR
        path = Path(DEEP_RESEARCH_DIR) / self.session_id
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning("Cannot create research state dir %s: %s", path, exc)
            return None
        return path

    def _persist_scratchpad(self, round_num: int, scratchpad: str) -> None:
        """Write scratchpad_rN.json for rollback / partial recovery."""
        state_dir = self._session_state_dir()
        if state_dir is None or not scratchpad:
            return
        path = state_dir / f"scratchpad_r{round_num}.json"
        try:
            path.write_text(scratchpad, encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to persist scratchpad %s: %s", path, exc)

    def _load_latest_scratchpad(self) -> str:
        """Load the newest scratchpad_rN.json for this session, if any."""
        state_dir = self._session_state_dir()
        if state_dir is None:
            return ""
        files = sorted(state_dir.glob("scratchpad_r*.json"))
        if not files:
            return ""
        try:
            return files[-1].read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to load scratchpad from %s: %s", files[-1], exc)
            return ""

    def _index_finding_evidence(self, url: str, content: str, finding: dict) -> None:
        """Chunk page content and add it to the evidence store."""
        if self.evidence_store is None:
            return
        try:
            from src.chunking import chunk_text
            chunks = chunk_text(content or finding.get("evidence", "") or finding.get("summary", ""))
            if not chunks:
                summary = finding.get("summary") or finding.get("evidence") or ""
                if summary:
                    chunks = [summary]
            if chunks:
                self.evidence_store.add_finding(
                    url=url,
                    chunks=chunks,
                    title=finding.get("title", ""),
                )
        except Exception as exc:
            logger.debug("Evidence indexing skipped for %s: %s", url, exc)

    def _maybe_queue_outbound_links(
        self,
        url: str,
        question: str,
        content: str,
        finding: dict,
    ) -> None:
        """Queue up to 3 outbound links from index/list pages for the next round."""
        rational = str(finding.get("rational") or "").lower()
        looks_like_index = any(
            token in rational
            for token in ("index", "list page", "table of contents", "toc", "directory")
        )
        hrefs = re.findall(r"https?://[^\s\)\]\"'<>]+", content or "")
        if not looks_like_index and len(hrefs) < 8:
            return
        candidates: list[dict[str, str]] = []
        seen: set[str] = set()
        for href in hrefs:
            key = normalize_url(href) or href
            if not key or key in self.urls_fetched or key in seen:
                continue
            if key == (normalize_url(url) or url):
                continue
            seen.add(key)
            candidates.append({"url": href, "title": href})
            if len(candidates) >= 12:
                break
        if not candidates:
            return

        ranked = candidates
        if self.evidence_store is not None and hasattr(self.evidence_store, "rank_texts"):
            try:
                ranked = self.evidence_store.rank_texts(
                    question,
                    [c["url"] for c in candidates],
                    top_k=3,
                )
                ranked = [{"url": item, "title": item} for item in ranked]
            except Exception:
                ranked = candidates[:3]
        else:
            ranked = candidates[:3]

        for item in ranked[:3]:
            self._pending_outbound.append(item)

    def _parse_json_object(self, text: str) -> dict | None:
        """Extract a JSON object from LLM output."""
        text = self._strip_code_block(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Greedy match to capture the full outermost object
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return None

    def _format_findings(self, findings: list[dict]) -> str:
        """Format findings list into readable text for synthesis prompt."""
        parts = []
        for i, f in enumerate(findings, 1):
            url = f.get("url", "unknown")
            title = f.get("title", "")
            summary = f.get("summary", "")
            evidence = f.get("evidence", "")
            # Use summary if available, fall back to truncated evidence
            content = summary if summary else (evidence[:1000] if evidence else "(no content)")
            parts.append(f"**Finding {i}** — [{title}]({url})\n{content}")
        return "\n\n".join(parts)

    def _fallback_report(self, question: str, findings: list[dict]) -> str:
        """Compile gathered findings into a basic report.

        Used when compose did not complete but search rounds collected findings
        (#1551).
        """
        return (
            f"# {question}\n\n"
            "_Automatic composition did not complete, so this answer lists the "
            f"{len(findings)} finding(s) gathered during research._\n\n"
            f"{self._format_findings(findings)}"
        )

    def get_stats(self) -> dict:
        """Return research statistics."""
        elapsed = time.time() - self._start_time if self._start_time else 0
        stats = {
            "Duration": f"{elapsed:.1f}s",
            "Rounds": self.round_count,
            "Queries": len(self.queries_used),
            "URLs": len(self.urls_fetched),
            "Extracted": self.extract_ok,
            "ExtractFailed": self.extract_failed,
            "ExtractTimeouts": self.extract_timeout,
            "Model": self.llm_model,
        }
        if self.providers_used:
            stats["Search"] = ", ".join(self.providers_used)
        if self.extract_ok == 0 and len(self.urls_fetched) > 0:
            stats["FailureReason"] = "llm_extraction"
        return stats
