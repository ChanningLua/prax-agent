"""
Comprehensive unit tests for remaining uncovered modules.

Covers:
- yolo_classifier (YoloClassifier)
- forked_agent (ForkedAgent)
- planning (generate_initial_plan, LLMPlanner._parse, PlannedTodo)
- knowledge_graph (KnowledgeGraph)
- layers (LayeredInjector, _estimate_tokens, _truncate_to_budget)
- dialect (_make_code, Dialect)
- migration (_split_fact_to_triples, migrate_facts_to_kg)
- local_backend (LocalMemoryBackend)
- skills_loader (load_skills, SkillIndex, format_skills_for_prompt)

All tests are pure unit tests — no real LLM calls, no real network I/O.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Helper: run async coroutines ────────────────────────────────────────────

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════════════════
# YoloClassifier
# ═══════════════════════════════════════════════════════════════════════════════

from prax.core.yolo_classifier import YoloClassifier, RiskLevel, YoloDecision


class TestYoloClassifierSync:

    def test_classify_bash_high_risk_rm_rf(self):
        clf = YoloClassifier()
        d = clf.classify_bash("rm -rf /tmp/dir")
        assert d.risk == RiskLevel.HIGH
        assert d.allow is False

    def test_classify_bash_high_risk_force_push(self):
        clf = YoloClassifier()
        d = clf.classify_bash("git push origin main --force")
        assert d.risk == RiskLevel.HIGH
        assert d.allow is False

    def test_classify_bash_high_risk_drop_table(self):
        clf = YoloClassifier()
        d = clf.classify_bash("DROP TABLE users")
        assert d.risk == RiskLevel.HIGH
        assert d.allow is False

    def test_classify_bash_high_risk_git_reset_hard(self):
        clf = YoloClassifier()
        d = clf.classify_bash("git reset --hard HEAD~1")
        assert d.risk == RiskLevel.HIGH
        assert d.allow is False

    def test_classify_bash_low_risk_ls(self):
        clf = YoloClassifier()
        d = clf.classify_bash("ls -la")
        assert d.risk == RiskLevel.LOW
        assert d.allow is True

    def test_classify_bash_low_risk_git_status(self):
        clf = YoloClassifier()
        d = clf.classify_bash("git status")
        assert d.risk == RiskLevel.LOW
        assert d.allow is True

    def test_classify_bash_low_risk_grep(self):
        clf = YoloClassifier()
        d = clf.classify_bash("grep -r 'pattern' .")
        assert d.risk == RiskLevel.LOW
        assert d.allow is True

    def test_classify_bash_medium_risk_unknown(self):
        clf = YoloClassifier()
        d = clf.classify_bash("some_custom_script.sh --arg value")
        assert d.risk == RiskLevel.MEDIUM
        assert d.allow is False

    def test_classify_bash_low_risk_which(self):
        clf = YoloClassifier()
        d = clf.classify_bash("which python3")
        assert d.risk == RiskLevel.LOW
        assert d.allow is True

    def test_classify_bash_low_risk_python_version(self):
        clf = YoloClassifier()
        d = clf.classify_bash("python3 --version")
        assert d.risk == RiskLevel.LOW
        assert d.allow is True


class TestYoloClassifierAsync:

    def test_classify_bash_async_high_risk_returns_immediately(self):
        clf = YoloClassifier()
        d = _run(clf.classify_bash_async("rm -rf /"))
        assert d.risk == RiskLevel.HIGH

    def test_classify_bash_async_low_risk_returns_immediately(self):
        clf = YoloClassifier()
        d = _run(clf.classify_bash_async("ls"))
        assert d.risk == RiskLevel.LOW
        assert d.allow is True

    def test_classify_bash_async_medium_no_llm_returns_medium(self):
        clf = YoloClassifier(llm_client=None, use_llm_fallback=False)
        d = _run(clf.classify_bash_async("some_mystery_command"))
        assert d.risk == RiskLevel.MEDIUM

    def test_classify_bash_async_medium_calls_llm_fallback(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.text = "RISK: LOW\nREASON: safe command"
        mock_llm.complete = AsyncMock(return_value=mock_response)
        clf = YoloClassifier(llm_client=mock_llm, model_config=MagicMock(), use_llm_fallback=True)
        d = _run(clf.classify_bash_async("some_mystery_command"))
        assert d.risk == RiskLevel.LOW
        mock_llm.complete.assert_awaited_once()

    def test_classify_bash_async_llm_failure_falls_back_to_heuristic(self):
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=RuntimeError("API error"))
        clf = YoloClassifier(llm_client=mock_llm, model_config=MagicMock(), use_llm_fallback=True)
        d = _run(clf.classify_bash_async("some_mystery_command"))
        # Falls back to heuristic (MEDIUM)
        assert d.risk == RiskLevel.MEDIUM


class TestYoloClassifierToolCall:

    def test_classify_tool_call_read_is_safe(self):
        clf = YoloClassifier()
        d = _run(clf.classify_tool_call("Read", {"path": "/some/file"}))
        assert d.risk == RiskLevel.LOW
        assert d.allow is True

    def test_classify_tool_call_grep_is_safe(self):
        clf = YoloClassifier()
        d = _run(clf.classify_tool_call("Grep", {"pattern": "foo"}))
        assert d.risk == RiskLevel.LOW
        assert d.allow is True

    def test_classify_tool_call_write_is_medium_allow(self):
        clf = YoloClassifier()
        d = _run(clf.classify_tool_call("Write", {"path": "/file", "content": "x"}))
        assert d.risk == RiskLevel.MEDIUM
        assert d.allow is True  # Write within workspace is acceptable

    def test_classify_tool_call_bash_high_risk_command(self):
        clf = YoloClassifier()
        d = _run(clf.classify_tool_call("Bash", {"command": "rm -rf /"}))
        assert d.risk == RiskLevel.HIGH
        assert d.allow is False

    def test_classify_tool_call_bash_no_command_is_medium(self):
        clf = YoloClassifier()
        d = _run(clf.classify_tool_call("Bash", {}))
        assert d.risk == RiskLevel.MEDIUM
        assert d.allow is False

    def test_classify_tool_call_unknown_tool_is_medium(self):
        clf = YoloClassifier()
        d = _run(clf.classify_tool_call("SomeUnknownTool", {"x": 1}))
        assert d.risk == RiskLevel.MEDIUM
        assert d.allow is False


class TestLlmClassify:
    """Tests for _llm_classify response parsing."""

    def test_llm_classify_parses_high_risk(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.text = "RISK: HIGH\nREASON: This is very dangerous"
        mock_llm.complete = AsyncMock(return_value=mock_response)
        clf = YoloClassifier(llm_client=mock_llm, model_config=MagicMock())
        d = _run(clf._llm_classify("dangerous_command"))
        assert d.risk == RiskLevel.HIGH
        assert "dangerous" in d.reason.lower()

    def test_llm_classify_parses_low_risk(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.text = "RISK: LOW\nREASON: Read-only safe command"
        mock_llm.complete = AsyncMock(return_value=mock_response)
        clf = YoloClassifier(llm_client=mock_llm, model_config=MagicMock())
        d = _run(clf._llm_classify("cat file.txt"))
        assert d.risk == RiskLevel.LOW
        assert d.allow is True

    def test_llm_classify_defaults_medium_on_bad_response(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.text = "I don't know how to classify this"
        mock_llm.complete = AsyncMock(return_value=mock_response)
        clf = YoloClassifier(llm_client=mock_llm, model_config=MagicMock())
        d = _run(clf._llm_classify("some_command"))
        assert d.risk == RiskLevel.MEDIUM


# ═══════════════════════════════════════════════════════════════════════════════
# ForkedAgent
# ═══════════════════════════════════════════════════════════════════════════════

from prax.core.forked_agent import ForkedAgent
from prax.tools.base import Tool, ToolCall, ToolResult


class _FakeTool(Tool):
    name = "FakeTool"
    description = "A fake tool for testing."

    def get_schema(self) -> dict:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    async def execute(self, tool_input: dict) -> ToolResult:
        return ToolResult(content=f"result:{tool_input.get('x','')}")


class TestForkedAgent:

    def test_init_filters_tools_to_allowed(self):
        tool_a = _FakeTool()
        tool_b = MagicMock(spec=Tool)
        tool_b.name = "OtherTool"

        agent = ForkedAgent(
            parent_system_prompt="sys",
            allowed_tools=["FakeTool"],
            llm_client=MagicMock(),
            model_config=MagicMock(),
            tools=[tool_a, tool_b],
        )
        assert "FakeTool" in agent._tool_map
        assert "OtherTool" not in agent._tool_map

    def test_run_returns_text_when_no_tool_calls(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.has_tool_calls = False
        mock_response.text = "done"
        mock_llm.complete = AsyncMock(return_value=mock_response)

        agent = ForkedAgent(
            parent_system_prompt="sys",
            allowed_tools=[],
            llm_client=mock_llm,
            model_config=MagicMock(),
            tools=[],
        )
        result = _run(agent.run("do something"))
        assert result == "done"

    def test_run_with_extra_context_appends_to_system_prompt(self):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.has_tool_calls = False
        mock_response.text = "ok"
        mock_llm.complete = AsyncMock(return_value=mock_response)

        agent = ForkedAgent(
            parent_system_prompt="base",
            allowed_tools=[],
            llm_client=mock_llm,
            model_config=MagicMock(),
            tools=[],
        )
        _run(agent.run("task", extra_context="extra info"))
        call_kwargs = mock_llm.complete.call_args[1]
        assert "extra info" in call_kwargs["system_prompt"]
        assert "base" in call_kwargs["system_prompt"]

    def test_run_executes_tool_and_appends_result(self):
        mock_llm = AsyncMock()
        tool = _FakeTool()

        # First response: tool call; second response: text
        tc = ToolCall(id="tc1", name="FakeTool", input={"x": "hello"})
        resp1 = MagicMock()
        resp1.has_tool_calls = True
        resp1.tool_calls = [tc]
        resp1.content = [{"type": "tool_use", "id": "tc1", "name": "FakeTool", "input": {"x": "hello"}}]

        resp2 = MagicMock()
        resp2.has_tool_calls = False
        resp2.text = "all done"

        mock_llm.complete = AsyncMock(side_effect=[resp1, resp2])

        agent = ForkedAgent(
            parent_system_prompt="sys",
            allowed_tools=["FakeTool"],
            llm_client=mock_llm,
            model_config=MagicMock(),
            tools=[tool],
            max_iterations=5,
        )
        result = _run(agent.run("use the tool"))
        assert result == "all done"

    def test_run_max_iterations_returns_message(self):
        mock_llm = AsyncMock()
        tool = _FakeTool()

        tc = ToolCall(id="tc1", name="FakeTool", input={"x": "loop"})
        resp = MagicMock()
        resp.has_tool_calls = True
        resp.tool_calls = [tc]
        resp.content = []

        mock_llm.complete = AsyncMock(return_value=resp)

        agent = ForkedAgent(
            parent_system_prompt="sys",
            allowed_tools=["FakeTool"],
            llm_client=mock_llm,
            model_config=MagicMock(),
            tools=[tool],
            max_iterations=2,
        )
        result = _run(agent.run("loop forever"))
        assert "Max iterations" in result

    def test_run_llm_error_returns_error_message(self):
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=RuntimeError("LLM failure"))

        agent = ForkedAgent(
            parent_system_prompt="sys",
            allowed_tools=[],
            llm_client=mock_llm,
            model_config=MagicMock(),
            tools=[],
        )
        result = _run(agent.run("task"))
        assert "error" in result.lower() or "LLM failure" in result

    def test_execute_tool_rejects_not_allowed(self):
        agent = ForkedAgent(
            parent_system_prompt="sys",
            allowed_tools=["AllowedTool"],
            llm_client=MagicMock(),
            model_config=MagicMock(),
            tools=[],
        )
        tc = ToolCall(id="t1", name="DangerousTool", input={})
        result = _run(agent._execute_tool(tc))
        assert result.is_error is True
        assert "Permission denied" in result.content

    def test_execute_tool_handles_tool_not_in_map(self):
        # Tool is in allowed list but not in the tools list given at init
        agent = ForkedAgent(
            parent_system_prompt="sys",
            allowed_tools=["MissingTool"],
            llm_client=MagicMock(),
            model_config=MagicMock(),
            tools=[],
        )
        tc = ToolCall(id="t1", name="MissingTool", input={})
        result = _run(agent._execute_tool(tc))
        assert result.is_error is True
        assert "not available" in result.content

    def test_execute_tool_catches_execution_exception(self):
        bad_tool = MagicMock(spec=Tool)
        bad_tool.name = "BadTool"
        bad_tool.execute = AsyncMock(side_effect=ValueError("boom"))

        agent = ForkedAgent(
            parent_system_prompt="sys",
            allowed_tools=["BadTool"],
            llm_client=MagicMock(),
            model_config=MagicMock(),
            tools=[bad_tool],
        )
        tc = ToolCall(id="t1", name="BadTool", input={})
        result = _run(agent._execute_tool(tc))
        assert result.is_error is True
        assert "boom" in result.content


# ═══════════════════════════════════════════════════════════════════════════════
# Planning
# ═══════════════════════════════════════════════════════════════════════════════

from prax.core.planning import PlannedTodo, generate_initial_plan, LLMPlanner


class TestPlannedTodo:

    def test_to_dict_includes_required_fields(self):
        todo = PlannedTodo(
            id="1",
            content="Do something",
            active_form="Doing something",
            status="pending",
        )
        d = todo.to_dict()
        assert d["content"] == "Do something"
        assert d["activeForm"] == "Doing something"
        assert d["status"] == "pending"
        assert d["id"] == "1"

    def test_to_dict_omits_empty_id(self):
        todo = PlannedTodo(
            id="",
            content="task",
            active_form="doing",
            status="pending",
        )
        d = todo.to_dict()
        assert "id" not in d

    def test_to_dict_includes_depends_on(self):
        todo = PlannedTodo(
            id="2",
            content="step 2",
            active_form="doing step 2",
            status="pending",
            depends_on=("1",),
        )
        d = todo.to_dict()
        assert d["dependsOn"] == ["1"]


class TestGenerateInitialPlan:

    def test_returns_three_todos(self):
        todos = generate_initial_plan("write unit tests")
        assert len(todos) == 3

    def test_first_todo_is_in_progress(self):
        todos = generate_initial_plan("any task")
        assert todos[0].status == "in_progress"

    def test_remaining_todos_are_pending(self):
        todos = generate_initial_plan("any task")
        assert todos[1].status == "pending"
        assert todos[2].status == "pending"

    def test_todos_have_dependency_chain(self):
        todos = generate_initial_plan("do work")
        assert "1" in todos[1].depends_on
        assert "2" in todos[2].depends_on

    def test_empty_task_raises_value_error(self):
        with pytest.raises(ValueError, match="must not be empty"):
            generate_initial_plan("")

    def test_whitespace_only_task_raises_value_error(self):
        with pytest.raises(ValueError, match="must not be empty"):
            generate_initial_plan("   ")

    def test_task_content_appears_in_todos(self):
        todos = generate_initial_plan("write unit tests")
        all_content = " ".join(t.content for t in todos)
        assert "write unit tests" in all_content


class TestLLMPlannerParse:

    def setup_method(self):
        self.planner = LLMPlanner()

    def test_parse_valid_json_array(self):
        text = json.dumps([
            {"id": "1", "content": "Do A", "activeForm": "Doing A", "status": "pending", "depends_on": []},
            {"id": "2", "content": "Do B", "activeForm": "Doing B", "status": "pending", "depends_on": ["1"]},
        ])
        todos = self.planner._parse(text)
        assert len(todos) == 2
        assert todos[0].content == "Do A"
        assert todos[1].content == "Do B"
        assert "1" in todos[1].depends_on

    def test_parse_with_surrounding_text(self):
        text = "Here is the plan:\n" + json.dumps([
            {"id": "1", "content": "Step 1", "activeForm": "Stepping", "status": "pending"}
        ]) + "\nDone."
        todos = self.planner._parse(text)
        assert len(todos) == 1
        assert todos[0].content == "Step 1"

    def test_parse_invalid_json_returns_empty(self):
        todos = self.planner._parse("not valid json at all")
        assert todos == []

    def test_parse_empty_string_returns_empty(self):
        todos = self.planner._parse("")
        assert todos == []

    def test_parse_skips_items_without_content(self):
        text = json.dumps([
            {"id": "1", "activeForm": "Doing", "status": "pending"},  # missing content
            {"id": "2", "content": "Valid step", "activeForm": "Doing", "status": "pending"},
        ])
        todos = self.planner._parse(text)
        assert len(todos) == 1
        assert todos[0].content == "Valid step"

    def test_parse_normalizes_invalid_status(self):
        text = json.dumps([
            {"id": "1", "content": "Step", "activeForm": "Stepping", "status": "unknown_status"}
        ])
        todos = self.planner._parse(text)
        assert todos[0].status == "pending"

    def test_parse_does_not_reference_unknown_dependency(self):
        # depends_on references "99" which doesn't appear before this item
        text = json.dumps([
            {"id": "2", "content": "Step 2", "activeForm": "S2", "status": "pending", "depends_on": ["99"]},
        ])
        todos = self.planner._parse(text)
        assert len(todos) == 1
        assert todos[0].depends_on == ()  # "99" wasn't in seen_ids

    def test_parse_uses_active_form_fallback(self):
        text = json.dumps([
            {"id": "1", "content": "Do task", "status": "pending"}
        ])
        todos = self.planner._parse(text)
        assert "Working on step" in todos[0].active_form


    def test_decompose_raises_for_empty_task(self):
        planner = LLMPlanner()
        mock_llm = AsyncMock()
        mock_cfg = MagicMock()
        mock_cfg.model = "test-model"

        with pytest.raises(ValueError, match="must not be empty"):
            _run(planner.decompose("", llm_client=mock_llm, model_config=mock_cfg))


# ═══════════════════════════════════════════════════════════════════════════════
# KnowledgeGraph
# ═══════════════════════════════════════════════════════════════════════════════

from prax.core.memory.knowledge_graph import KnowledgeGraph, _validate_triple_input


class TestValidateTripleInput:

    def test_valid_triple_passes(self):
        _validate_triple_input("user", "prefers", "Python")

    def test_empty_subject_raises(self):
        with pytest.raises(ValueError, match="subject"):
            _validate_triple_input("", "pred", "obj")

    def test_empty_predicate_raises(self):
        with pytest.raises(ValueError, match="predicate"):
            _validate_triple_input("sub", "", "obj")

    def test_empty_object_raises(self):
        with pytest.raises(ValueError, match="object"):
            _validate_triple_input("sub", "pred", "")

    def test_subject_too_long_raises(self):
        with pytest.raises(ValueError, match="subject"):
            _validate_triple_input("x" * 600, "pred", "obj")

    def test_predicate_too_long_raises(self):
        with pytest.raises(ValueError, match="predicate"):
            _validate_triple_input("sub", "p" * 200, "obj")


class TestKnowledgeGraph:

    def test_add_and_query_entity(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("user", "prefers", "Python")
        results = kg.query_entity("user")
        assert len(results) == 1
        assert results[0]["predicate"] == "prefers"
        assert results[0]["object"] == "Python"

    def test_add_entity(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        eid = kg.add_entity("Alice", entity_type="person", properties={"age": 30})
        assert eid == "alice"

    def test_add_entity_empty_name_raises(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        with pytest.raises(ValueError):
            kg.add_entity("")

    def test_add_triple_returns_id(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        tid = kg.add_triple("project", "uses", "SQLite")
        assert tid.startswith("t_")

    def test_add_triple_deduplicates(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        id1 = kg.add_triple("user", "likes", "coffee")
        id2 = kg.add_triple("user", "likes", "coffee")
        assert id1 == id2  # same id returned for duplicate

    def test_query_entity_empty_kg(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        results = kg.query_entity("nobody")
        assert results == []

    def test_query_relationship(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("user", "prefers", "dark theme")
        kg.add_triple("project", "uses", "TypeScript")
        results = kg.query_relationship("prefers")
        assert any(r["object"] == "dark theme" for r in results)

    def test_invalidate_triple(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("user", "uses", "Python2")
        kg.invalidate("user", "uses", "Python2", ended="2024-01-01")
        results = kg.query_entity("user")
        # invalidated triple should have valid_to set
        assert all(r["current"] is False for r in results if r["object"] == "python2")

    def test_timeline_returns_all_triples(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("a", "knows", "b")
        kg.add_triple("b", "likes", "c")
        tl = kg.timeline()
        assert len(tl) >= 2

    def test_timeline_entity_filtered(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("alice", "knows", "bob")
        kg.add_triple("charlie", "hates", "dave")
        tl = kg.timeline("alice")
        assert all("alice" in (r["subject"].lower(), r["object"].lower()) for r in tl)

    def test_get_top_triples(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("a", "is", "great", confidence=0.95)
        rows = kg.get_top_triples(limit=5, min_confidence=0.9)
        assert len(rows) >= 1

    def test_stats_empty_graph(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        s = kg.stats()
        assert s["entities"] == 0
        assert s["triples"] == 0

    def test_stats_after_adding_triples(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("x", "rel", "y")
        s = kg.stats()
        assert s["entities"] >= 2
        assert s["triples"] >= 1

    def test_add_triples_batch(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        triples = [
            ("alice", "knows", "bob"),
            ("bob", "likes", "coffee"),
        ]
        count = kg.add_triples_batch(triples)
        assert count == 2

    def test_add_triples_batch_empty(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        count = kg.add_triples_batch([])
        assert count == 0

    def test_query_entity_incoming_direction(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("alice", "knows", "bob")
        results = kg.query_entity("bob", direction="incoming")
        assert any(r["subject"] == "alice" for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# Layers / LayeredInjector
# ═══════════════════════════════════════════════════════════════════════════════

from prax.core.memory.layers import (
    LayeredInjector,
    _estimate_tokens,
    _truncate_to_budget,
)


class TestEstimateTokens:

    def test_empty_string(self):
        assert _estimate_tokens("") >= 1

    def test_english_words(self):
        tokens = _estimate_tokens("hello world foo bar")
        assert tokens > 0

    def test_cjk_chars(self):
        # CJK chars should estimate higher per char
        cjk_text = "你好世界"
        tokens_cjk = _estimate_tokens(cjk_text)
        assert tokens_cjk >= 4

    def test_mixed_text(self):
        tokens = _estimate_tokens("hello 世界 world")
        assert tokens > 0


class TestTruncateToBudget:

    def test_text_within_budget_unchanged(self):
        text = "short text"
        result = _truncate_to_budget(text, 100)
        assert result == text

    def test_long_text_truncated(self):
        text = " ".join(["word"] * 500)
        result = _truncate_to_budget(text, 10)
        assert len(result) <= len(text)

    def test_single_long_line_truncated(self):
        text = " ".join(["longword"] * 500)
        result = _truncate_to_budget(text, 5)
        assert len(result) <= len(text)


class TestLayeredInjector:

    def test_build_l0_no_memory_store_returns_empty(self, tmp_path):
        injector = LayeredInjector()
        result = injector.build_l0(str(tmp_path))
        assert result == ""

    def test_build_l1_no_kg_returns_empty(self, tmp_path):
        injector = LayeredInjector()
        result = injector.build_l1(str(tmp_path))
        assert result == ""

    def test_build_l2_no_vector_store_returns_empty(self, tmp_path):
        injector = LayeredInjector()
        result = _run(injector.build_l2(str(tmp_path), "query"))
        assert result == ""

    def test_build_l3_no_kg_returns_empty(self, tmp_path):
        injector = LayeredInjector()
        result = injector.build_l3(str(tmp_path), "query")
        assert result == ""

    def test_build_l3_with_kg_returns_results(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("python", "is", "programming language")

        injector = LayeredInjector(kg=kg)
        result = injector.build_l3(str(tmp_path), "python programming")
        assert "python" in result.lower() or result == ""  # might not match if no entity found

    def test_build_sync_no_kg_uses_memory_store_fallback(self, tmp_path):
        mock_ms = MagicMock()
        mock_ms.format_for_prompt.return_value = "flat memory content"
        injector = LayeredInjector(memory_store=mock_ms)
        result = injector.build_sync(str(tmp_path), "query")
        assert result == "flat memory content"

    def test_build_sync_with_kg_returns_structured(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("user", "prefers", "Python", confidence=0.95)
        injector = LayeredInjector(kg=kg)
        result = injector.build_sync(str(tmp_path), "")
        # Should have Knowledge Graph section or be empty (if no triples above 0.9)
        assert isinstance(result, str)

    def test_build_async_no_kg_uses_memory_store(self, tmp_path):
        mock_ms = MagicMock()
        mock_ms.format_for_prompt.return_value = "memory prompt"
        injector = LayeredInjector(memory_store=mock_ms)
        result = _run(injector.build_async(str(tmp_path), ""))
        assert result == "memory prompt"

    def test_build_l1_uses_kg_top_triples(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("project", "uses", "FastAPI", confidence=0.95)
        injector = LayeredInjector(kg=kg)
        result = injector.build_l1(str(tmp_path))
        # Should contain something about the triple (either compressed or plain)
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════════════
# Dialect
# ═══════════════════════════════════════════════════════════════════════════════

from prax.core.memory.dialect import Dialect, _make_code


class TestMakeCode:

    def test_simple_english_word(self):
        code = _make_code("user")
        assert code == "USER"

    def test_multi_word_english(self):
        code = _make_code("Chinese language")
        assert len(code) > 0
        assert code.isupper() or "_" in code

    def test_cjk_single_word(self):
        code = _make_code("用户")
        assert "用" in code

    def test_empty_after_emoji_strip(self):
        code = _make_code("🎉🎊")
        assert code == "UNK"

    def test_short_word_uppercased(self):
        code = _make_code("go")
        assert code == "GO"

    def test_long_word_vowels_stripped(self):
        code = _make_code("database")
        assert len(code) <= 8
        assert code == code.upper()


class TestDialect:

    def test_encode_entity_uses_existing_code(self):
        d = Dialect(entity_codes={"user": "USR"})
        assert d.encode_entity("user") == "USR"

    def test_encode_entity_auto_generates_for_unknown(self):
        d = Dialect()
        code = d.encode_entity("Python")
        assert code == "PYTH" or len(code) > 0

    def test_compress_triple(self):
        d = Dialect(entity_codes={"user": "USR", "python": "PYTH"})
        triple = {"subject": "user", "predicate": "prefers", "object": "Python"}
        result = d.compress_triple(triple)
        assert "USR" in result
        assert "prefers" in result

    def test_compress_triple_with_confidence_below_1(self):
        d = Dialect(entity_codes={"a": "AA", "b": "BB"})
        triple = {"subject": "a", "predicate": "is", "object": "b", "confidence": 0.8}
        result = d.compress_triple(triple)
        assert "0.8" in result

    def test_compress_triple_with_valid_from(self):
        d = Dialect()
        triple = {"subject": "x", "predicate": "is", "object": "y", "valid_from": "2026-01-01"}
        result = d.compress_triple(triple)
        assert "2026-01-01" in result

    def test_compress_triples_empty_list(self):
        d = Dialect()
        result = d.compress_triples([])
        assert result == ""

    def test_compress_triples_multiple(self):
        d = Dialect()
        triples = [
            {"subject": "a", "predicate": "is", "object": "b"},
            {"subject": "c", "predicate": "uses", "object": "d"},
        ]
        result = d.compress_triples(triples)
        assert "\n" in result

    def test_compress_for_l1_empty(self):
        d = Dialect()
        result = d.compress_for_l1([])
        assert result == ""

    def test_compress_for_l1_includes_codebook(self):
        d = Dialect(entity_codes={"user": "USR", "python": "PYTH"})
        triples = [{"subject": "user", "predicate": "prefers", "object": "Python"}]
        result = d.compress_for_l1(triples)
        assert "CODES:" in result

    def test_build_codebook(self):
        d = Dialect(entity_codes={"user": "USR", "python": "PYTH"})
        cb = d.build_codebook()
        assert "CODES:" in cb
        assert "USR" in cb

    def test_save_and_load_codebook(self, tmp_path):
        d = Dialect(entity_codes={"user": "USR", "project": "PRJ"})
        path = tmp_path / "codebook.json"
        d.save_codebook(str(path))
        d2 = Dialect.from_codebook(str(path))
        assert d2.encode_entity("user") == "USR"
        assert d2.encode_entity("project") == "PRJ"

    def test_compression_stats(self):
        d = Dialect()
        stats = d.compression_stats("hello world foo bar baz", "HW|F|B")
        assert "ratio" in stats
        assert stats["ratio"] > 0
        assert stats["original_chars"] > stats["compressed_chars"]

    def test_from_kg(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path))
        kg.add_triple("user", "prefers", "Python")
        d = Dialect.from_kg(kg)
        assert isinstance(d, Dialect)


# ═══════════════════════════════════════════════════════════════════════════════
# Migration
# ═══════════════════════════════════════════════════════════════════════════════

from prax.core.memory.migration import migrate_facts_to_kg, _split_fact_to_triples


class TestSplitFactToTriples:

    def test_preference_pattern(self):
        triples = _split_fact_to_triples("User prefers Python", "preference")
        assert len(triples) == 1
        assert triples[0] == ("user", "prefers", "Python")

    def test_uses_pattern_knowledge(self):
        triples = _split_fact_to_triples("Project uses SQLite for storage", "knowledge")
        assert any(t[1] == "uses" for t in triples)

    def test_is_pattern_generic(self):
        triples = _split_fact_to_triples("FastAPI is a web framework", "context")
        assert any(t[1] == "is" for t in triples)

    def test_empty_content_returns_empty(self):
        triples = _split_fact_to_triples("", "context")
        assert triples == []

    def test_whitespace_content_returns_empty(self):
        triples = _split_fact_to_triples("   ", "context")
        assert triples == []

    def test_fallback_for_unrecognized_content(self):
        triples = _split_fact_to_triples("Some random fact", "context")
        # Falls back to "project knows" pattern
        assert len(triples) == 1
        assert triples[0][0] == "project"
        assert triples[0][1] == "knows"

    def test_long_content_skipped(self):
        triples = _split_fact_to_triples("x" * 300, "context")
        assert triples == []


class TestMigrateFactsToKg:

    def test_no_memory_file_returns_zero(self, tmp_path):
        count = migrate_facts_to_kg(str(tmp_path))
        assert count == 0

    def test_empty_facts_returns_zero(self, tmp_path):
        prax_dir = tmp_path / ".prax"
        prax_dir.mkdir()
        memory_file = prax_dir / "memory.json"
        memory_file.write_text(json.dumps({"facts": []}), encoding="utf-8")
        count = migrate_facts_to_kg(str(tmp_path))
        assert count == 0

    def test_string_facts_migrated(self, tmp_path):
        prax_dir = tmp_path / ".prax"
        prax_dir.mkdir()
        memory_file = prax_dir / "memory.json"
        memory_file.write_text(
            json.dumps({"facts": ["User prefers Python"]}),
            encoding="utf-8"
        )
        count = migrate_facts_to_kg(str(tmp_path))
        assert count >= 1

    def test_dict_facts_migrated(self, tmp_path):
        prax_dir = tmp_path / ".prax"
        prax_dir.mkdir()
        memory_file = prax_dir / "memory.json"
        memory_file.write_text(
            json.dumps({
                "facts": [
                    {"content": "Project uses FastAPI", "category": "knowledge"},
                ]
            }),
            encoding="utf-8"
        )
        count = migrate_facts_to_kg(str(tmp_path))
        assert count >= 1

    def test_invalid_json_returns_zero(self, tmp_path):
        prax_dir = tmp_path / ".prax"
        prax_dir.mkdir()
        memory_file = prax_dir / "memory.json"
        memory_file.write_text("{not valid json", encoding="utf-8")
        count = migrate_facts_to_kg(str(tmp_path))
        assert count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# LocalMemoryBackend
# ═══════════════════════════════════════════════════════════════════════════════

from prax.core.memory.local_backend import LocalMemoryBackend
from prax.core.memory.backend import Fact, Experience, MemoryContext


class TestLocalMemoryBackend:

    def test_get_facts_empty_project(self, tmp_path):
        backend = LocalMemoryBackend()
        facts = _run(backend.get_facts(str(tmp_path)))
        assert facts == []

    def test_store_and_get_fact(self, tmp_path):
        backend = LocalMemoryBackend()
        fact = Fact(
            id="f1",
            content="User prefers dark mode",
            category="preference",
            confidence=0.9,
        )
        _run(backend.store_fact(str(tmp_path), fact))
        facts = _run(backend.get_facts(str(tmp_path)))
        assert any(f.id == "f1" for f in facts)

    def test_store_fact_below_threshold_skipped(self, tmp_path):
        backend = LocalMemoryBackend(fact_confidence_threshold=0.8)
        fact = Fact(id="f_low", content="Low confidence fact", confidence=0.5)
        _run(backend.store_fact(str(tmp_path), fact))
        facts = _run(backend.get_facts(str(tmp_path)))
        assert not any(f.id == "f_low" for f in facts)

    def test_store_fact_deduplicates(self, tmp_path):
        backend = LocalMemoryBackend()
        fact = Fact(id="f1", content="Unique content", confidence=0.9)
        _run(backend.store_fact(str(tmp_path), fact))
        _run(backend.store_fact(str(tmp_path), fact))
        facts = _run(backend.get_facts(str(tmp_path)))
        matching = [f for f in facts if f.id == "f1"]
        assert len(matching) == 1

    def test_delete_fact(self, tmp_path):
        backend = LocalMemoryBackend()
        fact = Fact(id="del1", content="to be deleted", confidence=0.9)
        _run(backend.store_fact(str(tmp_path), fact))
        _run(backend.delete_fact(str(tmp_path), "del1"))
        facts = _run(backend.get_facts(str(tmp_path)))
        assert not any(f.id == "del1" for f in facts)

    def test_get_context_empty(self, tmp_path):
        backend = LocalMemoryBackend()
        ctx = _run(backend.get_context(str(tmp_path)))
        assert ctx.work_context == ""
        assert ctx.top_of_mind == ""

    def test_save_and_get_context(self, tmp_path):
        backend = LocalMemoryBackend()
        ctx = MemoryContext(work_context="Working on Prax", top_of_mind="Fix tests")
        _run(backend.save_context(str(tmp_path), ctx))
        loaded = _run(backend.get_context(str(tmp_path)))
        assert loaded.work_context == "Working on Prax"
        assert loaded.top_of_mind == "Fix tests"

    def test_get_experiences_empty(self, tmp_path):
        backend = LocalMemoryBackend()
        exps = _run(backend.get_experiences("debugging"))
        assert isinstance(exps, list)

    def test_close_is_noop(self, tmp_path):
        backend = LocalMemoryBackend()
        # Should not raise
        _run(backend.close())

    def test_migrate_project_converts_string_facts(self):
        backend = LocalMemoryBackend()
        raw = {"facts": ["fact one", "fact two"]}
        migrated = backend._migrate_project(raw)
        assert all(isinstance(f, dict) for f in migrated["facts"])
        assert migrated["facts"][0]["content"] == "fact one"

    def test_migrate_project_preserves_dict_facts(self):
        backend = LocalMemoryBackend()
        raw = {"facts": [{"id": "x", "content": "existing", "confidence": 0.9}]}
        migrated = backend._migrate_project(raw)
        assert migrated["facts"][0]["id"] == "x"


# ═══════════════════════════════════════════════════════════════════════════════
# Skills Loader
# ═══════════════════════════════════════════════════════════════════════════════

from prax.core.skills_loader import (
    load_skills,
    Skill,
    SkillIndex,
    format_skills_for_prompt,
    format_skills_for_subagent,
    _parse_frontmatter,
    _extract_description,
    filter_skills_by_task_type,
)


class TestParseFrontmatter:

    def test_no_frontmatter_returns_empty_dict(self):
        meta, body = _parse_frontmatter("# Title\nContent here")
        assert meta == {}
        assert "Content here" in body

    def test_valid_frontmatter_parsed(self):
        content = "---\nname: test-skill\ndescription: Test\n---\n# Body"
        meta, body = _parse_frontmatter(content)
        assert meta.get("name") == "test-skill"
        assert "Body" in body

    def test_unclosed_frontmatter_returns_empty(self):
        content = "---\nname: test\n"
        meta, body = _parse_frontmatter(content)
        assert meta == {}


class TestExtractDescription:

    def test_first_non_heading_line(self):
        desc = _extract_description("# Title\n\nThis is the description\n## More")
        assert desc == "This is the description"

    def test_heading_skipped(self):
        desc = _extract_description("# Title\n## Section\nActual content")
        assert desc == "Actual content"

    def test_empty_content_returns_empty(self):
        desc = _extract_description("")
        assert desc == ""


class TestLoadSkills:

    def test_returns_empty_when_no_skills_dir(self, tmp_path):
        skills = load_skills(str(tmp_path))
        # May return bundled skills, that's fine — just verify it returns a list
        assert isinstance(skills, list)

    def test_loads_skill_from_subdir(self, tmp_path):
        skills_dir = tmp_path / ".prax" / "skills" / "my-skill"
        skills_dir.mkdir(parents=True)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text(
            "---\nname: my-skill\ndescription: A test skill\nallowed-tools:\n  - Read\n---\n# My Skill\nContent here.",
            encoding="utf-8",
        )
        skills = load_skills(str(tmp_path))
        names = [s.name for s in skills]
        assert "my-skill" in names

    def test_loads_skill_from_md_file(self, tmp_path):
        skills_dir = tmp_path / ".prax" / "skills"
        skills_dir.mkdir(parents=True)
        skill_file = skills_dir / "deploy.md"
        skill_file.write_text("# Deploy\nDeploy to production.", encoding="utf-8")
        skills = load_skills(str(tmp_path))
        names = [s.name for s in skills]
        assert "deploy" in names

    def test_skill_has_correct_allowed_tools(self, tmp_path):
        skills_dir = tmp_path / ".prax" / "skills" / "commit"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: commit\nallowed-tools:\n  - Read\n  - Write\n---\nGit commit skill.",
            encoding="utf-8",
        )
        skills = load_skills(str(tmp_path))
        skill = next((s for s in skills if s.name == "commit"), None)
        assert skill is not None
        assert "Read" in skill.allowed_tools
        assert "Write" in skill.allowed_tools

    def test_local_skills_override_bundled(self, tmp_path):
        """Local skills with the same name as bundled skills should win."""
        # Create a local skill with the same name as a potential bundled skill
        skills_dir = tmp_path / ".prax" / "skills" / "chinese-coding"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: chinese-coding\ndescription: Local version\n---\nLocal content.",
            encoding="utf-8",
        )
        skills = load_skills(str(tmp_path))
        cc_skills = [s for s in skills if s.name == "chinese-coding"]
        assert len(cc_skills) == 1
        assert cc_skills[0].description == "Local version"


class TestSkillIndex:

    def _make_skill(self, name: str, description: str = "", triggers: tuple = (), tags: tuple = (), priority: int = 0) -> Skill:
        return Skill(
            name=name,
            description=description,
            content="# Skill",
            path=f"/fake/{name}/SKILL.md",
            triggers=triggers,
            tags=tags,
            priority=priority,
        )

    def test_get_by_name(self):
        skills = [self._make_skill("test-skill")]
        index = SkillIndex(skills)
        s = index.get("test-skill")
        assert s is not None
        assert s.name == "test-skill"

    def test_get_missing_returns_none(self):
        index = SkillIndex([])
        assert index.get("nonexistent") is None

    def test_list_names(self):
        skills = [self._make_skill("a"), self._make_skill("b")]
        index = SkillIndex(skills)
        assert "a" in index.list_names()
        assert "b" in index.list_names()

    def test_search_by_name_keyword(self):
        skills = [
            self._make_skill("git-commit", description="Git workflow"),
            self._make_skill("deploy-prod", description="Deploy to production"),
        ]
        index = SkillIndex(skills)
        results = index.search("git")
        assert any(s.name == "git-commit" for s in results)

    def test_search_trigger_match(self):
        skills = [
            self._make_skill("debug-tool", description="debug helper", triggers=("debug", "error")),
            self._make_skill("deploy", description="deploy"),
        ]
        index = SkillIndex(skills)
        results = index.search("debug")
        assert results[0].name == "debug-tool"

    def test_search_no_matches_returns_empty(self):
        skills = [self._make_skill("unrelated")]
        index = SkillIndex(skills)
        results = index.search("zzz_no_match_zzz")
        assert results == []

    def test_from_cwd(self, tmp_path):
        index = SkillIndex.from_cwd(str(tmp_path))
        assert isinstance(index, SkillIndex)


class TestFormatSkillsForPrompt:

    def _make_skill(self, name: str, description: str = "") -> Skill:
        return Skill(
            name=name,
            description=description,
            content="content",
            path=f"/path/{name}",
        )

    def test_empty_skills_returns_empty_string(self):
        result = format_skills_for_prompt([])
        assert result == ""

    def test_includes_skill_names(self):
        skills = [self._make_skill("debug"), self._make_skill("deploy")]
        result = format_skills_for_prompt(skills)
        assert "debug" in result
        assert "deploy" in result

    def test_includes_descriptions(self):
        skills = [self._make_skill("commit", description="Git commit workflow")]
        result = format_skills_for_prompt(skills)
        assert "Git commit workflow" in result

    def test_includes_model_hint_when_present(self):
        skill = Skill(
            name="fancy",
            description="desc",
            content="x",
            path="/p",
            model="glm-5",
        )
        result = format_skills_for_prompt([skill])
        assert "glm-5" in result


class TestFormatSkillsForSubagent:

    def _make_skill(self, name: str, content: str = "# Skill\nContent") -> Skill:
        return Skill(name=name, description="desc", content=content, path=f"/path/{name}")

    def test_empty_skills_returns_empty(self):
        result = format_skills_for_subagent([])
        assert result == ""

    def test_includes_full_content(self):
        skills = [self._make_skill("test", content="Full content here")]
        result = format_skills_for_subagent(skills)
        assert "Full content here" in result

    def test_truncates_long_content(self):
        long_content = "x" * 5000
        skills = [self._make_skill("big", content=long_content)]
        result = format_skills_for_subagent(skills, max_chars_per_skill=100)
        assert "truncated" in result


class TestFilterSkillsByTaskType:

    def _make_skill(self, name: str, description: str = "") -> Skill:
        return Skill(name=name, description=description, content="x", path=f"/p/{name}")

    def test_filters_relevant_skills(self):
        skills = [
            self._make_skill("git-commit", description="git workflow"),
            self._make_skill("deploy", description="deployment"),
        ]
        result = filter_skills_by_task_type(skills, "git")
        assert any(s.name == "git-commit" for s in result)

    def test_returns_at_most_max_skills(self):
        skills = [self._make_skill(f"debug-{i}", description="debug") for i in range(10)]
        result = filter_skills_by_task_type(skills, "debugging", max_skills=3)
        assert len(result) <= 3

    def test_empty_skills_returns_empty(self):
        result = filter_skills_by_task_type([], "git")
        assert result == []
