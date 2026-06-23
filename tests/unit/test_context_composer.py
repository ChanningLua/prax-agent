"""Unit tests for prax/core/context_composer.py."""
from __future__ import annotations

from prax.core.context_composer import ContextComposer
from prax.core.orchestrator_loop import OrchestratorLoop, StepResult, VerifyResult
from prax.core.run_journal import RunJournal


def _write(tmp_path, name, text):
    d = tmp_path / ".prax"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


class TestContextComposer:
    def test_injects_north_star_and_goal(self, tmp_path):
        _write(tmp_path, "north_star.md", "把 C 端 APP 的后台管理系统做出来")
        compose = ContextComposer(str(tmp_path))
        out = compose("实现登录页", None, 0)
        assert "把 C 端 APP 的后台管理系统做出来" in out
        assert "实现登录页" in out
        assert "上下文可能随时被重置" in out  # ASSUME INTERRUPTION header

    def test_constraints_injected_on_first_iteration(self, tmp_path):
        _write(tmp_path, "constraints.md", "NEVER 直接改生产数据库")
        compose = ContextComposer(str(tmp_path))
        assert "NEVER 直接改生产数据库" in compose("目标", None, 0)

    def test_constraints_injected_every_iteration(self, tmp_path):
        _write(tmp_path, "constraints.md", "NEVER 删除测试")
        compose = ContextComposer(str(tmp_path))
        # fresh-context-per-task: each step is a new claude -p context (no
        # --resume), so the prohibition must be present on EVERY iteration —
        # skipping it on a cadence would leave those fresh contexts unconstrained.
        for i in range(5):
            assert "NEVER 删除测试" in compose("g", None, i)

    def test_feedback_appended_only_when_present(self, tmp_path):
        compose = ContextComposer(str(tmp_path))
        assert "boom" in compose("g", "boom", 1)
        assert "未通过" not in compose("g", None, 0)

    def test_progress_injected_when_present(self, tmp_path):
        _write(tmp_path, "progress.md", "已完成：登录；待办：列表页")
        compose = ContextComposer(str(tmp_path))
        assert "待办：列表页" in compose("g", None, 0)

    def test_missing_files_are_skipped(self, tmp_path):
        compose = ContextComposer(str(tmp_path))
        out = compose("只有目标", None, 0)
        assert "只有目标" in out  # no north_star/constraints/progress, still works

    def test_plugs_into_loop_and_constraints_reach_executor(self, tmp_path):
        _write(tmp_path, "constraints.md", "NEVER 碰生产")

        class _Exec:
            def __init__(self) -> None:
                self.instructions: list[str] = []

            def run(self, instruction, *, session_id=None):
                self.instructions.append(instruction)
                return StepResult(text="ran", session_id="s")

        ex = _Exec()
        loop = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "rc"),
            executor=ex,
            verifier=lambda: VerifyResult(passed=True),
            max_iterations=5,
            compose=ContextComposer(str(tmp_path)),
        )
        loop.run("目标 X")
        assert "NEVER 碰生产" in ex.instructions[0]
        assert "目标 X" in ex.instructions[0]
