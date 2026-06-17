"""Unit tests for prax/core/stuck_detector.py."""
from __future__ import annotations

from prax.core.stuck_detector import StuckDetector


class TestStuckDetector:
    def test_not_stuck_when_signals_vary(self):
        assert StuckDetector().is_stuck(["a", "b", "c", "d"]) is False

    def test_stuck_on_consecutive_identical(self):
        assert StuckDetector(repeat_threshold=3).is_stuck(["x", "a", "a", "a"]) is True

    def test_not_stuck_when_below_threshold(self):
        assert StuckDetector(repeat_threshold=3).is_stuck(["x", "a", "a"]) is False

    def test_normalization_ignores_volatile_digits(self):
        # same failure with a different pid / line number still counts as repeat
        d = StuckDetector(repeat_threshold=3)
        assert d.is_stuck(["fail pid 123", "fail pid 456", "fail pid 789"]) is True

    def test_alternating_pattern_is_stuck(self):
        # isolate alternation: set repeat_threshold high so only A/B/A/B can trip
        d = StuckDetector(repeat_threshold=99, alternation_threshold=3)
        assert d.is_stuck(["a", "b", "a", "b", "a", "b"]) is True

    def test_recovers_after_distinct_signal(self):
        assert StuckDetector(repeat_threshold=3).is_stuck(["a", "a", "b", "c"]) is False

    def test_short_or_empty_not_stuck(self):
        d = StuckDetector(repeat_threshold=3)
        assert d.is_stuck([]) is False
        assert d.is_stuck(["a"]) is False
