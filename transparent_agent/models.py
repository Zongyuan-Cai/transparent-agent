"""Normalized model-layer types.

The agent core talks to this interface only. Provider-specific request and
response formats (OpenAI chat completions, ...) live in providers/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol, Union


class ModelError(Exception):
    """A model request failed: network, auth, API error or a broken stream."""


@dataclass
class ToolCall:
    """A tool call requested by the model.

    ``arguments`` is kept as the raw JSON text the model produced; the agent
    parses it when executing and echoes it back verbatim in later requests.
    """

    id: str
    name: str
    arguments: str


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # set on role="tool"
    reasoning: str | None = None  # opaque provider reasoning text, echoed back when present


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass
class ModelResponse:
    """The complete assistant response for one model request."""

    text: str
    tool_calls: list[ToolCall]
    finish_reason: str | None = None
    usage: Usage | None = None
    reasoning: str = ""


@dataclass
class TextDelta:
    """A chunk of streamed assistant text."""

    text: str


@dataclass
class ReasoningDelta:
    """Provider-visible reasoning, kept separately from the final answer."""

    text: str


StreamItem = Union[TextDelta, ReasoningDelta, ModelResponse]


class Model(Protocol):
    """A streaming model with native tool calling."""

    name: str

    def stream(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> Iterator[StreamItem]:
        """Stream one assistant response.

        Yields TextDelta items as text arrives and exactly one ModelResponse at
        the end. Raises ModelError on failure.
        """
        ...


def message_to_dict(message: Message) -> dict:
    data: dict = {"role": message.role, "content": message.content}
    if message.tool_calls:
        data["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        data["tool_call_id"] = message.tool_call_id
    if message.reasoning:
        data["reasoning"] = message.reasoning
    return data


def usage_to_dict(usage: Usage | None) -> dict | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }


def create_model(*, model: str, api_key: str | None, base_url: str | None, timeout: float,
                 stream_usage: bool = True, sink=None) -> Model:
    """Build the configured provider adapter."""
    from .providers.openai_chat import OpenAIChatModel

    return OpenAIChatModel(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        stream_usage=stream_usage,
        sink=sink,
    )
