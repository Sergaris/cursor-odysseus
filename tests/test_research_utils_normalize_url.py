"""Tests for URL normalize helper used by Deep Research dedup."""

from src.research_utils import normalize_url


def test_normalize_url_strips_www_and_tracking():
    assert normalize_url(
        "https://www.Example.com/path/?utm_source=x&fbclid=1&id=2"
    ) == "https://example.com/path?id=2"


def test_normalize_url_sorts_query_params():
    assert normalize_url("https://ex.com/a?b=2&a=1") == "https://ex.com/a?a=1&b=2"


def test_normalize_url_drops_fragment_and_trailing_slash():
    assert normalize_url("https://ex.com/docs/#section") == "https://ex.com/docs"


def test_normalize_url_empty():
    assert normalize_url("") == ""
    assert normalize_url("   ") == ""
