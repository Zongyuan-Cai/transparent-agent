"""Process ownership and cancellation for synchronous agent sessions."""

from __future__ import annotations

import json
import multiprocessing
import os
import re
import signal
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path

from ..config import Config
from ..events import NullSink
from ..runtime import Runtime, RuntimeOptions
from ..resources import Resources
from .trace_view import TraceView

SESSION_ID = re.compile(r"^[0-9a-f]{32}$")


class WebError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _run_worker(connection, config: Config, options: RuntimeOptions) -> None:
    interruptible = pending_cancel = shutting_down = False

    def interrupt(signum, frame):
        nonlocal pending_cancel, shutting_down
        if signum == signal.SIGTERM:
            shutting_down = True
        already_pending = pending_cancel
        pending_cancel = True
        if interruptible and not already_pending:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)

    class Observer:
        def emit(self, event):
            nonlocal interruptible
            if event.type == "agent_start":
                # This callback runs inside Agent.send's interruption handler.
                # A signal between dequeuing and send must not kill the worker.
                interruptible = True
            elif event.type == "agent_finish":
                interruptible = False
            connection.send({"kind": "event", "seq": event.seq})
            if event.type == "agent_start" and pending_cancel:
                raise KeyboardInterrupt

    try:
        with Runtime(config, sink=Observer(), options=options) as runtime:
            resources = {
                "skills": [{"name": s.name, "description": s.description} for s in runtime.resources.skills.values()],
                "prompts": list(runtime.resources.prompts),
                "tools": [s.name for s in runtime.agent.tools.specs()],
            }
            connection.send({"kind": "ready", "session_id": runtime.agent.session_id,
                             "trace_path": str(runtime.trace_path), "resources": resources})
            while not shutting_down:
                if not connection.poll(.25):
                    continue
                command = connection.recv()
                if command["kind"] == "shutdown":
                    break
                try:
                    if command["kind"] == "send":
                        if pending_cancel:
                            runtime.bus.publish("web_run_cancelled", {"message": "cancelled before starting"})
                            result = {"stop_reason": "cancelled", "text": ""}
                        else:
                            result = asdict(runtime.agent.send(command["text"]))
                        connection.send({"kind": "result", "result": result})
                    elif command["kind"] == "clear":
                        runtime.agent.clear()
                        connection.send({"kind": "result", "result": None})
                finally:
                    interruptible = pending_cancel = False
    except (EOFError, BrokenPipeError):
        pass
    except BaseException as exc:
        try:
            connection.send({"kind": "error", "error": f"{type(exc).__name__}: {exc}"})
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()


class Worker:
    def __init__(self, config: Config, options: RuntimeOptions, changed: threading.Condition):
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_run_worker, args=(child, config, options), daemon=False)
        self.status, self.error, self.result = "starting", None, None
        self.session_id, self.trace_path, self.resources = None, None, {}
        self.last_used = time.monotonic()
        self.changed, self.lock = changed, threading.RLock()
        self.ready, self.finished = threading.Event(), threading.Event()
        self.closing = False
        self.process.start()
        child.close()
        self.monitor = threading.Thread(target=self._monitor, daemon=True)
        self.monitor.start()
        if not self.ready.wait(20):
            self.close()
            raise WebError("session worker startup timed out", 503)
        if self.error:
            self.close()
            raise WebError(self.error, 409)

    def _monitor(self):
        try:
            while True:
                message = self.connection.recv()
                with self.lock:
                    if message["kind"] == "ready":
                        self.session_id = message["session_id"]
                        self.trace_path = Path(message["trace_path"])
                        self.resources = message["resources"]
                        self.status = "idle"
                        self.ready.set()
                    elif message["kind"] == "result":
                        self.result = message["result"]
                        self.status = "idle"
                        self.finished.set()
                    elif message["kind"] == "error":
                        self.status, self.error = "failed", message["error"]
                        self.ready.set()
                        self.finished.set()
                with self.changed:
                    self.changed.notify_all()
        except (EOFError, OSError):
            with self.lock:
                if not self.closing and self.status != "failed":
                    self.status, self.error = "failed", "session worker exited; resume from the saved session"
                self.ready.set()
                self.finished.set()
            with self.changed:
                self.changed.notify_all()

    def submit(self, kind: str, **data):
        with self.lock:
            if self.status != "idle":
                raise WebError("session is already running or unavailable", 409)
            self.status, self.result = "running", None
            self.last_used = time.monotonic()
            self.finished.clear()
            try:
                self.connection.send({"kind": kind, **data})
            except (OSError, EOFError) as exc:
                self.status, self.error = "failed", str(exc)
                raise WebError("session worker is unavailable", 503) from exc
        with self.changed:
            self.changed.notify_all()

    def cancel(self):
        with self.lock:
            if self.status in ("running", "cancelling") and self.process.is_alive():
                if self.status == "running":
                    self.status = "cancelling"
                    os.kill(self.process.pid, signal.SIGINT)
        with self.changed:
            self.changed.notify_all()

    def close(self):
        self.closing = True
        self.cancel()
        try:
            self.connection.send({"kind": "shutdown"})
        except (OSError, EOFError):
            pass
        self.process.join(5)
        if self.process.is_alive():
            self.process.terminate()  # SIGTERM uses the same graceful interruption handler.
            self.process.join(5)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(2)
        self.connection.close()
        self.monitor.join(1)


