"""Docker sandbox — container-isolated command execution.

Requires Docker CLI to be installed and the daemon to be running.
Each sandbox maps to a dedicated container with configurable resource limits.

Configuration via environment variables:
  PRAX_SANDBOX_IMAGE   — base image (default: python:3.12-slim)
  PRAX_SANDBOX_MEM     — memory limit (default: 512m)
  PRAX_SANDBOX_CPUS    — CPU quota (default: 1.0)
  PRAX_SANDBOX_TIMEOUT — default command timeout in seconds (default: 60)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import Sandbox, SandboxProvider

_DEFAULT_IMAGE = os.environ.get("PRAX_SANDBOX_IMAGE", "python:3.12-slim")
_DEFAULT_MEM = os.environ.get("PRAX_SANDBOX_MEM", "512m")
_DEFAULT_CPUS = os.environ.get("PRAX_SANDBOX_CPUS", "1.0")


def _docker() -> str:
    found = shutil.which("docker")
    if not found:
        raise RuntimeError("docker not found on PATH — install Docker to use DockerSandbox")
    return found


class DockerSandbox(Sandbox):
    """Runs commands inside a Docker container."""

    def __init__(
        self,
        sandbox_id: str,
        container_id: str,
        workdir: str = "/workspace",
    ) -> None:
        super().__init__(sandbox_id)
        self._container_id = container_id
        self._workdir = workdir

    @property
    def container_id(self) -> str:
        return self._container_id

    def execute_command(self, command: str, timeout: int = 60) -> str:
        docker = _docker()
        try:
            result = subprocess.run(
                [docker, "exec", self._container_id, "/bin/sh", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"

        output = result.stdout
        if result.stderr:
            output += ("\nStderr:\n" + result.stderr) if output else result.stderr
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"
        return output or "(no output)"

    def read_file(self, path: str) -> str:
        docker = _docker()
        result = subprocess.run(
            [docker, "exec", self._container_id, "cat", path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise OSError(0, result.stderr.strip() or "read failed", path)
        return result.stdout

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        docker = _docker()
        # Write to a temp file then copy into the container
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tmp", delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp = f.name
        try:
            # Ensure parent directory exists
            parent = str(Path(path).parent)
            subprocess.run(
                [docker, "exec", self._container_id, "mkdir", "-p", parent],
                check=True, capture_output=True,
            )
            if append:
                # Append via exec
                subprocess.run(
                    [docker, "exec", "-i", self._container_id, "/bin/sh", "-c", f"cat >> {path}"],
                    input=content, text=True, check=True, capture_output=True,
                )
            else:
                subprocess.run(
                    [docker, "cp", tmp, f"{self._container_id}:{path}"],
                    check=True, capture_output=True,
                )
        finally:
            os.unlink(tmp)

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        docker = _docker()
        result = subprocess.run(
            [docker, "exec", self._container_id, "find", path,
             "-maxdepth", str(max_depth), "-not", "-path", "*/.*"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return [f"Error: {result.stderr.strip()}"]
        return [line for line in result.stdout.splitlines() if line]


class DockerSandboxProvider(SandboxProvider):
    """Creates and manages Docker containers as sandboxes."""

    def __init__(
        self,
        image: str = _DEFAULT_IMAGE,
        mem_limit: str = _DEFAULT_MEM,
        cpus: str = _DEFAULT_CPUS,
        workdir: str = "/workspace",
    ) -> None:
        self._image = image
        self._mem_limit = mem_limit
        self._cpus = cpus
        self._workdir = workdir
        self._sandboxes: dict[str, DockerSandbox] = {}

    @staticmethod
    def is_available() -> bool:
        """Return True if Docker daemon is reachable."""
        docker = shutil.which("docker")
        if not docker:
            return False
        try:
            result = subprocess.run(
                [docker, "info"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def acquire(self, sandbox_id: str | None = None) -> str:
        import uuid
        sid = sandbox_id or f"prax_{uuid.uuid4().hex[:8]}"
        if sid in self._sandboxes:
            return sid

        docker = _docker()
        result = subprocess.run(
            [
                docker, "run", "-d",
                "--name", sid,
                "--memory", self._mem_limit,
                "--cpus", self._cpus,
                "--workdir", self._workdir,
                "--rm",
                self._image,
                "sleep", "infinity",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr.strip()}")

        container_id = result.stdout.strip()
        self._sandboxes[sid] = DockerSandbox(sid, container_id, self._workdir)
        return sid

    def get(self, sandbox_id: str) -> DockerSandbox | None:
        return self._sandboxes.get(sandbox_id)

    def release(self, sandbox_id: str) -> None:
        sandbox = self._sandboxes.pop(sandbox_id, None)
        if sandbox is None:
            return
        docker = shutil.which("docker")
        if docker:
            subprocess.run(
                [docker, "rm", "-f", sandbox.container_id],
                capture_output=True, timeout=15,
            )

    def shutdown(self) -> None:
        for sid in list(self._sandboxes):
            self.release(sid)
