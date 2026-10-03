"""Agent lifecycle events.

The core can use an optional sink. The application EventBus combines a required
trace channel with isolated UI and extension observers.
"""

from __future__ import annotations

import copy
import sys
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .storage import PersistenceError

AGENT_START = "agent_start"
USER_MESSAGE = "user_message"
MODEL_REQUEST_START = "model_request_start"
MODEL_TEXT_DELTA = "model_text_delta"
MODEL_REASONING_DELTA = "model_reasoning_delta"
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
    session_id: str | None = None
    run_id: str | None = None


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
            deliver(sink, event)


def deliver(sink: EventSink, event: Event) -> None:
    """Notify an isolated observer, but propagate required storage failures."""
    try:
        sink.emit(replace(event, data=copy.deepcopy(event.data)))
    except PersistenceError:
        raise
    except Exception as exc:
        print(f"[transparent_agent] event sink failed on {event.type}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)


class EventBus:
    """One ordered event stream for the loop, resources and HTTP transport."""

    def __init__(self, *, session_id: str, recorder: EventSink, observers=(), seq: int = 0):
        self.session_id = session_id
        self.run_id: str | None = None
        self.request_id: str | None = None
        self.seq = seq
        self.recorder = recorder
        self.observers = list(observers)

    def begin_run(self) -> None:
        self.run_id = uuid.uuid4().hex
        self.request_id = None

    def end_run(self) -> None:
        self.run_id = None
        self.request_id = None

    def publish(self, event_type: str, data: dict) -> None:
        self.seq += 1
        event = Event(event_type, copy.deepcopy(data), self.seq, time.time(),
                      self.session_id, self.run_id)
        # Required channel first: observers never get an unrecorded event.
        self.recorder.emit(event)
        for observer in self.observers:
            deliver(observer, event)

    def emit(self, event: Event) -> None:
        self.publish(event.type, event.data)


def publish(sink: EventSink, event_type: str, data: dict) -> None:
    if isinstance(sink, EventBus):
        sink.publish(event_type, data)
    else:
        deliver(sink, make_event(event_type, data, 0))


def make_event(event_type: str, data: dict[str, Any], seq: int) -> Event:
    return Event(type=event_type, data=data, seq=seq, timestamp=time.time())
