"""Unit tests for planning.py — static fallback and LLMPlanner._parse."""
from __future__ import annotations

import pytest

from prax.core.planning import (
    LLMPlanner,
    PlannedTodo,
    generate_initial_plan,
)


# ── generate_initial_plan (static fallback) ───────────────────────────────────

class TestGenerateInitialPlan:
    def test_returns_three_items(self):
        plan = generate_initial_plan("write a feature")
        assert len(plan) == 3

    def test_first_item_in_progress(self):
        plan = generate_initial_plan("do something")
        assert plan[0].status == "in_progress"

    def test_remaining_items_pending(self):
        plan = generate_initial_plan("do something")
        assert all(p.status == "pending" for p in plan[1:])

    def test_dependency_chain(self):
        plan = generate_initial_plan("task")
        assert plan[1].depends_on == ("1",)
        assert plan[2].depends_on == ("2",)

    def test_ids_assigned(self):
        plan = generate_initial_plan("task")
        assert [p.id for p in plan] == ["1", "2", "3"]

    def test_task_text_embedded_in_content(self):
        plan = generate_initial_plan("build the widget")
        assert all("build the widget" in p.content for p in plan)

    def test_normalizes_whitespace(self):
        plan = generate_initial_plan("  hello   world  ")
        assert "hello world" in plan[0].content

    def test_empty_task_raises(self):
        with pytest.raises(ValueError):
            generate_initial_plan("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            generate_initial_plan("   ")

    def test_returns_planned_todo_instances(self):
        plan = generate_initial_plan("x")
        assert all(isinstance(p, PlannedTodo) for p in plan)


# ── PlannedTodo.to_dict ───────────────────────────────────────────────────────

class TestPlannedTodoDict:
    def test_to_dict_basic_keys(self):
        t = PlannedTodo(id="1", content="do x", active_form="Doing x", status="pending")
        d = t.to_dict()
        assert d["content"] == "do x"
        assert d["activeForm"] == "Doing x"
        assert d["status"] == "pending"

    def test_to_dict_omits_id_when_empty(self):
        t = PlannedTodo(content="x", active_form="X", status="pending")
        assert "id" not in t.to_dict()

    def test_to_dict_includes_id_when_set(self):
        t = PlannedTodo(id="42", content="x", active_form="X", status="pending")
        assert t.to_dict()["id"] == "42"

    def test_to_dict_omits_depends_on_when_empty(self):
        t = PlannedTodo(id="1", content="x", active_form="X", status="pending")
        assert "dependsOn" not in t.to_dict()

    def test_to_dict_includes_depends_on_when_set(self):
        t = PlannedTodo(id="2", content="x", active_form="X", status="pending", depends_on=("1",))
        assert t.to_dict()["dependsOn"] == ["1"]


# ── LLMPlanner._parse ─────────────────────────────────────────────────────────

class TestLLMPlannerParse:
    def setup_method(self):
        self.planner = LLMPlanner()

    def _json(self, items: list[dict]) -> str:
        import json
        return json.dumps(items)

    def test_basic_parse(self):
        raw = self._json([
            {"id": "1", "content": "Setup", "activeForm": "Setting up", "status": "pending", "depends_on": []},
            {"id": "2", "content": "Build", "activeForm": "Building", "status": "pending", "depends_on": ["1"]},
        ])
        todos = self.planner._parse(raw)
        assert len(todos) == 2
        assert todos[0].content == "Setup"
        assert todos[1].depends_on == ("1",)

    def test_json_embedded_in_prose(self):
        """Parser must find JSON even when surrounded by LLM prose."""
        prose = (
            "Here is your plan:\n"
            '[{"id":"1","content":"Do it","activeForm":"Doing","status":"pending","depends_on":[]}]\n'
            "Let me know if you need changes."
        )
        todos = self.planner._parse(prose)
        assert len(todos) == 1
        assert todos[0].content == "Do it"

    def test_returns_empty_on_bad_json(self):
        assert self.planner._parse("not json") == []
        assert self.planner._parse("") == []
        assert self.planner._parse("{}") == []   # object not array

    def test_filters_forward_references(self):
        """depends_on referencing an id not yet seen must be dropped."""
        raw = self._json([
            {"id": "1", "content": "A", "activeForm": "A-ing", "status": "pending", "depends_on": ["99"]},
        ])
        todos = self.planner._parse(raw)
        assert todos[0].depends_on == ()

    def test_multi_dependency(self):
        raw = self._json([
            {"id": "1", "content": "A", "activeForm": "A-ing", "status": "pending", "depends_on": []},
            {"id": "2", "content": "B", "activeForm": "B-ing", "status": "pending", "depends_on": []},
            {"id": "3", "content": "C", "activeForm": "C-ing", "status": "pending", "depends_on": ["1", "2"]},
        ])
        todos = self.planner._parse(raw)
        assert todos[2].depends_on == ("1", "2")

    def test_skips_items_without_content(self):
        raw = self._json([
            {"id": "1", "content": "", "activeForm": "X", "status": "pending", "depends_on": []},
            {"id": "2", "content": "Valid", "activeForm": "Y", "status": "pending", "depends_on": []},
        ])
        todos = self.planner._parse(raw)
        assert len(todos) == 1
        assert todos[0].content == "Valid"

    def test_defaults_status_to_pending(self):
        raw = self._json([{"id": "1", "content": "x", "activeForm": "X"}])
        todos = self.planner._parse(raw)
        assert todos[0].status == "pending"

    def test_invalid_status_normalised_to_pending(self):
        raw = self._json([{"id": "1", "content": "x", "activeForm": "X", "status": "done"}])
        todos = self.planner._parse(raw)
        assert todos[0].status == "pending"

    def test_fallback_active_form_when_missing(self):
        raw = self._json([{"id": "1", "content": "x", "status": "pending"}])
        todos = self.planner._parse(raw)
        assert todos[0].active_form != ""

    def test_accepts_dependsOn_camelcase(self):
        """LLM may return camelCase dependsOn — both must be handled."""
        raw = self._json([
            {"id": "1", "content": "A", "activeForm": "A", "status": "pending", "dependsOn": []},
            {"id": "2", "content": "B", "activeForm": "B", "status": "pending", "dependsOn": ["1"]},
        ])
        todos = self.planner._parse(raw)
        assert todos[1].depends_on == ("1",)

    def test_skips_non_dict_items(self):
        import json
        raw = json.dumps(["string_item", {"id": "1", "content": "x", "activeForm": "X", "status": "pending"}])
        todos = self.planner._parse(raw)
        assert len(todos) == 1
