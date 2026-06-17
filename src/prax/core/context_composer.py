"""Context composer — builds each step's instruction with goal-drift defenses.

Borrowed from the long-running-agent research:
- Inject a persistent **north star** + **constraints** so the original goal and
  hard limits survive context rollover / compaction.
- **Re-inject prohibition constraints on a cadence.** "NEVER do X" (omission)
  constraints decay fastest over a long run — empirically from ~73% compliance
  down to ~20% (arXiv:2604.20911) — because a one-time mention erodes as context
  grows. So we restate ``constraints.md`` every ``reinject_every`` iterations
  (and always on iteration 0), keeping the policy near the top of attention.
- **ASSUME INTERRUPTION** header (Anthropic memory tool): tell the agent its
  context may reset and that only on-disk state counts.

All files live under ``.prax/`` and are optional, read fresh each step:
  ``north_star.md``  — immutable charter (always injected)
  ``constraints.md`` — prohibitions, re-injected on the cadence
  ``progress.md``    — rolling progress notes (injected when present)

Instances are callable with the OrchestratorLoop ``Composer`` signature
``(goal, feedback, iteration) -> str``.
"""
from __future__ import annotations

from pathlib import Path

_ASSUME_INTERRUPTION = (
    "[运行须知] 你的上下文可能随时被重置；进度只有写进 .prax/ 文件才算数。"
    "动手前先读下面的状态，不要假设你记得之前发生过什么。"
)


class ContextComposer:
    """Compose a step instruction from durable ``.prax/`` context files."""

    def __init__(self, cwd: str, *, reinject_every: int = 3) -> None:
        self._dir = Path(cwd) / ".prax"
        self._reinject_every = reinject_every

    def __call__(self, goal: str, feedback: str | None, iteration: int) -> str:
        parts: list[str] = [_ASSUME_INTERRUPTION]

        north = self._read("north_star.md")
        if north:
            parts.append(f"# 北极星（不可偏离）\n{north}")

        constraints = self._read("constraints.md")
        if constraints and self._should_reinject(iteration):
            parts.append(f"# 硬约束（务必遵守）\n{constraints}")

        progress = self._read("progress.md")
        if progress:
            parts.append(f"# 当前进度\n{progress}")

        parts.append(f"# 本轮目标\n{goal}")

        if feedback:
            parts.append(f"# 上一轮验证未通过，请据此修复\n{feedback}")

        return "\n\n".join(parts)

    # -- helpers --------------------------------------------------------------

    def _read(self, name: str) -> str | None:
        path = self._dir / name
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8").strip()
        return text or None

    def _should_reinject(self, iteration: int) -> bool:
        # always on the first iteration; then every k iterations (omission decay)
        if self._reinject_every <= 0:
            return iteration == 0
        return iteration % self._reinject_every == 0
