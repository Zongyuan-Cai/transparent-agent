"""Loopback HTTP API and packaged frontend. No terminal code is imported."""

from __future__ import annotations

import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from ..config import ConfigError
from ..storage import PersistenceError
from ..trace import safe_url
from .worker import SessionManager, WebError

MAX_BODY = 1024 * 1024
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8")}


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, manager: SessionManager, host: str = "127.0.0.1", port: int = 8765):
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError("Web UI only binds to loopback (127.0.0.1 or localhost)")
        self.manager = manager
        self.token = secrets.token_urlsafe(32)
        self.stopping = False
        super().__init__((host, port), Handler)
        self.hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}
        self.origins = {f"http://{host}" for host in self.hosts}

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}"

    def server_close(self):
        self.stopping = True
        with self.manager.changed:
            self.manager.changed.notify_all()
        self.manager.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # Task text and local paths do not belong in HTTP access logs.

    def _headers(self, status, content_type, length=None, **extra):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if length is not None:
            self.send_header("Content-Length", str(length))
        for key, value in extra.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()

    def _json(self, value, status=200):
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(payload))
        self.wfile.write(payload)

    def _boundary(self, write=False):
        if self.headers.get("Host") not in self.server.hosts:
            raise WebError("invalid Host", 403)
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.origins:
            raise WebError("cross-origin access denied", 403)
        if write:
            supplied = self.headers.get("X-Transparent-Token", "")
            if not secrets.compare_digest(supplied.encode("utf-8"), self.server.token.encode("ascii")):
                raise WebError("missing or invalid browser token", 403)
            if self.headers.get_content_type() != "application/json":
                raise WebError("write requests require application/json", 415)

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            raise WebError("chunked request bodies are unsupported", 400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise WebError("invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY:
            raise WebError("request body exceeds 1 MiB", 413)
        self.connection.settimeout(10)
        try:
            value = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError) as exc:
            raise WebError("invalid JSON body") from exc
        if not isinstance(value, dict):
            raise WebError("request body must be an object")
        return value

    def _dispatch(self, write=False):
        try:
            self._boundary(write)
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            manager = self.server.manager
            if not write and path in ASSETS:
                name, mime = ASSETS[path]
                payload = files("transparent_agent.web").joinpath("static", name).read_bytes()
                self._headers(200, mime, len(payload))
                self.wfile.write(payload)
                return
            if not write and path == "/api/config":
                config = manager.config
                self._json({"workspace": str(config.workspace), "model": config.model,
                            "base_url": safe_url(httpx.URL(config.base_url)) if config.base_url else "https://api.openai.com/v1",
                            "configured": bool(config.api_key), "token": self.server.token,
                            "max_steps": config.max_steps})
                return
            if path == "/api/sessions":
                if write:
                    body = self._body()
                    for field in ("workspace", "model"):
                        if field in body and (not isinstance(body[field], str) or not body[field].strip()):
                            raise WebError(f"{field} must be a non-empty string")
                    self._json(manager.create(body.get("workspace"), body.get("model")), 201)
                else:
                    self._json({"sessions": manager.list()})
                return
            parts = path.strip("/").split("/")
            if len(parts) < 3 or parts[:2] != ["api", "sessions"]:
                raise WebError("not found", 404)
            sid = parts[2]
            manager.path(sid)
            tail = parts[3:]
            if write:
                body = self._body()
                if tail == ["runs"]:
                    text = body.get("text")
                    if not isinstance(text, str) or not text.strip():
                        raise WebError("text must be a non-empty string")
                    manager.send(sid, text)
                    self._json(manager.status(sid), 202)
                elif tail == ["cancel"]:
                    manager.cancel(sid)
                    self._json(manager.status(sid), 202)
                elif tail == ["clear"]:
                    manager.clear(sid)
                    self._json(manager.detail(sid))
                else:
                    raise WebError("not found", 404)
                return
            if not tail:
                self._json(manager.detail(sid))
            elif tail == ["events"]:
                after, limit = self._cursor(query), self._integer(query, "limit", 200, 1, 500)
                self._json(manager.trace(sid).page(after, limit))
            elif tail == ["stream"]:
                after = self._cursor(query)
                try:
                    after = max(after, int(self.headers.get("Last-Event-ID", "0")))
                except ValueError as exc:
                    raise WebError("invalid Last-Event-ID") from exc
                self._stream(sid, after)
            elif len(tail) == 2 and tail[0] == "events":
                try:
                    seq = int(tail[1])
                except ValueError as exc:
                    raise WebError("invalid event sequence") from exc
                self._json(manager.trace(sid).record(seq))
            elif len(tail) == 2 and tail[0] == "requests":
                self._json(manager.trace(sid).request(tail[1]))
            elif tail == ["trace"]:
                # Fixed snapshot length; concurrent append belongs to the next download.
                with manager.trace(sid).path.open("rb") as handle:
                    length = handle.seek(0, 2)
                    handle.seek(0)
                    self._headers(200, "application/x-ndjson", length,
                                  Content_Disposition=f'attachment; filename="{sid}-trace.jsonl"')
                    remaining = length
                    while remaining:
                        chunk = handle.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            else:
                raise WebError("not found", 404)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except (WebError, ConfigError, PersistenceError, KeyError, OSError) as exc:
            self.close_connection = True
            status = exc.status if isinstance(exc, WebError) else (404 if isinstance(exc, (KeyError, FileNotFoundError)) else 409)
            self._json({"error": str(exc)}, status)

    @staticmethod
    def _integer(query, name, default, minimum, maximum):
        try:
            value = int(query.get(name, [str(default)])[0])
        except ValueError as exc:
            raise WebError(f"invalid {name}") from exc
        if not minimum <= value <= maximum:
            raise WebError(f"{name} out of range")
        return value

    def _cursor(self, query):
        return self._integer(query, "after", 0, 0, 2**53 - 1)

    def _stream(self, sid, after):
        view = self.server.manager.trace(sid)
        view.refresh()  # Report invalid traces before sending SSE headers.
        self.connection.settimeout(15)
        self._headers(200, "text/event-stream; charset=utf-8", Connection="close")
        self.close_connection = True
        self.wfile.write(b"retry: 1000\n\n")
        last_status, heartbeat = None, time.monotonic()
        while not self.server.stopping:
            try:
                page = view.page(after, 200)
            except (PersistenceError, OSError) as exc:
                self.wfile.write(f'event: trace_error\ndata: {json.dumps({"error": str(exc)})}\n\n'.encode("utf-8"))
                self.wfile.flush()
                return
            for record in page["events"]:
                self.wfile.write(f'id: {record["seq"]}\ndata: {json.dumps(record, ensure_ascii=False)}\n\n'.encode("utf-8"))
            after = page["next"]
            status = self.server.manager.status(sid)
            if status != last_status:
                # Results can contain a long reply; the snapshot API carries it.
                compact = {k: status[k] for k in ("status", "error")}
                if status["result"]:
                    compact["stop_reason"] = status["result"].get("stop_reason")
                self.wfile.write(f'event: status\ndata: {json.dumps(compact, ensure_ascii=False)}\n\n'.encode("utf-8"))
                last_status = status
            if time.monotonic() - heartbeat > 10:
                self.wfile.write(b": keepalive\n\n")
                heartbeat = time.monotonic()
            self.wfile.flush()
            if after < page["total"]:
                continue
            with self.server.manager.changed:
                self.server.manager.changed.wait(.5)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch(write=True)
