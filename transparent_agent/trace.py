"""Lossless JSONL events and an HTTPX transport that records SDK wire bodies."""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .events import Event, EventSink, publish
from .storage import PersistenceError, private_directory


def read_trace(path: Path):
    """Validate sequence and version without executing any recorded operation."""
    previous = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                record = json.loads(line)
                if record["schema_version"] != 1 or record["seq"] != previous + 1:
                    raise ValueError("unsupported version or non-contiguous sequence")
                if not isinstance(record["data"], dict):
                    raise ValueError("event data must be an object")
                previous = record["seq"]
            except (ValueError, KeyError, TypeError) as exc:
                raise PersistenceError(f"invalid trace {path} at line {line_number}: {exc}") from exc
            yield record


class TraceRecorder:
    def __init__(self, path: Path, session_id: str):
        self.path = path
        self.seq = 0
        try:
            private_directory(path.parent)
            if path.exists():
                for record in read_trace(path):
                    if record["session_id"] != session_id:
                        raise PersistenceError("trace belongs to a different session")
                    self.seq = record["seq"]
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            self._handle = os.fdopen(fd, "a", encoding="utf-8")
        except OSError as exc:
            raise PersistenceError(f"cannot open trace {path}: {exc}") from exc

    def emit(self, event: Event) -> None:
        record = {
            "schema_version": 1, "session_id": event.session_id, "run_id": event.run_id,
            "seq": event.seq, "type": event.type,
            "timestamp": datetime.fromtimestamp(event.timestamp, timezone.utc).isoformat(),
            "data": event.data,
        }
        try:
            if event.seq != self.seq + 1:
                raise ValueError("event sequence is not contiguous")
            line = json.dumps(record, ensure_ascii=False, allow_nan=False)
            self._handle.write(line + "\n")
            self._handle.flush()
            self.seq = event.seq
        except (OSError, ValueError, TypeError) as exc:
            raise PersistenceError(f"cannot record trace {self.path}: {exc}") from exc

    def close(self) -> None:
        try:
            self._handle.close()
        except OSError as exc:
            raise PersistenceError(f"cannot close trace {self.path}: {exc}") from exc


def sensitive_name(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    return any(part in normalized for part in ("authorization", "cookie", "token", "secret", "key", "password"))


def safe_headers(headers: httpx.Headers) -> list[list[str]]:
    return [[name, "[REDACTED]" if sensitive_name(name) else value]
            for name, value in headers.multi_items()]


def safe_url(url: httpx.URL) -> str:
    parts = urlsplit(str(url))
    host = parts.netloc.rsplit("@", 1)[-1]
    query = urlencode([(key, "[REDACTED]" if sensitive_name(key) else value)
                       for key, value in parse_qsl(parts.query, keep_blank_values=True)])
    return urlunsplit((parts.scheme, host, parts.path, query, parts.fragment))


class RecordingTransport(httpx.BaseTransport):
    def __init__(self, sink: EventSink, transport: httpx.BaseTransport | None = None):
        self.sink = sink
        self.transport = transport if transport is not None else httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request_id = getattr(self.sink, "request_id", None) or uuid.uuid4().hex
        publish(self.sink, "api_request", {
            "request_id": request_id, "method": request.method, "url": safe_url(request.url),
            "headers": safe_headers(request.headers),
            "body_base64": base64.b64encode(request.read()).decode("ascii"),
        })
        try:
            response = self.transport.handle_request(request)
        except BaseException as exc:
            publish(self.sink, "api_response_end", {
                "request_id": request_id, "status": "cancelled" if isinstance(exc, KeyboardInterrupt) else "error",
                "error_type": type(exc).__name__, "message": str(exc),
            })
            raise
        try:
            publish(self.sink, "api_response_start", {
                "request_id": request_id, "status_code": response.status_code,
                "headers": safe_headers(response.headers),
            })
        except BaseException:
            response.close()
            raise
        if response.is_stream_consumed:
            # In-memory transports may supply an already-read Response;
            # HTTPX will never iterate its stream a second time.
            publish(self.sink, "api_response_chunk", {
                "request_id": request_id, "body_base64": base64.b64encode(response.content).decode("ascii"),
            })
            publish(self.sink, "api_response_end", {"request_id": request_id, "status": "complete"})
            return response
        response.stream = RecordingStream(response.stream, self.sink, request_id)
        return response

    def close(self) -> None:
        self.transport.close()


class RecordingStream(httpx.SyncByteStream):
    def __init__(self, stream, sink, request_id):
        self.stream = stream
        self.sink = sink
        self.request_id = request_id
        self.ended = False

    def _end(self, status, **extra):
        if not self.ended:
            self.ended = True
            publish(self.sink, "api_response_end", {
                "request_id": self.request_id, "status": status, **extra,
            })

    def __iter__(self):
        try:
            for chunk in self.stream:
                publish(self.sink, "api_response_chunk", {
                    "request_id": self.request_id,
                    "body_base64": base64.b64encode(chunk).decode("ascii"),
                })
                yield chunk
            self._end("complete")
        except PersistenceError:
            raise
        except BaseException as exc:
            self._end("cancelled" if isinstance(exc, (KeyboardInterrupt, GeneratorExit)) else "error",
                      error_type=type(exc).__name__, message=str(exc))
            raise

    def close(self):
        try:
            self.stream.close()
        finally:
            self._end("closed")
