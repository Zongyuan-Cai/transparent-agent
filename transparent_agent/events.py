"""Agent lifecycle events.

The agent core emits events to an optional sink and never depends on one being
present. The TUI consumes the small subset it needs; a future transparency
layer can attach to the same boundary instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

AGENT_START = "agent_start"
USER_MESSAGE = "user_message"
MODEL_REQUEST_START = "model_request_start"
MODEL_TEXT_DELTA = "model_text_delta"
MODEL_RESPONSE = "model_response"
TOOL_CALL_START = "tool_call_start"
TOOL_CALL_END = "tool_call_end"
AGENT_ERROR = "agent_error"
AGENT_FINISH = "agent_finish"


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any]
    seq: int
    timestamp: float


class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...


class NullSink:
    """Default sink. The agent behaves identically with it attached."""

    def emit(self, event: Event) -> None:
        pass


class CompositeSink:
    """Fan out to several sinks."""

    def __init__(self, sinks):
        self._sinks = list(sinks)

    def emit(self, event: Event) -> None:
        for sink in self._sinks:
            sink.emit(event)


def make_event(event_type: str, data: dict[str, Any], seq: int) -> Event:
    return Event(type=event_type, data=data, seq=seq, timestamp=time.time())
