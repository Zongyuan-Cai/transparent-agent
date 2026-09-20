#!/usr/bin/env python3
"""Example: an external observer attached to the agent's event stream.

The agent emits events to its sink and never depends on one being present.
This script attaches a second sink (a JSON Lines file) next to the normal TUI
renderer. It is not part of the agent and the agent does not know about it --
a future transparency layer would attach at exactly this boundary.

Usage:
    OPENAI_API_KEY=... python3 examples/event_logger.py -C ./some/project \
        --log events.jsonl "fix the failing test"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transparent_agent.cli import build_agent  # noqa: E402
from transparent_agent.config import Config, load_dotenv  # noqa: E402
from transparent_agent.events import CompositeSink  # noqa: E402
from transparent_agent.tui.render import Renderer, Style  # noqa: E402


class JsonlSink:
    """Writes every event, as received, to a JSON Lines file."""

    def __init__(self, path: str):
        self._file = open(path, "a", encoding="utf-8")

    def emit(self, event) -> None:
        self._file.write(json.dumps(
            {"seq": event.seq, "timestamp": event.timestamp, "type": event.type, "data": event.data},
            ensure_ascii=False,
        ) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-C", "--workspace", default=".")
    parser.add_argument("--log", default="events.jsonl")
    parser.add_argument("task")
    args = parser.parse_args()

    load_dotenv()
    config = Config.from_env(workspace=args.workspace)
    renderer = Renderer(style=Style(enabled=sys.stdout.isatty()))
    observer = JsonlSink(args.log)
    agent = build_agent(config, sink=CompositeSink([renderer, observer]))

    try:
        result = agent.send(args.task)
    finally:
        observer.close()
    print(f"[event_logger] events written to {args.log} (stop_reason={result.stop_reason})")
    return 0 if result.stop_reason == "final" else 1


if __name__ == "__main__":
    raise SystemExit(main())
