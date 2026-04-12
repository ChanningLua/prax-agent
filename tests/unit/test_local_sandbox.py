"""Unit tests for LocalSandbox and LocalSandboxProvider.

No real subprocess execution — subprocess.run is mocked where needed.
Filesystem tests use tmp_path so all I/O is ephemeral.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prax.core.sandbox.base import SandboxResult
from prax.core.sandbox.local import LocalSandbox, LocalSandboxProvider


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_run_ok(stdout: str = "output", stderr: str = "", returncode: int = 0):
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


# ── LocalSandbox.execute_command ─────────────────────────────────────────────


def test_execute_command_returns_stdout(tmp_path) -> None:
    sb = LocalSandbox("s1", cwd=str(tmp_path))
    with patch("subprocess.run", return_value=_mock_run_ok(stdout="hello\n")):
        result = sb.execute_command("echo hello")
    assert "hello" in result


def test_execute_command_appends_exit_code_on_nonzero(tmp_path) -> None:
    sb = LocalSandbox("s1", cwd=str(tmp_path))
    with patch("subprocess.run", return_value=_mock_run_ok(stdout="", returncode=2)):
        result = sb.execute_command("false")
    assert "Exit code: 2" in result


def test_execute_command_timeout_returns_error_string(tmp_path) -> None:
    sb = LocalSandbox("s1", cwd=str(tmp_path))
    with patch(
        "subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)
    ):
        result = sb.execute_command("slow", timeout=10)
    assert "timed out" in result


def test_execute_command_no_output_returns_placeholder(tmp_path) -> None:
    sb = LocalSandbox("s1", cwd=str(tmp_path))
    with patch("subprocess.run", return_value=_mock_run_ok(stdout="")):
        result = sb.execute_command("noop")
    assert result == "(no output)"


# ── LocalSandbox.execute_command_v2 ──────────────────────────────────────────


def test_execute_command_v2_returns_sandbox_result(tmp_path) -> None:
    sb = LocalSandbox("s1", cwd=str(tmp_path))
    with patch("subprocess.run", return_value=_mock_run_ok(stdout="done", returncode=0)):
        result = sb.execute_command_v2("ls")
    assert isinstance(result, SandboxResult)
    assert result.exit_code == 0
    assert "done" in result.output
    assert result.timed_out is False


def test_execute_command_v2_timeout_sets_timed_out_flag(tmp_path) -> None:
    sb = LocalSandbox("s1", cwd=str(tmp_path))
    with patch(
        "subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)
    ):
        result = sb.execute_command_v2("hang", timeout=5)
    assert result.timed_out is True
    assert result.exit_code == -1


# ── LocalSandbox.read_file ────────────────────────────────────────────────────


def test_read_file_returns_content(tmp_path) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("file content", encoding="utf-8")
    sb = LocalSandbox("s1")
    result = sb.read_file(str(target))
    assert result == "file content"


def test_read_file_raises_oserror_for_missing_file(tmp_path) -> None:
    sb = LocalSandbox("s1")
    with pytest.raises(OSError):
        sb.read_file(str(tmp_path / "nonexistent.txt"))


# ── LocalSandbox.write_file ───────────────────────────────────────────────────


def test_write_file_creates_file(tmp_path) -> None:
    sb = LocalSandbox("s1")
    target = str(tmp_path / "out.txt")
    sb.write_file(target, "hello world")
    assert Path(target).read_text(encoding="utf-8") == "hello world"


def test_write_file_append_mode(tmp_path) -> None:
    sb = LocalSandbox("s1")
    target = str(tmp_path / "append.txt")
    sb.write_file(target, "line1\n")
    sb.write_file(target, "line2\n", append=True)
    content = Path(target).read_text(encoding="utf-8")
    assert "line1" in content
    assert "line2" in content


def test_write_file_creates_parent_directories(tmp_path) -> None:
    sb = LocalSandbox("s1")
    nested = str(tmp_path / "deep" / "dir" / "file.txt")
    sb.write_file(nested, "data")
    assert Path(nested).exists()


# ── LocalSandbox.list_dir ─────────────────────────────────────────────────────


def test_list_dir_returns_entries(tmp_path) -> None:
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    sb = LocalSandbox("s1")
    result = sb.list_dir(str(tmp_path))
    paths = [str(e) for e in result]
    assert any("a.py" in p for p in paths)
    assert any("b.py" in p for p in paths)


def test_list_dir_returns_error_for_non_directory(tmp_path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x")
    sb = LocalSandbox("s1")
    result = sb.list_dir(str(f))
    assert len(result) == 1
    assert result[0].startswith("Error:")


def test_list_dir_respects_max_depth(tmp_path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "deep_file.txt").write_text("")
    sb = LocalSandbox("s1")
    result = sb.list_dir(str(tmp_path), max_depth=1)
    # max_depth=1 → only direct children of tmp_path should appear
    assert not any("deep_file.txt" in e for e in result)


# ── LocalSandboxProvider ──────────────────────────────────────────────────────


def test_local_provider_acquire_returns_id(tmp_path) -> None:
    provider = LocalSandboxProvider(cwd=str(tmp_path))
    sid = provider.acquire("test-sandbox")
    assert sid == "test-sandbox"
    assert provider.get("test-sandbox") is not None


def test_local_provider_acquire_auto_generates_id(tmp_path) -> None:
    provider = LocalSandboxProvider(cwd=str(tmp_path))
    sid = provider.acquire()
    assert sid.startswith("local_")


def test_local_provider_acquire_idempotent(tmp_path) -> None:
    provider = LocalSandboxProvider(cwd=str(tmp_path))
    s1 = provider.acquire("same")
    s2 = provider.acquire("same")
    assert s1 == s2
    assert provider.get("same") is provider.get("same")


def test_local_provider_release_removes_sandbox(tmp_path) -> None:
    provider = LocalSandboxProvider(cwd=str(tmp_path))
    provider.acquire("s1")
    provider.release("s1")
    assert provider.get("s1") is None


def test_local_provider_shutdown_clears_all(tmp_path) -> None:
    provider = LocalSandboxProvider(cwd=str(tmp_path))
    provider.acquire("a")
    provider.acquire("b")
    provider.shutdown()
    assert provider.get("a") is None
    assert provider.get("b") is None
