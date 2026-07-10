"""Unit tests for prax/core/three_step_router.py.

Covers the 三步走 routing table: each of the three steps is reachable, the guards
escalate in the right order, and the step falls out of OBJECTIVE signals (verify
strength + change breadth), not a subjective flag.
"""
from __future__ import annotations

from prax.core.three_step_router import (
    STEP_AUTONOMOUS,
    STEP_HUMAN_CHECK,
    STEP_SUMMARIZE_VERIFY,
    VERIFY_BEHAVIORAL,
    VERIFY_BUILD,
    VERIFY_NONE,
    VERIFY_TESTS,
    WorkSignal,
    route,
    verify_strength_of,
)


class TestComplexityScore:
    def test_trivial_is_simple(self):
        assert WorkSignal(files_touched=1).complexity_level == "SIMPLE"

    def test_a_few_files_is_moderate(self):
        assert WorkSignal(files_touched=4).complexity_level == "MODERATE"

    def test_multi_repo_plus_design_is_complex(self):
        s = WorkSignal(files_touched=6, multi_repo=True, needs_design=True)
        assert s.complexity_level == "COMPLEX"


class TestRouting:
    def test_no_verifier_forces_summary_verify(self):
        # Even a trivial change: no automated acceptance → a human must verify.
        r = route(WorkSignal(verify_strength=VERIFY_NONE, files_touched=1))
        assert r.step == STEP_SUMMARIZE_VERIFY

    def test_complex_work_summary_verify_even_with_tests(self):
        r = route(
            WorkSignal(
                verify_strength=VERIFY_TESTS,
                files_touched=8,
                cross_language=True,
                needs_design=True,
            )
        )
        assert r.step == STEP_SUMMARIZE_VERIFY

    def test_simple_with_behavioral_is_autonomous(self):
        r = route(WorkSignal(verify_strength=VERIFY_BEHAVIORAL, files_touched=1))
        assert r.step == STEP_AUTONOMOUS

    def test_simple_but_only_build_is_human_check(self):
        # build-only is NOT strong enough to be the gate → human spot-checks.
        r = route(WorkSignal(verify_strength=VERIFY_BUILD, files_touched=1))
        assert r.step == STEP_HUMAN_CHECK

    def test_moderate_with_tests_is_human_check(self):
        r = route(WorkSignal(verify_strength=VERIFY_TESTS, files_touched=3))
        assert r.step == STEP_HUMAN_CHECK

    def test_behavioral_but_moderate_is_not_autonomous(self):
        # A strong verifier alone doesn't unlock autonomy — complexity gates it.
        r = route(WorkSignal(verify_strength=VERIFY_BEHAVIORAL, files_touched=4))
        assert r.step == STEP_HUMAN_CHECK

    def test_summary_mentions_step_and_axes(self):
        s = route(WorkSignal(verify_strength=VERIFY_NONE)).summary()
        assert "总结验证" in s and "验收" in s


class TestVerifyStrengthOf:
    def test_empty_is_none(self):
        assert verify_strength_of("") == VERIFY_NONE
        assert verify_strength_of("   ") == VERIFY_NONE

    def test_build_command(self):
        assert verify_strength_of("npm run build") == VERIFY_BUILD

    def test_test_runner(self):
        assert verify_strength_of("pytest -q") == VERIFY_TESTS
        assert verify_strength_of("npm test") == VERIFY_TESTS

    def test_behavioral_wins_over_build(self):
        # e2e signal must not be shadowed by a co-occurring word
        assert verify_strength_of("node verify-features.mjs") == VERIFY_BEHAVIORAL
        assert verify_strength_of("npx playwright test") == VERIFY_BEHAVIORAL

    def test_opaque_script_is_test_grade_not_autonomous(self):
        # a real but unclassifiable gate → tests-grade (won't unlock 自主循环)
        assert verify_strength_of("./.prax/scripts/check.sh") == VERIFY_TESTS


class TestDeclaredComplexity:
    def test_declared_wins_over_derived(self):
        # breadth says COMPLEX, but the curator declared SIMPLE → SIMPLE
        s = WorkSignal(files_touched=9, multi_repo=True, declared_complexity="simple")
        assert s.complexity_level == "SIMPLE"

    def test_declared_complex_blocks_autonomy(self):
        # even simple breadth + behavioral verifier: a COMPLEX declaration routes
        # to 总结验证
        r = route(
            WorkSignal(
                verify_strength=VERIFY_BEHAVIORAL,
                files_touched=1,
                declared_complexity="complex",
            )
        )
        assert r.step == STEP_SUMMARIZE_VERIFY

    def test_invalid_declaration_falls_back_to_derived(self):
        s = WorkSignal(files_touched=1, declared_complexity="whatever")
        assert s.complexity_level == "SIMPLE"
