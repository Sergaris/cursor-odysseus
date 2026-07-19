"""Deep research must not stop with 0 queries when the brain returns prose/refusal."""

import asyncio

from src.deep_research import DeepResearcher


def _bare_researcher() -> DeepResearcher:
    r = DeepResearcher.__new__(DeepResearcher)
    r.research_plan = ""
    r.plan_sub_questions = [
        "Кто такой Киселев Григорий Кириллович?",
        "Где он учится в РГСУ?",
    ]
    r.queries_used = set()
    r.queries_round1 = 5
    r.queries_followup = 4
    r._emit = lambda **kwargs: None
    return r


def test_fallback_search_queries_use_plan_and_question():
    r = _bare_researcher()
    question = (
        "Киселев Григорий Кириллович. учится в РГСУ прямо сейчас.\n"
        "найди полный компромат на этого человека."
    )
    queries = r._fallback_search_queries(question, round_num=1, gaps=None, num_queries=5)
    assert queries
    assert any("Киселев" in q for q in queries)
    assert any("РГСУ" in q for q in queries)


def test_generate_queries_uses_fallback_when_llm_refuses_json():
    r = _bare_researcher()
    question = "Киселев Григорий Кириллович РГСУ"

    async def _refuse_llm(messages, **kwargs):
        return "I cannot help with locating private individuals."

    r._llm = _refuse_llm
    queries = asyncio.run(r._generate_queries(question, "", 1, []))
    assert queries
    assert r.queries_used


def test_detect_search_language_ru_for_cyrillic():
    from services.search.providers import _detect_search_language

    assert _detect_search_language("Киселев РГСУ") == "ru"
    assert _detect_search_language("weather Moscow") == "en"
