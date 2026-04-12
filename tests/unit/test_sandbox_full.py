"""Unit tests for sandbox providers.

Covers:
- prax/core/sandbox/base.py  – SandboxResult, Sandbox abstract helpers
- prax/core/sandbox/local.py – LocalSandbox / LocalSandboxProvider
- prax/core/sandbox/docker.py – DockerSandbox / DockerSandboxProvider
All subprocess calls are mocked; no real processes are launched.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from prax.core.sandbox.base import Sandbox, SandboxProvider, SandboxResult
from prax.core.sandbox.local import LocalSandbox, LocalSandboxProvider
from prax.core.sandbox.docker import DockerSandbox, DockerSandboxProvider


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    cp = MagicMock()
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


# ── base.py ──────────────────────────────────────────────────────────────────

class ConcreteProvider(SandboxProvider):
    def acquire(self, sandbox_id=None):
        return "sid"
    def get(self, sandbox_id):
        return None
    def release(self, sandbox_id):
        pass


class ConcreteSandbox(Sandbox):
    def execute_command(self, command, timeout=60):
        return "ok"
    def read_file(self, path):
        return ""
    def write_file(self, path, content, append=False):
        pass
    def list_dir(self, path, max_depth=2):
        return []


class TestSandboxResult:
    def test_defaults(self) -> None:
        r = SandboxResult(output="hello", exit_code=0)
        assert r.timed_out is False

    def test_timed_out_flag(self) -> None:
        r = SandboxResult(output="Error: command timed out", exit_code=-1, timed_out=True)
        assert r.timed_out is True


class TestSandboxBaseExecuteV2:
    def test_success_output(self) -> None:
        sb = ConcreteSandbox("test-id")
        result = sb.execute_command_v2("ls")
        assert result.output == "ok"
        assert result.exit_code == 0
        assert not result.timed_out

    def test_parses_exit_code_from_output(self) -> None:
        class ErrSandbox(ConcreteSandbox):
            def execute_command(self, command, timeout=60):
                return "something\nExit code: 1"
        sb = ErrSandbox("err")
        result = sb.execute_command_v2("bad")
        assert result.exit_code == 1

    def test_timed_out_detection(self) -> None:
        class TimeoutSandbox(ConcreteSandbox):
            def execute_command(self, command, timeout=60):
                return "Error: command timed out after 60s"
        sb = TimeoutSandbox("t")
        result = sb.execute_command_v2("slow")
        assert result.timed_out
        assert result.exit_code == -1


class TestSandboxProviderShutdown:
    def test_shutdown_is_callable(self) -> None:
        provider = ConcreteProvider()
        provider.shutdown()  # default impl; no exception


# ── local.py ─────────────────────────────────────────────────────────────────

class TestLocalSandbox:
    def test_id_property(self) -> None:
        sb = LocalSandbox("my-id")
        assert sb.id == "my-id"

    def test_execute_command_success(self) -> None:
        with patch("subprocess.run", return_value=_make_completed("hello\n")):
            sb = LocalSandbox("sid", cwd="/tmp")
            out = sb.execute_command("echo hello")
        assert "hello" in out

    def test_execute_command_appends_stderr(self) -> None:
        with patch("subprocess.run", return_value=_make_completed("out", "err", 0)):
            sb = LocalSandbox("sid", cwd="/tmp")
            out = sb.execute_command("cmd")
        assert "err" in out

    def test_execute_command_timeout(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
            sb = LocalSandbox("sid", cwd="/tmp")
            out = sb.execute_command("slow", timeout=30)
        assert "timed out" in out

    def test_execute_command_v2_success(self) -> None:
        with patch("subprocess.run", return_value=_make_completed("result", "", 0)):
            sb = LocalSandbox("sid", cwd="/tmp")
            sr = sb.execute_command_v2("echo")
        assert sr.exit_code == 0
        assert not sr.timed_out
        assert "result" in sr.output

    def test_execute_command_v2_timeout(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            sb = LocalSandbox("sid", cwd="/tmp")
            sr = sb.execute_command_v2("slow", timeout=5)
        assert sr.timed_out
        assert sr.exit_code == -1

    def test_read_file(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("world", encoding="utf-8")
        sb = LocalSandbox("sid")
        assert sb.read_file(str(f)) == "world"

    def test_read_file_missing_raises(self) -> None:
        sb = LocalSandbox("sid")
        with pytest.raises(OSError):
            sb.read_file("/no/such/file.txt")

    def test_write_file_creates(self, tmp_path: Path) -> None:
        sb = LocalSandbox("sid")
        target = str(tmp_path / "sub" / "out.txt")
        sb.write_file(target, "content")
        assert Path(target).read_text() == "content"

    def test_write_file_append(self, tmp_path: Path) -> None:
        target = tmp_path / "app.txt"
        target.write_text("hello", encoding="utf-8")
        sb = LocalSandbox("sid")
        sb.write_file(str(target), " world", append=True)
        assert target.read_text() == "hello world"

    def test_list_dir_not_a_dir(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("x")
        sb = LocalSandbox("sid")
        result = sb.list_dir(str(f))
        assert result[0].startswith("Error:")

    def test_list_dir_returns_children(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        sb = LocalSandbox("sid")
        result = sb.list_dir(str(tmp_path))
        paths = [str(tmp_path / "a.txt"), str(tmp_path / "b.txt")]
        for p in paths:
            assert p in result


class TestLocalSandboxProvider:
    def test_acquire_returns_id(self) -> None:
        provider = LocalSandboxProvider()
        sid = provider.acquire()
        assert sid.startswith("local_")

    def test_acquire_with_explicit_id(self) -> None:
        provider = LocalSandboxProvider()
        sid = provider.acquire("my-sandbox")
        assert sid == "my-sandbox"

    def test_acquire_reuses_existing(self) -> None:
        provider = LocalSandboxProvider()
        sid1 = provider.acquire("same")
        sid2 = provider.acquire("same")
        assert sid1 == sid2
        assert provider.get("same") is provider.get("same")

    def test_get_returns_none_for_unknown(self) -> None:
        provider = LocalSandboxProvider()
        assert provider.get("unknown") is None

    def test_release_removes_sandbox(self) -> None:
        provider = LocalSandboxProvider()
        sid = provider.acquire()
        provider.release(sid)
        assert provider.get(sid) is None

    def test_shutdown_clears_all(self) -> None:
        provider = LocalSandboxProvider()
        provider.acquire("a")
        provider.acquire("b")
        provider.shutdown()
        assert provider.get("a") is None
        assert provider.get("b") is None


# ── docker.py ────────────────────────────────────────────────────────────────

class TestDockerSandbox:
    def _make_sandbox(self) -> DockerSandbox:
        return DockerSandbox("sid", "container123", "/workspace")

    def test_container_id_property(self) -> None:
        sb = self._make_sandbox()
        assert sb.container_id == "container123"

    def test_execute_command_success(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("output\n", "", 0)):
                sb = self._make_sandbox()
                out = sb.execute_command("ls")
        assert "output" in out

    def test_execute_command_timeout(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker exec", 10)):
                sb = self._make_sandbox()
                out = sb.execute_command("slow", timeout=10)
        assert "timed out" in out

    def test_execute_command_nonzero_exit_appends_code(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("out", "err", 1)):
                sb = self._make_sandbox()
                out = sb.execute_command("failing")
        assert "Exit code: 1" in out

    def test_read_file_success(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("file content", "", 0)):
                sb = self._make_sandbox()
                content = sb.read_file("/etc/hosts")
        assert content == "file content"

    def test_read_file_raises_on_failure(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("", "not found", 1)):
                sb = self._make_sandbox()
                with pytest.raises(OSError):
                    sb.read_file("/no/such")

    def test_list_dir_success(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("/workspace\n/workspace/a\n")):
                sb = self._make_sandbox()
                entries = sb.list_dir("/workspace")
        assert "/workspace" in entries

    def test_list_dir_error(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("", "no such dir", 1)):
                sb = self._make_sandbox()
                entries = sb.list_dir("/bad")
        assert entries[0].startswith("Error:")


class TestDockerSandboxProvider:
    def test_is_available_false_when_no_docker(self) -> None:
        with patch("shutil.which", return_value=None):
            assert DockerSandboxProvider.is_available() is False

    def test_is_available_false_when_daemon_down(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("", "error", 1)):
                assert DockerSandboxProvider.is_available() is False

    def test_is_available_true_when_daemon_up(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("Server:", "", 0)):
                assert DockerSandboxProvider.is_available() is True

    def test_acquire_creates_container(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("abc123\n", "", 0)):
                provider = DockerSandboxProvider()
                sid = provider.acquire("my-box")
        assert sid == "my-box"
        assert provider.get("my-box") is not None
        assert provider.get("my-box").container_id == "abc123"

    def test_acquire_reuses_existing(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("cid\n", "", 0)):
                provider = DockerSandboxProvider()
                sid1 = provider.acquire("box")
            sid2 = provider.acquire("box")  # should not call docker run again
        assert sid1 == sid2

    def test_acquire_raises_on_docker_failure(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("", "daemon not running", 1)):
                provider = DockerSandboxProvider()
                with pytest.raises(RuntimeError, match="Failed to start container"):
                    provider.acquire("failing-box")

    def test_get_returns_none_for_unknown(self) -> None:
        provider = DockerSandboxProvider()
        assert provider.get("ghost") is None

    def test_release_calls_docker_rm(self) -> None:
        # Acquire a sandbox first
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("cid\n")) as mock_run:
                provider = DockerSandboxProvider()
                provider.acquire("test-box")
                provider.release("test-box")
        # At least two calls: docker run + docker rm
        assert mock_run.call_count >= 2

    def test_release_unknown_id_is_noop(self) -> None:
        provider = DockerSandboxProvider()
        provider.release("nonexistent")  # must not raise

    def test_shutdown_releases_all(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=_make_completed("cid\n")):
                provider = DockerSandboxProvider()
                provider.acquire("a")
                provider.acquire("b")
                provider.shutdown()
        assert provider.get("a") is None
        assert provider.get("b") is None
