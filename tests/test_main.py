from __future__ import annotations

from prax.main import _handle_slash_command


class TestSlashCommands:
    def test_non_slash_task_passes_through(self, capsys):
        handled = _handle_slash_command("read /tmp/demo.txt")
        captured = capsys.readouterr()

        assert not handled
        assert captured.out == ""

    def test_ralph_loop_is_rejected_with_correct_guidance(self, capsys):
        handled = _handle_slash_command('/ralph-loop "Execute PRD"')
        captured = capsys.readouterr()

        assert handled
        assert "/ralph-loop" in captured.out
        assert "ralph.sh" in captured.out
        assert "./ralph.sh --tool claude" in captured.out

    def test_ralph_skill_is_rejected_with_clarification(self, capsys):
        handled = _handle_slash_command("/ralph")
        captured = capsys.readouterr()

        assert handled
        assert "/ralph" in captured.out
        assert "generate `prd.json`" in captured.out

    def test_unknown_slash_command_is_rejected(self, capsys):
        handled = _handle_slash_command("/unknown-cmd")
        captured = capsys.readouterr()

        assert handled
        assert "does not support slash commands" in captured.out
        assert "/unknown-cmd" in captured.out
