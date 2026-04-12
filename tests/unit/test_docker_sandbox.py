"""Unit tests for DockerSandbox and DockerSandboxProvider.

All subprocess.run calls are mocked — no Docker daemon required.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from prax.core.sandbox.docker import DockerSandbox, DockerSandboxProvider


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_sandbox(container_id: str = "abc123") -> DockerSandbox:
    return DockerSandbox(sandbox_id="s1", container_id=container_id)


def _ok(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


# ── DockerSandbox.execute_command ─────────────────────────────────────────────


def test_execute_command_returns_stdout() -> None:
    sb = _make_sandbox()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok(stdout="hello\n")) as mock_run:
            result = sb.execute_command("echo hello")

    assert "hello" in result
    mock_run.assert_called_once()


def test_execute_command_appends_stderr_when_present() -> None:
    sb = _make_sandbox()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok(stdout="out", stderr="err", returncode=0)):
            result = sb.execute_command("cmd")

    assert "out" in result
    assert "err" in result or "Stderr" in result


def test_execute_command_appends_exit_code_on_failure() -> None:
    sb = _make_sandbox()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok(stdout="", returncode=1)):
            result = sb.execute_command("bad")

    assert "Exit code: 1" in result


def test_execute_command_timeout_returns_error_message() -> None:
    sb = _make_sandbox()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            result = sb.execute_command("slow", timeout=5)

    assert "timed out" in result


def test_execute_command_no_output_returns_placeholder() -> None:
    sb = _make_sandbox()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok(stdout="")):
            result = sb.execute_command("noop")

    assert result == "(no output)"


# ── DockerSandbox.read_file ───────────────────────────────────────────────────


def test_read_file_returns_content() -> None:
    sb = _make_sandbox()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok(stdout="file content")):
            result = sb.read_file("/workspace/file.txt")

    assert result == "file content"


def test_read_file_raises_oserror_on_failure() -> None:
    sb = _make_sandbox()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch(
            "subprocess.run", return_value=_ok(stderr="no such file", returncode=1)
        ):
            with pytest.raises(OSError):
                sb.read_file("/missing/file.txt")


# ── DockerSandbox.write_file ──────────────────────────────────────────────────


def test_write_file_runs_cp_command() -> None:
    sb = _make_sandbox("ctr1")
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            sb.write_file("/workspace/out.txt", "content")

    # mkdir + cp = at least 2 calls
    assert mock_run.call_count >= 2
    all_args = [str(c) for c in mock_run.call_args_list]
    assert any("cp" in a for a in all_args)


def test_write_file_append_uses_cat_append() -> None:
    sb = _make_sandbox("ctr1")
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            sb.write_file("/workspace/out.txt", "more content", append=True)

    all_args = [str(c) for c in mock_run.call_args_list]
    assert any(">>" in a or "cat" in a for a in all_args)


# ── DockerSandbox.list_dir ────────────────────────────────────────────────────


def test_list_dir_returns_entries() -> None:
    sb = _make_sandbox()
    output = "/workspace\n/workspace/a.py\n/workspace/b.py\n"
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok(stdout=output)):
            result = sb.list_dir("/workspace")

    assert "/workspace/a.py" in result
    assert "/workspace/b.py" in result


def test_list_dir_returns_error_string_on_failure() -> None:
    sb = _make_sandbox()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch(
            "subprocess.run",
            return_value=_ok(stderr="permission denied", returncode=1),
        ):
            result = sb.list_dir("/root")

    assert len(result) == 1
    assert result[0].startswith("Error:")


# ── DockerSandboxProvider.is_available ────────────────────────────────────────


def test_provider_is_available_true_when_docker_runs() -> None:
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok(returncode=0)):
            assert DockerSandboxProvider.is_available() is True


def test_provider_is_available_false_when_docker_not_on_path() -> None:
    with patch("shutil.which", return_value=None):
        assert DockerSandboxProvider.is_available() is False


def test_provider_is_available_false_when_docker_info_fails() -> None:
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok(returncode=1)):
            assert DockerSandboxProvider.is_available() is False


def test_provider_is_available_false_on_exception() -> None:
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", side_effect=Exception("daemon down")):
            assert DockerSandboxProvider.is_available() is False


# ── DockerSandboxProvider.acquire / get / release ─────────────────────────────


def test_provider_acquire_returns_sandbox_id() -> None:
    provider = DockerSandboxProvider()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch(
            "subprocess.run", return_value=_ok(stdout="container-abc\n")
        ):
            sid = provider.acquire("mysandbox")

    assert sid == "mysandbox"
    assert provider.get("mysandbox") is not None


def test_provider_acquire_idempotent_second_call() -> None:
    """Acquiring the same sandbox_id twice returns the same id without creating
    a new container."""
    provider = DockerSandboxProvider()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch(
            "subprocess.run", return_value=_ok(stdout="container-id\n")
        ) as mock_run:
            provider.acquire("box1")
            run_count_after_first = mock_run.call_count
            provider.acquire("box1")
            # Second acquire must not call subprocess.run again
            assert mock_run.call_count == run_count_after_first


def test_provider_release_removes_sandbox() -> None:
    provider = DockerSandboxProvider()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok(stdout="cid\n")):
            provider.acquire("s1")

        with patch("subprocess.run", return_value=_ok()):
            provider.release("s1")

    assert provider.get("s1") is None


def test_provider_shutdown_releases_all_sandboxes() -> None:
    provider = DockerSandboxProvider()
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", return_value=_ok(stdout="cid\n")):
            provider.acquire("a")
            provider.acquire("b")

        with patch("subprocess.run", return_value=_ok()):
            provider.shutdown()

    assert provider.get("a") is None
    assert provider.get("b") is None
