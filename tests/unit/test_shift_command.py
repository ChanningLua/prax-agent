"""Public Shift entry routing and task-backed cron parsing; no worker or scheduler."""
from __future__ import annotations

import sys
from unittest.mock import Mock

import pytest


def test_shift_help_exposes_contract_commands(monkeypatch, capsys):
    from prax.main import main

    monkeypatch.setattr(sys, "argv", ["prax", "shift", "--help"])
    with pytest.raises(SystemExit) as outcome:
        main()
    assert outcome.value.code == 0
    help_text = capsys.readouterr().out
    assert "prax shift" in help_text
    assert "--task" in help_text and "--resume-existing" in help_text
    assert "pause" in help_text and "Legacy compatibility" in help_text


def test_short_task_spelling_uses_same_kernel(monkeypatch):
    from prax.commands.shift import main

    kernel = Mock(return_value=2)
    monkeypatch.setattr("prax.repository_harness.main", kernel)
    assert main(["--task", ".prax/task.json"]) == 2
    kernel.assert_called_once_with(["run", "--task", ".prax/task.json"], prog="prax shift")


def test_main_preserves_contract_exit_code(monkeypatch):
    from prax.main import main

    monkeypatch.setattr(sys, "argv", ["prax", "shift", "run", "--task", ".prax/task.json"])
    worker = Mock(return_value=3)
    monkeypatch.setattr("prax.commands.shift.main", worker)
    with pytest.raises(SystemExit) as outcome:
        main()
    assert outcome.value.code == 3
    worker.assert_called_once_with(["run", "--task", ".prax/task.json"])


@pytest.mark.parametrize("entry", ["shift", "harness"])
def test_main_preserves_current_verification_authorization(monkeypatch, entry):
    from prax.main import main

    args = ["run", "--task", ".prax/verification.json", "--authorize-verification"]
    monkeypatch.setattr(sys, "argv", ["prax", entry, *args])
    kernel = Mock(return_value=2)
    target = "prax.commands.shift.main" if entry == "shift" else "prax.repository_harness.main"
    monkeypatch.setattr(target, kernel)
    with pytest.raises(SystemExit) as outcome:
        main()
    assert outcome.value.code == 2
    kernel.assert_called_once_with(args)


def test_cron_task_add_does_not_require_prompt_or_install_scheduler(tmp_path, monkeypatch):
    from prax.cli import main
    from prax.core.cron_store import CronStore

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "prax", "cron", "add", "--name", "offline-task", "--schedule", "0 * * * *",
        "--run-mode", "shift", "--task", ".prax/task.json",
    ])
    main()
    job, = CronStore(str(tmp_path)).load()
    assert job.task_path == ".prax/task.json" and job.prompt == ""
    assert job.run_mode == "orchestrate" and job.agent_host is None
