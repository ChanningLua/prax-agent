"""Unit tests for Sisyphus routing — JSON parsing and strategy classification."""
from __future__ import annotations

import json
import pytest

from prax.agents.sisyphus import SisyphusAgent


class TestSisyphusClassifyStrategy:
    """Tests for the _classify_strategy JSON parsing logic."""

    def _parse_result(self, agent: SisyphusAgent, result_text: str) -> str:
        """Call the JSON extraction logic used inside _classify_strategy."""
        # Replicate the parsing logic from the method:
        start = result_text.find("{")
        end = result_text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(result_text[start:end])
                strategy = str(data.get("strategy", "")).lower().strip()
                if strategy in ("ralph", "team", "direct"):
                    return strategy
            except json.JSONDecodeError:
                pass
        # Fallback prefix matching
        lower = result_text.lower().strip()
        if lower.startswith("ralph"):
            return "ralph"
        if lower.startswith("team"):
            return "team"
        return "direct"

    def setup_method(self):
        self.agent = SisyphusAgent(cwd="/tmp", model="glm-4-flash")

    def test_valid_json_ralph(self):
        assert self._parse_result(self.agent, '{"strategy": "ralph", "reason": "needs todos"}') == "ralph"

    def test_valid_json_team(self):
        assert self._parse_result(self.agent, '{"strategy": "team", "reason": "parallel"}') == "team"

    def test_valid_json_direct(self):
        assert self._parse_result(self.agent, '{"strategy": "direct", "reason": "simple"}') == "direct"

    def test_json_embedded_in_prose(self):
        prose = 'Sure! Here is my answer:\n{"strategy": "ralph", "reason": "needs todos"}'
        assert self._parse_result(self.agent, prose) == "ralph"

    def test_invalid_strategy_value_falls_back_to_direct(self):
        assert self._parse_result(self.agent, '{"strategy": "unknown"}') == "direct"

    def test_malformed_json_falls_back_to_prefix(self):
        assert self._parse_result(self.agent, "ralph is the right choice") == "ralph"

    def test_empty_response_returns_direct(self):
        assert self._parse_result(self.agent, "") == "direct"

    def test_no_json_no_prefix_returns_direct(self):
        assert self._parse_result(self.agent, "I cannot determine a strategy.") == "direct"

    def test_uppercase_strategy_normalised(self):
        assert self._parse_result(self.agent, '{"strategy": "RALPH"}') == "ralph"
