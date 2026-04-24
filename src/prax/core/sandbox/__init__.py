"""Prax sandbox subsystem.

Provides pluggable isolated execution environments for agent tool calls.

Usage::

    from prax.core.sandbox import get_sandbox_provider

    provider = get_sandbox_provider()          # auto-selects docker or local
    sid = provider.acquire()
    sandbox = provider.get(sid)
    output = sandbox.execute_command("python3 --version")
    provider.release(sid)

Backend selection (in order):
  1. PRAX_SANDBOX_BACKEND env var ("docker" | "local")
  2. Docker if daemon is reachable
  3. Local fallback
"""
from .base import Sandbox, SandboxProvider, SandboxResult
from .local import LocalSandbox, LocalSandboxProvider
from .docker import DockerSandbox, DockerSandboxProvider
from .provider import get_sandbox_provider, reset_sandbox_provider, set_sandbox_provider

__all__ = [
    "Sandbox",
    "SandboxProvider",
    "SandboxResult",
    "LocalSandbox",
    "LocalSandboxProvider",
    "DockerSandbox",
    "DockerSandboxProvider",
    "get_sandbox_provider",
    "reset_sandbox_provider",
    "set_sandbox_provider",
]
