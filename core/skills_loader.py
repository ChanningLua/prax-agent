"""Skills loader for .prax/skills/ directory.

Supports frontmatter-based markdown skills:
---
name: skill-name
description: What this skill does
allowed-tools: [Read, Write, Edit, Bash]
model: glm-5
---
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Skill:
    """A reusable skill loaded from .prax/skills/."""
    name: str          # Directory name or file name (without extension)
    description: str   # From frontmatter or first non-empty, non-heading line
    content: str       # Full content (for /skills show <name>)
    path: str          # Absolute path to SKILL.md (for LLM to read)
    model: str | None = None           # Preferred model from frontmatter
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)  # type: ignore
    triggers: tuple[str, ...] = field(default_factory=tuple)  # type: ignore  # 触发关键词
    tags: tuple[str, ...] = field(default_factory=tuple)  # type: ignore       # 分类标签
    priority: int = 0                                                            # 注入优先级


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content.

    Returns (metadata_dict, body_without_frontmatter).
    """
    if not content.startswith("---"):
        return {}, content

    end = content.find("\n---", 3)
    if end == -1:
        return {}, content

    try:
        import yaml
        frontmatter_text = content[3:end].strip()
        metadata = yaml.safe_load(frontmatter_text) or {}
        body = content[end + 4:].lstrip("\n")
        return metadata, body
    except Exception:
        return {}, content


