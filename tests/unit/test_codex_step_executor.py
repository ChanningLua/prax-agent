"""Tests for the synchronous Codex OrchestratorLoop adapter."""

from __future__ import annotations

from prax.core.codex_cli_executor import CodexExecutionResult
from prax.core.codex_step_executor import CodexStepExecutor
from prax.core.orchestrator_loop import OrchestratorLoop, VerifyResult
from prax.core.run_journal import RunJournal


class FakeCodexCli:
    def __init__(self, *, text: str = "done", session_id: str = "thread-1"):
        self.text = text
        self.session_id = session_id
        self.calls: list[dict] = []

    async def run(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return CodexExecutionResult(text=self.text, session_id=self.session_id)


def test_maps_result_and_passes_model_sandbox_and_cwd(tmp_path):
    cli = FakeCodexCli()
    executor = CodexStepExecutor(
        str(tmp_path),
        model="gpt-5.6-codex",
        sandbox="read-only",
        cli=cli,
    )

    result = executor.run("inspect", session_id="old-thread")

    assert result.text == "done"
    assert result.session_id == "thread-1"
    assert cli.calls == [
        {
            "prompt": "inspect",
            "session_id": None,
            "model": "gpt-5.6-codex",
            "sandbox": "read-only",
            "cwd": str(tmp_path),
        }
    ]


def test_resume_is_explicit_opt_in(tmp_path):
    cli = FakeCodexCli()
    executor = CodexStepExecutor(str(tmp_path), cli=cli, resume_sessions=True)
    executor.run("continue", session_id="old-thread")
    assert cli.calls[0]["session_id"] == "old-thread"


def test_satisfies_orchestrator_executor_protocol(tmp_path):
    cli = FakeCodexCli(text="implemented")
    loop = OrchestratorLoop(
        journal=RunJournal(str(tmp_path), "codex-loop"),
        executor=CodexStepExecutor(str(tmp_path), cli=cli),
        verifier=lambda: VerifyResult(passed=True),
        max_iterations=2,
    )

    outcome = loop.run("goal")

    assert outcome.verified is True
    assert outcome.iterations == 1
    assert len(cli.calls) == 1
