"""Adapter for any OpenAI-compatible Chat Completions endpoint.

Works with api.openai.com and with compatible servers (DeepSeek, local
OpenAI-compatible proxies, ...) by pointing base_url at them.
"""

from __future__ import annotations

from typing import Iterator

import openai
from openai import OpenAI

from ..models import (
    Message,
    ModelError,
    ModelResponse,
    StreamItem,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
)


class OpenAIChatModel:
    def __init__(self, *, model: str, api_key: str | None, base_url: str | None,
                 timeout: float = 300.0, stream_usage: bool = True, client: OpenAI | None = None):
        if not api_key:
            raise ModelError(
                "No API key configured. Put OPENAI_API_KEY in ~/.config/transparent-agent/.env "
                "(or in a .env in the current directory), or set it in the environment."
            )
        self.name = model
        self.base_url = str(base_url) if base_url else "https://api.openai.com/v1"
        self._stream_usage = stream_usage
        self._client = client or OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)

    def stream(self, *, system: str, messages: list[Message], tools: list[ToolSpec]
               ) -> Iterator[StreamItem]:
        request = {
            "model": self.name,
            "messages": [{"role": "system", "content": system}]
            + [_encode_message(m) for m in messages],
            "stream": True,
        }
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    },
                }
                for spec in tools
            ]
        if self._stream_usage:
            request["stream_options"] = {"include_usage": True}

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        pending: dict[int, dict] = {}
        finish_reason = None
        usage = None

        try:
            stream = self._client.chat.completions.create(**request)
            for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = Usage(
                        prompt_tokens=chunk.usage.prompt_tokens or 0,
                        completion_tokens=chunk.usage.completion_tokens or 0,
                        total_tokens=chunk.usage.total_tokens or 0,
                    )
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                if delta is None:
                    continue
                # Some providers stream reasoning text in a non-standard field.
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_parts.append(reasoning)
                if delta.content:
                    text_parts.append(delta.content)
                    yield TextDelta(delta.content)
                for fragment in delta.tool_calls or []:
                    # Tool calls arrive in fragments that must be reassembled by index.
                    slot = pending.setdefault(fragment.index, {"id": "", "name": "", "arguments": ""})
                    if fragment.id:
                        slot["id"] = fragment.id
                    function = fragment.function
                    if function is not None:
                        if function.name:
                            slot["name"] += function.name
                        if function.arguments:
                            slot["arguments"] += function.arguments
        except openai.OpenAIError as exc:
            raise ModelError(f"Model request failed: {type(exc).__name__}: {exc}") from exc

        tool_calls = [
            ToolCall(id=slot["id"], name=slot["name"], arguments=slot["arguments"])
            for _, slot in sorted(pending.items())
        ]
        yield ModelResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            reasoning="".join(reasoning_parts),
        )


def _encode_message(message: Message) -> dict:
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content or "",
        }
    if message.role == "assistant":
        data: dict = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            data["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in message.tool_calls
            ]
        if message.reasoning:
            # DeepSeek-style providers require reasoning text to be echoed back.
            data["reasoning_content"] = message.reasoning
        return data
    return {"role": message.role, "content": message.content or ""}
