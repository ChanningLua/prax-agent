from __future__ import annotations

import json
import sqlite3

import pytest

from prax.article_integrations import (
    fetch_article,
    load_trendradar_export,
    main,
    probe_integrations,
)


def test_probe_reports_every_supported_open_source_adapter():
    statuses = {status.name: status for status in probe_integrations()}

    assert {
        "trendradar",
        "autocli",
        "crawl4ai",
        "gpt-researcher",
        "storm",
    }.issubset(statuses)
    assert all(status.detail for status in statuses.values())


def test_probe_cli_writes_machine_readable_status(tmp_path):
    output = tmp_path / "integration-status.json"

    exit_code = main(["probe", "--format", "json", "--json-out", str(output)])

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert {item["name"] for item in payload} >= {"autocli", "trendradar"}


def test_trendradar_ingest_normalizes_json_markdown_and_sqlite(tmp_path):
    (tmp_path / "news.json").write_text(
        json.dumps(
            {
                "platforms": {
                    "github": [
                        {
                            "title": "MCP goes stateless",
                            "url": "https://example.com/mcp",
                            "rank": 1,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "report.md").write_text(
        "- [AI credit pools](https://example.com/credits)\n"
        "- [MCP goes stateless](https://example.com/mcp)\n",
        encoding="utf-8",
    )
    database = tmp_path / "rss.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE news (title TEXT, url TEXT, platform TEXT, position INTEGER)"
    )
    connection.execute(
        "INSERT INTO news VALUES (?, ?, ?, ?)",
        ("RAG evaluation", "https://example.com/rag", "rss", 2),
    )
    connection.commit()
    connection.close()

    records = load_trendradar_export(tmp_path)

    assert {record.title for record in records} == {
        "MCP goes stateless",
        "AI credit pools",
        "RAG evaluation",
    }
    rag = next(record for record in records if record.title == "RAG evaluation")
    assert rag.rank == 2
    assert rag.platform == "rss"


def test_trendradar_cli_writes_normalized_signals(tmp_path):
    source = tmp_path / "trend.json"
    output = tmp_path / "signals.json"
    source.write_text(
        json.dumps(
            [{"title": "A real signal", "link": "https://example.com/signal"}]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        ["ingest-trendradar", str(source), "--json-out", str(output)]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["signals"][0]["title"] == "A real signal"


@pytest.mark.parametrize(
    "url",
    ("file:///tmp/article.html", "http://localhost/private", "relative/path"),
)
def test_fetch_rejects_non_public_urls_before_network_access(url):
    with pytest.raises(ValueError):
        fetch_article(url)
