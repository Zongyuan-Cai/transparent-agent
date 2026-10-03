"""Explicit Python extensions: tools and isolated event observers."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from .events import EventBus
from .tools import ToolRegistry


class ExtensionError(ValueError):
    pass


class ExtensionAPI:
    def __init__(self, registry: ToolRegistry, bus: EventBus):
        self._registry = registry
        self._bus = bus

    def register_tool(self, tool):
        self._registry.register(tool)

    def subscribe(self, sink):
        if not callable(getattr(sink, "emit", None)):
            raise ExtensionError("observer must provide emit(event)")
        self._bus.observers.append(sink)


def load_extensions(paths, registry: ToolRegistry, bus: EventBus, workspace: Path):
    for raw in paths:
        path = Path(raw).expanduser()
        path = (workspace / path if not path.is_absolute() else path).resolve()
        try:
            spec = importlib.util.spec_from_file_location("ta_extension_" + uuid.uuid4().hex, path)
            if spec is None or spec.loader is None:
                raise ExtensionError("expected a Python module file")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            before = set(registry.names())
            module.register(ExtensionAPI(registry, bus))
            bus.publish("extension_loaded", {"path": str(path), "tools": [n for n in registry.names() if n not in before]})
        except Exception as exc:
            from .storage import PersistenceError
            if isinstance(exc, PersistenceError):
                raise
            raise ExtensionError(f"cannot load extension {path}: {exc}") from exc
