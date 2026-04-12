"""Web search and crawl tools for real-time information retrieval."""

from __future__ import annotations

import os
from typing import Any

from .base import PermissionLevel, Tool, ToolResult

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False


class WebSearchTool(Tool):
    """Search the web via Tavily API for current information."""

    name = "WebSearch"
    is_concurrency_safe = True
    description = (
        "Search the web for current information (docs, best practices, error solutions). "
        "Requires TAVILY_API_KEY environment variable. "
        "Use when you need up-to-date information not in your training data."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query"
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Maximum number of results (default: 5)"
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.getenv("TAVILY_API_KEY")

    @classmethod
    def is_available(cls) -> bool:
        return _HTTPX_AVAILABLE and bool(os.getenv("TAVILY_API_KEY"))

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        if not _HTTPX_AVAILABLE:
            return ToolResult(
                content="Error: httpx not installed. Run: pip install httpx",
                is_error=True,
            )
        if not self._api_key:
            return ToolResult(
                content="Error: TAVILY_API_KEY not set",
                is_error=True,
            )

        query = params.get("query", "")
        max_results = params.get("max_results", 5)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": self._api_key,
                        "query": query,
                        "max_results": max_results,
                        "include_answer": True,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            parts: list[str] = []
            answer = data.get("answer", "")
            if answer:
                parts.append(f"## Answer\n{answer}\n")

            results = data.get("results", [])
            if results:
                parts.append("## Sources")
                for item in results:
                    title = item.get("title", "")
                    url = item.get("url", "")
                    snippet = item.get("content", "")[:300]
                    parts.append(f"**{title}**\n{url}\n{snippet}\n")

            return ToolResult(content="\n".join(parts) or "No results found.")

        except Exception as e:
            return ToolResult(content=f"Search error: {e}", is_error=True)


class WebCrawlerTool(Tool):
    """Fetch and extract structured content from a web page."""

    name = "WebCrawler"
    description = (
        "Fetch a web page and extract its content. "
        "Supports extracting text, code blocks, links, or tables. "
        "Use for reading documentation, GitHub READMEs, or any public URL."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch"
            },
            "extract": {
                "type": "string",
                "enum": ["text", "code", "links", "tables"],
                "description": "What to extract (default: text)"
            }
        },
        "required": ["url"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.SAFE

    @classmethod
    def is_available(cls) -> bool:
        return _HTTPX_AVAILABLE and _BS4_AVAILABLE

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        if not _HTTPX_AVAILABLE:
            return ToolResult(
                content="Error: httpx not installed. Run: pip install httpx",
                is_error=True,
            )
        if not _BS4_AVAILABLE:
            return ToolResult(
                content="Error: beautifulsoup4 not installed. Run: pip install beautifulsoup4",
                is_error=True,
            )

        url = params.get("url", "")
        extract = params.get("extract", "text")

        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; PraxBot/1.0)"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text

            soup = BeautifulSoup(html, "html.parser")

            # Remove script/style noise
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()

            if extract == "text":
                content = soup.get_text(separator="\n", strip=True)
                # Collapse excessive blank lines
                import re
                content = re.sub(r"\n{3,}", "\n\n", content)
            elif extract == "code":
                blocks = soup.find_all("code")
                content = "\n\n".join(b.get_text() for b in blocks) or "No code blocks found."
            elif extract == "links":
                links = soup.find_all("a", href=True)
                lines = [f"{a.get_text(strip=True)}: {a['href']}" for a in links if a.get_text(strip=True)]
                content = "\n".join(lines) or "No links found."
            elif extract == "tables":
                tables = soup.find_all("table")
                content = "\n\n".join(str(t) for t in tables) or "No tables found."
            else:
                content = soup.get_text(separator="\n", strip=True)

            # Truncate to avoid overwhelming context
            if len(content) > 8000:
                content = content[:8000] + f"\n\n[truncated — {len(content)} chars total]"

            return ToolResult(content=content)

        except Exception as e:
            return ToolResult(content=f"Crawl error: {e}", is_error=True)
