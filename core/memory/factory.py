"""Memory backend factory — selects implementation from config.

Priority (from .prax/config.yaml [memory.backend]):

  1. "openviking"          → OpenVikingBackend, falls back to local if unreachable
  2. "local" (default)     → LocalMemoryBackend
  3. "my.module.MyBackend" → loaded via importlib reflection, must subclass MemoryBackend

Config example (.prax/config.yaml or ~/.prax/config.yaml)::

    memory:
      backend: local          # "local" | "openviking" | dotted.class.Path

      openviking:
        host: localhost
        port: 50051
        ping_timeout_seconds: 2

      local:
        max_facts: 100
        fact_confidence_threshold: 0.7
        max_experiences: 500
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any

from .backend import MemoryBackend

logger = logging.getLogger(__name__)

# ── Global singleton (one backend per process, reset-able for tests) ─────────

_instance: MemoryBackend | None = None
_lock = threading.Lock()


def get_memory_backend(config: dict[str, Any] | None = None) -> MemoryBackend:
    """Return the process-wide MemoryBackend singleton.

    On first call the backend is constructed from *config*.
    Subsequent calls return the cached instance regardless of *config*.
    Call :func:`reset_memory_backend` to force re-initialisation (e.g. in tests).
    """
    global _instance
    if _instance is not None:
        return _instance

    with _lock:
        if _instance is not None:
            return _instance
        _instance = _build_backend(config or {})
    return _instance


def reset_memory_backend() -> None:
    """Destroy the singleton so the next call to get_memory_backend() rebuilds it.

    Useful in tests and when the user changes memory config at runtime.
    """
    global _instance
    with _lock:
        _instance = None


# ── Builder ───────────────────────────────────────────────────────────────────


def _build_backend(config: dict[str, Any]) -> MemoryBackend:
    memory_cfg: dict[str, Any] = config.get("memory", {})
    backend_key: str = memory_cfg.get("backend", "local")

    if backend_key == "openviking":
        return _try_openviking(memory_cfg) or _build_local(memory_cfg)

    if backend_key == "local":
        return _build_local(memory_cfg)

    if backend_key == "sqlite":
        return _build_sqlite(memory_cfg)

    # Reflection path (e.g. "mypackage.mymodule.MyBackend")
    return _load_custom(backend_key, memory_cfg) or _build_local(memory_cfg)


def _build_local(memory_cfg: dict[str, Any]) -> MemoryBackend:
    from .local_backend import LocalMemoryBackend

    local_cfg: dict[str, Any] = memory_cfg.get("local", {})
    backend = LocalMemoryBackend(
        max_facts=int(local_cfg.get("max_facts", 100)),
        fact_confidence_threshold=float(
            local_cfg.get("fact_confidence_threshold", 0.7)
        ),
        max_experiences=int(local_cfg.get("max_experiences", 500)),
    )
    logger.debug("Memory backend: LocalMemoryBackend")
    return backend


def _build_sqlite(memory_cfg: dict[str, Any]) -> MemoryBackend:
    from .sqlite_backend import SQLiteMemoryBackend

    sqlite_cfg: dict[str, Any] = memory_cfg.get("sqlite", {})
    backend = SQLiteMemoryBackend(
        max_facts=int(sqlite_cfg.get("max_facts", 100)),
        fact_confidence_threshold=float(sqlite_cfg.get("fact_confidence_threshold", 0.7)),
        max_experiences=int(sqlite_cfg.get("max_experiences", 500)),
    )
    logger.debug("Memory backend: SQLiteMemoryBackend")
    return backend


def _try_openviking(memory_cfg: dict[str, Any]) -> MemoryBackend | None:
    """Try to build an OpenVikingBackend; return None on failure."""
    try:
        from .openviking_backend import OpenVikingBackend

        ov_cfg: dict[str, Any] = memory_cfg.get("openviking", {})
        backend = OpenVikingBackend(
            host=str(ov_cfg.get("host", "localhost")),
            port=int(ov_cfg.get("port", 50051)),
            ping_timeout_seconds=float(ov_cfg.get("ping_timeout_seconds", 2.0)),
        )
        if backend.verified:
            logger.info(
                "Memory backend: OpenVikingBackend (%s:%d)",
                ov_cfg.get("host", "localhost"),
                int(ov_cfg.get("port", 50051)),
            )
            return backend

        logger.warning(
            "OpenViking unreachable — falling back to LocalMemoryBackend"
        )
        return None
    except Exception as e:
        logger.warning(
            "OpenVikingBackend init failed: %s — falling back to LocalMemoryBackend", e
        )
        return None


def _load_custom(class_path: str, memory_cfg: dict[str, Any]) -> MemoryBackend | None:
    """Load a custom MemoryBackend subclass via reflection."""
    try:
        module_path, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)

        if not (isinstance(cls, type) and issubclass(cls, MemoryBackend)):
            raise TypeError(
                f"{class_path!r} is not a MemoryBackend subclass"
            )

        instance: MemoryBackend = cls()
        logger.info("Memory backend: %s (custom)", class_path)
        return instance
    except Exception as e:
        logger.error(
            "Failed to load custom memory backend %r: %s — falling back to LocalMemoryBackend",
            class_path,
            e,
        )
        return None
