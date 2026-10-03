"""Incremental disk index: summaries stream, original records remain on disk."""

from __future__ import annotations

import base64
import gzip
import json
import threading
import zlib
from pathlib import Path

from ..storage import PersistenceError


def summarize(record: dict) -> dict:
    data = record["data"]
    small = {key: data[key] for key in (
        "request_id", "step", "call_id", "name", "status", "status_code", "stop_reason",
        "is_error", "cancelled", "duration_s", "truncated", "usage", "error", "error_type",
    ) if key in data}
    for key in ("text", "content", "reasoning", "output", "message", "arguments"):
        if isinstance(data.get(key), str):
            small[key] = data[key][:8192]
    if record["type"] == "api_response_chunk":
        small["bytes"] = len(base64.b64decode(data["body_base64"]))
    if isinstance(data.get("tool_calls"), list):
        small["tool_calls"] = [{k: c[k] for k in ("id", "name") if k in c} for c in data["tool_calls"]]
    return {**{k: record[k] for k in ("schema_version", "session_id", "run_id", "seq", "type", "timestamp")}, "data": small}


class TraceView:
    def __init__(self, path: Path, session_id: str):
        self.path, self.session_id = path, session_id
        self._offset = 0
        self._positions: list[tuple[int, int]] = []
        self._summaries: list[dict] = []
        self._requests: dict[str, list[int]] = {}
        self._lock = threading.RLock()

    def refresh(self) -> None:
        with self._lock:
            if self.path.stat().st_size < self._offset:
                raise PersistenceError("trace was truncated while reading")
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                while True:
                    line = handle.readline()
                    if not line.endswith(b"\n"):
                        break  # A concurrent append may not have completed yet.
                    try:
                        record = json.loads(line)
                        seq = len(self._positions) + 1
                        if (record["schema_version"] != 1 or record["seq"] != seq
                                or record["session_id"] != self.session_id or not isinstance(record["data"], dict)):
                            raise ValueError("invalid trace identity, version or sequence")
                        summary = summarize(record)
                    except (ValueError, KeyError, TypeError) as exc:
                        raise PersistenceError(f"invalid trace at event {len(self._positions) + 1}: {exc}") from exc
                    self._positions.append((self._offset, len(line)))
                    self._summaries.append(summary)
                    request_id = record["data"].get("request_id")
                    if request_id:
                        self._requests.setdefault(request_id, []).append(seq)
                    self._offset += len(line)

    def page(self, after: int = 0, limit: int = 200) -> dict:
        with self._lock:
            self.refresh()
            events = self._summaries[after:after + limit]
            return {"events": events, "total": len(self._positions),
                    "next": events[-1]["seq"] if events else after}

    def record(self, seq: int) -> dict:
        with self._lock:
            self.refresh()
            if seq < 1 or seq > len(self._positions):
                raise KeyError("event not found")
            offset, length = self._positions[seq - 1]
            with self.path.open("rb") as handle:
                handle.seek(offset)
                return json.loads(handle.read(length))

    def request(self, request_id: str) -> dict:
        with self._lock:
            self.refresh()
            if request_id not in self._requests:
                raise KeyError("request not found")
            records = [self.record(seq) for seq in self._requests[request_id]]
            wire = b"".join(base64.b64decode(r["data"]["body_base64"]) for r in records
                            if r["type"] == "api_response_chunk")
            headers = next((r["data"]["headers"] for r in records if r["type"] == "api_response_start"), [])
            encoding = next((value.lower() for key, value in headers if key.lower() == "content-encoding"), "")
            decoded = wire
            decode_error = None
            try:
                if encoding == "gzip":
                    decoded = gzip.decompress(wire)
                elif encoding == "deflate":
                    decoded = zlib.decompress(wire)
                elif encoding not in ("", "identity"):
                    raise ValueError(f"unsupported content encoding: {encoding}")
                response_text = decoded.decode("utf-8")
            except (OSError, EOFError, UnicodeError, ValueError, zlib.error) as exc:
                response_text, decode_error = None, str(exc)
            return {"request_id": request_id, "records": records,
                    "response_body_base64": base64.b64encode(wire).decode("ascii"),
                    "response_text": response_text, "decode_error": decode_error}