class SessionManager:
    def __init__(self, config: Config, options: RuntimeOptions | None = None, max_workers: int = 8):
        if os.name != "posix":
            raise WebError("run the Web server on Linux/macOS or inside WSL for process cancellation")
        self.config, self.options = config, options or RuntimeOptions()
        self.state_dir = (self.options.state_dir or Path(os.environ.get("TRANSPARENT_AGENT_STATE_DIR", "~/.local/state/transparent-agent"))).expanduser().resolve()
        self.workers: dict[str, Worker] = {}
        self.views: dict[str, TraceView] = {}
        self.changed, self.lock = threading.Condition(), threading.RLock()
        self.max_workers = max_workers
        self.closed = False

    def path(self, session_id: str) -> Path:
        if not SESSION_ID.fullmatch(session_id):
            raise WebError("invalid session ID", 404)
        return self.state_dir / "sessions" / session_id / "session.json"

    def snapshot(self, session_id: str) -> dict:
        try:
            saved = json.loads(self.path(session_id).read_text(encoding="utf-8"))
            if saved["schema_version"] != 1 or saved["session_id"] != session_id:
                raise ValueError("invalid session snapshot")
            return saved
        except FileNotFoundError as exc:
            raise WebError("session not found", 404) from exc
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise WebError(f"cannot read session: {exc}", 409) from exc

    def status(self, session_id: str) -> dict:
        with self.lock:
            worker = self.workers.get(session_id)
            if worker:
                with worker.lock:
                    return {"status": worker.status, "error": worker.error, "result": worker.result}
            return {"status": "idle", "error": None, "result": None}

    def detail(self, session_id: str, *, include_resources=True) -> dict:
        saved = self.snapshot(session_id)
        with self.lock:
            worker = self.workers.get(session_id)
            resources = worker.resources if worker else {"skills": [], "prompts": [], "tools": []}
        if include_resources and not worker:
            catalog = Resources(workspace=Path(saved["workspace"]), sink=NullSink(), skill_dirs=self.options.skill_dirs,
                                prompt_dirs=self.options.prompt_dirs)
            resources = {"skills": [{"name": s.name, "description": s.description} for s in catalog.skills.values()],
                         "prompts": list(catalog.prompts), "tools": []}
        # System prompt and original inputs are available through event inspection.
        return {k: saved[k] for k in ("session_id", "workspace", "model", "messages")} | self.status(session_id) | {"resources": resources}

    def list(self) -> list[dict]:
        result = []
        for path in (self.state_dir / "sessions").glob("*/session.json"):
            try:
                item = self.detail(path.parent.name, include_resources=False)
                title = next((m.get("content", "")[:100] for m in item["messages"] if m["role"] == "user"), "新会话")
                result.append({k: item[k] for k in ("session_id", "workspace", "model", "status", "error")} | {
                    "title": title, "updated_at": path.stat().st_mtime})
            except (WebError, OSError, KeyError, TypeError):
                continue
        return sorted(result, key=lambda item: item["updated_at"], reverse=True)

    def _capacity(self):
        if self.closed:
            raise WebError("server is shutting down", 503)
        if len(self.workers) >= self.max_workers:
            candidates = [(w.last_used, sid, w) for sid, w in self.workers.items() if w.status in ("idle", "failed")]
            if not candidates:
                raise WebError("all session workers are busy", 503)
            _, sid, worker = min(candidates, key=lambda item: item[0])
            worker.close()
            del self.workers[sid]

    def create(self, workspace: str | None = None, model: str | None = None) -> dict:
        with self.lock:
            self._capacity()
            config = replace(self.config, workspace=Path(workspace) if workspace else self.config.workspace,
                             model=model or self.config.model)
            worker = Worker(config, replace(self.options, resume=None, state_dir=self.state_dir), self.changed)
            self.workers[worker.session_id] = worker
            return self.detail(worker.session_id)

    def activate(self, session_id: str) -> Worker:
        with self.lock:
            worker = self.workers.get(session_id)
            if worker and worker.status != "failed":
                return worker
            if worker:
                worker.close()
                del self.workers[session_id]
            self._capacity()
            saved = self.snapshot(session_id)
            config = replace(self.config, workspace=Path(saved["workspace"]), model=saved["model"])
            worker = Worker(config, replace(self.options, resume=self.path(session_id), trace_dir=None), self.changed)
            self.workers[session_id] = worker
            return worker

    def send(self, session_id: str, text: str):
        with self.lock:
            self.activate(session_id).submit("send", text=text)

    def cancel(self, session_id: str):
        with self.lock:
            self.snapshot(session_id)
            if session_id in self.workers:
                self.workers[session_id].cancel()

    def clear(self, session_id: str):
        with self.lock:
            worker = self.activate(session_id)
            worker.submit("clear")
        if not worker.finished.wait(10):
            raise WebError("clear is still in progress", 503)
        if worker.error:
            raise WebError(worker.error, 409)

    def trace(self, session_id: str) -> TraceView:
        with self.lock:
            if session_id not in self.views:
                saved = self.snapshot(session_id)
                self.views[session_id] = TraceView(Path(saved["trace_path"]), session_id)
            return self.views[session_id]

    def close(self):
        with self.lock:
            self.closed = True
            workers = list(self.workers.values())
        with self.changed:
            self.changed.notify_all()
        for worker in workers:
            worker.close()
