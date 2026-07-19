"""Tests for academic search adapters (mocked HTTP)."""

from unittest.mock import MagicMock, patch

from services.search.academic import (
    arxiv_search,
    github_search,
    search_academic,
    semantic_scholar_search,
)

_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Quantum Widgets</title>
    <summary>A paper about quantum widgets.</summary>
    <published>2024-01-02T00:00:00Z</published>
    <id>http://arxiv.org/abs/2401.00001</id>
    <link href="https://arxiv.org/abs/2401.00001" rel="alternate" type="text/html"/>
  </entry>
</feed>
"""


def test_arxiv_search_parses_atom():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = _ARXIV_XML

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp

    with patch("services.search.academic.httpx.Client", return_value=mock_client):
        hits = arxiv_search("quantum widgets", 5)

    assert len(hits) == 1
    assert hits[0]["title"] == "Quantum Widgets"
    assert "arxiv.org" in hits[0]["url"]
    assert hits[0]["provider"] == "arxiv"


def test_semantic_scholar_search_parses_json():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": [
            {
                "title": "S2 Paper",
                "abstract": "About widgets",
                "url": "https://www.semanticscholar.org/paper/abc",
                "year": 2023,
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp

    with patch("services.search.academic.httpx.Client", return_value=mock_client):
        hits = semantic_scholar_search("widgets", 5)

    assert hits[0]["title"] == "S2 Paper"
    assert hits[0]["provider"] == "semantic_scholar"


def test_github_search_parses_items():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "items": [
            {
                "html_url": "https://github.com/org/repo/issues/1",
                "title": "Bug in deep research",
                "body": "Details",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp

    with patch("services.search.academic.httpx.Client", return_value=mock_client):
        hits = github_search("deep research", 5)

    assert hits[0]["provider"] == "github"
    assert "issues/1" in hits[0]["url"]


def test_search_academic_routes_github_strategy():
    with patch("services.search.academic.github_search", return_value=[{"url": "u"}]) as gh:
        out = search_academic("topic", "github", 5)
    assert out == [{"url": "u"}]
    gh.assert_called_once()
