"""feature_list.json — a model-hard-to-rewrite plan for multi-feature long runs.

Anthropic long-running-harness pattern (design §8.1-2): the agent works a fixed
list of features, each with its own acceptance criteria; it advances the
highest-priority unfinished one and may mark it ``done`` ONLY when verified —
never deleting items or tests. This module loads / queries / persists that list.

This first slice is the read+persist layer: the orchestrate loop injects the
board (so the agent sees the plan + which item is next), and ``mark_done`` is
available for the later control-flow slice (auto-select-and-advance).

Schema — ``.prax/feature_list.json``::

    {"features": [
       {"id": "f1", "title": "登录页", "acceptance": "pytest tests/test_login.py",
        "priority": 1, "status": "pending"}
    ]}

``status`` ∈ {"pending", "in_progress", "done"}; lower ``priority`` = higher
priority. All fields but ``id`` are optional. A missing / malformed file yields
an empty list (best-effort — never raises into the loop).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Feature:
    id: str
    title: str = ""
    acceptance: str = ""
    priority: int = 100
    status: str = "pending"
    # Optional curator complexity call (simple|moderate|complex) for the 三步走
    # router — change breadth is unknown before the feature runs, so the plan
    # declares it. Absent → the router defaults conservatively (never 自主循环).
    complexity: str = ""

    @property
    def done(self) -> bool:
        return self.status == "done"


class FeatureList:
    """Load / query / persist ``.prax/feature_list.json``."""

    def __init__(self, cwd: str) -> None:
        self._path = Path(cwd) / ".prax" / "feature_list.json"

    def load(self) -> list[Feature]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, dict):
            return []
        out: list[Feature] = []
        for item in raw.get("features", []):
            if not isinstance(item, dict) or "id" not in item:
                continue
            prio = item.get("priority", 100)
            out.append(
                Feature(
                    id=str(item["id"]),
                    title=str(item.get("title", "")),
                    acceptance=str(item.get("acceptance", "")),
                    priority=prio if isinstance(prio, int) else 100,
                    status=str(item.get("status", "pending")),
                    complexity=str(item.get("complexity", "")),
                )
            )
        return out

    def next_pending(self) -> Feature | None:
        """Highest-priority not-done feature (lowest ``priority`` number), or None."""
        pending = [f for f in self.load() if not f.done]
        if not pending:
            return None
        return sorted(pending, key=lambda f: f.priority)[0]

    def mark_done(self, feature_id: str) -> bool:
        """Persist ``status="done"`` for ``feature_id``. Returns True if it changed.

        Only flips status — never adds/removes items (the list is the agent's
        fixed charter). Best-effort: returns False on a missing/malformed file.
        """
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(raw, dict):
            return False
        changed = False
        for item in raw.get("features", []):
            if isinstance(item, dict) and str(item.get("id")) == feature_id and item.get("status") != "done":
                item["status"] = "done"
                changed = True
        if changed:
            self._path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        return changed

    def format_for_prompt(self) -> str:
        """Render the board for injection (empty string if no list)."""
        items = self.load()
        if not items:
            return ""
        nxt = self.next_pending()
        lines = ["（只在验收通过时才标 done；勿删功能项、勿删测试）"]
        for f in sorted(items, key=lambda f: (f.done, f.priority)):
            box = "[x]" if f.done else "[ ]"
            mark = "  ← 本轮推进" if nxt and f.id == nxt.id else ""
            acc = f"（验收：{f.acceptance}）" if f.acceptance and not f.done else ""
            lines.append(f"- {box} {f.id} {f.title}{acc}{mark}")
        return "\n".join(lines)
