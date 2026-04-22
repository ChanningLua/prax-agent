"""Regression guards for the phased ai-news-daily tutorial + its sample vault."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_tutorial_has_four_phases():
    pkg_root = Path(__file__).resolve().parents[2]
    tut = pkg_root / "docs" / "tutorials" / "ai-news-daily.md"
    assert tut.exists(), f"ai-news-daily tutorial missing at {tut}"
    text = tut.read_text(encoding="utf-8")

    # Each phase must be present and named so a beginner can choose when to stop.
    for phase in ("Phase 1", "Phase 2", "Phase 3", "Phase 4"):
        assert phase in text, f"missing {phase!r}"

    # Phases must have explicit purposes.
    for capability in ("整理", "抓取", "定时", "推送"):
        assert capability in text, f"phase capability {capability!r} must be named"

    # Scenario persona — the foolproof convention.
    assert "你是谁" in text
    assert "你是产品经理" in text or "你是" in text

    # Per-phase troubleshooting tables — each phase can fail, each phase gets recoveries.
    assert text.count("排错") >= 3 or text.count("| 症状 |") >= 3, (
        "each phase should have its own troubleshooting table"
    )

    # Cross-link to the prerequisite getting-started page.
    assert "getting-started" in text


def test_sample_vault_exists_and_is_loadable():
    """The 6 sample markdown files power Phase 1's zero-dependency demo.
    If any disappear or drift, beginners can't complete Phase 1 and the whole
    tutorial loses its entry point."""
    pkg_root = Path(__file__).resolve().parents[2]
    vault = pkg_root / "docs" / "tutorials" / "ai-news-daily" / "sample-vault"
    assert vault.is_dir(), f"sample vault missing at {vault}"

    md_files = sorted(vault.glob("*.md"))
    assert len(md_files) >= 5, f"sample vault should have ≥5 files; got {len(md_files)}"

    # At least one source of each kind so knowledge-compile has real clustering fuel.
    sources_seen = set()
    for md in md_files:
        body = md.read_text(encoding="utf-8")
        # Must have YAML frontmatter with a parseable `source:` field.
        assert body.startswith("---\n"), f"{md.name} is missing frontmatter"
        end = body.find("\n---", 3)
        assert end != -1, f"{md.name} frontmatter is not terminated"
        meta = yaml.safe_load(body[3:end])
        assert isinstance(meta, dict), f"{md.name} frontmatter didn't parse as a dict"
        for field in ("source", "url", "scraped_at"):
            assert field in meta, f"{md.name} frontmatter missing {field!r}"
        sources_seen.add(meta["source"])

    # Diverse sources so the compile step has something to cluster.
    assert len(sources_seen) >= 3, (
        f"sample vault should cover ≥3 distinct sources; got {sources_seen}"
    )


def test_tutorial_sample_vault_path_matches():
    """The Phase 1 cp command references a specific relative path. If that
    path diverges from where the files actually ship, Phase 1 Step 2 silently
    breaks."""
    pkg_root = Path(__file__).resolve().parents[2]
    tut_text = (pkg_root / "docs" / "tutorials" / "ai-news-daily.md").read_text(
        encoding="utf-8"
    )
    # The cp command in the tutorial uses this exact path suffix.
    assert "docs/tutorials/ai-news-daily/sample-vault" in tut_text, (
        "tutorial's cp command path must match on-disk sample-vault location"
    )
