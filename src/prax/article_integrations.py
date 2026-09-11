"""Optional open-source adapters for the article production pipeline.

The core Prax install stays small. TrendRadar exports, AutoCLI, Crawl4AI, and
GPT Researcher are discovered at runtime and fail with explicit diagnostics
instead of silently returning empty research.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

HTML_SKIP_TAGS = {
    "script",
    "style",
    "svg",
    "noscript",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
}


@dataclass(frozen=True)
class IntegrationStatus:
    name: str
    available: bool
    mode: str
    detail: str


@dataclass(frozen=True)
class SignalRecord:
    id: str
    title: str
    url: str
    source: str
    platform: str
    rank: int | None = None
    published_at: str = ""
    metric: str = ""
    summary: str = ""


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _find_command(name: str) -> str | None:
    discovered = shutil.which(name)
    if discovered:
        return discovered
    local = Path.cwd() / ".venv" / "bin" / name
    return str(local) if local.is_file() else None


def _probe_autocli(executable: str | None) -> IntegrationStatus:
    if not executable:
        return IntegrationStatus(
            "autocli",
            False,
            "cli",
            "autocli was not found on PATH or in .venv/bin",
        )
    try:
        completed = subprocess.run(
            [executable, "doctor"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return IntegrationStatus("autocli", False, "cli", str(exc))
    output = f"{completed.stdout}\n{completed.stderr}"
    connected = bool(
        re.search(r"[✓✔]\s*Chrome extension connected", output)
        or re.search(
            r"Chrome extension connected\s*:\s*(?:yes|ok|true)",
            output,
            re.IGNORECASE,
        )
    )
    if connected:
        detail = f"{executable}; Chrome extension connected"
    else:
        detail = f"{executable}; binary installed, Chrome extension not connected"
    return IntegrationStatus("autocli", connected, "cli", detail)


def probe_integrations() -> list[IntegrationStatus]:
    trendradar_path = os.getenv("TRENDRADAR_OUTPUT")
    if trendradar_path and Path(trendradar_path).exists():
        trendradar = IntegrationStatus(
            "trendradar",
            True,
            "export",
            f"reading {trendradar_path}",
        )
    else:
        trendradar = IntegrationStatus(
            "trendradar",
            False,
            "export",
            "set TRENDRADAR_OUTPUT to its output directory or exported file",
        )

    autocli_status = _probe_autocli(_find_command("autocli"))

    crwl = _find_command("crwl")
    crawl4ai_module = _module_available("crawl4ai")
    crawl4ai_status = IntegrationStatus(
        "crawl4ai",
        bool(crwl or crawl4ai_module),
        "python" if crawl4ai_module else "cli",
        (
            "crawl4ai Python package"
            if crawl4ai_module
            else crwl or "install crawl4ai and run crawl4ai-setup"
        ),
    )

    gpt_researcher = _module_available("gpt_researcher")
    has_llm_key = any(
        os.getenv(name)
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")
    )
    researcher_status = IntegrationStatus(
        "gpt-researcher",
        gpt_researcher and has_llm_key,
        "python",
        (
            "package and LLM credentials detected"
            if gpt_researcher and has_llm_key
            else "install gpt-researcher and configure an LLM plus retriever"
        ),
    )

    storm_path = os.getenv("STORM_OUTPUT")
    storm_status = IntegrationStatus(
        "storm",
        bool(storm_path and Path(storm_path).exists()),
        "report-import",
        (
            f"importing reports from {storm_path}"
            if storm_path and Path(storm_path).exists()
            else "set STORM_OUTPUT to an existing STORM output directory"
        ),
    )
    return [
        trendradar,
        autocli_status,
        crawl4ai_status,
        researcher_status,
        storm_status,
    ]


def _stable_id(source: str, title: str, url: str) -> str:
    digest = hashlib.sha256(f"{source}\0{title}\0{url}".encode()).hexdigest()[:12]
    return f"{source}-{digest}"


def _as_rank(value: Any) -> int | None:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return None
    return rank if rank >= 0 else None


def _record_from_mapping(value: dict[str, Any], source: str) -> SignalRecord | None:
    title = next(
        (
            str(value[key]).strip()
            for key in ("title", "name", "headline", "text")
            if str(value.get(key, "")).strip()
        ),
        "",
    )
    if not title:
        return None
    url = next(
        (
            str(value[key]).strip()
            for key in ("url", "link", "href")
            if str(value.get(key, "")).strip()
        ),
        "",
    )
    platform = next(
        (
            str(value[key]).strip()
            for key in ("platform", "source", "site", "channel")
            if str(value.get(key, "")).strip()
        ),
        source,
    )
    rank = next(
        (
            _as_rank(value[key])
            for key in ("rank", "position", "index", "order")
            if key in value
        ),
        None,
    )
    published_at = next(
        (
            str(value[key]).strip()
            for key in ("published_at", "timestamp", "time", "date", "created_at")
            if str(value.get(key, "")).strip()
        ),
        "",
    )
    metric = next(
        (
            str(value[key]).strip()
            for key in ("metric", "hot", "score", "views", "heat")
            if str(value.get(key, "")).strip()
        ),
        "",
    )
    summary = next(
        (
            str(value[key]).strip()
            for key in ("summary", "description", "content", "snippet")
            if str(value.get(key, "")).strip()
        ),
        "",
    )
    return SignalRecord(
        id=_stable_id(source, title, url),
        title=title,
        url=url,
        source=source,
        platform=platform,
        rank=rank,
        published_at=published_at,
        metric=metric,
        summary=summary,
    )


def _walk_json(value: Any, source: str) -> list[SignalRecord]:
    records: list[SignalRecord] = []
    if isinstance(value, dict):
        record = _record_from_mapping(value, source)
        if record:
            records.append(record)
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                records.extend(_walk_json(nested, source))
    elif isinstance(value, list):
        for nested in value:
            records.extend(_walk_json(nested, source))
    return records


def _load_markdown(path: Path, source: str) -> list[SignalRecord]:
    text = path.read_text(encoding="utf-8")
    records = []
    for title, url in re.findall(r"\[([^\]\n]{3,})\]\((https?://[^)\s]+)\)", text):
        clean_title = re.sub(r"\s+", " ", title).strip()
        records.append(
            SignalRecord(
                id=_stable_id(source, clean_title, url),
                title=clean_title,
                url=url,
                source=source,
                platform=source,
            )
        )
    return records


def _load_sqlite(path: Path, source: str) -> list[SignalRecord]:
    records: list[SignalRecord] = []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if isinstance(row[0], str) and not row[0].startswith("sqlite_")
        ]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = [
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({quoted})")
            ]
            lowered = {column.lower(): column for column in columns}
            if not any(name in lowered for name in ("title", "name", "headline")):
                continue
            rows = connection.execute(f"SELECT * FROM {quoted} LIMIT 1000")
            for row in rows:
                mapping = dict(zip(columns, row, strict=False))
                record = _record_from_mapping(mapping, source)
                if record:
                    records.append(record)
    finally:
        connection.close()
    return records


def load_trendradar_export(path: Path) -> list[SignalRecord]:
    """Load TrendRadar Markdown, JSON, or SQLite exports without copying its code."""

    if not path.exists():
        raise ValueError(f"TrendRadar export does not exist: {path}")
    files = (
        [path]
        if path.is_file()
        else sorted(
            item
            for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in {".json", ".md", ".db"}
        )
    )
    records: list[SignalRecord] = []
    for item in files[:2000]:
        source = f"trendradar:{item.stem}"
        try:
            if item.suffix.lower() == ".json":
                payload = json.loads(item.read_text(encoding="utf-8"))
                records.extend(_walk_json(payload, source))
            elif item.suffix.lower() == ".md":
                records.extend(_load_markdown(item, source))
            elif item.suffix.lower() == ".db":
                records.extend(_load_sqlite(item, source))
        except (OSError, UnicodeError, json.JSONDecodeError, sqlite3.DatabaseError):
            continue

    unique: dict[tuple[str, str], SignalRecord] = {}
    for record in records:
        key = (record.url.rstrip("/"), record.title.casefold())
        unique.setdefault(key, record)
    return list(unique.values())


def collect_with_autocli(
    source: str, *, limit: int = 20, query: str = ""
) -> list[SignalRecord]:
    executable = _find_command("autocli")
    if not executable:
        raise RuntimeError("AutoCLI is not installed")
    commands: dict[str, list[str]] = {
        "twitter": ["twitter", "timeline"],
        "zhihu": ["zhihu", "hot"],
        "bilibili": ["bilibili", "hot"],
        "hackernews": ["hackernews", "top"],
        "reddit": ["reddit", "subreddit", "--name", query or "MachineLearning"],
    }
    if source == "twitter-search":
        if not query:
            raise ValueError("twitter-search requires --query")
        command = ["twitter", "search", "--query", query]
    else:
        command = commands.get(source, [])
    if not command:
        raise ValueError(f"unsupported AutoCLI source: {source}")
    completed = subprocess.run(
        [executable, *command, "--limit", str(limit), "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"AutoCLI failed: {detail}")
    payload = json.loads(completed.stdout)
    return _walk_json(payload, f"autocli:{source}")


class _ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in HTML_SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in {"h1", "h2", "h3", "p", "li", "blockquote", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in HTML_SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in {"h1", "h2", "h3", "p", "li", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = re.sub(r"\s+", " ", data).strip()
        if not cleaned:
            return
        if self._in_title:
            self.title = f"{self.title} {cleaned}".strip()
        else:
            self.parts.append(cleaned + " ")

    def markdown(self, url: str) -> str:
        body = re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()
        title = self.title or url
        return f"# {title}\n\nSource: {url}\n\n{body}\n"


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("only absolute HTTP(S) URLs are supported")
    hostname = parsed.hostname or ""
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ValueError("local addresses are not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("private, loopback, and link-local addresses are not allowed")


def _fetch_with_httpx(url: str) -> str:
    user_agent = "PraxArticleResearch/1.0"
    with httpx.Client(
        follow_redirects=True,
        timeout=30,
        headers={"User-Agent": user_agent},
    ) as client:
        robots_url = urljoin(url, "/robots.txt")
        robots_response = client.get(robots_url)
        if robots_response.status_code < 400:
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(robots_response.text.splitlines())
            if not parser.can_fetch(user_agent, url):
                raise RuntimeError(f"robots.txt disallows fetching {url}")
        response = client.get(url)
        response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if urlparse(str(response.url)).path.endswith(".md") or "text/markdown" in content_type:
        text = response.text.strip()
        return f"Source: {response.url}\n\n{text}\n"
    parser = _ReadableHTML()
    parser.feed(response.text)
    return parser.markdown(str(response.url))


async def _fetch_with_crawl4ai(url: str) -> str:
    from crawl4ai import AsyncWebCrawler  # type: ignore[import-not-found]

    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url)
    markdown = getattr(result, "markdown", "")
    if hasattr(markdown, "raw_markdown"):
        markdown = markdown.raw_markdown
    if not str(markdown).strip():
        raise RuntimeError(f"Crawl4AI returned no Markdown for {url}")
    return str(markdown)


def fetch_article(url: str, *, backend: str = "auto") -> tuple[str, str]:
    _validate_public_url(url)
    if backend not in {"auto", "crawl4ai", "httpx"}:
        raise ValueError("backend must be auto, crawl4ai, or httpx")
    if backend in {"auto", "crawl4ai"} and _module_available("crawl4ai"):
        return asyncio.run(_fetch_with_crawl4ai(url)), "crawl4ai-python"
    crwl = _find_command("crwl")
    if backend in {"auto", "crawl4ai"} and crwl:
        completed = subprocess.run(
            [crwl, url, "-o", "markdown"],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout, "crawl4ai-cli"
        if backend == "crawl4ai":
            raise RuntimeError(completed.stderr.strip() or "Crawl4AI failed")
    if backend == "crawl4ai":
        raise RuntimeError("Crawl4AI is not installed")
    return _fetch_with_httpx(url), "httpx-fallback"


async def _research_with_gpt_researcher(query: str) -> str:
    from gpt_researcher import GPTResearcher  # type: ignore[import-not-found]

    researcher = GPTResearcher(query=query)
    await researcher.conduct_research()
    report = await researcher.write_report()
    return str(report)


def run_gpt_researcher(query: str) -> str:
    if not _module_available("gpt_researcher"):
        raise RuntimeError("gpt-researcher is not installed")
    if not any(
        os.getenv(name)
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")
    ):
        raise RuntimeError("no supported LLM credential is configured")
    return asyncio.run(_research_with_gpt_researcher(query))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Article pipeline integrations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="Report optional integration status")
    probe.add_argument("--format", choices=("text", "json"), default="text")
    probe.add_argument("--json-out", type=Path)

    trend = subparsers.add_parser(
        "ingest-trendradar", help="Normalize a TrendRadar export"
    )
    trend.add_argument("path", type=Path)
    trend.add_argument("--json-out", type=Path, required=True)

    autocli = subparsers.add_parser("collect-autocli", help="Collect one AutoCLI source")
    autocli.add_argument(
        "source",
        choices=(
            "twitter",
            "twitter-search",
            "zhihu",
            "bilibili",
            "hackernews",
            "reddit",
        ),
    )
    autocli.add_argument("--limit", type=int, default=20)
    autocli.add_argument("--query", default="")
    autocli.add_argument("--json-out", type=Path, required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch one public article as Markdown")
    fetch.add_argument("url")
    fetch.add_argument("--backend", choices=("auto", "crawl4ai", "httpx"), default="auto")
    fetch.add_argument("--out", type=Path, required=True)

    research = subparsers.add_parser(
        "research", help="Run the optional GPT Researcher backend"
    )
    research.add_argument("query")
    research.add_argument("--out", type=Path, required=True)
    return parser


def _write_records(path: Path, records: list[SignalRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"signals": [asdict(record) for record in records]}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "probe":
            statuses = probe_integrations()
            rendered = json.dumps(
                [asdict(status) for status in statuses],
                ensure_ascii=False,
                indent=2,
            )
            if args.json_out:
                args.json_out.parent.mkdir(parents=True, exist_ok=True)
                args.json_out.write_text(rendered + "\n", encoding="utf-8")
            if args.format == "json":
                print(rendered)
            else:
                for status in statuses:
                    marker = "READY" if status.available else "MISSING"
                    print(f"{marker:7} {status.name:16} {status.detail}")
            return 0
        if args.command == "ingest-trendradar":
            _write_records(args.json_out, load_trendradar_export(args.path))
            return 0
        if args.command == "collect-autocli":
            records = collect_with_autocli(
                args.source,
                limit=max(1, min(args.limit, 200)),
                query=args.query,
            )
            _write_records(args.json_out, records)
            return 0
        if args.command == "fetch":
            markdown, backend = fetch_article(args.url, backend=args.backend)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                f"<!-- fetched-with: {backend} -->\n\n{markdown}",
                encoding="utf-8",
            )
            return 0
        if args.command == "research":
            report = run_gpt_researcher(args.query)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(report, encoding="utf-8")
            return 0
    except (
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        httpx.HTTPError,
    ) as exc:
        print(f"article-integrations: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
