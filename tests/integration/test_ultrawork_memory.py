"""Integration tests: ultrawork() → MemoryBackend."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from prax.core.memory.backend import Experience


class TestUltraworkMemory:
    """Verify that ultrawork() uses MemoryBackend and stores experience."""

    @pytest.mark.asyncio
    async def test_store_experience_called_on_success(self, tmp_path):
        """ultrawork should call memory.store_experience after a successful run."""
        mock_memory = AsyncMock()
        mock_memory.store_experience = AsyncMock()
        mock_memory.close = AsyncMock()
        mock_memory.format_for_prompt = AsyncMock(return_value="")

        from prax.agents.base import AgentResult

        fake_result = AgentResult(
            text="Task complete",
            stop_reason="end_turn",
            iterations=2,
            had_errors=False,
        )
        mock_agent = AsyncMock()
        mock_agent.run.return_value = fake_result

        with (
            patch("prax.workflows.ultrawork.get_memory_backend", return_value=mock_memory),
            patch("prax.workflows.ultrawork.load_memory_config", return_value={}),
            patch("prax.workflows.ultrawork.ModelRouter") as MockRouter,
            patch("prax.workflows.ultrawork.SisyphusAgent", return_value=mock_agent),
        ):
            mock_router_instance = MagicMock()
            mock_router_instance.route.return_value = "glm-4-flash"
            mock_router_instance.classify_task.return_value = "quick_tasks"
            MockRouter.from_cwd.return_value = mock_router_instance

            from prax.workflows.ultrawork import ultrawork

            result = await ultrawork("quick task", cwd=str(tmp_path))

        assert result == "Task complete"
        mock_memory.store_experience.assert_called_once()
        call_args = mock_memory.store_experience.call_args[0][0]
        assert isinstance(call_args, Experience)
        assert call_args.outcome == "success"
        mock_memory.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_memory_config_called_when_none(self, tmp_path):
        """When memory_config=None, load_memory_config should be called with cwd."""
        mock_memory = AsyncMock()
        mock_memory.store_experience = AsyncMock()
        mock_memory.close = AsyncMock()

        from prax.agents.base import AgentResult

        fake_result = AgentResult(
            text="done", stop_reason="end_turn", iterations=1, had_errors=False
        )
        mock_agent = AsyncMock()
        mock_agent.run.return_value = fake_result

        with (
            patch("prax.workflows.ultrawork.get_memory_backend", return_value=mock_memory),
            patch("prax.workflows.ultrawork.load_memory_config", return_value={}) as mock_load_cfg,
            patch("prax.workflows.ultrawork.ModelRouter") as MockRouter,
            patch("prax.workflows.ultrawork.SisyphusAgent", return_value=mock_agent),
        ):
            mock_router_instance = MagicMock()
            mock_router_instance.route.return_value = "glm-4-flash"
            mock_router_instance.classify_task.return_value = "default"
            MockRouter.from_cwd.return_value = mock_router_instance

            from prax.workflows.ultrawork import ultrawork

            await ultrawork("do something", cwd=str(tmp_path), memory_config=None)

        mock_load_cfg.assert_called_once_with(str(tmp_path))
