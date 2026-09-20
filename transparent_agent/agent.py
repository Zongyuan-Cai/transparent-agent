"""The agent core: a model-driven iterative tool-calling loop.

The agent owns the conversation history and tool execution. It communicates
outward only through its model, its tools and the events it emits; it never
prints, and it behaves identically whether or not a sink is attached.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from . import events
from .events import Event, EventSink, NullSink
from .models import (
    Message,
    Model,
    ModelError,
    ModelResponse,
    TextDelta,
    ToolCall,
    Usage,
    message_to_dict,
    usage_to_dict,
)
from .tools import ToolContext, ToolRegistry, ToolResult

FINAL = "final"
MAX_STEPS = "max_steps"
CANCELLED = "cancelled"
ERROR = "error"

_CANCELLED_RESULT = ToolResult(output="cancelled by the user before execution", is_error=True)


@dataclass
class TurnResult:
    text: str
    stop_reason: str  # final | max_steps | cancelled | error
    steps: int
    usage: Usage
    error: str | None = None


class Agent:
    def __init__(self, *, model: Model, tools: ToolRegistry, tool_context: ToolContext,
                 system_prompt: str, max_steps: int = 40, sink: EventSink | None = None):
        self.model = model
        self.tools = tools
        self.tool_context = tool_context
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.sink: EventSink = sink or NullSink()
        self.history: list[Message] = []
        self.total_usage = Usage()
        self._seq = 0
        self._step_count = 0
        self._turn_usage = Usage()
        self._turn_started = 0.0

    def clear(self) -> None:
        self.history.clear()

    def send(self, user_text: str) -> TurnResult:
        """Run one user turn: model -> tools -> model -> ... until an answer.

        Returns when the model answers without tool calls, when the step limit
        is reached, when the user interrupts (KeyboardInterrupt) or when the
        model request fails. The conversation stays valid in every case.
        """
        self._step_count = 0
        self._turn_usage = Usage()
        self._turn_started = time.monotonic()
        try:
            return self._run_turn(user_text)
        except KeyboardInterrupt:
            # Interrupted somewhere outside the tool/model handlers; the
            # conversation must still be valid for the next request.
            self._repair_history()
            return self._finish(CANCELLED, "")

    def _run_turn(self, user_text: str) -> TurnResult:
        self.history.append(Message(role="user", content=user_text))
        self._emit(events.AGENT_START, {
            "model": self.model.name,
            "workspace": str(self.tool_context.workspace),
            "max_steps": self.max_steps,
        })
        self._emit(events.USER_MESSAGE, {"text": user_text})

        specs = self.tools.specs()

        for step in range(1, self.max_steps + 1):
            self._step_count = step
            self._emit(events.MODEL_REQUEST_START, {
                "step": step,
                "system": self.system_prompt,
                "messages": [message_to_dict(message) for message in self.history],
                "tools": [
                    {"name": spec.name, "description": spec.description, "parameters": spec.parameters}
                    for spec in specs
                ],
            })

            try:
                response = self._stream_response(step, specs)
            except ModelError as exc:
                message = str(exc)
                self._emit(events.AGENT_ERROR, {"stage": "model_request", "step": step, "message": message})
                return self._finish(ERROR, "", error=message)

            if response.usage:
                self._turn_usage = self._turn_usage + response.usage
                self.total_usage = self.total_usage + response.usage
            self._emit(events.MODEL_RESPONSE, {
                "step": step,
                "text": response.text,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in response.tool_calls
                ],
                "finish_reason": response.finish_reason,
                "usage": usage_to_dict(response.usage),
            })
            self.history.append(Message(
                role="assistant",
                content=response.text or None,
                tool_calls=response.tool_calls,
                reasoning=response.reasoning or None,
            ))

            if not response.tool_calls:
                return self._finish(FINAL, response.text)

            if self._run_tool_calls(response.tool_calls, step):
                return self._finish(CANCELLED, "")

        return self._finish(
            MAX_STEPS, "",
            error=f"reached the step limit ({self.max_steps} model requests) before finishing the task",
        )

    def _stream_response(self, step: int, specs) -> ModelResponse:
        response: ModelResponse | None = None
        for item in self.model.stream(system=self.system_prompt, messages=list(self.history), tools=specs):
            if isinstance(item, TextDelta):
                self._emit(events.MODEL_TEXT_DELTA, {"step": step, "text": item.text})
            elif isinstance(item, ModelResponse):
                response = item
        if response is None:
            raise ModelError("the model stream ended without a complete response")
        return response

    def _run_tool_calls(self, calls: list[ToolCall], step: int) -> bool:
        """Execute the tool calls of one assistant message, in order.

        Returns True if the user interrupted. Every call is answered in the
        conversation, so the history stays valid for the next request.
        """
        interrupted = False
        for call in calls:
            self._emit(events.TOOL_CALL_START, {
                "step": step,
                "call_id": call.id,
                "name": call.name,
                "arguments": call.arguments,
            })
            started = time.monotonic()
            if interrupted:
                result = _CANCELLED_RESULT
            else:
                try:
                    result = self.tools.execute(call.name, call.arguments, self.tool_context)
                except KeyboardInterrupt:
                    interrupted = True
                    result = _CANCELLED_RESULT
            content = result.output if not result.is_error else f"Error: {result.output}"
            self.history.append(Message(role="tool", content=content, tool_call_id=call.id))
            self._emit(events.TOOL_CALL_END, {
                "step": step,
                "call_id": call.id,
                "name": call.name,
                "output": result.output,
                "display": result.display,
                "is_error": result.is_error,
                "cancelled": interrupted,
                "duration_s": round(time.monotonic() - started, 3),
            })
        return interrupted

    def _repair_history(self) -> None:
        """Answer any tool call an interrupt left unanswered."""
        last_assistant: Message | None = None
        answered: set[str | None] = set()
        for message in self.history:
            if message.role == "assistant" and message.tool_calls:
                last_assistant = message
                answered = set()
            elif message.role == "tool":
                answered.add(message.tool_call_id)
        if last_assistant is None:
            return
        for call in last_assistant.tool_calls:
            if call.id in answered:
                continue
            self.history.append(Message(
                role="tool",
                content=f"Error: {_CANCELLED_RESULT.output}",
                tool_call_id=call.id,
            ))
            answered.add(call.id)

    def _finish(self, stop_reason: str, text: str, error: str | None = None) -> TurnResult:
        usage = self._turn_usage
        self._emit(events.AGENT_FINISH, {
            "stop_reason": stop_reason,
            "steps": self._step_count,
            "usage": usage_to_dict(usage),
            "duration_s": round(time.monotonic() - self._turn_started, 3),
            "text": text,
            "error": error,
        })
        return TurnResult(text=text, stop_reason=stop_reason, steps=self._step_count,
                          usage=usage, error=error)

    def _emit(self, event_type: str, data: dict) -> None:
        self._seq += 1
        event = Event(type=event_type, data=data, seq=self._seq, timestamp=time.time())
        try:
            self.sink.emit(event)
        except Exception as exc:  # a broken observer must never break the agent
            print(
                f"[transparent_agent] event sink failed on {event_type}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
