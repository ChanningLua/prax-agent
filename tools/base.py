"""Base tool class — Claude-format compatible."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jsonschema import SchemaError, ValidationError, validate as jsonschema_validate


class PermissionLevel(str, Enum):
    SAFE = "safe"          # auto-approve (Read, Glob, Grep)
    REVIEW = "review"      # show user, approve by default (Write, Edit)
    DANGEROUS = "dangerous" # require explicit confirmation (Bash, rm, etc.)


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


@dataclass
class ToolCall:
    """Represents a tool call extracted from model response (Claude format)."""
    id: str = field(default_factory=lambda: f"toolu_{uuid.uuid4().hex[:12]}")
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolFileAccess:
    path: str
    write: bool = False


class ToolInputValidationError(ValueError):
    """Raised when a tool invocation does not satisfy its declared schema."""


class Tool(ABC):
    """Base class for all Prax tools.

    Follows Claude tool definition format:
    {
        "name": "Read",
        "description": "...",
        "input_schema": { JSON Schema }
    }
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    permission_level: PermissionLevel = PermissionLevel.SAFE
    # Read-only tools that don't modify shared state can run in parallel
    is_concurrency_safe: bool = False

    def to_claude_format(self) -> dict:
        """Convert to Claude API tool definition format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_openai_format(self) -> dict:
        """Convert to OpenAI function calling format (for GLM/GPT)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def required_permission(self, params: dict[str, Any]) -> PermissionLevel:
        """Return the permission level required for this invocation."""
        return self.permission_level

    def file_accesses(self, params: dict[str, Any]) -> list[ToolFileAccess]:
        """Return any file paths this tool intends to access."""
        return []

    def validate_params(self, params: dict[str, Any]) -> None:
        """Validate invocation parameters against the declared JSON schema."""
        try:
            jsonschema_validate(instance=params, schema=self.input_schema)
        except ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path)
            suffix = f" at '{location}'" if location else ""
            raise ToolInputValidationError(
                f"Invalid input for {self.name}{suffix}: {exc.message}"
            ) from exc
        except SchemaError as exc:
            raise ToolInputValidationError(
                f"Invalid schema for {self.name}: {exc.message}"
            ) from exc

    @abstractmethod
    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Execute this tool with given parameters."""
        ...
