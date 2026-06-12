"""CLI adapter registry — Claude Code, Codex, OpenCode.

PRD §4 L2. Each adapter knows how to format a delegation packet (§7.2) for
its target CLI. The conductor dispatches via `get_adapter(role_config)`.
"""

from __future__ import annotations

import json
import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class DelegationPacket:
    role: str
    objective: str
    context_paths: list[str]
    allowed_tools: list[str]
    write_scope: list[str]
    output_schema: str
    max_steps: int
    timeout_seconds: int
    budget_cents: int
    extra: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "role": self.role,
                "objective": self.objective,
                "context_paths": self.context_paths,
                "allowed_tools": self.allowed_tools,
                "write_scope": self.write_scope,
                "output_schema": self.output_schema,
                "max_steps": self.max_steps,
                "timeout_seconds": self.timeout_seconds,
                "budget_cents": self.budget_cents,
                **self.extra,
            },
            indent=2,
        )


class CLIAdapter(ABC):
    """Abstract base for CLI tool adapters."""

    name: str = "abstract"

    @abstractmethod
    def build_invocation(
        self, packet: DelegationPacket, prompt: str
    ) -> list[str]:
        """Return the argv to exec, including the prompt."""


class ClaudeCodeAdapter(CLIAdapter):
    """Adapter for the `claude` CLI (Claude Code)."""

    name = "claude_code"

    def build_invocation(
        self, packet: DelegationPacket, prompt: str
    ) -> list[str]:
        return [
            "claude",
            "--print",
            "--tools", ",".join(packet.allowed_tools),
            "--max-steps", str(packet.max_steps),
            prompt,
        ]


class CodexAdapter(CLIAdapter):
    """Adapter for the `codex` CLI (Codex CLI)."""

    name = "codex"

    def build_invocation(
        self, packet: DelegationPacket, prompt: str
    ) -> list[str]:
        return [
            "codex",
            "exec",
            "--max-steps", str(packet.max_steps),
            prompt,
        ]


class OpenCodeAdapter(CLIAdapter):
    """Adapter for the `opencode` CLI."""

    name = "opencode"

    def build_invocation(
        self, packet: DelegationPacket, prompt: str
    ) -> list[str]:
        return [
            "opencode",
            "run",
            "--max-steps", str(packet.max_steps),
            prompt,
        ]


_REGISTRY: dict[str, type[CLIAdapter]] = {
    "claude_code": ClaudeCodeAdapter,
    "codex": CodexAdapter,
    "opencode": OpenCodeAdapter,
}


def get_adapter(name: str) -> CLIAdapter:
    cls = _REGISTRY.get(name)
    if not cls:
        raise ValueError(f"Unknown CLI adapter: {name!r}")
    return cls()


def list_adapters() -> list[str]:
    return sorted(_REGISTRY.keys())


def quote_invocation(argv: list[str]) -> str:
    """Return a shell-safe single-line string for the invocation."""
    return " ".join(shlex.quote(a) for a in argv)
