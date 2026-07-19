"""Official-docs bias for product/SDK research — vendor-agnostic heuristics."""

from services.search.ranking import rank_search_results
from src.deep_research import DeepResearcher


def test_product_queries_prefer_official_docs_over_forum_and_blogs():
    results = [
        {
            "title": "Introducing the Example Python SDK",
            "url": "https://forum.example.com/t/introducing-the-python-sdk/161367",
            "snippet": "Public beta announcement on the forum.",
        },
        {
            "title": "Example SDK developer guide",
            "url": "https://blog.example.net/sdk-developer-guide",
            "snippet": "Secondary blog overview of Client.create.",
        },
        {
            "title": "TypeScript SDK",
            "url": "https://docs.example.com/sdk/typescript",
            "snippet": "Official documentation for the TypeScript SDK Client.create API.",
        },
        {
            "title": "cookbook Session wrapper",
            "url": "https://github.com/example/cookbook",
            "snippet": "Example session wrapper around the SDK.",
        },
    ]

    ranked = rank_search_results("Example TypeScript SDK Client.create docs", results)
    assert ranked[0]["url"] == "https://docs.example.com/sdk/typescript"


def test_prioritize_official_docs_reserves_slots():
    ranked = [
        {"url": "https://example.com/blog/sdk"},
        {"url": "https://forum.example.com/t/x"},
        {"url": "https://docs.example.com/sdk/python"},
        {"url": "https://developer.example.com/api/endpoints"},
        {"url": "https://news.ycombinator.com/item?id=1"},
    ]
    out = DeepResearcher._prioritize_official_docs(ranked, cap=3, min_docs=2)
    top = [r["url"] for r in out[:3]]
    assert "https://docs.example.com/sdk/python" in top
    assert "https://developer.example.com/api/endpoints" in top


def test_query_needs_web_docs_for_sdk_tagged_github():
    assert DeepResearcher._query_needs_web_docs(
        "Example SDK Client.create site:github.com", "github",
    )
    assert not DeepResearcher._query_needs_web_docs(
        "transformer attention site:arxiv.org", "academic",
    )
