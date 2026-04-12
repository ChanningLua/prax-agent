"""Unit tests for prax.core.claude_cli_executor — subprocess-based Claude CLI execution."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from prax.core.claude_cli_executor import ClaudeCliExecutor, ExecutionResult, is_available


def test_is_available_returns_true_when_claude_binary_exists():
    """is_available returns True when 'claude' binary is in PATH."""
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        assert is_available() is True


def test_is_available_returns_false_when_claude_binary_missing():
    """is_available returns False when 'claude' binary is not in PATH."""
    with patch("shutil.which", return_value=None):
        assert is_available() is False


@pytest.mark.asyncio
async def test_run_basic_text_response():
    """run returns ExecutionResult with text from assistant event."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello"}]}}),
        json.dumps({"type": "result", "result": "", "usage": {"input_tokens": 10, "output_tokens": 5}}),
    ])
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await executor.run("test prompt")

    assert result.text == "Hello"
    assert result.usage == {"input_tokens": 10, "output_tokens": 5}
    mock_proc.stdin.write.assert_called_once_with(b"test prompt")


@pytest.mark.asyncio
async def test_run_with_session_id():
    """run includes --session-id flag when valid UUID provided."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([
        json.dumps({"type": "result", "result": "done", "session_id": "12345678-1234-1234-1234-123456789abc"}),
    ])
    mock_proc.wait = AsyncMock()

    captured_cmd = None

    async def capture_cmd(*args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = args
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=capture_cmd):
        result = await executor.run("test", session_id="12345678-1234-1234-1234-123456789abc")

    assert "--session-id" in captured_cmd
    assert "12345678-1234-1234-1234-123456789abc" in captured_cmd
    assert result.session_id == "12345678-1234-1234-1234-123456789abc"


@pytest.mark.asyncio
async def test_run_skips_invalid_session_id():
    """run skips --session-id flag when session_id is not a valid UUID."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([json.dumps({"type": "result", "result": "done"})])
    mock_proc.wait = AsyncMock()

    captured_cmd = None

    async def capture_cmd(*args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = args
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=capture_cmd):
        await executor.run("test", session_id="invalid-id")

    assert "--session-id" not in captured_cmd


@pytest.mark.asyncio
async def test_run_with_model():
    """run includes --model flag when model starts with 'claude-'."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([json.dumps({"type": "result", "result": "done"})])
    mock_proc.wait = AsyncMock()

    captured_cmd = None

    async def capture_cmd(*args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = args
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=capture_cmd):
        await executor.run("test", model="claude-3-5-sonnet-20241022")

    assert "--model" in captured_cmd
    assert "claude-3-5-sonnet-20241022" in captured_cmd


@pytest.mark.asyncio
async def test_run_skips_non_claude_model():
    """run skips --model flag when model does not start with 'claude-'."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([json.dumps({"type": "result", "result": "done"})])
    mock_proc.wait = AsyncMock()

    captured_cmd = None

    async def capture_cmd(*args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = args
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=capture_cmd):
        await executor.run("test", model="gpt-4")

    assert "--model" not in captured_cmd


@pytest.mark.asyncio
async def test_run_with_permission_mode():
    """run includes --permission-mode flag with specified value."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([json.dumps({"type": "result", "result": "done"})])
    mock_proc.wait = AsyncMock()

    captured_cmd = None

    async def capture_cmd(*args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = args
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=capture_cmd):
        await executor.run("test", permission_mode="requirePermissions")

    assert "--permission-mode" in captured_cmd
    assert "requirePermissions" in captured_cmd


@pytest.mark.asyncio
async def test_run_with_cwd():
    """run passes cwd to subprocess."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([json.dumps({"type": "result", "result": "done"})])
    mock_proc.wait = AsyncMock()

    captured_kwargs = None

    async def capture_kwargs(*args, **kwargs):
        nonlocal captured_kwargs
        captured_kwargs = kwargs
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=capture_kwargs):
        await executor.run("test", cwd="/tmp/workspace")

    assert captured_kwargs["cwd"] == "/tmp/workspace"


@pytest.mark.asyncio
async def test_run_removes_claudecode_env_var():
    """run removes CLAUDECODE from environment."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([json.dumps({"type": "result", "result": "done"})])
    mock_proc.wait = AsyncMock()

    captured_kwargs = None

    async def capture_kwargs(*args, **kwargs):
        nonlocal captured_kwargs
        captured_kwargs = kwargs
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=capture_kwargs):
        with patch.dict("os.environ", {"CLAUDECODE": "1"}):
            await executor.run("test")

    assert "CLAUDECODE" not in captured_kwargs["env"]


@pytest.mark.asyncio
async def test_run_calls_on_text_callback():
    """run calls on_text callback for each text chunk."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([
        json.dumps({"type": "text", "text": "chunk1"}),
        json.dumps({"type": "text", "text": "chunk2"}),
        json.dumps({"type": "result", "result": ""}),
    ])
    mock_proc.wait = AsyncMock()

    on_text = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await executor.run("test", on_text=on_text)

    assert on_text.call_count == 2
    on_text.assert_any_call("chunk1")
    on_text.assert_any_call("chunk2")
    assert result.text == "chunk1chunk2"


@pytest.mark.asyncio
async def test_run_collects_tool_calls():
    """run collects tool_use blocks into tool_calls list."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}},
                    {"type": "tool_use", "id": "t2", "name": "read", "input": {"path": "/file"}},
                ]
            }
        }),
        json.dumps({"type": "result", "result": ""}),
    ])
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await executor.run("test")

    assert len(result.tool_calls) == 2
    assert result.tool_calls[0]["id"] == "t1"
    assert result.tool_calls[0]["name"] == "bash"
    assert result.tool_calls[1]["id"] == "t2"
    assert result.tool_calls[1]["name"] == "read"


@pytest.mark.asyncio
async def test_run_deduplicates_tool_calls():
    """run deduplicates tool_use blocks with same ID."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}},
                ]
            }
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}},
                ]
            }
        }),
        json.dumps({"type": "result", "result": ""}),
    ])
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await executor.run("test")

    assert len(result.tool_calls) == 1


@pytest.mark.asyncio
async def test_run_handles_tool_use_without_id():
    """run generates synthetic ID for tool_use blocks without id field."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "bash", "input": {"command": "ls"}},
                ]
            }
        }),
        json.dumps({"type": "result", "result": ""}),
    ])
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await executor.run("test")

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "bash"


@pytest.mark.asyncio
async def test_run_extracts_session_id_from_system_init():
    """run extracts session_id from system init event."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([
        json.dumps({"type": "system", "subtype": "init", "session_id": "new-session-123"}),
        json.dumps({"type": "result", "result": "done"}),
    ])
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await executor.run("test")

    assert result.session_id == "new-session-123"


@pytest.mark.asyncio
async def test_run_skips_invalid_json_lines():
    """run silently skips lines that are not valid JSON."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([
        "not json",
        json.dumps({"type": "text", "text": "valid"}),
        "also not json",
        json.dumps({"type": "result", "result": ""}),
    ])
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await executor.run("test")

    assert result.text == "valid"


@pytest.mark.asyncio
async def test_run_uses_result_text_when_no_collected_text():
    """run uses result.result field when no text was collected from events."""
    executor = ClaudeCliExecutor()

    mock_proc = MagicMock()
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = _async_lines([
        json.dumps({"type": "result", "result": "fallback text"}),
    ])
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await executor.run("test")

    assert result.text == "fallback text"


def _async_lines(lines: list[str]):
    """Helper to create an async iterator over lines."""
    async def _gen():
        for line in lines:
            yield (line + "\n").encode()
    return _gen()
