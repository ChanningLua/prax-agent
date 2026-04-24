"""Sandbox abstraction layer for isolated command execution.

Provides a pluggable sandbox interface so tools can execute commands in
different isolation environments without changing their implementation.

Supported backends:
  local   — direct host execution (no isolation, for trusted dev environments)
  docker  — Docker container isolation (requires docker CLI)

Usage::

    from prax.core.sandbox import get_sandbox_provider

    provider = get_sandbox_provider()
    sandbox_id = provider.acquire()
    sandbox = provider.get(sandbox_id)
    output = sandbox.execute_command("ls -la")
    provider.release(sandbox_id)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SandboxResult:
    output: str
    exit_code: int
    timed_out: bool = False


class Sandbox(ABC):
    """Abstract sandbox environment."""

    def __init__(self, sandbox_id: str) -> None:
        self._id = sandbox_id

    @property
    def id(self) -> str:
        return self._id

    @abstractmethod
    def execute_command(self, command: str, timeout: int = 60) -> str:
        """Execute a shell command and return combined stdout+stderr."""

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read a text file from the sandbox filesystem."""

    @abstractmethod
    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """Write text content to a file in the sandbox filesystem."""

    @abstractmethod
    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        """List directory contents up to max_depth levels."""

    def execute_command_v2(self, command: str, timeout: int = 60) -> SandboxResult:
        """Execute with structured result. Subclasses should override."""
        output = self.execute_command(command, timeout=timeout)
        timed_out = output.startswith("Error: command timed out")
        exit_code = 0
        if "Exit code: " in output:
            try:
                exit_code = int(output.rsplit("Exit code: ", 1)[1].strip())
            except (ValueError, IndexError):
                exit_code = 1
        if timed_out:
            exit_code = -1
        return SandboxResult(output=output, exit_code=exit_code, timed_out=timed_out)


class SandboxProvider(ABC):
    """Abstract factory for sandbox lifecycle management."""

    @abstractmethod
    def acquire(self, sandbox_id: str | None = None) -> str:
        """Create or reuse a sandbox and return its ID."""

    @abstractmethod
    def get(self, sandbox_id: str) -> Sandbox | None:
        """Retrieve an existing sandbox by ID."""

    @abstractmethod
    def release(self, sandbox_id: str) -> None:
        """Destroy a sandbox and free its resources."""

    def shutdown(self) -> None:
        """Release all sandboxes (called on process exit)."""
