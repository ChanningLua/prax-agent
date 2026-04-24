"""Simple rule-based task classifier."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class Classifier:
    """Classifies user tasks into tiers based on keyword matching."""

    def __init__(self, rules_path: str | None = None):
        self._rules: list[dict[str, Any]] = []
        self._tier_models: dict[str, list[str]] = {}

        if rules_path:
            self._load_rules(rules_path)

    def _load_rules(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            return
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            self._rules = data.get("rules", [])
            self._tier_models = data.get("tier_models", {})
        except Exception:
            pass

    def classify(self, task: str) -> str:
        """Return the tier for a task based on keyword matching."""
        task_lower = task.lower()
        for rule in self._rules:
            keywords = rule.get("keywords", [])
            if any(kw in task_lower for kw in keywords):
                return rule.get("tier", "standard")
        return "standard"

    def get_model_for_tier(self, tier: str) -> str | None:
        """Return the first model for a given tier."""
        models = self._tier_models.get(tier, [])
        return models[0] if models else None

    def select_model(self, task: str, default: str = "glm-4-flash") -> str:
        """Classify task and return the recommended model."""
        tier = self.classify(task)
        return self.get_model_for_tier(tier) or default
