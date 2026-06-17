"""Unit tests for prax/core/claude_step_executor.py.

A fake async CLI is injected so the adapter's mapping logic (ExecutionResult ->
StepResult, arg passing, async->sync bridge, cost extraction) is verified
without spawning a real `claude -p`. End-to-end validation against the real
subscription is the M0 spike (which needs the user's login and can't run here).
"""
from __future__ import annotations

from prax.core.claude_cli_executor import ExecutionResult
from prax.core.claude_step_executor import ClaudeStepExecutor
from prax.core.orchestrator_loop import OrchestratorLoop, VerifyResult
from prax.core.run_journal import RunJournal


class FakeCli:
    def __init__(self, *, text="ok", session_id="sess_9", usage=None, cost_usd=None):
        self.calls: list[dict] = []
        self._text = text
        self._session_id = session_id
        self._usage = usage
        self._cost_usd = cost_usd

    async def run(
        self,
        prompt,
        *,
        session_id=None,
        model=None,
        permission_mode="bypassPermissions",
        cwd=None,
        on_text=None,
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "session_id": session_id,
                "model": model,
                "cwd": cwd,
                "permission_mode": permission_mode,
            }
        )
        return ExecutionResult(
            text=self._text,
            usage=self._usage,
            session_id=self._session_id,
            cost_usd=self._cost_usd,
        )


class TestClaudeStepExecutor:
    def test_maps_execution_result_to_step_result(self, tmp_path):
        cli = FakeCli(text="done", session_id="s1", cost_usd=0.02)
        ex = ClaudeStepExecutor(str(tmp_path), cli=cli)
        step = ex.run("实现登录页")

        assert step.text == "done"
        assert step.session_id == "s1"
        assert step.cost_usd == 0.02
        assert cli.calls[0]["prompt"] == "实现登录页"
        assert cli.calls[0]["cwd"] == str(tmp_path)

    def test_passes_model_through(self, tmp_path):
        cli = FakeCli()
        ex = ClaudeStepExecutor(str(tmp_path), model="claude-sonnet-4-8", cli=cli)
        ex.run("x")
        assert cli.calls[0]["model"] == "claude-sonnet-4-8"

    def test_cost_none_when_not_provided(self, tmp_path):
        ex = ClaudeStepExecutor(str(tmp_path), cli=FakeCli(cost_usd=None))
        assert ex.run("x").cost_usd is None

    def test_satisfies_executor_protocol_in_loop(self, tmp_path):
        cli = FakeCli(text="ran")
        loop = OrchestratorLoop(
            journal=RunJournal(str(tmp_path), "rce"),
            executor=ClaudeStepExecutor(str(tmp_path), cli=cli),
            verifier=lambda: VerifyResult(passed=True),
            max_iterations=3,
        )
        outcome = loop.run("目标")

        assert outcome.verified is True
        assert len(cli.calls) == 1
