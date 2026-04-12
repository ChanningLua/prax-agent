"""Session Memory — per-session running summary for context compaction.

Maintains a Markdown file at:
  ~/.claude/projects/<project-hash>/session_memory.md

The file has three sections:
  ## Session Title
  ## Current State
  ## Task Specification

The `lastSummarizedMessageId` field tracks which messages have been
incorporated so the compaction path can skip already-summarized content.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SECTION_TITLE = "## Session Title"
_SECTION_STATE = "## Current State"
_SECTION_TASK = "## Task Specification"

_DEFAULT_TEMPLATE = """\
<!-- lastSummarizedMessageId: none -->

## Session Title
Untitled Session

## Current State
No summary yet.

## Task Specification
Not specified.
"""


def _project_hash(cwd: str) -> str:
    """Compute a short hash for the project path (used in directory name)."""
    return hashlib.sha1(cwd.encode()).hexdigest()[:12]


def _memory_dir(cwd: str) -> Path:
    """Return the per-project memory directory under ~/.claude/projects/."""
    proj_hash = _project_hash(cwd)
    return Path.home() / ".claude" / "projects" / proj_hash


class SessionMemory:
    """Read/write session memory for a given project directory.

    The session memory file is project-scoped, not session-scoped,
    so multiple sessions with the same cwd share one running summary.
    """

    def __init__(self, cwd: str, session_id: str | None = None) -> None:
        self._cwd = cwd
        self._session_id = session_id
        self._dir = _memory_dir(cwd)
        self._path = self._dir / "session_memory.md"

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> str:
        """Load the raw Markdown content, creating default if absent."""
        if not self._path.exists():
            return ""
        try:
            return self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read session_memory.md: %s", exc)
            return ""

    def save(self, content: str) -> None:
        """Write content to the session memory file."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path.write_text(content, encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to write session_memory.md: %s", exc)

    def initialize(self, title: str = "Untitled Session") -> None:
        """Create the session memory file with default template if not present."""
        if self._path.exists():
            return
        content = _DEFAULT_TEMPLATE.replace("Untitled Session", title)
        self.save(content)

    def get_last_summarized_id(self) -> str | None:
        """Extract the lastSummarizedMessageId from the file header."""
        content = self.load()
        match = re.search(r"<!--\s*lastSummarizedMessageId:\s*(\S+)\s*-->", content)
        if match:
            val = match.group(1)
            return None if val == "none" else val
        return None

    def set_last_summarized_id(self, message_id: str) -> None:
        """Update the lastSummarizedMessageId in the file header."""
        content = self.load() or _DEFAULT_TEMPLATE
        new_header = f"<!-- lastSummarizedMessageId: {message_id} -->"
        if re.search(r"<!--\s*lastSummarizedMessageId:", content):
            content = re.sub(
                r"<!--\s*lastSummarizedMessageId:\s*\S+\s*-->",
                new_header,
                content,
            )
        else:
            content = f"{new_header}\n{content}"
        self.save(content)

    def update_section(self, section: str, new_content: str) -> None:
        """Update a named section in the Markdown file.

        Args:
            section: One of "## Session Title", "## Current State",
                     "## Task Specification".
            new_content: New text body for the section (without the header).
        """
        content = self.load() or _DEFAULT_TEMPLATE
        sections = [_SECTION_TITLE, _SECTION_STATE, _SECTION_TASK]

        if section not in sections:
            logger.warning("Unknown session memory section: %s", section)
            return

        idx = sections.index(section)
        next_sections = sections[idx + 1:]

        # Build a regex that captures everything from `section` up to the next section header
        if next_sections:
            boundary = "|".join(re.escape(s) for s in next_sections)
            pattern = rf"({re.escape(section)})(.*?)(?={boundary})"
        else:
            pattern = rf"({re.escape(section)})(.*?)$"

        replacement = f"\\1\n{new_content.strip()}\n\n"
        new_content_full, count = re.subn(pattern, replacement, content, flags=re.DOTALL)

        if count == 0:
            # Section not found — append
            new_content_full = f"{content.rstrip()}\n\n{section}\n{new_content.strip()}\n"

        self.save(new_content_full)

    def get_summary_for_compaction(self) -> str | None:
        """Return the full session memory content formatted for use as a compaction summary.

        Returns None if the file doesn't exist or is empty.
        """
        content = self.load()
        if not content or content.strip() == _DEFAULT_TEMPLATE.strip():
            return None
        # Strip the comment header
        content = re.sub(r"<!--.*?-->\n?", "", content, flags=re.DOTALL).strip()
        if not content:
            return None
        return f"[Session Memory Summary]\n\n{content}"

    def update_from_messages(
        self,
        messages: list[dict[str, Any]],
        title: str | None = None,
        state_summary: str | None = None,
    ) -> None:
        """Update session memory from a message list (without LLM call).

        This is a lightweight update that extracts basic info from messages:
        - Title: first user message (truncated)
        - State: last assistant text response
        - Task: not updated here (requires LLM analysis)
        """
        if not self._path.exists():
            initial_title = title or "Untitled Session"
            # Try to extract from first user message
            for msg in messages:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        initial_title = content[:60].strip()
                        if len(content) > 60:
                            initial_title += "..."
                    break
            self.initialize(title=initial_title)

        if title:
            self.update_section(_SECTION_TITLE, title)

        if state_summary:
            self.update_section(_SECTION_STATE, state_summary)
        else:
            # Extract last assistant text as state summary
            last_assistant_text = ""
            for msg in reversed(messages):
                if msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        last_assistant_text = content[:500]
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                last_assistant_text = block.get("text", "")[:500]
                    if last_assistant_text:
                        break
            if last_assistant_text:
                self.update_section(_SECTION_STATE, last_assistant_text)