def _extract_description(content: str) -> str:
    """Extract first non-empty, non-heading line from content."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def load_skills(cwd: str) -> list[Skill]:
    """Load all skills from .prax/skills/ directory.

    Supports two formats:
    - Subdirectory: .prax/skills/commit/SKILL.md
    - Single file: .prax/skills/deploy.md

    Also loads bundled package skills from prax/skills/ as defaults.
    Project-local skills override bundled skills with the same name.

    Returns empty list if no skills found.
    """
    bundled_skills: dict[str, Skill] = {}
    claude_skills: dict[str, Skill] = {}
    local_skills: dict[str, Skill] = {}

    # Load bundled package skills first (lower priority)
    pkg_skills_dir = Path(__file__).parent.parent / "skills"
    if pkg_skills_dir.exists():
        for skill in _scan_skills_dir(pkg_skills_dir):
            bundled_skills[skill.name] = skill

    # Load project-local Claude skills next (project workflow baseline)
    claude_skills_dir = Path(cwd) / ".claude" / "skills"
    if claude_skills_dir.exists():
        for skill in _scan_skills_dir(claude_skills_dir):
            claude_skills[skill.name] = skill

    # Load project-local skills (higher priority, override bundled)
    local_skills_dir = Path(cwd) / ".prax" / "skills"
    if local_skills_dir.exists():
        for skill in _scan_skills_dir(local_skills_dir):
            local_skills[skill.name] = skill

    # Merge priority: bundled < .claude < .prax
    merged = {**bundled_skills, **claude_skills, **local_skills}
    return sorted(merged.values(), key=lambda s: s.name)


def _scan_skills_dir(skills_dir: Path) -> list[Skill]:
    """Scan a skills directory for SKILL.md files."""
    skills = []
    seen_paths: set[Path] = set()

    for skill_file in sorted(skills_dir.rglob("SKILL.md")):
        if not skill_file.is_file():
            continue
        seen_paths.add(skill_file.resolve())
        skill = _load_skill_file(skill_file, skill_file.parent.name)
        if skill:
            skills.append(skill)

    for item in sorted(skills_dir.iterdir()):
        if item.is_file() and item.suffix.lower() == ".md" and item.name.lower() != "readme.md":
            if item.resolve() in seen_paths:
                continue
            skill = _load_skill_file(item, item.stem)
            if skill:
                skills.append(skill)
    return skills


def _load_skill_file(path: Path, default_name: str) -> Skill | None:
    """Load a single skill file, parsing frontmatter if present."""
    try:
        raw_content = path.read_text(encoding="utf-8")
    except Exception:
        return None

    metadata, body = _parse_frontmatter(raw_content)

    name = str(metadata.get("name", default_name))
    description = str(metadata.get("description", "") or _extract_description(body))
    model = metadata.get("model")
    allowed_tools_raw = metadata.get("allowed-tools", [])
    allowed_tools = tuple(str(t) for t in allowed_tools_raw) if isinstance(allowed_tools_raw, list) else ()
    triggers_raw = metadata.get("triggers", [])
    triggers = tuple(str(t) for t in triggers_raw) if isinstance(triggers_raw, list) else ()
    tags_raw = metadata.get("tags", [])
    tags = tuple(str(t) for t in tags_raw) if isinstance(tags_raw, list) else ()
    priority = int(metadata.get("priority", 0))

    return Skill(
        name=name,
        description=description,
        content=raw_content,
        path=str(path),
        model=str(model) if model else None,
        allowed_tools=allowed_tools,
        triggers=triggers,
        tags=tags,
        priority=priority,
    )


def format_skills_for_prompt(skills: list[Skill]) -> str:
    """Format skills as a summary table for system prompt injection.

    Returns only name + description (not full content) to avoid token bloat.
    LLM can use Read tool to get full content when needed.
    """
    if not skills:
        return ""

    lines = ["## Available Skills"]
    lines.append("Use the Read tool to read a skill's full content when needed.")
    lines.append("")

    for skill in skills:
        desc = f" - {skill.description}" if skill.description else ""
        model_hint = f" [model: {skill.model}]" if skill.model else ""
        lines.append(f"- **{skill.name}**{desc}{model_hint} (path: {skill.path})")

    return "\n".join(lines)


# ── Skill routing helpers ─────────────────────────────────────────────────────

# Keyword maps: task_type → skill name fragments that are likely relevant
_TASK_TYPE_SKILL_KEYWORDS: dict[str, list[str]] = {
    "debugging": ["debug", "fix", "error", "diagnose", "troubleshoot"],
    "deep_research": ["research", "survey", "analyze", "investigate", "benchmark"],
    "architecture": ["architect", "design", "refactor", "pattern", "structure"],
    "complex_reasoning": ["reason", "plan", "think", "explain", "analyze"],
    "quick_tasks": ["quick", "simple", "summary", "list", "brief"],
    "chinese_content": ["chinese", "cn", "zh"],
    "code_review": ["review", "audit", "quality", "check", "lint"],
    "translation": ["translate", "i18n", "l10n", "locale"],
    "git": ["git", "commit", "branch", "merge", "pr", "pull"],
    "testing": ["test", "spec", "coverage", "pytest", "jest", "unit"],
    "deploy": ["deploy", "ci", "cd", "docker", "kubernetes", "release"],
}


def _score_skill(skill: Skill, task_type: str) -> int:
    """Return a relevance score for a skill against a task type (higher = more relevant)."""
    score = 0
    name_lower = skill.name.lower()
    desc_lower = skill.description.lower()
    task_lower = task_type.lower()

    # Trigger keyword match (highest priority — direct signal from skill author)
    for trigger in skill.triggers:
        if trigger.lower() in task_lower:
            score += 15

    # Direct task_type name match in skill name/description
    if task_type in name_lower or task_type.replace("_", "-") in name_lower:
        score += 10
    if task_type in desc_lower:
        score += 5

    # Keyword match
    keywords = _TASK_TYPE_SKILL_KEYWORDS.get(task_type, [])
    for kw in keywords:
        if kw in name_lower:
            score += 4
        if kw in desc_lower:
            score += 2

    # Priority boost (higher priority skills get a tie-breaker boost)
    if score > 0 and skill.priority > 0:
        score += skill.priority

    return score


class SkillIndex:
    """Wraps loaded skills and provides search/lookup."""

    def __init__(self, skills: list[Skill]):
        self._skills = skills
        self._by_name: dict[str, Skill] = {s.name: s for s in skills}

    def search(self, query: str, max_results: int = 5) -> list[Skill]:
        """Score-based search using keyword logic."""
        scored = [(skill, _score_skill(skill, query)) for skill in self._skills]
        relevant = [(s, sc) for s, sc in scored if sc > 0]
        relevant.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in relevant[:max_results]]

    def get(self, name: str) -> Skill | None:
        return self._by_name.get(name)

    def list_names(self) -> list[str]:
        return list(self._by_name.keys())

    @classmethod
    def from_cwd(cls, cwd: str) -> "SkillIndex":
        return cls(load_skills(cwd))


def filter_skills_by_task_type(
    skills: list[Skill],
    task_type: str,
    max_skills: int = 5,
) -> list[Skill]:
    """Return the top-N skills most relevant to the given task type.

    Backward-compatible alias — delegates to SkillIndex.search.
    """
    return SkillIndex(skills).search(task_type, max_results=max_skills)


def format_skills_for_subagent(skills: list[Skill], max_chars_per_skill: int = 2000) -> str:
    """Format skills with full content for subagent prompt injection.

    Unlike ``format_skills_for_prompt`` (which gives only a summary table),
    this function embeds the full skill content so subagents can use it
    without needing a Read tool call.

    Args:
        skills: Skills to format.
        max_chars_per_skill: Maximum characters to include per skill content.

    Returns:
        Formatted string ready to prepend to a subagent prompt, or empty
        string if no skills provided.
    """
    if not skills:
        return ""

    lines = ["## Injected Skills\n"]
    for skill in skills:
        lines.append(f"### Skill: {skill.name}")
        if skill.description:
            lines.append(f"_{skill.description}_\n")
        content = skill.content
        if len(content) > max_chars_per_skill:
            content = content[:max_chars_per_skill] + "\n... (truncated)"
        lines.append(content)
        lines.append("")

    return "\n".join(lines)
