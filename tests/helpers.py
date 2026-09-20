"""Test doubles: a scripted model, an event recorder and small builders."""

from __future__ import annotations

from pathlib import Path

from transparent_agent.agent import Agent
from transparent_agent.models import ModelResponse, TextDelta, ToolCall
from transparent_agent.tools import ToolContext, ToolRegistry, build_registry


class FakeModel:
    """Replays a script: one entry per model request, each a list of stream items."""

    name = "fake-model"

    def __init__(self, script=None):
        self.script = list(script or [])
        self.requests: list[dict] = []

    def stream(self, *, system, messages, tools):
        self.requests.append({
            "system": system,
            "messages": list(messages),
            "tools": list(tools),
        })
        turn = self.script.pop(0) if self.script else [final_response("script exhausted")]
        for item in turn:
            yield item


class RecordingSink:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)

    @property
    def types(self):
        return [event.type for event in self.events]

    def of_type(self, event_type):
        return [event for event in self.events if event.type == event_type]


def final_response(text: str) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=[])


def text_turn(text: str) -> list:
    """A streamed answer with no tool calls."""
    return [TextDelta(text), final_response(text)]


def tool_turn(text: str, *calls: ToolCall) -> list:
    """An assistant message that requests tool calls."""
    return [ModelResponse(text=text, tool_calls=list(calls))]


def call(call_id: str, name: str, arguments: str) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def make_agent(workspace: Path, script=None, *, sink=None, tools: ToolRegistry | None = None,
               max_steps: int = 40, system_prompt: str = "system prompt") -> Agent:
    return Agent(
        model=FakeModel(script),
        tools=tools or build_registry(),
        tool_context=ToolContext(workspace=workspace),
        system_prompt=system_prompt,
        max_steps=max_steps,
        sink=sink,
    )
