"""Context manager — builds system prompt with memory backend integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .memory_store import MemoryStore
from .skills_loader import SkillIndex, load_skills, format_skills_for_prompt, filter_skills_by_task_type
from .memory.layers import LayeredInjector

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .memory.backend import MemoryBackend
    from .openviking import OpenVikingClient
    from .trace import TraceContext


INTENT_GATE_PROMPT = """
## Step 0: Verbalize Intent (执行任何操作前必须完成)

在分类任务之前，先识别用户的真实意图，并口头宣告路由决策：

| 表层形式 | 真实意图 | 行动 |
|---------|---------|------|
| "解释X"、"X怎么工作" | 研究/理解 | 只读探索 → 回答 |
| "实现X"、"添加Y"、"创建Z" | 明确实现 | 规划 → 执行 |
| "修复"、"debug"、"为什么" | 问题诊断 | 调查 → 修复 |
| "重构"、"优化"、"清理" | 代码改进 | 审查 → 逐步修改 |

**必须口述**（在任何工具调用前）：
> "我检测到 [研究/实现/诊断/重构] 意图 — [原因]。我的方案：[具体步骤]。"

违反此规则视为执行失败。
"""

# Static portion of the system prompt — identity, instructions, tool guide.
# This text does NOT change between calls → mark with cache_control for caching.
_DEFAULT_EPISODIC_DAYS = 3

SYSTEM_PROMPT_TEMPLATE = """\
You are an AI coding assistant operating through the Prax orchestration engine.
Prax is a lightweight orchestration layer on top of Claude Code.
Claude Code provides all file/bash/git tools — use them directly.

## Orchestration Tools
- Use TodoWrite for non-trivial tasks to maintain a clear task list with statuses.
- Use Task to delegate complex or verbose subtasks into isolated context.
- Use VerifyCommand for repository-local validation commands such as `pytest -q`, `python -m pytest -q`, `npm test`, `pnpm test`, `cargo test`, and `go test`.
- Prefer VerifyCommand over SandboxBash when you are checking whether a fix worked.

## Working Directory
{cwd}

{project_context}

