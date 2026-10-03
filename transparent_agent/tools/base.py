"""Shared tool infrastructure: result types, workspace path safety, truncation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class ToolError(Exception):
    """An expected tool failure (bad arguments, path outside workspace, ...).

    Its message is returned to the model as a normal tool observation.
    """


@dataclass
class ToolResult:
    output: str  # what the model sees
    is_error: bool = False
    display: str | None = None  # short form for interfaces (defaults to output)
    raw_output: str | None = None  # full output before model-context truncation
    metadata: dict = field(default_factory=dict)

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(output=message, is_error=True)


class ToolInterrupted(KeyboardInterrupt):
    """Cancellation carrying the output collected before process cleanup."""

    def __init__(self, result: ToolResult):
        self.result = result


@dataclass
class ToolContext:
    """Everything a tool needs from the outside world."""

    workspace: Path
    bash_timeout: float = 120.0
    max_bash_timeout: float = 600.0
    max_output_chars: int = 30_000


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[[dict, ToolContext], ToolResult]


def resolve_in_workspace(ctx: ToolContext, raw: Any) -> Path:
    """Resolve a tool path against the workspace, refusing to escape it.

    Symlinks are resolved, so a link inside the workspace pointing outside is
    rejected too. Works for paths that do not exist yet (parents are still
    checked).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("argument 'path' must be a non-empty string")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = ctx.workspace / candidate
    resolved = candidate.resolve()
    workspace = ctx.workspace.resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise ToolError(
            f"path {raw!r} is outside the workspace ({workspace}); "
            "all tool paths must stay inside the workspace"
        )
    return resolved


def require_str(args: dict, name: str, *, allow_empty: bool = False) -> str:
    value = args.get(name)
    if not isinstance(value, str):
        raise ToolError(f"missing required string argument '{name}'")
    if not allow_empty and not value.strip():
        raise ToolError(f"argument '{name}' must not be empty")
    return value


def rel_path(workspace: Path, path: Path) -> str:
    """Path as shown to the model: relative to the workspace when possible."""
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def truncate_middle(text: str, limit: int) -> str:
    """Keep the beginning and the end; announce the omission in the middle."""
    if len(text) <= limit or limit <= 0:
        return text
    if limit < 48:
        return "[truncated]"[:limit]
    # Reserve room for the omission marker as well as the retained text.
    budget = limit - len(f"\n... [{len(text)} characters omitted] ...\n")
    head = int(budget * 0.6)
    tail = budget - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n... [{omitted} characters omitted] ...\n{text[-tail:]}"
