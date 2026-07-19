# src/goal_based_extractor.py
"""
Goal-based content extraction prompt inspired by Alibaba Tongyi DeepResearch.
"""

EXTRACTOR_SYSTEM = """Extract relevant information from a webpage for a given research goal.

Goal: {goal}

Task guidelines:
1. Locate the specific sections directly related to the goal within the provided webpage content.
2. "evidence": full quotes/context (up to 3 paragraphs) — stored in findings archive.
3. "summary": max 3 sentences — used for scratchpad and insights ONLY.
4. Organize evidence with logical flow, judging each piece's contribution to the goal.

Respond in JSON with exactly these fields: "rational", "evidence", "summary".

Example:
{{
    "rational": "This section discusses X which directly relates to the goal of understanding Y",
    "evidence": "Full quotes and context from the page...",
    "summary": "Concise summary of how this information answers the goal"
}}
"""
