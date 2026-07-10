"""Three-step router — pick the autonomy step for a piece of work.

Encodes the "三步走" trust gradient of the 三循环×三步走 method: the 外部循环
(recurring triage) discovers work; this router decides HOW MUCH to let the
开发者循环 run on it, keyed on OBJECTIVE signals (verification strength + change
breadth) — never a subjective "feels simple" (that's how everything becomes
"simple" and verification gets skipped).

Three steps (ascending autonomy):
  复杂项目·总结验证 (SUMMARIZE_VERIFY) — agent works, produces a summary, a human
        deep-verifies. For complex work OR work with no automated acceptance
        (the agent can't self-verify → a human must).
  简单项目·人check结果 (HUMAN_CHECK)   — agent works, a human spot-checks the
        result (PR review). For bounded work with a real but not behavioral
        verifier.
  自主循环 (AUTONOMOUS)                — full loop-until-done, no human in the
        loop. ONLY when the work is simple AND the automated verification is
        strong enough to BE the gate (behavioral/e2e). Autonomy ≠ no gate; it is
        a gate strong enough to not need a human.

Why these two axes (evidence):
  • SWE-bench Pro: complex/multi-file/cross-language success ~45% (halves vs
    single-file) → complex work never runs unattended, a human verifies.
  • verification tax / DORA: speed without an automatic acceptance gate just
    moves the bottleneck from writing to reviewing → no verifier ⇒ human deep-
    verify (summarize), and autonomy demands the STRONGEST verifier.
"""
from __future__ import annotations

from dataclasses import dataclass

# Verifier strength (ascending) — how strong the AUTOMATED acceptance is.
VERIFY_NONE = 0  # no automated acceptance at all
VERIFY_BUILD = 1  # compiles / builds only (e.g. `npm run build`)
VERIFY_TESTS = 2  # unit / integration tests pass
VERIFY_BEHAVIORAL = 3  # behavioral / e2e (Puppeteer, curl probe) — proves it RUNS

_VERIFY_NAMES: dict[int, str] = {
    VERIFY_NONE: "none",
    VERIFY_BUILD: "build-only",
    VERIFY_TESTS: "tests",
    VERIFY_BEHAVIORAL: "behavioral/e2e",
}

# Keyword → verify strength, checked strength-DESCENDING so "npm run test:e2e"
# reads as behavioral, "npm test" as tests, "npm run build" as build.
_BEHAVIORAL_HINTS = ("puppeteer", "playwright", "cypress", "e2e", "verify-features", "curl ", ".mjs")
_TEST_HINTS = ("pytest", "jest", "vitest", "unittest", " test", "test ", "go test", "cargo test")
_BUILD_HINTS = ("build", "compile", "tsc", "cargo build", "go build")


def verify_strength_of(command: str) -> int:
    """Classify an acceptance command into a verify strength (objective axis).

    Descending check so the strongest matching signal wins. An empty command =
    no automated acceptance (VERIFY_NONE). A non-empty command we can't classify
    (e.g. an opaque ``./script``) is treated conservatively as VERIFY_TESTS — a
    real gate the curator chose, but NOT strong enough to unlock 自主循环 on its
    own (that still needs a behavioral signal)."""
    c = (command or "").strip().lower()
    if not c:
        return VERIFY_NONE
    if any(h in c for h in _BEHAVIORAL_HINTS):
        return VERIFY_BEHAVIORAL
    if any(h in c for h in _TEST_HINTS):
        return VERIFY_TESTS
    if any(h in c for h in _BUILD_HINTS):
        return VERIFY_BUILD
    return VERIFY_TESTS  # non-empty but opaque → assume a real (test-grade) gate

# The three steps (user's exact terms).
STEP_SUMMARIZE_VERIFY = "复杂项目·总结验证"
STEP_HUMAN_CHECK = "简单项目·人check结果"
STEP_AUTONOMOUS = "自主循环"


@dataclass
class WorkSignal:
    """Objective signals about a piece of work the 外部循环 surfaced.

    Deliberately NOT an ``is_simple: bool`` — the step must fall out of
    measurable facts, not a caller's gut feel."""

    verify_strength: int = VERIFY_NONE
    files_touched: int = 1
    multi_repo: bool = False
    cross_language: bool = False
    needs_design: bool = False  # ambiguous requirement / needs a design decision
    # A curator's up-front complexity call (SIMPLE/MODERATE/COMPLEX). Change
    # breadth is unknown BEFORE a task runs, so a plan (feature_list) can declare
    # it; when set it wins over the derived score. Absent → derive from breadth.
    declared_complexity: str = ""

    @property
    def complexity(self) -> int:
        """Additive complexity score (0 = trivial). Mirrors risk_scorer's style."""
        score = 0
        if self.files_touched > 5:
            score += 2
        elif self.files_touched >= 2:
            score += 1
        if self.multi_repo:
            score += 2
        if self.cross_language:
            score += 1
        if self.needs_design:
            score += 2
        return score

    @property
    def complexity_level(self) -> str:
        # A declared level wins (the curator knew the breadth up front).
        decl = self.declared_complexity.strip().upper()
        if decl in ("SIMPLE", "MODERATE", "COMPLEX"):
            return decl
        # Else derive. SIMPLE is reserved for genuinely atomic work (score 0 —
        # single-file, no cross-cutting signal): only that earns a shot at 自主
        # 循环. Any breadth at all is at least MODERATE (SWE-bench: multi-file
        # success ~halves).
        c = self.complexity
        if c == 0:
            return "SIMPLE"
        if c <= 3:
            return "MODERATE"
        return "COMPLEX"


@dataclass
class Routing:
    step: str
    reason: str
    complexity_level: str
    verify_name: str

    def summary(self) -> str:
        return (
            f"三步走 → {self.step}"
            f"（复杂度={self.complexity_level}, 验收={self.verify_name}）：{self.reason}"
        )


def route(signal: WorkSignal) -> Routing:
    """Pick the autonomy step. Deterministic, no LLM.

    Order matters — the escalating guards fall through to the most autonomous
    step only when every safety condition holds."""
    lvl = signal.complexity_level
    vname = _VERIFY_NAMES[signal.verify_strength]

    # Rule 1 — no automated acceptance: the agent CANNOT self-verify, so a human
    # must. Summarize for deep human verification regardless of size.
    if signal.verify_strength == VERIFY_NONE:
        return Routing(
            STEP_SUMMARIZE_VERIFY, "无自动验收，agent 无法自证 → 人必须深度验证", lvl, vname
        )

    # Rule 2 — complex work: even with a verifier, ~45% success on real multi-
    # file/cross-language work → never unattended; agent summarizes, human verifies.
    if lvl == "COMPLEX":
        return Routing(
            STEP_SUMMARIZE_VERIFY, "复杂项目（多文件/跨仓/跨语言/需设计）→ 总结验证", lvl, vname
        )

    # Rule 3 — autonomy: only simple work WITH a behavioral verifier strong
    # enough to BE the gate. The only path to no-human-in-loop.
    if lvl == "SIMPLE" and signal.verify_strength >= VERIFY_BEHAVIORAL:
        return Routing(STEP_AUTONOMOUS, "简单 + 行为级验收（够强当门）→ 自主循环", lvl, vname)

    # Rule 4 — everything else (moderate complexity, or verifier only build/
    # tests): agent works, human spot-checks the result (PR review).
    return Routing(STEP_HUMAN_CHECK, "有验收但未达自主门槛 → 人 check 结果（PR 抽查）", lvl, vname)
