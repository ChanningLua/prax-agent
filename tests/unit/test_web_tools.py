"""Unit tests for WebSearchTool and WebCrawlerTool.

All HTTP calls are mocked — no real network access.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# We need httpx available for import; patch it at module level if missing
pytest.importorskip("httpx")

from prax.tools.web_search import WebSearchTool, WebCrawlerTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(json_data: dict | None = None, text: str = "", status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


def _async_client_ctx(response):
    """Build a mock AsyncClient context manager that returns ``response``."""
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.get = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# WebSearchTool — metadata
# ---------------------------------------------------------------------------

def test_web_search_name():
    tool = WebSearchTool(api_key="test-key")
    assert tool.name == "WebSearch"


def test_web_search_description():
    tool = WebSearchTool(api_key="test-key")
    assert tool.description  # non-empty


# ---------------------------------------------------------------------------
# WebSearchTool.is_available
# ---------------------------------------------------------------------------

def test_web_search_is_available_with_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    with patch("prax.tools.web_search._HTTPX_AVAILABLE", True):
        assert WebSearchTool.is_available() is True


def test_web_search_is_available_no_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with patch("prax.tools.web_search._HTTPX_AVAILABLE", True):
        assert WebSearchTool.is_available() is False


def test_web_search_is_available_no_httpx(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    with patch("prax.tools.web_search._HTTPX_AVAILABLE", False):
        assert WebSearchTool.is_available() is False


# ---------------------------------------------------------------------------
# WebSearchTool.execute — success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_search_execute_success():
    tavily_data = {
        "answer": "Paris is the capital of France.",
        "results": [
            {"title": "France", "url": "https://en.wikipedia.org/wiki/France",
             "content": "France is a country in Western Europe."},
        ],
    }
    resp = _mock_response(json_data=tavily_data)
    ctx = _async_client_ctx(resp)

    with patch("httpx.AsyncClient", return_value=ctx):
        tool = WebSearchTool(api_key="tvly-test")
        result = await tool.execute({"query": "capital of France"})

    assert not result.is_error
    assert "Paris" in result.content
    assert "France" in result.content


@pytest.mark.asyncio
async def test_web_search_execute_no_api_key():
    tool = WebSearchTool(api_key=None)
    # Ensure env var is absent too
    with patch.dict("os.environ", {}, clear=True):
        tool._api_key = None
        result = await tool.execute({"query": "test"})
    assert result.is_error
    assert "TAVILY_API_KEY" in result.content


@pytest.mark.asyncio
async def test_web_search_execute_api_error():
    resp = _mock_response(status_code=401)
    ctx = _async_client_ctx(resp)

    with patch("httpx.AsyncClient", return_value=ctx):
        tool = WebSearchTool(api_key="bad-key")
        result = await tool.execute({"query": "test"})

    assert result.is_error
    assert "error" in result.content.lower() or "Search error" in result.content


@pytest.mark.asyncio
async def test_web_search_execute_empty_results():
    tavily_data = {"answer": "", "results": []}
    resp = _mock_response(json_data=tavily_data)
    ctx = _async_client_ctx(resp)

    with patch("httpx.AsyncClient", return_value=ctx):
        tool = WebSearchTool(api_key="tvly-test")
        result = await tool.execute({"query": "something obscure"})

    assert not result.is_error
    assert "No results" in result.content or result.content.strip() == ""


@pytest.mark.asyncio
async def test_web_search_execute_httpx_not_available():
    with patch("prax.tools.web_search._HTTPX_AVAILABLE", False):
        tool = WebSearchTool(api_key="tvly-test")
        result = await tool.execute({"query": "test"})
    assert result.is_error
    assert "httpx" in result.content.lower()


# ---------------------------------------------------------------------------
# WebCrawlerTool — metadata
# ---------------------------------------------------------------------------

def test_web_crawler_name():
    tool = WebCrawlerTool()
    assert tool.name == "WebCrawler"


def test_web_crawler_description():
    tool = WebCrawlerTool()
    assert tool.description  # non-empty


# ---------------------------------------------------------------------------
# WebCrawlerTool.is_available
# ---------------------------------------------------------------------------

def test_web_crawler_is_available_both_present():
    with patch("prax.tools.web_search._HTTPX_AVAILABLE", True), \
         patch("prax.tools.web_search._BS4_AVAILABLE", True):
        assert WebCrawlerTool.is_available() is True


def test_web_crawler_is_available_missing_bs4():
    with patch("prax.tools.web_search._HTTPX_AVAILABLE", True), \
         patch("prax.tools.web_search._BS4_AVAILABLE", False):
        assert WebCrawlerTool.is_available() is False


# ---------------------------------------------------------------------------
# WebCrawlerTool.execute — success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_crawler_execute_success():
    html = "<html><body><p>Hello world</p></body></html>"
    resp = _mock_response(text=html)
    ctx = _async_client_ctx(resp)

    with patch("httpx.AsyncClient", return_value=ctx), \
         patch("prax.tools.web_search._BS4_AVAILABLE", True):
        tool = WebCrawlerTool()
        result = await tool.execute({"url": "https://example.com"})

    assert not result.is_error
    assert "Hello world" in result.content


@pytest.mark.asyncio
async def test_web_crawler_execute_http_error():
    resp = _mock_response(status_code=404, text="")
    ctx = _async_client_ctx(resp)

    with patch("httpx.AsyncClient", return_value=ctx), \
         patch("prax.tools.web_search._BS4_AVAILABLE", True):
        tool = WebCrawlerTool()
        result = await tool.execute({"url": "https://example.com/missing"})

    assert result.is_error
    assert "Crawl error" in result.content or result.is_error


@pytest.mark.asyncio
async def test_web_crawler_execute_httpx_not_available():
    with patch("prax.tools.web_search._HTTPX_AVAILABLE", False), \
         patch("prax.tools.web_search._BS4_AVAILABLE", True):
        tool = WebCrawlerTool()
        result = await tool.execute({"url": "https://example.com"})
    assert result.is_error
    assert "httpx" in result.content.lower()


@pytest.mark.asyncio
async def test_web_crawler_execute_bs4_not_available():
    with patch("prax.tools.web_search._HTTPX_AVAILABLE", True), \
         patch("prax.tools.web_search._BS4_AVAILABLE", False):
        tool = WebCrawlerTool()
        result = await tool.execute({"url": "https://example.com"})
    assert result.is_error
    assert "beautifulsoup4" in result.content.lower() or "bs4" in result.content.lower()