## Instructions
- Be concise and focused.
- Only make changes that are directly requested.
- Do not add unnecessary comments, docstrings, or error handling.
- Prioritize correctness and simplicity.
"""


class Context:
    """Prepares execution context for an agent loop invocation.

    Accepts an optional MemoryBackend for async prompt building,
    or falls back to local file loading for the sync path.
    Keeps backward compat with the legacy openviking parameter.
    """

    def __init__(
        self,
        cwd: str | None = None,
        model: str = "glm-4-flash",
        thinking_enabled: bool = False,
        reasoning_effort: str | None = None,
        openviking: "OpenVikingClient | None" = None,
        memory_backend: "MemoryBackend | None" = None,
        agent_system_prompt: str | None = None,
        agent_name: str | None = None,
        trace_ctx: "TraceContext | None" = None,
        intent_gate: bool = False,
        task_type: str = "general",
    ):
        self.cwd = cwd or str(Path.cwd())
        self.model = model
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort
        self._openviking = openviking          # legacy, kept for compat
        self._memory_backend = memory_backend  # preferred
        self.agent_system_prompt = agent_system_prompt
        self.agent_name = agent_name
        self.trace_ctx = trace_ctx
        self.intent_gate = intent_gate
        self.task_type = task_type
        self._skill_index: SkillIndex | None = None
        self._layered_injector: LayeredInjector | None = None

        # Auto-load AgentSpec if agent_name is set
        if agent_name:
            from .config_files import load_agent_spec
            spec = load_agent_spec(agent_name, self.cwd)
            if spec is not None:
                if spec.model:
                    self.model = spec.model
                if spec.system_prompt and not agent_system_prompt:
                    self.agent_system_prompt = spec.system_prompt

    def build_system_prompt(self, task_type: str = "general") -> str:
        """Build system prompt with project context (sync, local sources only)."""
        project_context = self._load_local_context(task_type=task_type)
        base_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            cwd=self.cwd,
            project_context=project_context,
        ).strip()
        prompt = f"{base_prompt}\n\n{INTENT_GATE_PROMPT}".strip() if self.intent_gate else base_prompt
        if self.agent_system_prompt:
            prompt = f"{prompt}\n\n## Agent Role\n\n{self.agent_system_prompt}"
        return prompt

    def _load_local_context(self, task_type: str = "general") -> str:
        """Load project context from local files with task-type filtering.

        Layer 1 (Rules): .claude/rules/*.md and .prax/rules/*.md
          — For task_type="general", all rule files are injected.
          — For specific task types, files are filtered by name relevance.
        Layer 2 (Memory): .prax/memory.json — always injected.
        Layer 4 (Skills): Only skills relevant to task_type are listed.
        """
        parts: list[str] = []

        # Layer 2: Persistent memory — use LayeredInjector when KG is available
        memory_store = MemoryStore(self.cwd)
        if self._layered_injector is None:
            kg = None
            if self._memory_backend is not None:
                try:
                    kg = self._memory_backend.get_knowledge_graph(self.cwd)
                except Exception:
                    pass
            self._layered_injector = LayeredInjector(kg=kg, memory_store=memory_store)

        memory_content = self._layered_injector.build_sync(self.cwd)
        if memory_content:
            parts.append(memory_content)

        episodic_content = self._load_recent_episodic_memory()
        if episodic_content:
            parts.append(episodic_content)

        # Project CLAUDE.md files — always inject
        project_context_candidates = [
            ("CLAUDE.md", Path(self.cwd) / "CLAUDE.md"),
            (".claude/CLAUDE.md", Path(self.cwd) / ".claude" / "CLAUDE.md"),
        ]
        for label, context_file in project_context_candidates:
            if not context_file.exists():
                continue
            try:
                content = context_file.read_text(encoding="utf-8")
                parts.append(f"## Project Context (from {label})\n{content}")
            except Exception:
                pass

        # .prax/context.yaml — always inject
        prax_ctx = Path(self.cwd) / ".prax" / "context.yaml"
        if prax_ctx.exists():
            try:
                content = prax_ctx.read_text(encoding="utf-8")
                parts.append(f"## Project Context (from .prax/context.yaml)\n{content}")
            except Exception:
                pass

        # Layer 1: Rules — task-type filtered
        # "general" injects all rules; specific types filter by filename relevance.
        rules_filter = _build_rules_filter(task_type)

        # .claude/rules/*.md（对齐基线 Claude CLI）
        rules_dir = Path(self.cwd) / ".claude" / "rules"
        if rules_dir.exists():
            for rule_file in sorted(rules_dir.glob("*.md")):
                if not rules_filter(rule_file.stem):
                    logger.debug("Skipping rule %s for task_type=%s", rule_file.name, task_type)
                    continue
                try:
                    content = rule_file.read_text(encoding="utf-8")
                    parts.append(f"## Project Rule [{rule_file.stem}]\n{content}")
                except Exception as e:
                    logger.warning("Failed to load rule file %s: %s", rule_file, e)

        # .prax/rules/*.md（prax 私有规则，优先级更高）
        prax_rules_dir = Path(self.cwd) / ".prax" / "rules"
        if prax_rules_dir.exists():
            for rule_file in sorted(prax_rules_dir.glob("*.md")):
                if not rules_filter(rule_file.stem):
                    logger.debug("Skipping prax rule %s for task_type=%s", rule_file.name, task_type)
                    continue
                try:
                    content = rule_file.read_text(encoding="utf-8")
                    parts.append(f"## Prax Rule [{rule_file.stem}]\n{content}")
                except Exception as e:
                    logger.warning("Failed to load prax rule file %s: %s", rule_file, e)

        # Layer 4: Skills — task-type filtered (relevant skills only)
        all_skills = load_skills(self.cwd)
        self._skill_index = SkillIndex(all_skills)
        if task_type == "general":
            relevant_skills = all_skills
        else:
            relevant_skills = filter_skills_by_task_type(all_skills, task_type, max_skills=5)
            # Fall back to all skills when no relevant ones found
            if not relevant_skills:
                relevant_skills = all_skills
        skills_content = format_skills_for_prompt(relevant_skills)
        if skills_content:
            parts.append(skills_content)

        return "\n\n".join(parts)

    def _load_recent_episodic_memory(self, days: int = _DEFAULT_EPISODIC_DAYS) -> str:
        """Load recent episodic facts from .prax/sessions/*-facts.json."""
        sessions_dir = Path(self.cwd) / ".prax" / "sessions"
        if not sessions_dir.exists():
            return ""

        episodic_files = sorted(
            sessions_dir.glob("*-facts.json"),
            key=lambda path: path.stem,
            reverse=True,
        )[:days]
        if not episodic_files:
            return ""

        facts: list[str] = []
        for episodic_file in episodic_files:
            try:
                import json

                data = json.loads(episodic_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            date = str(data.get("date", episodic_file.stem.replace("-facts", ""))).strip()
            for fact in data.get("facts", []):
                if not isinstance(fact, dict):
                    continue
                content = str(fact.get("content", "")).strip()
                if not content:
                    continue
                category = str(fact.get("category", "context")).strip() or "context"
                facts.append(f"- [{date}][{category}] {content}")

        if not facts:
            return ""

        return "## Recent Episodic Memory\n" + "\n".join(facts)


# ── Rules filtering ───────────────────────────────────────────────────────────

# Maps task_type keywords to rule file name fragments that are relevant.
# A rule file matches if its stem contains any of the listed fragments,
# OR if the task_type is "general" (inject all).
_RULES_TASK_KEYWORDS: dict[str, list[str]] = {
    "git": ["git", "commit", "coding", "quality"],
    "testing": ["test", "quality", "coding"],
    "code_review": ["coding", "quality", "security"],
    "debugging": ["coding", "quality", "security"],
    "architecture": ["coding", "quality", "security", "platform"],
    "deploy": ["deploy", "security", "quality"],
    "security": ["security", "coding"],
    "multi_platform": ["platform", "multi", "coding"],
}


def _build_rules_filter(task_type: str) -> Callable[[str], bool]:
    """Return a predicate that decides whether a rule file stem should be injected.

    For task_type="general" (or unknown types), always returns True.
    For known task types, returns True if the file stem contains any
    of the relevant keywords for that type, or matches "general" rules.
    """
    if task_type == "general":
        return lambda _stem: True

    keywords = _RULES_TASK_KEYWORDS.get(task_type)
    if not keywords:
        # Unknown task type — inject all rules (safe default)
        return lambda _stem: True

    def _filter(stem: str) -> bool:
        stem_lower = stem.lower().replace("-", "_").replace(" ", "_")
        return any(kw in stem_lower for kw in keywords)

    return _filter
