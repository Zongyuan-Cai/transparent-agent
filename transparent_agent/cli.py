"""Command line entry point: wires configuration, model, tools, agent and TUI."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .agent import Agent, CANCELLED, ERROR, FINAL
from .config import Config, ConfigError, load_dotenv
from .models import ModelError, create_model
from .prompt import build_system_prompt
from .tools import ToolContext, build_registry
from .tui.app import Tui
from .tui.render import Renderer, Style


def build_agent(config: Config, sink) -> Agent:
    model = create_model(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.request_timeout,
        stream_usage=config.stream_usage,
    )
    return Agent(
        model=model,
        tools=build_registry(),
        tool_context=ToolContext(
            workspace=config.workspace,
            bash_timeout=config.bash_timeout,
            max_bash_timeout=config.max_bash_timeout,
            max_output_chars=config.max_output_chars,
        ),
        system_prompt=build_system_prompt(config.workspace),
        max_steps=config.max_steps,
        sink=sink,
    )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="transparent-agent",
        description="A lightweight terminal coding agent.",
    )
    parser.add_argument("-C", "--workspace", help="workspace root (default: current directory)")
    parser.add_argument("--model", help="model name (default: $OPENAI_MODEL)")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL (default: $OPENAI_BASE_URL)")
    parser.add_argument("--max-steps", type=int, help="model requests allowed per user turn (default: $MAX_STEPS or 40)")
    parser.add_argument(
        "-p", "--print", dest="prompt", metavar="PROMPT",
        help="run one task non-interactively, then exit",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    load_dotenv()
    try:
        config = Config.from_env(
            workspace=args.workspace, model=args.model, base_url=args.base_url, max_steps=args.max_steps
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    interactive = args.prompt is None
    style = Style(enabled=sys.stdout.isatty() if interactive else sys.stderr.isatty())
    out = sys.stdout
    log = sys.stdout if interactive else sys.stderr
    renderer = Renderer(style=style, out=out, log=log)

    try:
        agent = build_agent(config, sink=renderer)
    except ModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not interactive:
        try:
            result = agent.send(args.prompt)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return 130
        if result.stop_reason == FINAL:
            return 0
        if result.stop_reason == CANCELLED:
            return 130
        if result.stop_reason == ERROR and result.error:
            print(f"error: {result.error}", file=sys.stderr)
        return 1

    tui = Tui(agent, renderer, style)
    try:
        return tui.run()
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
