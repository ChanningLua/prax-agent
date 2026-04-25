"""Regression guard for docs/getting-started.md — the landing page every
tutorial links to. If its structure drifts, the whole beginner onboarding
flow breaks silently."""

from __future__ import annotations

from pathlib import Path


def test_getting_started_covers_install_key_and_first_prompt():
    pkg_root = Path(__file__).resolve().parents[2]
    doc = pkg_root / "docs" / "getting-started.md"
    assert doc.exists(), f"getting-started must exist at {doc}"
    text = doc.read_text(encoding="utf-8")

    # Step headings that tutorials depend on (they send beginners back here).
    for step in ("Step 1", "Step 2", "Step 3", "Step 4"):
        assert step in text, f"missing {step!r}"

    # The single supported install route (npm) must be documented.
    # PyPI publishing is not yet done; do not claim `pip install prax-agent`.
    assert "npm install -g praxagent" in text, "npm install command must be documented"
    assert "pip install prax-agent" not in text, (
        "must not claim a PyPI package that hasn't been published"
    )

    # At least the three providers we actually support.
    for provider_hint in ("ZHIPU_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        assert provider_hint in text, f"provider env var {provider_hint!r} must be documented"

    # The canonical sanity-check is `prax providers` (we used to point users
    # at `prax doctor`, but doctor is the Claude-Code-integration doctor and
    # doesn't tell new users whether their LLM key is wired up — the
    # provider listing does).
    assert "prax providers" in text

    # First-prompt example + expected-output convention.
    assert "prax prompt" in text
    assert "应该看到" in text

    # Bridges to the tutorial + recipe network so beginners can navigate out.
    for follow_up in (
        "tutorials/support-digest.md",
        "tutorials/ai-news-daily.md",
    ):
        assert follow_up in text, f"must link to {follow_up!r}"


def test_getting_started_warns_against_committing_env():
    """One of the top foot-guns for beginners is checking `.prax/.env` into
    git. The doc must flag this loudly."""
    pkg_root = Path(__file__).resolve().parents[2]
    text = (pkg_root / "docs" / "getting-started.md").read_text(encoding="utf-8")
    assert ".prax/.env" in text
    assert "不要 commit" in text or "do not commit" in text.lower()


def test_getting_started_has_troubleshooting_tables():
    """Every Step must either succeed or tell the beginner exactly how to
    recover. Guard the presence of at least two troubleshooting tables."""
    pkg_root = Path(__file__).resolve().parents[2]
    text = (pkg_root / "docs" / "getting-started.md").read_text(encoding="utf-8")
    # Simple heuristic: count "| 错误" / "| 症状" header rows or any table
    # that lives right after a Step.
    table_headers = text.count("| 场景 |") + text.count("| 错误信息 |") + text.count("| 症状 |")
    assert table_headers >= 2, (
        f"getting-started should have ≥2 troubleshooting tables; found {table_headers}"
    )
