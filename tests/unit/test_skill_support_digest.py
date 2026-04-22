"""Structural regression test for skills/support-digest/SKILL.md + sample data."""

from __future__ import annotations

import json
from pathlib import Path

from prax.core.skills_loader import load_skills


def test_support_digest_skill_is_discoverable(tmp_path):
    skills = {s.name: s for s in load_skills(str(tmp_path))}
    assert "support-digest" in skills, f"support-digest must be bundled. Found: {sorted(skills)}"
    skill = skills["support-digest"]

    triggers = {t.lower() for t in skill.triggers}
    for expected in ("support digest", "客服简报", "ticket digest"):
        assert expected in triggers, f"should trigger on {expected!r}; got {sorted(triggers)}"

    # Read + Write + Glob (no Bash, no external network).
    assert "Read" in skill.allowed_tools
    assert "Write" in skill.allowed_tools
    assert "Glob" in skill.allowed_tools
    assert "Notify" in skill.allowed_tools

    body = skill.content

    # Headline differentiator: local-only processing for compliance.
    assert "本地处理" in body or "local-only" in body.lower() or "零外部 API" in body
    # PII redaction must happen before any further processing.
    assert "脱敏" in body or "redact" in body.lower()
    for pii_type in ("email", "phone", "手机"):
        assert pii_type.lower() in body.lower(), f"{pii_type!r} must be covered by redaction rules"

    # Caps protecting the digest format.
    assert "highlights" in body.lower() or "亮点" in body
    assert "5" in body  # highlights cap
    # Archive move so the same day isn't double-processed.
    assert "archive" in body.lower() or "归档" in body

    # Hard boundary: no external API.
    assert "不调" in body or "zero" in body.lower() or "no external" in body.lower()


def test_support_digest_sample_data_is_valid_json():
    pkg_root = Path(__file__).resolve().parents[2]
    sample = pkg_root / "docs" / "recipes" / "support-digest" / "sample-tickets.json"
    assert sample.exists(), "sample-tickets.json must ship with the recipe"

    data = json.loads(sample.read_text(encoding="utf-8"))
    assert isinstance(data, list), "sample must be a JSON array"
    assert len(data) >= 5, "sample should cover at least 5 tickets for meaningful clustering"

    # Minimum schema on every record.
    for record in data:
        for field in ("id", "created_at", "body"):
            assert field in record, f"ticket missing required field {field!r}: {record}"

    # At least one high-severity ticket so demos surface a real highlight.
    severities = {r.get("severity") for r in data}
    assert "high" in severities, "sample should include at least one high-severity ticket"

    # At least three distinct categories so clustering demonstrates differentiation.
    categories = {r.get("category") for r in data if r.get("category")}
    assert len(categories) >= 3, f"sample should cover ≥3 categories; got {categories}"


def test_support_digest_recipe_exists():
    pkg_root = Path(__file__).resolve().parents[2]
    recipe = pkg_root / "docs" / "recipes" / "support-digest.md"
    assert recipe.exists()
    text = recipe.read_text(encoding="utf-8")
    for keyword in (".prax/inbox", ".prax/vault/support", "sample-tickets.json", "脱敏"):
        assert keyword in text, f"recipe should mention {keyword!r}"
