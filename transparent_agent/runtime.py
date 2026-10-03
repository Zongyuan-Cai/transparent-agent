"""Application composition; loop and TUI remain independently usable."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .agent import Agent
from .config import Config
from .events import EventBus
from .extensions import load_extensions
from .models import Model, create_model
from .prompt import build_system_prompt
from .resources import Resources, build_context
from .sessions import SessionStore, repair_pending
from .storage import PersistenceError
from .tools import ToolContext, build_registry
from .trace import TraceRecorder


@dataclass
class RuntimeOptions:
    state_dir: Path | None = None
    trace_dir: Path | None = None
    resume: Path | None = None
    model_override: bool = False
    skill_dirs: list[Path] = field(default_factory=list)
    prompt_dirs: list[Path] = field(default_factory=list)
    extensions: list[Path] = field(default_factory=list)


class Runtime:
    def __init__(self, config: Config, *, sink=None, options: RuntimeOptions | None = None,
                 model: Model | None = None, home: Path | None = None):
        self.store = self.recorder = self.model = None
        options = options or RuntimeOptions()
        try:
            state = (options.state_dir or Path(os.environ.get("TRANSPARENT_AGENT_STATE_DIR", "~/.local/state/transparent-agent"))).expanduser().resolve()
            saved = None
            if options.resume:
                self.store = SessionStore(options.resume.expanduser().resolve())
                saved = self.store.load()
                if Path(saved["workspace"]).resolve() != config.workspace:
                    raise PersistenceError("session workspace does not match -C; use the original workspace")
                session_id = saved["session_id"]
                if not options.model_override:
                    config.model = saved["model"]
                trace_path = Path(saved["trace_path"]).resolve()
                if not trace_path.is_file():
                    raise PersistenceError("session trace is missing; cannot silently start a replacement")
                if options.trace_dir is not None:
                    raise PersistenceError("--trace-dir cannot relocate an existing session trace")
            else:
                session_id = uuid.uuid4().hex
                directory = state / "sessions" / session_id
                self.store = SessionStore(directory / "session.json")
                trace_path = ((options.trace_dir.expanduser().resolve() / session_id) if options.trace_dir else directory) / "trace.jsonl"
            self.recorder = TraceRecorder(trace_path, session_id)
            self.bus = EventBus(session_id=session_id, recorder=self.recorder,
                                observers=[] if sink is None else [sink], seq=self.recorder.seq)
            self.bus.publish("session_resumed" if saved else "session_start", {
                "workspace": str(config.workspace), "model": config.model,
                "session_path": str(self.store.path), "trace_path": str(trace_path),
                "config": {"max_steps": config.max_steps, "max_output_chars": config.max_output_chars,
                           "model_timeout_s": config.request_timeout, "bash_timeout_s": config.bash_timeout},
            })
            for raw in [*options.skill_dirs, *options.prompt_dirs]:
                path = Path(raw).expanduser()
                path = path if path.is_absolute() else config.workspace / path
                if not path.is_dir():
                    self.bus.publish("resource_diagnostic", {"path": str(path), "message": "explicit resource directory does not exist"})
            self.resources = Resources(workspace=config.workspace, sink=self.bus,
                                       skill_dirs=options.skill_dirs, prompt_dirs=options.prompt_dirs, home=home)
            registry = build_registry()
            if self.resources.skills:
                registry.register(self.resources.tool())
            load_extensions(options.extensions, registry, self.bus, config.workspace)
            system = saved["system"] if saved else build_context(config.workspace, build_system_prompt(config.workspace), self.resources)
            self.bus.publish("context_loaded", {"source": "session" if saved else "assembled_system", "content": system})
            self.model = model or create_model(model=config.model, api_key=config.api_key, base_url=config.base_url,
                                              timeout=config.request_timeout, stream_usage=config.stream_usage, sink=self.bus)
            self.agent = Agent(model=self.model, tools=registry,
                               tool_context=ToolContext(workspace=config.workspace, bash_timeout=config.bash_timeout,
                                                        max_bash_timeout=config.max_bash_timeout, max_output_chars=config.max_output_chars),
                               system_prompt=system, max_steps=config.max_steps, sink=self.bus)
            self.agent.session_store = self.store
            self.agent.session_id = session_id
            self.agent.trace_path = trace_path
            self.agent.resources = self.resources
            if saved:
                self.agent.history = saved["history"]
                repaired = repair_pending(self.agent.history)
                if repaired:
                    self.bus.publish("session_repaired", {"call_ids": repaired, "message": "unknown execution state; no tools replayed"})
                if saved["model"] != self.model.name:
                    self.bus.publish("model_changed", {"previous": saved["model"], "model": self.model.name})
            self.agent._save()
        except BaseException:
            self.close()
            raise

    @property
    def session_path(self) -> Path:
        return self.store.path

    @property
    def trace_path(self) -> Path:
        return self.recorder.path

    def close(self) -> None:
        try:
            if self.model is not None:
                close = getattr(self.model, "close", None)
                if close is not None:
                    close()
                self.model = None
        finally:
            try:
                if self.recorder is not None:
                    self.recorder.close()
                    self.recorder = None
            finally:
                if self.store is not None:
                    self.store.close()
                    self.store = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
