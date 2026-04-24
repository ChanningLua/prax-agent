"""Multi-model router — DeerFlow-style adapter pattern for model selection.

Routes tasks to the most appropriate model based on task type:
- Claude Opus 4.6: complex reasoning, architecture, code
- GPT-4.1: deep research, debugging
- GLM-5: Chinese content, quick tasks (cost-efficient)

Supports fallback chain: Claude → GPT → GLM
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# mtime-based cache: config_path → (mtime, ModelRouter)
_router_cache: dict[str, tuple[float, "ModelRouter"]] = {}
_cache_lock = threading.Lock()

# Default routing rules (can be overridden by config/models.yaml)
DEFAULT_ROUTING_RULES: dict[str, str] = {
    "complex_reasoning": "claude-opus-4-7",
    "architecture": "claude-opus-4-7",
    "code_review": "claude-opus-4-7",
    "deep_research": "gpt-5.4",
    "debugging": "gpt-5.4",
    "chinese_content": "glm-4-flash",
    "quick_tasks": "glm-4-flash",
    "translation": "glm-4-flash",
    "default": "claude-opus-4-7",
}

# Keyword patterns for task type detection
TASK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "chinese_content": ["中文", "中国", "国内", "汉字", "普通话", "简体", "繁体"],
    "deep_research": ["research", "investigate", "analyze", "survey", "compare", "benchmark"],
    "debugging": ["debug", "fix bug", "error", "exception", "traceback", "why is", "not working"],
    "architecture": ["architect", "design", "structure", "pattern", "refactor", "reorganize"],
    "complex_reasoning": ["reason", "explain", "understand", "how does", "why does", "complex"],
    "quick_tasks": ["quick", "simple", "brief", "short", "summarize", "list"],
}

# Default fallback chain
DEFAULT_FALLBACK_CHAIN = ["claude-opus-4-7", "gpt-5.4", "glm-4-flash"]


class ModelRouter:
    """Routes tasks to appropriate models based on task type.

    Implements DeerFlow's adapter pattern:
    - Classify task type from content
    - Map task type to optimal model
    - Support fallback chain on failures
    """

    def __init__(self, config_path: str | None = None):
        self._routing_rules = dict(DEFAULT_ROUTING_RULES)
        self._fallback_chain = list(DEFAULT_FALLBACK_CHAIN)
        self._models_config: dict[str, Any] = {}

        if config_path:
            self._load_config(config_path)

    def _load_config(self, path: str) -> None:
        """Load routing configuration from YAML file."""
        p = Path(path)
        if not p.exists():
            return
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                routing = data.get("routing", {})
                if isinstance(routing, dict):
                    rules = routing.get("rules", {})
                    if isinstance(rules, dict):
                        self._routing_rules.update(rules)
                    chain = routing.get("fallback_chain", [])
                    if chain:
                        self._fallback_chain = chain
                self._models_config = data
        except Exception as e:
            logger.warning("Failed to load model router config from %s: %s", path, e)

    def classify_task(self, task: str) -> str:
        """Classify task into a task type based on content."""
        task_lower = task.lower()
        for task_type, keywords in TASK_TYPE_KEYWORDS.items():
            if any(kw in task_lower for kw in keywords):
                return task_type
        return "default"

    def route(self, task: str, context: dict[str, Any] | None = None) -> str:
        """Route task to the most appropriate model.

        Args:
            task: The task description
            context: Optional context dict with hints (e.g., {"force_model": "glm-4-flash"})

        Returns:
            Model name string
        """
        ctx = context or {}

        # Explicit override
        if "force_model" in ctx:
            return ctx["force_model"]

        task_type = self.classify_task(task)
        model = self._routing_rules.get(task_type) or self._routing_rules.get("default", "claude-opus-4-7")

        logger.debug("ModelRouter: task_type=%s → model=%s", task_type, model)
        return model

    def get_fallback_chain(self, starting_model: str | None = None) -> list[str]:
        """Get the fallback chain starting from the given model.

        If starting_model is not in the chain, returns the full chain.
        """
        if starting_model is None:
            return list(self._fallback_chain)

        try:
            idx = self._fallback_chain.index(starting_model)
            return self._fallback_chain[idx:]
        except ValueError:
            return [starting_model] + self._fallback_chain

    def is_chinese_task(self, task: str) -> bool:
        """Quick check if task is primarily Chinese content."""
        return self.classify_task(task) == "chinese_content"

    @classmethod
    def from_cwd(cls, cwd: str) -> "ModelRouter":
        """Create router from project config or global config.

        Results are mtime-cached so repeated calls with the same config file
        are cheap.  The cache is invalidated when the file's mtime changes.
        """
        # Try project-local config first
        local_config = Path(cwd) / ".prax" / "models.yaml"
        if local_config.exists():
            return cls._from_config_cached(str(local_config))

        # Try package config
        pkg_config = Path(__file__).parent.parent.parent / "config" / "models.yaml"
        if pkg_config.exists():
            return cls._from_config_cached(str(pkg_config))

        return cls()

    @classmethod
    def _from_config_cached(cls, config_path: str) -> "ModelRouter":
        """Return a cached ModelRouter for *config_path*, refreshing on mtime change."""
        try:
            current_mtime = Path(config_path).stat().st_mtime
        except OSError:
            return cls(config_path)

        with _cache_lock:
            cached = _router_cache.get(config_path)
            if cached is not None:
                cached_mtime, router = cached
                if cached_mtime == current_mtime:
                    return router
            # Cache miss or stale — build fresh
            router = cls(config_path)
            _router_cache[config_path] = (current_mtime, router)
            return router
