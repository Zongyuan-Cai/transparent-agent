"""Tool registry and executor."""

from __future__ import annotations

import json
import copy
import math
import re
from dataclasses import replace

from jsonschema import Draft202012Validator, validators

from ..models import ToolSpec
from . import fs, search, shell
from .base import Tool, ToolContext, ToolError, ToolResult, truncate_middle
from ..storage import PersistenceError


def finite_number(checker, value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


Validator = validators.extend(Draft202012Validator, type_checker=Draft202012Validator.TYPE_CHECKER.redefine("number", finite_number))


def reject_constant(value: str):
    raise ValueError(f"invalid JSON constant {value}")

__all__ = ["Tool", "ToolContext", "ToolError", "ToolResult", "ToolRegistry", "build_registry"]


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if (not isinstance(tool, Tool) or not isinstance(tool.name, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool.name) or tool.name in self._tools):
            raise ValueError(f"invalid or duplicate tool name: {getattr(tool, 'name', None)!r}")
        if not isinstance(tool.parameters, dict):
            raise ValueError(f"tool {tool.name} parameters must be a JSON Schema object")
        schema = copy.deepcopy(tool.parameters)
        if "properties" in schema:
            schema.setdefault("additionalProperties", False)
        Validator.check_schema(schema)
        self._tools[tool.name] = replace(tool, parameters=schema)

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(name=tool.name, description=tool.description, parameters=copy.deepcopy(tool.parameters))
            for tool in self._tools.values()
        ]

    def names(self) -> list[str]:
        return list(self._tools)

    @staticmethod
    def bound(result: ToolResult, ctx: ToolContext) -> ToolResult:
        if len(result.output) > ctx.max_output_chars:
            return replace(result, output=truncate_middle(result.output, ctx.max_output_chars),
                           raw_output=result.raw_output if result.raw_output is not None else result.output)
        return result

    def execute(self, name: str, raw_arguments: str, ctx: ToolContext) -> ToolResult:
        """Execute one tool call.

        Expected failures (bad arguments, missing files, failing commands) come
        back as error results for the model to read. Only KeyboardInterrupt
        (user cancellation) propagates.
        """
        tool = self._tools.get(name)
        if tool is None:
            return self.bound(ToolResult.error(
                f"unknown tool {name!r}; available tools: {', '.join(self._tools)}"
            ), ctx)
        try:
            arguments = json.loads(raw_arguments, parse_constant=reject_constant) if raw_arguments.strip() else {}
        except ValueError as exc:
            return self.bound(ToolResult.error(
                f"arguments are not valid JSON ({exc}); received: {raw_arguments[:200]!r}"
            ), ctx)
        if not isinstance(arguments, dict):
            return self.bound(ToolResult.error("arguments must be a JSON object"), ctx)

        try:
            error = next(iter(Validator(tool.parameters).iter_errors(arguments)), None)
            if error is not None:
                raise ToolError(f"invalid arguments for {name}: {error.message}")
            result = tool.run(arguments, ctx)
            if not isinstance(result, ToolResult):
                raise ToolError(f"tool {name} did not return ToolResult")
        except ToolError as exc:
            return self.bound(ToolResult.error(str(exc)), ctx)
        except PersistenceError:
            raise
        except Exception as exc:  # a bug inside a tool must not kill the agent
            return self.bound(ToolResult.error(f"tool {name!r} crashed: {type(exc).__name__}: {exc}"), ctx)

        return self.bound(result, ctx)


def build_registry() -> ToolRegistry:
    return ToolRegistry([*fs.TOOLS, *search.TOOLS, *shell.TOOLS])
