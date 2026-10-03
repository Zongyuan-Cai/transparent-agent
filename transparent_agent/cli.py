"""CLI composition and offline trace inspection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .agent import Agent, CANCELLED, FINAL
from .config import Config, ConfigError, load_dotenv
from .extensions import ExtensionError
from .models import ModelError
from .runtime import Runtime, RuntimeOptions
from .storage import PersistenceError
from .trace import read_trace
from .tui.app import Tui
from .tui.render import Renderer, Style


def build_agent(config: Config, sink) -> Agent:
    """Compatibility entry point. Close agent.runtime after embedding it."""
    runtime = Runtime(config, sink=sink)
    runtime.agent.runtime = runtime
    return runtime.agent


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="transparent-agent", description="A transparent terminal coding agent.")
    parser.add_argument("-C", "--workspace", help="workspace root (default: current directory or resumed workspace)")
    parser.add_argument("--model", help="model name (default: $OPENAI_MODEL)")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL")
    parser.add_argument("--max-steps", type=int, help="model requests per user turn")
    parser.add_argument("-p", "--print", dest="prompt", metavar="PROMPT", help="run one task, then exit")
    parser.add_argument("--state-dir", type=Path, help="local session state root")
    parser.add_argument("--trace-dir", type=Path, help="trace root for new sessions")
    parser.add_argument("--resume", type=Path, metavar="SESSION_JSON", help="resume history without replaying tools")
    parser.add_argument("--skill-dir", type=Path, action="append", default=[], help="additional skills directory (repeatable)")
    parser.add_argument("--prompt-dir", type=Path, action="append", default=[], help="additional prompt directory (repeatable)")
    parser.add_argument("--extension", type=Path, action="append", default=[], help="explicit Python extension (repeatable)")
    parser.add_argument("--inspect", type=Path, metavar="TRACE_JSONL", help="inspect trace offline; no API key required")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.inspect:
            for record in read_trace(args.inspect.expanduser().resolve()):
                print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0
        load_dotenv()
        workspace = args.workspace
        if args.resume and workspace is None:
            try:
                workspace = json.loads(args.resume.expanduser().read_text(encoding="utf-8"))["workspace"]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise PersistenceError(f"cannot read resumed workspace: {exc}") from exc
        config = Config.from_env(workspace=workspace, model=args.model, base_url=args.base_url, max_steps=args.max_steps)
        interactive = args.prompt is None
        style = Style(enabled=sys.stdout.isatty() if interactive else sys.stderr.isatty())
        renderer = Renderer(style=style, out=sys.stdout, log=sys.stdout if interactive else sys.stderr)
        options = RuntimeOptions(state_dir=args.state_dir, trace_dir=args.trace_dir, resume=args.resume,
                                 model_override=args.model is not None, skill_dirs=args.skill_dir,
                                 prompt_dirs=args.prompt_dir, extensions=args.extension)
        with Runtime(config, sink=renderer, options=options) as runtime:
            print(f"trace: {runtime.trace_path}\nsession: {runtime.session_path}", file=sys.stderr)
            if interactive:
                return Tui(runtime.agent, renderer, style).run()
            result = runtime.agent.send(args.prompt)
            if result.stop_reason == FINAL:
                return 0
            return 130 if result.stop_reason == CANCELLED else 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except (ConfigError, ModelError, PersistenceError, ExtensionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
