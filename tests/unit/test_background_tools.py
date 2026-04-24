"""Unit tests for background task tools.

Uses tmp_path for a real BackgroundTaskStore on disk. The detached
subprocess spawn (``_spawn_background_runner``) is patched so these
tests don't actually fork a runner — that path is covered separately
in the end-to-end smoke.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from prax.core.background_store import BackgroundTask, BackgroundTaskStore
from prax.tools.background_task import (
    CancelTaskTool,
    CheckTaskTool,
    ListTasksTool,
    StartTaskTool,
    UpdateTaskTool,
)


# Patch the spawn helper by default so tests don't accidentally fork a
# real subprocess. Tests that need the real thing opt out explicitly.
@pytest.fixture(autouse=True)
def _no_real_spawn():
    with patch(
        "prax.tools.background_task._spawn_background_runner",
        return_value=99999,
    ) as mock_spawn:
        yield mock_spawn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(tmp_path) -> BackgroundTaskStore:
    return BackgroundTaskStore(cwd=str(tmp_path))


def _make_task(store: BackgroundTaskStore, task_id: str, status: str = "running") -> BackgroundTask:
    task = BackgroundTask(
        task_id=task_id,
        description="test task",
        prompt="do something",
        subagent_type="general-purpose",
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
    )
    store.create(task)
    return task


# ---------------------------------------------------------------------------
# StartTaskTool
# ---------------------------------------------------------------------------

def test_start_task_name(tmp_path):
    store = _store(tmp_path)
    executor = AsyncMock(return_value="done")
    tool = StartTaskTool(store=store, cwd=str(tmp_path), executor=executor)
    assert tool.name == "StartTask"


def test_start_task_description_non_empty(tmp_path):
    store = _store(tmp_path)
    tool = StartTaskTool(store=store, cwd=str(tmp_path), executor=AsyncMock())
    assert tool.description.strip()


@pytest.mark.asyncio
async def test_start_task_returns_task_id(tmp_path):
    store = _store(tmp_path)
    executor = AsyncMock(return_value="result text")
    tool = StartTaskTool(store=store, cwd=str(tmp_path), executor=executor)
    result = await tool.execute({"description": "my task", "prompt": "do stuff"})
    assert not result.is_error
    payload = json.loads(result.content)
    assert "task_id" in payload
    assert payload["status"] == "running"


@pytest.mark.asyncio
async def test_start_task_persists_to_store(tmp_path):
    store = _store(tmp_path)
    executor = AsyncMock(return_value="ok")
    tool = StartTaskTool(store=store, cwd=str(tmp_path), executor=executor)
    result = await tool.execute({"description": "persist test", "prompt": "do it"})
    payload = json.loads(result.content)
    task_id = payload["task_id"]
    task = store.get(task_id)
    assert task is not None
    assert task.status == "running"
    assert task.description == "persist test"


@pytest.mark.asyncio
async def test_start_task_empty_description_is_error(tmp_path):
    store = _store(tmp_path)
    tool = StartTaskTool(store=store, cwd=str(tmp_path), executor=AsyncMock())
    result = await tool.execute({"description": "   ", "prompt": "do stuff"})
    assert result.is_error
    assert "description" in result.content.lower()


@pytest.mark.asyncio
async def test_start_task_empty_prompt_is_error(tmp_path):
    store = _store(tmp_path)
    tool = StartTaskTool(store=store, cwd=str(tmp_path), executor=AsyncMock())
    result = await tool.execute({"description": "valid", "prompt": ""})
    assert result.is_error
    assert "prompt" in result.content.lower()


# ---------------------------------------------------------------------------
# M1 regression: detached-subprocess lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_task_spawns_detached_runner(tmp_path, _no_real_spawn):
    """StartTask must invoke _spawn_background_runner with task_id + cwd."""
    store = _store(tmp_path)
    tool = StartTaskTool(store=store, cwd=str(tmp_path))
    result = await tool.execute({"description": "d", "prompt": "p"})
    payload = json.loads(result.content)
    task_id = payload["task_id"]
    _no_real_spawn.assert_called_once_with(task_id, str(tmp_path))
    # The returned payload exposes the PID from the mock.
    assert payload["pid"] == 99999


@pytest.mark.asyncio
async def test_start_task_persists_cwd_and_pid(tmp_path, _no_real_spawn):
    """cwd + pid must survive a store roundtrip so CheckTask can use them."""
    store = _store(tmp_path)
    tool = StartTaskTool(store=store, cwd=str(tmp_path))
    result = await tool.execute({"description": "d", "prompt": "p"})
    task_id = json.loads(result.content)["task_id"]
    task = store.get(task_id)
    assert task is not None
    assert task.cwd == str(tmp_path)
    assert task.pid == 99999


@pytest.mark.asyncio
async def test_start_task_records_spawn_failure_as_error(tmp_path, _no_real_spawn):
    """If the OS refuses to spawn the subprocess, surface it as task error."""
    _no_real_spawn.side_effect = OSError("out of file descriptors")
    store = _store(tmp_path)
    tool = StartTaskTool(store=store, cwd=str(tmp_path))
    result = await tool.execute({"description": "d", "prompt": "p"})
    assert result.is_error
    payload = json.loads(result.content)
    task = store.get(payload["task_id"])
    assert task is not None
    assert task.status == "error"
    assert task.exit_code == -1


@pytest.mark.asyncio
async def test_check_task_reconciles_dead_pid(tmp_path):
    """If status='running' but pid is dead, CheckTask flips it to 'error'."""
    store = _store(tmp_path)
    # pid=1 on POSIX is init and we have no permission to kill it — but for
    # this test we want a pid that is *not* alive, so pick a deliberately
    # huge number that won't exist on any sane host.
    task = BackgroundTask(
        task_id="task_zombie",
        description="zombie",
        prompt="p",
        subagent_type="general-purpose",
        status="running",
        created_at="2026-01-01T00:00:00+00:00",
        pid=2**30,  # astronomically unlikely to exist
    )
    store.create(task)

    tool = CheckTaskTool(store=store)
    result = await tool.execute({"task_id": "task_zombie"})
    payload = json.loads(result.content)
    assert payload["status"] == "error"
    assert "no longer alive" in payload["error"]
    # Store should be updated too, not just the response.
    reloaded = store.get("task_zombie")
    assert reloaded is not None
    assert reloaded.status == "error"


@pytest.mark.asyncio
async def test_check_task_running_with_alive_pid_stays_running(tmp_path):
    """If pid is alive, CheckTask must NOT flip the task to error."""
    import os
    store = _store(tmp_path)
    task = BackgroundTask(
        task_id="task_alive",
        description="alive",
        prompt="p",
        subagent_type="general-purpose",
        status="running",
        created_at="2026-01-01T00:00:00+00:00",
        pid=os.getpid(),  # ourselves — definitely alive
    )
    store.create(task)

    tool = CheckTaskTool(store=store)
    result = await tool.execute({"task_id": "task_alive"})
    payload = json.loads(result.content)
    assert payload["status"] == "running"


@pytest.mark.asyncio
async def test_cancel_task_signals_live_pid(tmp_path):
    """CancelTask should send SIGTERM when the task has a live pid."""
    from unittest.mock import patch as _patch
    store = _store(tmp_path)
    task = BackgroundTask(
        task_id="task_cancel",
        description="c",
        prompt="p",
        subagent_type="general-purpose",
        status="running",
        created_at="2026-01-01T00:00:00+00:00",
        pid=12345,
    )
    store.create(task)

    tool = CancelTaskTool(store=store)
    # Make the pid "alive" and watch for the kill call.
    with _patch("prax.tools.background_task._pid_alive", return_value=True), \
         _patch("prax.tools.background_task.os.kill") as mock_kill:
        result = await tool.execute({"task_id": "task_cancel"})

    import signal
    mock_kill.assert_called_once_with(12345, signal.SIGTERM)
    payload = json.loads(result.content)
    assert payload["cancelled"] is True
    assert payload["signalled"] is True
    assert store.get("task_cancel").status == "cancelled"


# ---------------------------------------------------------------------------
# CheckTaskTool
# ---------------------------------------------------------------------------

def test_check_task_name(tmp_path):
    assert CheckTaskTool(store=_store(tmp_path)).name == "CheckTask"


@pytest.mark.asyncio
async def test_check_task_running(tmp_path):
    store = _store(tmp_path)
    _make_task(store, "task_abc123", status="running")
    tool = CheckTaskTool(store=store)
    result = await tool.execute({"task_id": "task_abc123"})
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["status"] == "running"
    assert payload["task_id"] == "task_abc123"


@pytest.mark.asyncio
async def test_check_task_not_found(tmp_path):
    store = _store(tmp_path)
    tool = CheckTaskTool(store=store)
    result = await tool.execute({"task_id": "task_nonexistent"})
    assert result.is_error
    assert "not found" in result.content.lower() or "not found" in json.loads(result.content).get("error", "").lower()


@pytest.mark.asyncio
async def test_check_task_no_task_id(tmp_path):
    store = _store(tmp_path)
    tool = CheckTaskTool(store=store)
    result = await tool.execute({"task_id": ""})
    assert result.is_error
    assert "task_id" in result.content.lower()


@pytest.mark.asyncio
async def test_check_task_success_includes_result(tmp_path):
    store = _store(tmp_path)
    task = _make_task(store, "task_done", status="running")
    store.update_status("task_done", "success", result="great output")
    tool = CheckTaskTool(store=store)
    result = await tool.execute({"task_id": "task_done"})
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["status"] == "success"
    assert payload["result"] == "great output"
    assert "finished_at" in payload


# ---------------------------------------------------------------------------
# UpdateTaskTool
# ---------------------------------------------------------------------------

def test_update_task_name(tmp_path):
    assert UpdateTaskTool(store=_store(tmp_path)).name == "UpdateTask"


@pytest.mark.asyncio
async def test_update_task_appends_message(tmp_path):
    store = _store(tmp_path)
    _make_task(store, "task_run", status="running")
    tool = UpdateTaskTool(store=store)
    result = await tool.execute({"task_id": "task_run", "message": "please hurry"})
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["updated"] is True
    # Verify message was persisted
    task = store.get("task_run")
    assert "please hurry" in task.prompt


@pytest.mark.asyncio
async def test_update_task_not_running(tmp_path):
    store = _store(tmp_path)
    task = _make_task(store, "task_done", status="running")
    store.update_status("task_done", "success", result="done")
    tool = UpdateTaskTool(store=store)
    result = await tool.execute({"task_id": "task_done", "message": "too late"})
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["updated"] is False


@pytest.mark.asyncio
async def test_update_task_not_found(tmp_path):
    store = _store(tmp_path)
    tool = UpdateTaskTool(store=store)
    result = await tool.execute({"task_id": "task_ghost", "message": "hello"})
    assert result.is_error


@pytest.mark.asyncio
async def test_update_task_missing_message(tmp_path):
    store = _store(tmp_path)
    _make_task(store, "task_run2", status="running")
    tool = UpdateTaskTool(store=store)
    result = await tool.execute({"task_id": "task_run2", "message": ""})
    assert result.is_error
    assert "message" in result.content.lower()


# ---------------------------------------------------------------------------
# CancelTaskTool
# ---------------------------------------------------------------------------

def test_cancel_task_name(tmp_path):
    assert CancelTaskTool(store=_store(tmp_path)).name == "CancelTask"


@pytest.mark.asyncio
async def test_cancel_task_success(tmp_path):
    store = _store(tmp_path)
    _make_task(store, "task_cancel", status="running")
    tool = CancelTaskTool(store=store)
    result = await tool.execute({"task_id": "task_cancel"})
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["cancelled"] is True
    assert store.get("task_cancel").status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_task_already_done(tmp_path):
    store = _store(tmp_path)
    task = _make_task(store, "task_done2", status="running")
    store.update_status("task_done2", "success", result="completed")
    tool = CancelTaskTool(store=store)
    result = await tool.execute({"task_id": "task_done2"})
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["cancelled"] is False


@pytest.mark.asyncio
async def test_cancel_task_not_found(tmp_path):
    store = _store(tmp_path)
    tool = CancelTaskTool(store=store)
    result = await tool.execute({"task_id": "task_ghost2"})
    assert result.is_error


# ---------------------------------------------------------------------------
# ListTasksTool
# ---------------------------------------------------------------------------

def test_list_tasks_name(tmp_path):
    assert ListTasksTool(store=_store(tmp_path)).name == "ListTasks"


@pytest.mark.asyncio
async def test_list_tasks_empty(tmp_path):
    store = _store(tmp_path)
    tool = ListTasksTool(store=store)
    result = await tool.execute({})
    assert not result.is_error
    tasks = json.loads(result.content)
    assert tasks == []


@pytest.mark.asyncio
async def test_list_tasks_all(tmp_path):
    store = _store(tmp_path)
    _make_task(store, "task_aaa", status="running")
    _make_task(store, "task_bbb", status="running")
    store.update_status("task_bbb", "success", result="done")
    tool = ListTasksTool(store=store)
    result = await tool.execute({})
    assert not result.is_error
    tasks = json.loads(result.content)
    assert len(tasks) == 2


@pytest.mark.asyncio
async def test_list_tasks_filtered_by_status(tmp_path):
    store = _store(tmp_path)
    _make_task(store, "task_ccc", status="running")
    _make_task(store, "task_ddd", status="running")
    store.update_status("task_ddd", "success", result="done")
    tool = ListTasksTool(store=store)
    result = await tool.execute({"status": "running"})
    assert not result.is_error
    tasks = json.loads(result.content)
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "task_ccc"
    assert tasks[0]["status"] == "running"
