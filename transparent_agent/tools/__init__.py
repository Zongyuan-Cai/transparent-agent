"""Tool registry and executor."""

from __future__ import annotations

import json

from ..models import ToolSpec
from . import fs, search, shell
from .base import Tool, ToolContext, ToolError, ToolResult, truncate_middle

__all__ = ["Tool", "ToolContext", "ToolError", "ToolResult", "ToolRegistry", "build_registry"]


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {tool.name: tool for tool in tools}

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(name=tool.name, description=tool.description, parameters=tool.parameters)
            for tool in self._tools.values()
        ]

    def names(self) -> list[str]:
        return list(self._tools)

    def execute(self, name: str, raw_arguments: str, ctx: ToolContext) -> ToolResult:
        """Execute one tool call.

        Expected failures (bad arguments, missing files, failing commands) come
        back as error results for the model to read. Only KeyboardInterrupt
        (user cancellation) propagates.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.error(
                f"unknown tool {name!r}; available tools: {', '.join(self._tools)}"
            )
        try:
            arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return ToolResult.error(
                f"arguments are not valid JSON ({exc}); received: {raw_arguments[:200]!r}"
            )
        if not isinstance(arguments, dict):
            return ToolResult.error("arguments must be a JSON object")

        try:
            result = tool.run(arguments, ctx)
        except ToolError as exc:
            return ToolResult.error(str(exc))
        except Exception as exc:  # a bug inside a tool must not kill the agent
            return ToolResult.error(f"tool {name!r} crashed: {type(exc).__name__}: {exc}")

        if len(result.output) > ctx.max_output_chars:
            result = ToolResult(
                output=truncate_middle(result.output, ctx.max_output_chars),
                is_error=result.is_error,
            )
        return result


def build_registry() -> ToolRegistry:
    return ToolRegistry([*fs.TOOLS, *search.TOOLS, *shell.TOOLS])
