"""Atomic session snapshots, non-replaying recovery and process ownership."""

from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # native Windows; runtime does not require terminal access
    fcntl = None

from .models import Message, ToolCall, message_to_dict
from .storage import PersistenceError, atomic_json, private_directory


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = None
        try:
            private_directory(path.parent)
            fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
            self._lock = os.fdopen(fd, "a")
            if fcntl is not None:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    self._lock.write("\0")
                    self._lock.flush()
                self._lock.seek(0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.close()
            raise PersistenceError(f"session is locked or unavailable: {path}") from exc

    def load(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value["schema_version"] != 1:
                raise ValueError("unsupported session version")
            if not isinstance(value["session_id"], str) or not isinstance(value["system"], str):
                raise ValueError("invalid session identity or system")
            if not isinstance(value["messages"], list):
                raise ValueError("messages must be a list")
            for key in ("workspace", "model", "trace_path"):
                if not isinstance(value[key], str) or not value[key]:
                    raise ValueError(f"invalid session {key}")
            value["history"] = [message_from_dict(item) for item in value["messages"]]
            return value
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PersistenceError(f"cannot restore session {self.path}: {exc}") from exc

    def save(self, *, session_id: str, workspace: Path, model: str, system: str,
             history: list[Message], trace_path: Path) -> None:
        atomic_json(self.path, {
            "schema_version": 1, "session_id": session_id, "workspace": str(workspace),
            "model": model, "system": system, "messages": [message_to_dict(m) for m in history],
            "trace_path": str(trace_path),
        })

    def close(self) -> None:
        if self._lock is not None:
            self._lock.close()
            self._lock = None


def message_from_dict(value: dict) -> Message:
    if value["role"] not in ("user", "assistant", "tool"):
        raise ValueError("unsupported message role")
    for key in ("content", "reasoning", "tool_call_id"):
        if value.get(key) is not None and not isinstance(value[key], str):
            raise ValueError(f"invalid message {key}")
    calls = []
    for call in value.get("tool_calls", []):
        if not all(isinstance(call[key], str) and call[key] for key in ("id", "name")):
            raise ValueError("invalid tool call identity")
        if not isinstance(call["arguments"], str):
            raise ValueError("invalid tool call arguments")
        calls.append(ToolCall(call["id"], call["name"], call["arguments"]))
    return Message(role=value["role"], content=value.get("content"), reasoning=value.get("reasoning"),
                   tool_call_id=value.get("tool_call_id"), tool_calls=calls)


def repair_pending(history: list[Message]) -> list[str]:
    pending: dict[str, ToolCall] = {}
    for message in history:
        if pending and message.role != "tool":
            raise PersistenceError("unanswered tool calls precede another message")
        for call in message.tool_calls:
            if call.id in pending:
                raise PersistenceError("duplicate pending tool call ID")
            pending[call.id] = call
        if message.role == "tool":
            if message.tool_call_id not in pending:
                raise PersistenceError("tool result has no matching call")
            del pending[message.tool_call_id]
    for call in pending.values():
        history.append(Message(role="tool", tool_call_id=call.id,
                               content="Error: execution state unknown after interruption; not replayed. Inspect the workspace before retrying."))
    return list(pending)
