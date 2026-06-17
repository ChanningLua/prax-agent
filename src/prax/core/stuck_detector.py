"""Stuck detector — flags a thrashing loop so the orchestrator can stop/escalate.

Ported from OpenHands' StuckDetector patterns: a run is "stuck" when recent
signals show either (a) the same thing N times in a row, or (b) an A/B/A/B
alternation. Signals are compared *semantically* — volatile bits (PIDs, line
numbers, timestamps → any digit run) are normalised away, so "same failure with
a different pid" still counts as repetition.

Pure + sync. The orchestrator feeds it the per-iteration signal sequence (e.g.
verify-failure outputs) and stops when ``is_stuck`` is True — never crashing,
just degrading to a paused/escalated state (OpenHands' lesson: a stuck detector
must not hard-crash, and must reset when fresh human/instruction input arrives).
"""
from __future__ import annotations

import re

_DIGIT_RUN = re.compile(r"\d+")


def _normalize(signal: str) -> str:
    """Collapse whitespace and digit runs so volatile values don't defeat the
    repetition check (pids, line numbers, timestamps all become ``#``)."""
    return _DIGIT_RUN.sub("#", " ".join(signal.split())).strip()


class StuckDetector:
    def __init__(self, *, repeat_threshold: int = 3, alternation_threshold: int = 3) -> None:
        # repeat_threshold: N identical signals in a row => stuck
        # alternation_threshold: N repeats of an A,B pair (=> 2N signals) => stuck
        self._repeat_threshold = repeat_threshold
        self._alternation_threshold = alternation_threshold

    def is_stuck(self, signals: list[str]) -> bool:
        norm = [_normalize(s) for s in signals if s is not None]
        return self._consecutive_identical(norm) or self._alternating(norm)

    def _consecutive_identical(self, norm: list[str]) -> bool:
        if self._repeat_threshold <= 0 or len(norm) < self._repeat_threshold:
            return False
        tail = norm[-self._repeat_threshold :]
        return len(set(tail)) == 1

    def _alternating(self, norm: list[str]) -> bool:
        need = 2 * self._alternation_threshold
        if self._alternation_threshold <= 0 or len(norm) < need:
            return False
        tail = norm[-need:]
        a, b = tail[0], tail[1]
        if a == b:
            return False
        return all(tail[i] == (a if i % 2 == 0 else b) for i in range(need))
