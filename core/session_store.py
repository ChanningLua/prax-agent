"""Persistent session storage for Prax conversations."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .persistence import atomic_write_json


SESSION_SCHEMA_VERSION = "prax.session.v1"


@dataclass
class SessionData:
    session_id: str
    cwd: str
    model: str | None = None
    messages: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    schema_version: str = SESSION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "model": self.model,
            "messages": self.messages or [],
            "metadata": self.metadata or {},
        }


class FileSessionStore:
    def __init__(self, root_dir: str):
        self._root_dir = Path(root_dir)

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def create_session_id(self) -> str:
        return f"session_{uuid.uuid4().hex[:12]}"

    def session_path(self, session_id: str) -> Path:
        return self._root_dir / f"{session_id}.json"

    def load(self, session_id: str) -> SessionData | None:
        path = self.session_path(session_id)
        if not path.exists():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))
        return SessionData(
            session_id=data["session_id"],
            cwd=data["cwd"],
            model=data.get("model"),
            messages=list(data.get("messages", [])),
            metadata=dict(data.get("metadata", {})),
            schema_version=str(data.get("schema_version", SESSION_SCHEMA_VERSION)),
        )

    def save(self, session: SessionData) -> Path:
        self._root_dir.mkdir(parents=True, exist_ok=True)
        path = self.session_path(session.session_id)
        atomic_write_json(path, session.to_dict())
        return path
