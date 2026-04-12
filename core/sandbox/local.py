"""Local sandbox — direct host execution, no isolation.

Suitable for trusted development environments where the agent runs
with the same permissions as the user.  For production use, prefer
DockerSandbox which provides process and filesystem isolation.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .base import Sandbox, SandboxProvider, SandboxResult


class LocalSandbox(Sandbox):
    """Executes commands directly on the host filesystem."""

    def __init__(self, sandbox_id: str, cwd: str | None = None) -> None:
        super().__init__(sandbox_id)
        self.cwd = cwd or str(Path.cwd())

    @staticmethod
    def _get_shell() -> str:
        for candidate in ("/bin/zsh", "/bin/bash", "/bin/sh"):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        found = shutil.which("sh")
        if found:
            return found
        raise RuntimeError("No shell found on PATH")

    def execute_command(self, command: str, timeout: int = 60) -> str:
        shell = self._get_shell()
        try:
            result = subprocess.run(
                command,
                executable=shell,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.cwd,
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"

        output = result.stdout
        if result.stderr:
            output += ("\nStderr:\n" + result.stderr) if output else result.stderr
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"
        return output or "(no output)"

    def execute_command_v2(self, command: str, timeout: int = 60) -> SandboxResult:
        shell = self._get_shell()
        try:
            result = subprocess.run(
                command,
                executable=shell,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.cwd,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                output=f"Error: command timed out after {timeout}s",
                exit_code=-1,
                timed_out=True,
            )
        output = result.stdout
        if result.stderr:
            output += ("\nStderr:\n" + result.stderr) if output else result.stderr
        return SandboxResult(
            output=output or "(no output)",
            exit_code=result.returncode,
        )

    def read_file(self, path: str) -> str:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(exc.errno, exc.strerror, path) from None

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        try:
            p.open(mode, encoding="utf-8").write(content)
        except OSError as exc:
            raise OSError(exc.errno, exc.strerror, path) from None

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        entries: list[str] = []
        base = Path(path)
        if not base.is_dir():
            return [f"Error: {path} is not a directory"]

        def _walk(p: Path, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                for child in sorted(p.iterdir()):
                    entries.append(str(child))
                    if child.is_dir():
                        _walk(child, depth + 1)
            except PermissionError:
                pass

        _walk(base, 1)
        return entries


class LocalSandboxProvider(SandboxProvider):
    """Manages a pool of LocalSandbox instances."""

    def __init__(self, cwd: str | None = None) -> None:
        self._cwd = cwd
        self._sandboxes: dict[str, LocalSandbox] = {}

    def acquire(self, sandbox_id: str | None = None) -> str:
        import uuid
        sid = sandbox_id or f"local_{uuid.uuid4().hex[:8]}"
        if sid not in self._sandboxes:
            self._sandboxes[sid] = LocalSandbox(sid, cwd=self._cwd)
        return sid

    def get(self, sandbox_id: str) -> LocalSandbox | None:
        return self._sandboxes.get(sandbox_id)

    def release(self, sandbox_id: str) -> None:
        self._sandboxes.pop(sandbox_id, None)

    def shutdown(self) -> None:
        self._sandboxes.clear()
