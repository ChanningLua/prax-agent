"""Sandbox provider singleton factory."""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

from .base import SandboxProvider

logger = logging.getLogger(__name__)

_provider: SandboxProvider | None = None
_lock = threading.Lock()


def get_sandbox_provider(
    backend: str | None = None,
    **kwargs: Any,
) -> SandboxProvider:
    """Return the process-wide SandboxProvider singleton.

    Backend selection order:
      1. ``backend`` argument
      2. ``PRAX_SANDBOX_BACKEND`` environment variable
      3. ``docker`` if Docker daemon is reachable
      4. ``local`` fallback

    Args:
        backend: ``"local"`` or ``"docker"``
        **kwargs: Forwarded to the provider constructor
    """
    global _provider
    if _provider is not None:
        return _provider

    with _lock:
        if _provider is not None:
            return _provider

        chosen = backend or os.environ.get("PRAX_SANDBOX_BACKEND", "auto")

        if chosen == "docker":
            _provider = _make_docker(**kwargs)
        elif chosen == "local":
            _provider = _make_local(**kwargs)
        else:  # auto
            sandbox_policy = os.environ.get("PRAX_SANDBOX_POLICY", "fail_open")
            from .docker import DockerSandboxProvider
            if DockerSandboxProvider.is_available():
                _provider = _make_docker(**kwargs)
            elif sandbox_policy == "fail_closed":
                raise RuntimeError(
                    "Docker sandbox is not available and PRAX_SANDBOX_POLICY=fail_closed. "
                    "Install Docker or set PRAX_SANDBOX_BACKEND=local to allow local fallback."
                )
            else:
                # Surfaced at INFO so a first-time user (no Docker installed)
                # doesn't see a red WARNING on every single run. Operators who
                # care about enforcement can still get visibility via
                # PRAX_SANDBOX_POLICY=fail_closed or by raising log level.
                logger.info(
                    "Docker sandbox unavailable — falling back to local sandbox. "
                    "Set PRAX_SANDBOX_BACKEND=docker to enforce Docker, "
                    "or PRAX_SANDBOX_POLICY=fail_closed to prevent local fallback."
                )
                _provider = _make_local(**kwargs)

    return _provider


def _make_docker(**kwargs: Any) -> SandboxProvider:
    from .docker import DockerSandboxProvider
    docker_kwargs = dict(kwargs)
    cwd = docker_kwargs.pop("cwd", None)
    if cwd and "workdir" not in docker_kwargs:
        docker_kwargs["workdir"] = cwd
    return DockerSandboxProvider(**docker_kwargs)


def _make_local(**kwargs: Any) -> SandboxProvider:
    from .local import LocalSandboxProvider
    return LocalSandboxProvider(**kwargs)


def reset_sandbox_provider() -> None:
    """Reset singleton (for tests or config changes)."""
    global _provider
    with _lock:
        if _provider is not None:
            _provider.shutdown()
        _provider = None


def set_sandbox_provider(provider: SandboxProvider) -> None:
    """Inject a custom provider (useful for testing)."""
    global _provider
    with _lock:
        _provider = provider
