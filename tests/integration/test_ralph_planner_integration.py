"""Integration tests: RalphAgent + LLMPlanner auto todo writing."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from prax.core.planning import LLMPlanner, PlannedTodo
from prax.core.todo_store import TodoStore


class TestRalphPlannerIntegration:
    """Verify that RalphAgent auto-writes todos via LLMPlanner before execution."""

    @pytest.mark.asyncio
    async def test_llm_planner_todos_written_to_store(self, tmp_path):
        """When LLMPlanner returns todos, they are saved into TodoStore."""
        planned = [
            PlannedTodo(id="1", content="Inspect codebase", active_form="Inspecting", status="pending"),
            PlannedTodo(id="2", content="Implement feature", active_form="Implementing", status="pending"),
        ]

        # Patch LLMPlanner.decompose and run_agent_loop to avoid real LLM calls
        with (
            patch("prax.agents.ralph.LLMPlanner") as MockPlanner,
            patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock) as mock_loop,
        ):
            mock_instance = AsyncMock()
            mock_instance.decompose.return_value = planned
            MockPlanner.return_value = mock_instance

            mock_loop.return_value = "done"

            from prax.agents.ralph import RalphAgent

            agent = RalphAgent(
                cwd=str(tmp_path),
                model="glm-4-flash",
                models_config={"glm-4-flash": {"provider": "zhipuai", "api_key": "test"}},
                use_llm_planner=True,
            )

            # Stub _resolve_model to avoid real client
            from prax.agents.base import AgentResult
            from prax.core.llm_client import ModelConfig
            agent._resolve_model = MagicMock(
                return_value=ModelConfig(
                    provider="zhipuai", model="glm-4-flash", base_url="", api_key="test", api_format="openai"
                )
            )

            # Patch LLMClient so no real connection is made
            with patch("prax.agents.ralph.LLMClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.resolve_model.return_value = ModelConfig(
                    provider="zhipuai", model="glm-4-flash", base_url="", api_key="test", api_format="openai"
                )
                MockClient.return_value = mock_client

                # Will hit max_iterations quickly since todos stay pending
                agent.max_iterations = 1
                await agent.run("build the feature")

            store = TodoStore(str(tmp_path))
            todos = store.load()
            contents = [t.content for t in todos]
            assert "Inspect codebase" in contents
            assert "Implement feature" in contents

    @pytest.mark.asyncio
    async def test_empty_plan_does_not_write_todos(self, tmp_path):
        """When LLMPlanner returns empty list, TodoStore should remain empty."""
        with (
            patch("prax.agents.ralph.LLMPlanner") as MockPlanner,
            patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock) as mock_loop,
        ):
            mock_instance = AsyncMock()
            mock_instance.decompose.return_value = []
            MockPlanner.return_value = mock_instance

            mock_loop.return_value = "done"

            from prax.agents.ralph import RalphAgent
            from prax.core.llm_client import ModelConfig

            agent = RalphAgent(
                cwd=str(tmp_path),
                model="glm-4-flash",
                models_config={},
                use_llm_planner=True,
            )
            agent.max_iterations = 1

            with patch("prax.agents.ralph.LLMClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.resolve_model.return_value = ModelConfig(
                    provider="zhipuai", model="glm-4-flash", base_url="", api_key="test", api_format="openai"
                )
                MockClient.return_value = mock_client
                await agent.run("quick task")

            store = TodoStore(str(tmp_path))
            assert store.load() == []

    @pytest.mark.asyncio
    async def test_checkpoint_resume_skips_planning(self, tmp_path):
        """When resuming from checkpoint, LLMPlanner should NOT be called."""
        from prax.core.checkpoint import CheckpointStore

        # Create a fake checkpoint
        cp = CheckpointStore.create_checkpoint(
            session_id="ralph_test",
            iteration=2,
            task="build the feature",
            model="glm-4-flash",
            message_history=[{"role": "assistant", "content": "previous turn"}],
            todo_snapshot=[],
        )
        CheckpointStore(cwd=str(tmp_path)).save(cp)

        with (
            patch("prax.agents.ralph.LLMPlanner") as MockPlanner,
            patch("prax.agents.ralph.run_agent_loop", new_callable=AsyncMock) as mock_loop,
        ):
            mock_instance = AsyncMock()
            MockPlanner.return_value = mock_instance

            mock_loop.return_value = "resumed result"

            from prax.agents.ralph import RalphAgent
            from prax.core.llm_client import ModelConfig

            agent = RalphAgent(
                cwd=str(tmp_path),
                model="glm-4-flash",
                models_config={},
                session_id="ralph_test",
                use_llm_planner=True,
            )
            agent.max_iterations = 3  # allow at least one continuation loop

            with patch("prax.agents.ralph.LLMClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.resolve_model.return_value = ModelConfig(
                    provider="zhipuai", model="glm-4-flash", base_url="", api_key="test", api_format="openai"
                )
                MockClient.return_value = mock_client
                await agent.run("build the feature")

            # decompose should NOT have been called during a checkpoint resume
            mock_instance.decompose.assert_not_called()
