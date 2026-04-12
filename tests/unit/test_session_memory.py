"""Unit tests for prax/core/session_memory.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from prax.core import session_memory as sm_module
from prax.core.session_memory import (
    SessionMemory,
    _DEFAULT_TEMPLATE,
    _SECTION_STATE,
    _SECTION_TASK,
    _SECTION_TITLE,
    _memory_dir,
)


# ---------------------------------------------------------------------------
# Fixture: redirect _memory_dir to tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() to a temp directory so tests never touch ~/.claude."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


@pytest.fixture
def mem(tmp_path, fake_home):
    """A SessionMemory instance whose backing dir lives under tmp_path."""
    cwd = str(tmp_path / "project")
    Path(cwd).mkdir()
    return SessionMemory(cwd=cwd)


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------

class TestLoad:
    def test_missing_file_returns_empty_string(self, mem):
        assert mem.load() == ""

    def test_existing_file_returns_content(self, mem):
        mem.save("hello world")
        assert mem.load() == "hello world"


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

class TestSave:
    def test_creates_dirs_and_writes(self, mem):
        mem.save("test content")
        assert mem.path.exists()
        assert mem.path.read_text(encoding="utf-8") == "test content"


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------

class TestInitialize:
    def test_creates_template_when_absent(self, mem):
        mem.initialize("My Session")
        content = mem.load()
        assert "My Session" in content
        assert _SECTION_TITLE in content
        assert _SECTION_STATE in content
        assert _SECTION_TASK in content

    def test_noop_if_file_already_exists(self, mem):
        mem.save("existing content")
        mem.initialize("New Title")
        assert mem.load() == "existing content"


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------

class TestExists:
    def test_false_when_missing(self, mem):
        assert mem.exists() is False

    def test_true_when_present(self, mem):
        mem.save("x")
        assert mem.exists() is True


# ---------------------------------------------------------------------------
# get_last_summarized_id
# ---------------------------------------------------------------------------

class TestGetLastSummarizedId:
    def test_none_literal_returns_none(self, mem):
        mem.save("<!-- lastSummarizedMessageId: none -->\n\n## Session Title\nTest\n")
        assert mem.get_last_summarized_id() is None

    def test_actual_id_returned(self, mem):
        mem.save("<!-- lastSummarizedMessageId: msg-abc123 -->\n\n## Session Title\nTest\n")
        assert mem.get_last_summarized_id() == "msg-abc123"

    def test_no_header_returns_none(self, mem):
        mem.save("## Session Title\nTest\n")
        assert mem.get_last_summarized_id() is None


# ---------------------------------------------------------------------------
# set_last_summarized_id
# ---------------------------------------------------------------------------

class TestSetLastSummarizedId:
    def test_updates_existing_header(self, mem):
        mem.save("<!-- lastSummarizedMessageId: old-id -->\n\n## Session Title\nTest\n")
        mem.set_last_summarized_id("new-id")
        assert mem.get_last_summarized_id() == "new-id"

    def test_adds_header_when_missing(self, mem):
        mem.save("## Session Title\nTest\n")
        mem.set_last_summarized_id("fresh-id")
        assert mem.get_last_summarized_id() == "fresh-id"


# ---------------------------------------------------------------------------
# update_section
# ---------------------------------------------------------------------------

class TestUpdateSection:
    def test_update_session_title(self, mem):
        mem.initialize()
        mem.update_section(_SECTION_TITLE, "My New Title")
        content = mem.load()
        assert "My New Title" in content

    def test_update_current_state(self, mem):
        mem.initialize()
        mem.update_section(_SECTION_STATE, "Working on feature X")
        content = mem.load()
        assert "Working on feature X" in content

    def test_update_task_specification(self, mem):
        mem.initialize()
        mem.update_section(_SECTION_TASK, "Implement the widget")
        content = mem.load()
        assert "Implement the widget" in content

    def test_unknown_section_logs_warning_and_no_write(self, mem, caplog):
        mem.initialize()
        original = mem.load()
        import logging
        with caplog.at_level(logging.WARNING, logger="prax.core.session_memory"):
            mem.update_section("## Unknown Section", "content")
        assert mem.load() == original

    def test_section_not_found_appends(self, mem):
        # File without the standard sections
        mem.save("<!-- lastSummarizedMessageId: none -->\n\nSome content\n")
        mem.update_section(_SECTION_TASK, "Appended task")
        content = mem.load()
        assert "Appended task" in content
        assert _SECTION_TASK in content


# ---------------------------------------------------------------------------
# get_summary_for_compaction
# ---------------------------------------------------------------------------

class TestGetSummaryForCompaction:
    def test_empty_file_returns_none(self, mem):
        assert mem.get_summary_for_compaction() is None

    def test_default_template_returns_none(self, mem):
        mem.save(_DEFAULT_TEMPLATE)
        assert mem.get_summary_for_compaction() is None

    def test_real_content_returns_formatted_summary(self, mem):
        mem.save(
            "<!-- lastSummarizedMessageId: msg-1 -->\n\n"
            "## Session Title\nReal Work\n\n"
            "## Current State\nDone with step 1.\n\n"
            "## Task Specification\nBuild the thing.\n"
        )
        result = mem.get_summary_for_compaction()
        assert result is not None
        assert result.startswith("[Session Memory Summary]")
        assert "Real Work" in result


# ---------------------------------------------------------------------------
# update_from_messages
# ---------------------------------------------------------------------------

class TestUpdateFromMessages:
    def test_new_file_extracts_title_from_first_user_message(self, mem):
        messages = [
            {"role": "user", "content": "Please help me build a widget"},
            {"role": "assistant", "content": "Sure, I can help."},
        ]
        mem.update_from_messages(messages)
        content = mem.load()
        assert "Please help me build a widget" in content

    def test_explicit_title_used_when_provided(self, mem):
        messages = [{"role": "user", "content": "some task"}]
        mem.update_from_messages(messages, title="Explicit Title")
        content = mem.load()
        assert "Explicit Title" in content

    def test_state_summary_updates_current_state(self, mem):
        mem.initialize()
        messages = [{"role": "user", "content": "task"}]
        mem.update_from_messages(messages, state_summary="Step 2 complete")
        content = mem.load()
        assert "Step 2 complete" in content

    def test_extracts_last_assistant_text(self, mem):
        mem.initialize()
        messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "continue"},
            {"role": "assistant", "content": "Final response text"},
        ]
        mem.update_from_messages(messages)
        content = mem.load()
        assert "Final response text" in content

    def test_assistant_content_as_list_of_blocks(self, mem):
        mem.initialize()
        messages = [
            {"role": "user", "content": "do something"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Block response text"},
                    {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
                ],
            },
        ]
        mem.update_from_messages(messages)
        content = mem.load()
        assert "Block response text" in content
